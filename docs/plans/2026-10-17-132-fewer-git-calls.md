---
title: Make fewer git calls on each vault write
issue: "#132"
status: approved
approved_by: claude, the lead (autonomy grant)
approved_at: "2026-10-17"
---

# Make fewer git calls on each vault write

## Goal

Each write asks git each question once, and reads from the git dir's files what they hold: whether HEAD is on a
branch, and HEAD's commit id. Every commit, its message and what it holds stay as they are. A doc put goes from 12 git
calls to 7, a release from 39 to 22, an import of 200 notes from 217 to 13.

- In scope: the calls every commit makes (`Vault.commit`, `Git.commit`, the recovery check), the run files a commit
  sweeps in, curate's take-in, `put_many`'s doc histories, release's repeated HEAD questions, and a reader of `.git`
  files that #134 can share.
- Landed separately, in its own PR ("Part of #132", after #152): on Windows, git started without a console window of
  its own when Docket has no console (No console per git call, below). This plan builds on it and doesn't redo it.
- Out of scope: a folder vault's open, which #152 caches (two `git rev-parse` per open on main, none after the first
  per folder and process), the consuming repo's lookup for a mapper, `git lfs install` at init, the test kit's own git
  (#149). The staged check after `add` and one `ls-files -u` per commit stay (Design). Each write keeps its commits:
  none are merged.

## The count

Traced on this desktop (Windows 11, git 2.47): each path's command run in process on a fresh temp vault built by the
test kit, with conftest's isolated git and Docket folders, `subprocess.run` wrapped to record every git call, who
asked and how long it took. Three runs each, medians; a git call costs 16 to 20 ms here (the issue measured about
40 ms on CI).

| Path | Command traced | Calls | Asked twice | Git time | Wall time | Calls after |
|---|---|---|---|---|---|---|
| doc put | `docket doc put` (the service's `doc_put` makes the same 12) | 12 | 2 | 271 ms | 339 ms | 7 |
| promote | `docket staging promote`, one source | 13 | 4 | 282 ms | 351 ms | 8 |
| curate add | `docket curate add`, a create and an alias | 17 | 3 | 347 ms | 423 ms | 8 |
| release | one recording: four lock holds, two commits | 39 | 22 | 760 ms | 1,104 ms | 22 |
| import notes | `docket import notes`, 11 notes | 28 | 2 | 584 ms | 768 ms | 13 |
| `vault.yaml` write | `docket redaction set phone on` (`rewrite`) | 12 | 2 | 238 ms | 292 ms | 7, or 6 with #134 |
| ingest | `docket ingest` from a folder: stages, commits nothing | 1 | 0 | 17 ms | 137 ms | 1 |

"Asked twice" counts calls identical to an earlier one in the same write. Not all can go: promote asks for
uncommitted run files before and after it writes one, and release asks about its hold file again after a commit.

These are repo vaults, whose `Vault.open` asks git nothing. A folder vault's open adds two `git rev-parse` calls on
main; #152, likely merged first, makes it none after the first open per folder and process, so the counts above hold
for folder vaults too once it lands.

Expected, at the 17 ms a call costs here:

| Path | Calls removed | Time saved | Wall time after |
|---|---|---|---|
| doc put | 5 | about 85 ms | about 255 ms |
| promote | 5 | about 85 ms | about 265 ms |
| curate add | 9 | about 155 ms | about 270 ms |
| release | 17 | about 290 ms | about 815 ms |
| import notes, 11 notes | 15 | about 255 ms | about 510 ms |
| import notes, 200 notes | 204 | about 3.5 s | about 5.5 s of #136's 9 s |
| `vault.yaml` write | 5, or 6 with #134 | about 85 ms | about 205 ms |

Every write that commits makes this sequence, the recovery check twice when the commit runs inside `writing()`:

| Call | What it asks | After |
|---|---|---|
| `rev-parse --abbrev-ref HEAD` | is HEAD on a branch (the recovery check) | read from `.git/HEAD`, at every check |
| `ls-files -u` | any unmerged path (the recovery check) | right before each commit's `add`; no longer at `writing()`'s entry |
| the same two again, from `Vault.commit` in the same lock hold | the same | the branch from the file; `ls-files -u` is the one before the `add` |
| `ls-files -z --others -- raw` | run files git doesn't track, ignored ones too | one `git status` answers both |
| `diff --cached --name-only --diff-filter=A -z -- raw` | run files an add staged whose commit never came | (the same call) |
| `add` | | kept |
| `diff --cached --name-only -z -- .` | did the add stage any of these paths; else nothing to commit | kept |
| `config --get-regexp ^user[.](name\|email)$` | does git have an identity, or Docket passes its fallback | kept, once per opened vault |
| `commit -q --only` | | kept |
| `rev-parse HEAD` | the new commit's id | read from the branch's ref file |

Each path's own calls:

| Path | Calls today | After |
|---|---|---|
| doc put | `log -- <doc>`: the doc's revs, under the lock | kept |
| import notes | `rev-parse --show-toplevel` and `ls-files -co` on the notes folder; `log -- processed/legacy-note` for the latest revs; `rev-parse --verify HEAD`, `diff HEAD` and `ls-files --others` on `processed/legacy-note`; then one `log -- <doc>` per note under the lock (11 here; 200 in #136's measure, 3.6 s of its 9 s) | the HEAD test from files; the per-note logs as one `rev-list --merges` and one log over the batch's folders |
| curate add | `rev-parse --verify HEAD`, `diff HEAD` and `ls-files --others` on `entities/_log` for a stopped write's lines, then the same three on `entities` for the take-in | the HEAD tests from files; one `diff` and one `ls-files` of `entities`, filtered for `_log` |
| release | the recovery check six times (four lock holds, two nested commits), `rev-parse --verify HEAD` three times, the identity twice, `show HEAD:./.gitattributes` twice around a commit, the hold and log files' untracked and diff checks around a commit, `rev-parse --show-toplevel` for the mapper | `ls-files -u` once where the release starts, before the owner sees a screen, and before each of the two adds; the HEAD tests from files; the identity once; the rest kept, since a commit moved HEAD between |
| `vault.yaml` write | `show HEAD:./vault.yaml` | kept here; #134's cache of HEAD's `vault.yaml`, keyed by HEAD's id, answers it from a file while HEAD stays |

The whole suite, `-n auto` with a counting plugin (3,873 tests, 410 s): 24,222 git calls, 5,967 s of git summed
over 32 workers, about 45% of their time. The calls this plan removes:

| Kind | In the suite | Removed |
|---|---|---|
| `rev-parse --abbrev-ref HEAD` | 2,261 | all, read from `.git/HEAD` |
| `ls-files -u` | 2,227 | about 850: left, one per commit that adds |
| `diff --cached --name-only` | 2,711 | about 1,330, the staged-new half of the run-file check |
| `rev-parse HEAD` | 1,503 | about 1,330, the new commit's id |
| `rev-parse --verify --quiet` | 827 | about 800, HEAD's existence |
| `log` per doc in `put_many` | part of 879 | the import tests' |
| `config --get-regexp ^user` | 1,370 | each commit after the first through one opened vault: a release, and tests that write several times through one vault |

About 6,500 calls, 27%. At the suite's own cost per call that is about 1,600 worker-seconds of 5,967, about 12% of
the suite's time on this desktop: 410 s to about 360 s. CI's 4-core Windows runner spends a larger share in git (81%
of `test_docstore`), so it should gain more.

## Design

### The git dir's files (DR-0528)

A module, `vault/gitfiles.py`, reads what git keeps as plain files:

| Function | Reads | Answers |
|---|---|---|
| `head(git_dir)` | `HEAD` | a branch ref (`ref: refs/heads/main`), a commit id (detached), or unknown |
| `head_id(git_dir)` | `HEAD`, the ref's file under `refs/`, else its line in `packed-refs` | the commit id; "no commit yet" only when the ref has no file and `packed-refs` is absent or read cleanly without it; else unknown |

- It takes #152's rule rather than one of its own: it reads only a plain checkout's git dir, the top's `.git` that
  passes `home.is_git_dir`, which is what `home.discover` keeps an answer for. A git dir with a `commondir` file (a
  linked worktree) is unknown; Docket refuses those for a vault anyway (`home.py`).
- Unknown, and the caller makes today's git call: any read error, a git dir whose refs aren't files (reftable:
  `refs/heads/.invalid` in `HEAD`, or `extensions.refStorage` set), or a file that doesn't read as git writes it (an
  id not 40 or 64 hex).
- Used by: the recovery check's branch test, `changed_against_head`'s HEAD test, and `Git.commit`'s new id. `host.py`'s
  HEAD test runs when a folder vault is set up or converted, not on a write, and stays.
- Shared with #134, without either waiting: #134's committed-settings cache keys on HEAD's id read from these same
  files. Whichever merges first adds `gitfiles.py`; the other rebases onto it. #132 changes nothing in settings, and
  #134 can keep its own reader until then.

### No console per git call (landed separately)

Built in its own PR, "Part of #132", after #152 merges (both touch `home.py`), with a decision entry of its own. Kept
here as the measurement it rests on; this plan's steps don't include it.

The service runs under pythonw on Windows, with no console, and the vault's git runner passes no creation flags, so
each git process gets a console of its own. Timed on this desktop, 90 calls each (`git --version`, `rev-parse HEAD`,
`ls-files -u`), two runs:

| Docket's process | No flag, median | `CREATE_NO_WINDOW`, median |
|---|---|---|
| pythonw, no console | 157 to 170 ms | 32 ms |
| python, with a console | 17 to 18 ms | 29 to 31 ms |

- Every git process Docket starts (`Git.run`, `home.rev_parse`, `git.toplevel`, convert's calls) passes
  `CREATE_NO_WINDOW` when Docket's own process has no console, asked once per process (`GetConsoleWindow`), and no
  flag when it has one, where git inherits it. Elsewhere than Windows nothing changes.
- The models' and documents' workers pass the flag always, which is right for them: one worker per run. For git, the
  command line and the tests would pay 12 ms more on each of thousands of calls.
- For the service, the flag alone cuts each git call by about 130 ms: a doc put through it, 12 calls today, from about
  2 s of git to about 0.4 s, and to about 0.2 s with 7 calls.

### The recovery check (DR-0528)

The marker files (a rebase, merge, cherry-pick or revert in progress) and the branch test are file reads, so they run
at every check: at `writing()`'s entry and in every commit. `ls-files -u` moves to right before each commit's `add`
and no longer runs at `writing()`'s entry. Every commit asks it, one that stages nothing too, such as promote's
first, so a write during a conflict refuses as it does on main.

Why next to the add: `git add` marks an unmerged path resolved and stages its conflict markers, and `git commit
--only` re-reads the paths it names and resolves an unmerged one too. On git 2.47 a `git stash pop` conflict on a
vault file, followed by Docket's add and commit, put the markers into HEAD and left `ls-files -u` empty. A check
earlier in the lock hold can't see a conflict that appears after it, so the question sits as close to the commit as it
can.

A conflict that lands after the check and before the commit, such as a stash pop after Docket's add, is still
committed: the window is three git starts (`add`, the staged check, the identity), where it is about six on main. The
lead accepted that window rather than a second `ls-files -u` before each commit (Risks).

A refusal there leaves the index as git left it. On main a conflict that appeared mid-hold was refused by the commit's
check, and the write's cleanup then ran `git reset` on the doc and cleared the user's conflict. The refusal is now an
`UnmergedError`, and the doc store's and entity store's cleanups put their files back without the reset.

A writer that asks the owner something before its first commit asks for unmerged paths first, too: a release calls
`Vault.refuse_unmerged` in its first lock hold, before the screen and the code. Otherwise a conflict refused only the
release's own commit, after the owner typed the code, with the log line left on disk; the next run then committed that
line and said the recording was held again by a merge, which was false (F1, the code review of #156). One call per
release.

The check stays vault-wide, a plain `ls-files -u` from the vault's folder as today, not limited to the commit's paths:
`ls-files` takes no `--pathspec-from-file`, and a backlog of run files on the command line could pass Windows'
32,767-character limit.

### Each question once (DR-0529, DR-0530)

| Question | Today | After |
|---|---|---|
| uncommitted run files | `ls-files --others` and `diff --cached --diff-filter=A` | `git --no-optional-locks status --porcelain=v1 -z --untracked-files=all --ignored=traditional -- raw`: `??` and `!!` are what `ls-files --others` lists, `A` in the first column what `diff --cached --diff-filter=A` lists; a staged rename's second NUL field is read as part of its entry. Status names paths from the repo's top, so the prefix is taken off as `_vault_names` does (checked on a folder vault: the same three files both ways) |
| curate's entity paths | `entities/_log`, then `entities` | `entities` once, filtered for `_log`; asked again only if `_finish` committed |
| `put_many`'s histories | one `log -- <doc>` per doc | `rev-list --merges -1 HEAD -- <the batch's doc folders>` first. No merge: one `log -z --no-renames --no-show-signature --format=%H%x1f%B --name-only -- <the folders>` under the lock, names made vault-relative as `_vault_names` does; a commit counts for a doc when it touched the doc's file and its message has the doc's `Doc:` line, as each per-file log counts it. A merge there: one log per doc, as today, since a folder log isn't the union of per-file logs across a merge (a rev the merge discarded can appear, and the newest rev can come from the discarded side) |
| HEAD's `.gitattributes` | `show HEAD:./.gitattributes` per check | once per HEAD id in a lock hold |
| git's identity | per commit | once per opened vault, which lives for one command or one service call (DR-0530) |

A question about the working tree or the index is asked again after any write of Docket's own: promote's two run-file
checks around the run file it writes both stay.

### What stays

- `ls-files -u` once per commit: the index holds the answer, and reading git's index format (four versions,
  split and sparse indexes) would be a second implementation to keep right.
- The staged check after `add`: it decides "nothing new" exactly. A status before the add can't tell a file equal to
  HEAD from one whose staged copy differs; git would then refuse an empty commit where Docket now returns nothing.
- Git's identity beyond one opened vault: the service opens the vault for every call, so a config changed in place
  between two calls still decides the next author as git would (DR-0530).

## Doors and data

None. No file, format or name changes; `.git` files are only read. A folder vault reads its host's git dir the same
way.

## Steps

1. Pin `init.defaultRefFormat=files` in the tests' git config (`TEST_GITCONFIG`, which conftest and `isolated_git`
   share), so the call lists don't depend on the git a machine has.
2. `gitfiles.py`, with `test_gitfiles.py` holding it to git's own answers.
3. The branch test from files at every check, `ls-files -u` right before each commit's add, the identity once per
   opened vault; call-count tests for doc put and promote, and the mid-hold conflict test.
4. `Git.commit`'s new id and `changed_against_head`'s HEAD test from files.
5. The run-file check as one status call, held to the two calls' answers.
6. curate's entity paths once; `put_many`'s histories in one log unless a merge touched the folders; HEAD's
   `.gitattributes` once per HEAD.
7. A call-count test per path; the trace before and after on Windows; the suite's git time with the counting plugin.

## Tests

| Test | What it proves |
|---|---|
| `test_git_calls.py`, new | per path (doc put, promote, curate add, import notes, `vault.yaml` write) on a temp vault, the exact list of git commands, so a call added later fails with its name; doc put and promote on a folder vault too. A release's list is in `test_release.py`, beside its owner at the console |
| the mid-hold conflict | a `git stash pop` conflict on a vault file that appears inside `writing()`, after its entry and before the commit's check: the commit refuses, HEAD stays, the markers aren't committed, and the conflict stays unmerged (on main the cleanup's reset cleared it) |
| a conflict after the add | a `git stash pop` conflict that lands after Docket's `add` and before its commit is committed, as on main: the test pins today's behaviour, so a later change to it is deliberate |
| `test_gitfiles.py`, new | `head` and `head_id` equal `git rev-parse` on a fresh repo, a branch with no commit, a detached HEAD, a ref only in `packed-refs`, a git dir with `commondir`, a folder vault's host; a malformed ref file and an unreadable `packed-refs` answer unknown, and the caller's git call runs |
| reftable | one vault made with `init.defaultRefFormat=reftable`: the reader answers unknown and the fallback calls run, so its call list has today's calls; skipped where the git lacks reftable |
| the run-file check | status and the two calls give the same list: an untracked run file, one a `.gitignore` hides, one staged by an add whose commit never came, a staged rename, none; on a repo vault and a folder vault |
| the batched histories | the same revs as one log per file: a hand commit that touched a doc without a `Doc:` line, a commit whose message names a doc it didn't touch, a doc deleted and put again; a folder vault under a non-ASCII path; and a merge in the doc folder's history, where one log per doc runs and the revs match |
| every vault test | unchanged, as the issue asks: commits, messages and trees as before |

Measured in the PR: each path before and after with the same trace (three runs, Windows), and the suite's git time
and wall time.

## Docs

- DR-0528 to DR-0530, proposed with this plan.
- `vault/git.py`, `vault/vault.py` and `vault/gitfiles.py` docstrings: what reads files and when git is asked.
- A `CHANGELOG.md` line: each write makes fewer git calls, with the doc put and import numbers.

## Risks

| Risk | Answer |
|---|---|
| a `.git` layout the reader gets wrong would give `Git.commit` a wrong id | it answers only a ref file or a cleanly read `packed-refs` line holding 40 or 64 hex, else unknown and today's call; tested against `git rev-parse` per layout |
| Git 3.0 makes reftable the default ref format | a vault made under it reads unknown and runs today's calls: right, and as slow as now; reading reftable or asking `rev-parse` once for both answers is a follow-up then. The tests pin the files format, and one reftable case keeps the fallback tested |
| status and the two calls differ under some config (`status.showUntrackedFiles`, the untracked cache, case on Windows) | the flags given override the config; equivalence tests on all three systems |
| the folder log lists a doc's commits differently from a per-file log | without a merge, the commits that touched the file are in both; with one, `rev-list --merges` sends `put_many` back to one log per doc; tested both ways |
| a user's git starts within one write | marker files and the branch are read at every check, and `ls-files -u` sits right before each add |
| a conflict lands between the check and the commit (a stash pop after Docket's add) | committed with its markers, as on main, in a window of three git starts against six; accepted by the lead. Not taken: a second `ls-files -u` before each commit (one more call per commit, and the window only moves), or a private index built from Docket's own bytes (a second staging of every commit). Pinned by a test |
| #134 changes `Vault` and the git layer too | the shared reader is one module; either rebases onto the other |
| #152 changes `home.py` | the reader uses `is_git_dir` and #152's plain-checkout rule; whichever lands second rebases |
| savings smaller elsewhere | a git call costs about 5 ms on macOS and Linux, so the share saved is the same and the seconds fewer |

## Plan review

| Round | Verdict | Findings |
|---|---|---|
| code review of #156 | one minor | F1: a release during a conflict reached the owner's code prompt before its commit refused, since `writing()` no longer asks for unmerged paths; the release now asks at its start, before the screen, and a test holds it to no prompt and nothing written |
| 2, the G1 re-check at ad92fab | resolved, one minor | G1a: `commit --only` resolves an unmerged path it names too, so a conflict between the check and the commit is still committed; the reason is corrected, the lead accepted the window (a Risks row, with the options not taken), the check stays vault-wide, and a second test pins a pop after the add. Approved by the lead after it |
| additions after round 1 | from the coordinator | #152's discover: the count notes folder vaults' opens, and the reader takes its plain-checkout rule and `is_git_dir`. The console flag: measured under pythonw and python, then moved to its own PR ("Part of #132") with its own decision entry; DR-0530 stays the identity alone |
| 1, at 90d0dac | changes needed | G1 (major): one `ls-files -u` per hold let a mid-hold conflict be committed; it now sits before each add. G2: the batched log takes `-z`, `--no-renames` and vault-relative names, tested under a non-ASCII path. G3: a merge in the folders' history sends `put_many` back to one log per doc, decided by one `rev-list --merges`. G4: the tests pin the files ref format, one reftable case tests the fallback, and Git 3.0's default is a risk. The reader answers "no commit yet" only from a cleanly read `packed-refs`, and status parsing reads a staged rename's second field |
