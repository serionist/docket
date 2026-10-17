---
title: Make fewer git calls on each vault write
issue: "#132"
status: draft
approved_by:
approved_at:
---

# Make fewer git calls on each vault write

## Goal

Each write asks git each question once, and reads from the git dir's files what they hold: whether HEAD is on a
branch, and HEAD's commit id. Every commit, its message and what it holds stay as they are. A doc put goes from 12 git
calls to 7, a release from 39 to 21, an import of 200 notes from 217 to 13.

- In scope: the calls every commit makes (`Vault.commit`, `Git.commit`, the recovery check), the run files a commit
  sweeps in, curate's take-in, `put_many`'s doc histories, release's repeated HEAD questions, and a reader of `.git`
  files that #134 can share.
- Out of scope: a folder vault's open (one `rev-parse` per `Vault.open`), the consuming repo's lookup for a mapper,
  `git lfs install` at init, the test kit's own git (#149). The staged check after `add` and one `ls-files -u` per
  commit stay (Design). Each write keeps its commits: none are merged.

## The count

Traced on this desktop (Windows 11, git 2.47): each path's command run in process on a fresh temp vault built by the
test kit, with conftest's isolated git and Docket folders, `subprocess.run` wrapped to record every git call, who
asked and how long it took. Three runs each, medians; a git call costs 16 to 20 ms here (the issue measured about
40 ms on CI).

| Path | Command traced | Calls | Asked twice | Git time | Wall time | Calls after |
|---|---|---|---|---|---|---|
| doc put | `docket doc put` (the service's `doc_put` makes the same 12) | 12 | 2 | 271 ms | 339 ms | 7 |
| promote | `docket staging promote`, one source | 13 | 4 | 282 ms | 351 ms | 7 |
| curate add | `docket curate add`, a create and an alias | 17 | 3 | 347 ms | 423 ms | 8 |
| release | one recording: four lock holds, two commits | 39 | 22 | 760 ms | 1,104 ms | 21 |
| import notes | `docket import notes`, 11 notes | 28 | 2 | 584 ms | 768 ms | 13 |
| `vault.yaml` write | `docket redaction set phone on` (`rewrite`) | 12 | 2 | 238 ms | 292 ms | 7, or 6 with #134 |
| ingest | `docket ingest` from a folder: stages, commits nothing | 1 | 0 | 17 ms | 137 ms | 1 |

"Asked twice" counts calls identical to an earlier one in the same write. Not all can go: promote asks for
uncommitted run files before and after it writes one, and release asks about its hold file again after a commit.

Expected, at the 17 ms a call costs here:

| Path | Calls removed | Time saved | Wall time after |
|---|---|---|---|
| doc put | 5 | about 85 ms | about 255 ms |
| promote | 6 | about 100 ms | about 250 ms |
| curate add | 9 | about 155 ms | about 270 ms |
| release | 18 | about 305 ms | about 800 ms |
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
| release | the recovery check six times (four lock holds, two nested commits), `rev-parse --verify HEAD` three times, the identity twice, `show HEAD:./.gitattributes` twice around a commit, the hold and log files' untracked and diff checks around a commit, `rev-parse --show-toplevel` for the mapper | `ls-files -u` before each of the two adds; the HEAD tests from files; the identity once; the rest kept, since a commit moved HEAD between |
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
| `head_id(git_dir)` | `HEAD`, the ref's file under `refs/`, else its line in `packed-refs`, through `commondir` when there is one | the commit id; "no commit yet" only when the ref has no file and `packed-refs` is absent or read cleanly without it; else unknown |

- Unknown, and the caller makes today's git call: any read error, a git dir whose refs aren't files (reftable:
  `refs/heads/.invalid` in `HEAD`, or `extensions.refStorage` set), or a file that doesn't read as git writes it (an
  id not 40 or 64 hex).
- Used by: the recovery check's branch test, `changed_against_head`'s HEAD test (`git.py` and `host.py`), and
  `Git.commit`'s new id.
- Docket refuses linked worktrees (`home.py`), so a vault's git dir is its common dir. `commondir` is followed anyway,
  so the reader is right for #134 too.
- Shared with #134, without either waiting: #134's committed-settings cache keys on HEAD's id read from these same
  files. Whichever merges first adds `gitfiles.py`; the other rebases onto it. #132 changes nothing in settings, and
  #134 can keep its own reader until then.

### The recovery check (DR-0528)

The marker files (a rebase, merge, cherry-pick or revert in progress) and the branch test are file reads, so they run
at every check: at `writing()`'s entry and in every commit. `ls-files -u` moves to right before each commit's `add`
and no longer runs at `writing()`'s entry. A commit that adds nothing, such as promote's first, asks it not at all.

Why next to the add: `git add` marks an unmerged path resolved and stages its conflict markers, and `git commit
--only` then succeeds. On git 2.47 a `git stash pop` conflict on a vault file, followed by Docket's add and commit,
put the markers into HEAD and left `ls-files -u` empty. A check earlier in the lock hold can't see a conflict that
appears after it, so the question sits where the conflict would be taken in.

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

- `ls-files -u` once per commit that adds: the index holds the answer, and reading git's index format (four versions,
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
| `test_git_calls.py`, new | per path (doc put, promote, curate add, release, import notes, `vault.yaml` write) on a temp vault, the exact list of git commands, so a call added later fails with its name; doc put and promote on a folder vault too |
| the mid-hold conflict | a `git stash pop` conflict on a vault file that appears inside `writing()`, after its entry: the commit refuses, HEAD stays, the markers aren't committed |
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
| #134 changes `Vault` and the git layer too | the shared reader is one module; either rebases onto the other |
| savings smaller elsewhere | a git call costs about 5 ms on macOS and Linux, so the share saved is the same and the seconds fewer |

## Plan review

| Round | Verdict | Findings |
|---|---|---|
| 1, at 90d0dac | changes needed | G1 (major): one `ls-files -u` per hold let a mid-hold conflict be committed; it now sits before each add. G2: the batched log takes `-z`, `--no-renames` and vault-relative names, tested under a non-ASCII path. G3: a merge in the folders' history sends `put_many` back to one log per doc, decided by one `rev-list --merges`. G4: the tests pin the files ref format, one reftable case tests the fallback, and Git 3.0's default is a risk. The reader answers "no commit yet" only from a cleanly read `packed-refs`, and status parsing reads a staged rename's second field |
