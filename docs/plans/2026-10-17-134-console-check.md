---
title: Ask at the owner's console before the CLI loosens the gate
issue: "#134"
status: draft
approved_by:
approved_at:
---

# Ask at the owner's console before the CLI loosens the gate

A loosening of `vault.yaml` applies only once committed, and the CLI commits one only after the owner types back a
code at their own console. The gate obeys HEAD's `vault.yaml` plus every part of an uncommitted edit that only keeps
more out; the release's check, moved to one module, asks in `settings.rewrite` for a command's loosening change and for
an uncommitted loosening edit it would take in. A stricter change asks nothing, and applies at once, as today.

## Goal

| Part | The issue's "done when" | This plan |
|---|---|---|
| Which | the loosening commands listed | every change `rewrite` makes for the CLI that `loosens(before, after)` flags: today `docket redaction set <rule> off`, `docket source new` adding a source or changing its entry, and `docket source new --folder` adding one or changing its folder; a command added later is asked by default. Also an uncommitted edit that loosens, which #141 refuses to take in: a CLI write at a console asks instead. And the looser part of a hand edit no longer applies before it is committed |
| Console | each asks as a release does: refused under `CLAUDECODE`, a pipe or a redirect, the code typed back | the release's check, code and question, moved to `docket/console.py` and called by both |
| Scripts | a documented way for the owner's scripts, if one is needed, that an agent can't reach | nothing to build: whatever a script running as the owner holds, an agent running as the owner reads too; the README says there is no way around the question |
| Tests | each command refuses under `CLAUDECODE=1` and from a pipe, and goes ahead at a console | per command: `CLAUDECODE=1`, a real pipe into a child process, the owner fixture typing the code back; a real terminal on Linux and macOS |

- In scope: the settings in force and their cache; the doc screen reading a doc as written; `docket/console.py`; the
  question in `rewrite`, for the command's change and for the take-in of an uncommitted edit; the three commands'
  output; the notice in `docket vault status`, the Settings page and the refusals that send the owner to `vault.yaml`;
  the setup skill handing its loosening commands to the owner one at a time; the door line, the glossary, the README;
  the tests.
- Out of scope:
  - the issue's Scripts row: nothing to build (Scripts, below);
  - a never-store value taken off: nothing to build, since no command does it (DR-0508); it is a hand edit, which
    the settings in force cover;
  - `docket init`: it writes a new vault's first `vault.yaml`, with nothing stored and no gate yet to loosen;
  - `docket vault convert`: it moves where the vault lives, not what it lets in, and asks its own question;
  - `docket staging classify`: it answers the gate's own question for one record and changes no setting;
  - a page taking in an uncommitted edit that loosens: it keeps #141's refusal;
  - an OS prompt (Windows Hello, Touch ID, polkit), for the release and the loosening together: an issue of its own.

## Design

### The settings in force

An uncommitted edit keeps more out at once and lets more in only once committed (DR-0521). Without this, an edit
alone loosens: Claude under auto mode deletes a person from `never_store.people`, runs `docket ingest`, and the
recordings reach raw, before any commit or question.

`effective(committed, file)` is HEAD's settings with every part of the file that only keeps more out put in, field by
field; everything else is HEAD's. Each field was checked against what the gate and the jobs do with it:

| Field | Applies at once | Why it only keeps more out |
|---|---|---|
| a never-store list | the values added; a value the file took off stays | a value only drops records |
| `credentials`, `phone` | on, if either has it on | a rule only puts markers in; every never-store screen reads the text as written (The doc screen, below) |
| a pattern of HEAD's | turned on, with nothing else of it changed | as above |
| a pattern under a name HEAD doesn't have, on or off | run after HEAD's patterns, wherever the file puts it | each rule reads only the text between markers, so one that runs last only adds markers; one run before an existing pattern could split that pattern's match and leave the rest, so the place the file gives it waits for the commit |
| a source's `sensitivity` | the stricter of the two | work to mixed to private |
| a source's `enabled` | off, if either has it off | an off source's records are refused at intake |
| a source's `exclude` | the entries added; an entry the file dropped stays | `exclude` is fnmatch with no negation, so an entry only leaves paths out, and their files go as gone, as a deleted file does |
| a source's `tools` | HEAD's tools the file still lists | a run takes its tools when it opens, and a capture from a tool not on the list marks the run short: the cursor stays and nothing more is stored |
| a source removed | HEAD's other sources | intake refuses its records as from an unknown source, promote holds what waits from it, and ingest, scan and sync refuse it; a rename is a removal and an addition, and only the removal applies at once |

What waits for the commit: a source added or turned on, a looser sensitivity, a never-store value taken off, a rule
turned off, a pattern changed, removed or put before another, an exclude entry dropped, a tool added, a mapper, folder
or media host changed, the owner, the primary machine, storage, and any key added later. `media_hosts` waits even for a
removal: nothing reads it until media_put (phase 2) says what a host drives.

| Part | What |
|---|---|
| one rule | `loosens(before, after)` is `effective(before, after) != after`: the per-field rules above are the only list. DR-0506 is amended knowingly, with a binds line added with this plan: `loosens()` no longer calls the four added changes (a pattern added and run last, an exclude entry added, a tool removed, a source removed) loosening. Pages gain no action: they still call only their own writers (`add_listed`, `set_rule` turning on, PR 3's), which the scan's general-entry row and `FOR_PAGES` hold |
| when the rest applies | once committed: by the owner's own `git commit`, by a CLI write at the console taking it in, or by a CLI command's own change at the console |
| the notice | `docket vault status` and the Settings page: "vault.yaml has an edit that isn't in force until you commit it". It names no value, and never says the edit lets more in: what waits can be neutral, such as a time zone |
| the refusals | the messages that send the owner to `vault.yaml` add the notice when the file isn't in force: sync's "lists no tools" (`service/sync.py`), the runner's missing source and missing mapper (`sources/runner.py`), the gate's "isn't in vault.yaml; add it" (`vault/gate.py`), the scan's missing source (`documents/scan.py`). #141's take-in refusal takes the same words: "vault.yaml has an edit that isn't in force until you commit it; commit it yourself or undo it, then try again" |
| HEAD unreadable | HEAD with no `vault.yaml`, or one that doesn't parse, stops Docket as a broken `vault.yaml` does today, with #141's message to commit it yourself. Init commits the file, and a folder vault's open already wants it tracked, so normal use never gets there |

Who reads which:

| Reader | Reads |
|---|---|
| `Gate._config()`, every call: stage, promote, the sweep, a release's staging | the settings in force |
| `Vault.open`, so `vault.config`: ingest and the runner's mapper, `docket scan`, `doc_put` and `docket doc redact`, `docket import notes`, the service's sync, curate's never-store hiding, `docket redaction` (with the notice), search's time zone | the settings in force, one loader for both |
| `rewrite` | the file: it changes the file, and weighs the take-in against HEAD and the change against the file |
| the Settings page | the file as written, with the notice at the top when it isn't in force |
| the session-start hook | the file, only to say whether it loads, as today |
| `docket vault convert` | the file: it rewrites `storage` and commits it after its own question |

- `Vault.open` finds the git dir from the file's `storage`, as today. An uncommitted change of `storage` stops
  Docket with the notice, since the git dir to read HEAD from depends on it; only convert moves it, and convert
  commits.
- `effective()` and the per-field rules live in a module of their own with no `Vault`, which `vault.py`, `gate.py`
  and `settings.py` import; `settings` keeps `loosens` for pages.

### The doc screen

`doc_put`, `docket doc redact` and a doc's hand-edit take-in screen never-store on the doc as written, as well as on
the redacted text they screen today (`derived.check`, `derived.hand_edit`).

- Today they redact first, so a rule turned on can hide a never-store keyword inside its marker and let in a doc the
  lists refuse: a page turning a rule on can do it now, and so could an edit in force. Intake, `docket scan` and
  `docket import notes` already match before they redact.
- With the doc read as written, turning a rule on or adding a pattern only keeps more out on every path.
- A doc that says a keyword inside what a rule redacts, stored today with a marker, is refused; the problem names the
  line as written and never the keyword.

### No git call per gate run

The service opens the vault on every call, and #132 is removing git calls, so the committed settings are cached.

- The key is HEAD's commit id, read from files: `HEAD`, then its ref under `refs/` or in `packed-refs`, through
  `commondir` in a linked worktree. While HEAD stays, no git call.
- Docket's own commits never take `vault.yaml` unless they name it (`--only`). The call that already reads a new
  commit's id reads its parent too: when the parent is the cached commit, the key moves forward with no call, and a
  commit naming `vault.yaml` stores its new text. Ingest after ingest makes no extra call.
- A miss (the owner's commit, a pull, another machine's merge) costs one `git show <id>:./vault.yaml`, with the id just
  read from `.git`, never `HEAD:`: a commit landing between the two reads can't put one commit's text under another's
  id. A repo on reftable, whose refs aren't files, reads the id with `git rev-parse HEAD` first: correct, slower.
- The cache is a file in the vault's private folder, under the git dir and never committed, written through a temp
  file and a rename. It is disposable like the run history: one that doesn't read is a miss. Its text is HEAD's,
  already in git.
- When the working file equals HEAD's text, the settings in force are the file's, with no comparison.

### Which changes ask

One rule at the one writer: `rewrite(via="cli")` asks when `loosens()` is true for the command's change, `before`
being the file as it stands, or for the uncommitted edit it would take in first, against HEAD's `vault.yaml`.

| Command | Change | Asks |
|---|---|---|
| `docket redaction set <rule> off` | credentials, phone or a pattern turned off | yes |
| `docket redaction set <rule> on` | turned on | no |
| `docket source new <name>` | a source added; or an entry already in `vault.yaml`, whose folder is gone, given a looser sensitivity, other tools or the mapper | yes |
| `docket source new <name> --folder <folder>` | a folder source added, or its folder changed | yes |
| either, giving the entry `vault.yaml` already has | nothing | no |
| any of them, a stricter one too, while `vault.yaml` holds an uncommitted edit that loosens against HEAD's | the take-in of that edit (A hand edit, below) | yes |
| a command added later that writes through `rewrite` | whatever `loosens()` says | by default |

- A stricter change with nothing loosening to take in asks nothing: the setup skill still turns phone redaction on,
  and the Settings page tightens as before. An uncommitted edit that only keeps more out, a pattern added by hand
  among them, is taken in unasked, as #141 has it.

### The question

The release's question, moved and shared, not copied. One question per command, whether the edit, the change or both
loosen.

| Step | What |
|---|---|
| refuse | something loosens and `console.problem()` finds `CLAUDECODE` set, or no console on stdin and stdout: refused before anything is written or committed, whatever the file's form; the message says which, the edit or the change |
| by hand | a file not in Docket's form gets its line printed, as today, and no code: nothing is written or taken in |
| show | for each part that loosens, the uncommitted edit against HEAD first, then the command's change: each change on a line of its own by its full path, such as `sources.calls.sensitivity: mixed -> work` or `redaction.phone: true -> false`, a source added with its keys under it; each never-store list as `never_store.people: 1 taken off, 1 added`, never a value, so a swap reads as one off and one added; then the command's warnings |
| ask | `Type KXQM to make this change, or n to leave vault.yaml as it is:`, one code drawn fresh for both parts |
| write | under the vault lock, only when the file's sha256 and HEAD's `vault.yaml` text are the ones asked about; else nothing, and "vault.yaml changed while you were asked; run the command again" |
| commit | as today, `Config: vault.yaml as edited` for the edit and `Config: redaction phone off` for the change, each part the owner confirmed with `confirmed at the console` in its body |

- The re-check compares HEAD's `vault.yaml`, not HEAD's commit id: Docket's own ingest commits while the owner reads,
  and a commit of another file must not stop the question. The cache answers it with no call when HEAD moved only by
  Docket's commits.
- The warnings reach the screen through a named parameter, `rewrite(..., warnings=...)`: a list the command knows
  from its own arguments, such as `set_rule`'s credentials-off note. `edit` keeps its one job, and the signature test
  pins the parameter.
- Asked outside the vault lock, as the release asks: the service's ingest and captures never wait on a person.
- `edit` runs once, so the change written is the change shown.
- The arrow is ASCII, so the screen reads the same on any console's code page.
- A HEAD with no `vault.yaml`, or one that doesn't parse, keeps #141's refusal at a console too: there is nothing to
  show the edit against.
- A no, a wrong code or the end of input writes and commits nothing and exits 1, as a hand-edited file does today;
  `docket source new` then removes the folder it wrote, as on any failure.

### docket/console.py

`sources/release.py`'s console part moves here, its behaviour and wording unchanged.

| Here | From the release |
|---|---|
| `problem(act)` | `_console_problem`: `CLAUDECODE` first, then stdin and stdout; `act` is "a release" or "a change that lets more into the vault" |
| `is_console(stream)` | `_is_console` and `_console_fd`: `GetConsoleMode` on both handles on Windows, `isatty` elsewhere |
| `code()`, `ask(prompt)`, `say(line)` | four letters with no I or O; the end of input answers q; every line through `printable` |
| `typed_back(lines, ...)` | `_question`: the screen, the code, then yes, no or stop |

- `release.py` and `settings.py` call it through the module (`console.ask(...)`), so a test stands in for the owner
  in one place.
- `rewrite` gains no parameter that answers, and `via` stays `cli` or `page`; a test pins its parameters, as
  `release()`'s are pinned.

### Windows, macOS and Linux

| System | A console | Refused |
|---|---|---|
| Windows | Windows Terminal, PowerShell, cmd, VS Code's terminal: `GetConsoleMode` works on both handles | a pipe, `< NUL`, `> file`, Git Bash's mintty without `winpty`, the service under `pythonw` |
| macOS, Linux | a terminal, or `ssh -t`: both ends a tty | a pipe, a redirect, `ssh host cmd`, cron, launchd, systemd |

- The refusal says where to run it, as the release's does: your own terminal with nothing piped in or out, in Git
  Bash's own window through `winpty`.
- The cache's key is read from files the same way on all three; paths are joined with `pathlib`, and a ref file's
  line ending is stripped.

### Scripts

Nothing to build: none is needed, and none could be out of an agent's reach.

- A script of the owner's runs as the owner: a key in the keychain, a file or a variable it reads, an agent running
  as the owner reads too, so a path for scripts would be the agent's path.
- Loosening is a setup-time change, rare enough for the console, or a hand edit and the owner's own commit.
- The human-only factor is an OS prompt, which DR-0382 and DR-0506 already defer to an issue of its own.

### A hand edit of vault.yaml

It stays possible, and it is the owner's own console edit; what only keeps more out applies at once, the rest once
committed.

- Every part of an uncommitted edit that only keeps more out applies at once (The settings in force); the rest waits,
  and the notice says so.
- The owner's own `git commit` of the edit is theirs, and applies it.
- Before a write, #141's take-in weighs an uncommitted edit by `loosens(HEAD's config, the file's config)`. One that
  doesn't loosen is taken in unasked, `Config: vault.yaml as edited`, as #141 has it.
- One that loosens is asked about before a CLI write, a stricter one too, with the command's change in the same
  question; on the yes it is taken in with `confirmed at the console`. A page keeps refusing it.
- Claude's edit of the file passes Claude Code's permission prompt with its diff. Under auto mode too, it applies
  nothing that loosens, and the next Docket write in Claude's shell is refused, never committed silently. The skills
  forbid a loosening edit and give the owner the line instead, as the Settings page does.

### The skills, the docs and the page

- setup: the sources the user picks are given to the owner one `docket source new` command at a time, each run in
  their own terminal before the next: a pasted block would feed its second line into the first one's question in
  conhost. Each shows what it adds and asks for its code. Claude still runs `docket init`, and turns a redaction
  rule on. "Either can change later" says turning one off asks at the owner's console.
- setup and cuts: never run a loosening command, edit `vault.yaml` to loosen, commit `vault.yaml`, wrap a command in
  a console of your own or clear `CLAUDECODE`; auto mode changes nothing. A refusal naming an uncommitted edit goes to
  the owner as it stands.
- The docs that send the owner to `vault.yaml` say an edit applies once committed: the by-hand step 2 in
  `examples/sources/plaud/SKILL.md`, the tools line in `plugin/skills/setup/sources.md`, the Sync page's hint
  ("then list its tools in vault.yaml"), and the README's `docket doc redact` row, which redacts with the settings in
  force.
- The Settings page: each loosening command says to run it in your own terminal, as the Staging page says for a
  release, and the notice sits at the top while the file isn't in force.

### Decided

Taken under the autonomy grant, each proposed for the maintainer's review.

| Decided | Not taken | Entry |
|---|---|---|
| ask in `rewrite` for every CLI change `loosens()` flags | a list of commands, or a check per command in `cli.py`: a later command loosens unasked, and a Python caller of `set_rule` skips it | DR-0517 |
| a CLI write at a console asks about an uncommitted edit that loosens, where #141 refuses; a page keeps refusing | #141's refusal at a console too: the owner who made the edit commits it by hand to go on, when the same question would do | DR-0517 |
| adding a source asks, and setup hands the commands over one at a time | sources added turned off, turned on at the console: a new command and DR-0506's list moved; a block of commands: conhost feeds line 2 into line 1's question | DR-0517 |
| init and convert left outside | asking there too: init has no gate yet, and convert lets no record in | DR-0517 |
| one console module for the release and the loosening | a copy in `settings.py`: two checks to keep in step, on three systems | DR-0518 |
| one question and one code for the edit and the change | two questions: two codes for one command, and a no to the second leaves the first committed | DR-0519 |
| each change by its full path, each list as taken off and added | a unified diff of the file: a swap hides as two moved lines, and its context lines carry values; a bare count: a swap reads as no change | DR-0519 |
| the file and HEAD's `vault.yaml` checked again, asked outside the lock | HEAD's commit id: Docket's own ingest commits while the owner reads; asking under the lock: the service waits on a person | DR-0519 |
| the warnings as a named parameter | `edit` returning its notes: its type changes for every caller, for one command's note | DR-0519 |
| `confirmed at the console` in each confirmed commit's body | no mark: git log can't tell which config commits the owner confirmed | DR-0519 |
| no way around the question, for scripts either | a token or a variable for scripts: Claude reads what a script reads | DR-0520 |
| the gate obeys HEAD plus every part of an uncommitted edit that only keeps more out, through one loader for every job that decides what gets in | the file as it stands: an edit lets records into raw before anyone confirms it; refusing every write while the file loosens: every sync stops on a stray edit | DR-0521 |
| one rule for `effective()` and `loosens()`, widened to every change that only keeps more out; DR-0506 amended knowingly | a separate, larger set for `effective()`: two rules drift, and the notice and the take-in would call a new pattern a loosening | DR-0521, DR-0506 |
| a new pattern runs after HEAD's until committed, wherever the file puts it | the file's order at once: a pattern put first can split an existing pattern's match | DR-0521 |
| the doc screen reads a doc as written too | patterns left waiting instead: rules turned on keep the hole, and a hand-added pattern lets the codes it names into raw until committed | DR-0521 |
| the committed settings cached by HEAD's commit id, read from files, moved forward by Docket's own commits, a miss read by that id | a git call per read: the service opens the vault on every call; `HEAD:` on a miss: a commit between the reads keys the wrong text | DR-0521 |

## Doors and data

| Door | What this plan does |
|---|---|
| the gate's loosening (DR-0506) | amended in the PR whose title names it: a loosening applies only once committed (DR-0521), and the CLI makes one, or takes in an edit that makes one, only at the owner's console with a code typed back (DR-0517); both `cost: one-way`; AGENTS.md's line says so. DR-0506 gains a binds line now: `loosens()` no longer calls a pattern added and run last, an exclude entry added, a tool removed or a source removed loosening; pages gain no action |
| what the gate obeys (DR-0025) | every call reads the settings in force, not the file as it stands; DR-0025 gains that line with `docs set` |
| the take-in (DR-0507, #141) | its refusal of an uncommitted loosening edit stays for pages, and for the CLI without a console; at a console the CLI asks instead. DR-0507's take-in line gains that, with `docs set`; its message takes the notice's words |
| the release's yes (DR-0202, DR-0382) | its check moves to `docket/console.py`, unchanged: the same refusals, prompt, code and answers; `release()`'s signature and the scans stay |
| `doc_put` (DR-0150) | its name and parameters unchanged; it refuses a doc whose never-store keyword a rule would hide |
| `vault.yaml` (DR-0021) and its keys | unchanged: no key added, the same Docket form |
| where Docket's own files live (DR-0141) | unchanged: the cache is a disposable file in the private folder, like the run history, not a door |
| the plugin's skill names | unchanged; the text of setup and cuts changes |

- Users' data: nothing migrates. A vault whose file holds an uncommitted edit that loosens when this ships stops
  applying that part until it is committed; `CHANGELOG.md` says so under Changed, with the notice's words.
- A confirmed commit carries one more body line.
- An owner's script that ran `docket redaction set <rule> off` or `docket source new` now gets the console refusal;
  `CHANGELOG.md` says so too.

## Steps

One PR, on main after #141 (1da3e24). Its title, "Ask at the owner's console before the CLI loosens the gate", names
the door. Each step leaves the suite green.

1. `docket/console.py` from `release.py`'s console part; `release.py` calls it. The `Owner` class and the `owner`
   fixture move from `test_release.py` to `conftest.py`, patching `docket.console`. Tests: `test_console.py` (the
   release's three console cases, moved); `test_release.py` otherwise unchanged.
2. `effective()` and the per-field rules in their own module; `loosens()` defined by it. Tests:
   `test_vault_settings.py`: the loosens table changes in four rows only (a pattern added after HEAD's, an exclude
   entry added, a tool removed, a source removed: no longer loosening), and an effective table.
3. The doc screen reads a doc as written: `derived.check` and `derived.hand_edit`. Tests: `test_derived.py`,
   `test_doc_redact.py`.
4. The settings in force: the loader and its cache, `Vault.open` and `Gate._config()` on it, the carry-forward in
   `Git.commit`, the notice in `docket vault status` and the four refusals, #141's message in its words. The test
   helpers that loosen by writing `vault.yaml` commit it, as the kit's `configure` does: `test_gate.py`'s `rewrite`
   helper, so `test_phone_turned_off_makes_a_record_sent_again_a_new_version_holding_the_number` and its neighbours
   bite as before. Tests: `test_settings_in_force.py`, `test_gate.py`, `test_cli.py`.
5. `rewrite`: the refusal, the screen, the question, the re-check, the body lines, `warnings`; the take-in asking
   where #141 refuses, for `via="cli"` only; `Rewritten.declined`. In the same step, every test that loosens through
   the CLI answers through `owner`: `test_cli.py`, `test_cli_redaction.py`, `test_cli_scan.py`, `test_source_new.py`.
   Test: `test_vault_settings.py`.
6. The three commands' output for a refusal and a no; `docket source new` removing its folder. Tests:
   `test_cli_redaction.py`, `test_source_new.py`, `test_cli_scan.py`.
7. The scans; the child-process and terminal cases. Tests: `test_settings_scan.py`, `test_console.py`.
8. The skills, the docs that send the owner to `vault.yaml`, the Settings page's notice and wording, the door, DR-0025's
   and DR-0507's lines, the glossary, the README, the design, the changelog. Tests: `test_plugin_files.py`,
   `test_web_settings.py`, `test_web_sync.py`.

## Tests

On temp vaults from the test kit, with invented sources, people and patterns (`calls`, `memo`, `ticket-id`, Dora
Example, a client code `CL-1234`); temp config, state and models folders, the failing keyring, `DOCKET_VAULT` unset, no
service.

| File | What it proves |
|---|---|
| `test_console.py` | the null device, a pipe and no stream are no console; both ends a console and no `CLAUDECODE` asks; the end of input stops; codes are four fresh letters. Each of the three commands run as `python -m docket` with a `y` piped in: exit 2, the console refusal, `vault.yaml` and HEAD unchanged. On Linux and macOS, `redaction set phone off` in a pseudo-terminal from `pty.openpty`: the code read off the screen and typed back, the change committed |
| `test_vault_settings.py`, the rule | the loosens table, four rows changed; `effective` per field: each change in the table above taken from the file, each change that waits kept as HEAD's; a list swap keeps both values; a reordered list is the file's; a pattern put before HEAD's runs after them, and `loosens` says so; a source renamed keeps neither name; a later top-level key and one inside a source stay HEAD's |
| `test_derived.py`, `test_doc_redact.py` | a doc whose never-store keyword sits inside what a rule redacts is refused at put and at take-in, the line as written named and the keyword never; a doc with no keyword is stored as before |
| `test_settings_in_force.py` | Dora Example deleted from `never_store.people`, uncommitted: an ingest naming her is still dropped, and after `git commit` stored. Credentials off, uncommitted: still redacted. A pattern for `CL-\d{4}` added by hand, uncommitted: an ingest's codes are redacted. `exclude: ["private/*"]` added, uncommitted: a scan leaves `private/` out. A tool removed, uncommitted: a capture from it marks the run short. A source removed, uncommitted: its ingest refused. A never-store keyword added or a source made private: applies at once. A source added, a pattern's regex changed, `primary_machine` changed, uncommitted: HEAD's, and the four refusals carry the notice. HEAD without `vault.yaml`, or one that doesn't parse: stops with the message. The cache: no git call over ten gate runs while HEAD stays; a Docket commit moves the key with no call; an owner's commit of another file costs one call; a miss reads by the id from `.git`, so a commit landing between the reads keys nothing wrong; a linked worktree and `packed-refs` read; a cache file that doesn't read is a miss |
| `test_vault_settings.py`, the change | a CLI change that loosens: refused under `CLAUDECODE=1` and without a console, the file's bytes and HEAD unchanged; a no, a wrong code and the end of input write nothing; the code writes and commits with `confirmed at the console`; a file changed while asked writes nothing; a stricter change asks nothing; a hand-edited file at a console gets its line and no code, and under `CLAUDECODE` the refusal; `rewrite`'s parameters pinned, `warnings` among them |
| `test_vault_settings.py`, the take-in | an uncommitted edit that loosens, before `redaction set phone on`: under `CLAUDECODE=1` refused, nothing committed; at a console one question, the code gives two commits, the take-in's with `confirmed at the console`, the change's without; a no commits nothing. A never-store swap shows `1 taken off, 1 added`, neither value on the screen. A sensitivity loosened shows `sources.calls.sensitivity: mixed -> work`. The edit and `phone off` both loosening: one code, two commits, each with the line. A commit of `vault.yaml` while asked writes nothing; a commit of another file while asked doesn't stop it. A HEAD with no `vault.yaml`, or one that doesn't parse, refused at a console too. An edit that only keeps more out, a pattern added, taken in unasked |
| `test_cli.py` | `docket vault status` says the notice while the file isn't in force, with no value, and nothing once it is committed |
| `test_web_settings.py`, `test_web_sync.py` | the notice at the top; each loosening command says your own terminal; the Sync page's hint says to commit |
| `test_web_settings_add.py` | #141's page refusal of an uncommitted loosening edit, in the notice's words |
| `test_cli_redaction.py` | credentials, phone and a pattern turned off: refused under `CLAUDECODE=1`, saying where to run it; at the console the credentials warning comes before the code; turned on stays unasked |
| `test_source_new.py`, `test_cli_scan.py` | `source new` and `source new --folder`, a new source and a changed entry: refused under `CLAUDECODE=1` with no folder left and `vault.yaml` unchanged; a no removes the folder; the code adds and commits; the same entry again asks nothing |
| `test_settings_scan.py` | only `release.py` and `settings.py` import `docket.console`; the direct-write row runs over the whole package, with `Vault.init` and convert the only writers of `vault.yaml` outside `rewrite`; only the loader calls `effective` for `Vault` and the gate |
| `test_release.py` | its cases unchanged, through the shared fixture |
| `test_plugin_files.py` | setup gives `docket source new` one at a time, its flags real; setup and cuts say never to run a loosening command, commit `vault.yaml` or clear `CLAUDECODE`; the Plaud example and `sources.md` say to commit |

- Coverage stays at the `docket` floor of 100 %; `_console_fd`'s two branches keep their `pragma`, one per system.
- The terminal case runs only where `pty` exists, read with a deadline; Windows proves its side through
  `GetConsoleMode` on the null device and a pipe, as the release's tests do.
- The git-call counts patch `Git.run` to count its calls, as `test_folder.py` patches it to fail one.

## Docs

- DR-0517 to DR-0521 in `packages/docket/index.d/`, and DR-0506's binds line, added with this plan.
- DR-0025, with `docs set`: every call reads the settings in force (DR-0521). DR-0507's take-in line: a CLI write at
  the owner's console asks instead of refusing (DR-0517).
- `AGENTS.md`, the gate's loosening door: every other change is named, never made by a page, route or tool; a
  loosening applies only once committed (DR-0521), and the CLI makes it, or takes in an edit that makes it, only at
  the owner's console with a code typed back (DR-0517).
- Glossary: loosening, what `loosens()` flags, made at the owner's console with a code typed back, or by hand and
  applied once committed; a new term, the settings in force.
- `packages/docket/index.yaml`'s reading list gains `docket/console.py` (contract: the console's yes, for the release
  and the loosening) and the settings-in-force module (contract: the per-field rules, the loader, the cache).
- `packages/docket/README.md`: the `docket redaction set` and `docket source new` rows say a loosening asks at your
  console; the `docket doc redact` row says the settings in force; a short part on hand edits (what applies at once,
  what waits for the commit, the notice) and on scripts.
- `docs/design.md`: the gate reads the settings in force, and the doc screen reads a doc as written; the Settings
  bullet says the CLI's loosening asks at the console.
- `CHANGELOG.md` under Changed.
- `packages/docket/src/docket/web/templates/settings/page.html`: the notice, and "in your own terminal" for each
  loosening command. `packages/docket/src/docket/web/templates/sync/page.html`: the hint says to commit.
- `plugin/skills/setup/SKILL.md`, `plugin/skills/setup/sources.md`, `plugin/skills/cuts/SKILL.md`,
  `examples/sources/plaud/SKILL.md`.

## Risks

| Risk | Status |
|---|---|
| an uncommitted edit lets records into raw before anyone confirms it (Claude under auto mode deletes a never-store person, then ingests) | resolved: the gate and every job that decides what gets in read the settings in force |
| a change made by hand that only keeps more out waits for a commit (a pattern, an exclude) | resolved: every change checked to only keep more out applies at once |
| what still waits: a source added or turned on, a looser sensitivity, a value taken off, a rule turned off, a pattern changed, removed or put before another, an exclude dropped, a tool added, a mapper or folder changed, the owner, the primary machine, storage, later keys | accepted: each lets more in, or can, or isn't about the gate; the notice, the refusals and the docs say it applies once committed |
| a pattern's regex narrowed or widened by hand waits, though a wider one would keep more out | accepted: a changed regex can match less, and telling wider from narrower is not decidable |
| a `media_hosts` entry removed waits | accepted: nothing reads it until media_put (phase 2) says what a host drives |
| a pattern put before HEAD's runs last until committed | accepted: its place applies once committed, with what it may split |
| a rule turned on hides a never-store keyword from the doc screen | resolved: the screen reads a doc as written too |
| a doc whose never-store keyword sits inside a credential, stored today with a marker, is now refused | accepted: a doc that says a never-store keyword is refused, as the lists mean |
| setup in Claude Code needs the owner at a terminal for each source | accepted: the point of the issue; one command and one code per source, once |
| Claude edits `vault.yaml` to loosen under auto mode, with no prompt | resolved: the edit applies nothing that loosens, no Docket write commits it unasked, and in Claude's shell the next CLI write is refused |
| in a folder vault, Claude's own commits in the host repo (`git commit -a`, a commit of the whole tree) can commit an edited `vault.yaml` with no question | accepted: a separate act, visible in git history with its diff, not a stray edit; the skills say never to commit `vault.yaml` |
| an owner used to an edit applying at once, such as taking a never-store value off to let a record in, sees nothing change | accepted: the notice in `docket vault status`, the Settings page and the refusals, the README and the changelog |
| an uncommitted loosening edit blocks Claude's stricter writes too, such as setup turning phone redaction on | accepted: the refusal names the edit, and the owner answers it once at a console, or commits or undoes it |
| the cache answers for an old HEAD, or keys one commit's text under another's id | resolved: keyed by the commit id from files, a miss read by that same id, moved forward only from the cached parent by a commit that didn't name `vault.yaml` |
| a repo on reftable, or a ref layout the reader doesn't know | accepted: one `git rev-parse HEAD` per read there, correct and slower |
| any process running as the user passes on purpose: a pseudo-terminal of its own, `CLAUDECODE` cleared | accepted, as for the release (DR-0202): a deliberate act the skills forbid |
| another agent's shell doesn't set `CLAUDECODE` | accepted: most run commands on pipes, which the check refuses; one that drives a terminal passes, as with the release |
| an owner's script that loosened now fails | accepted: the refusal says why and where; `CHANGELOG.md` names it |
| `vault.yaml` or HEAD's `vault.yaml` changes while the owner reads the screen | resolved: both checked again under the lock |
| a never-store value on the screen or in the scrollback | resolved: lists show counts taken off and added |
| tests that loosen by an uncommitted edit stop biting | resolved: their helpers commit the edit, step 4 |
| the terminal case is slow or flaky | resolved: one case with a deadline, never on Windows |

## Plan review

Round 1, the coordinator's reviewer at `d82a639`: changes needed, one major and six minors, all folded in `540addb`.

| Id | Severity | Finding | Status |
|---|---|---|---|
| P1 | major | the gate reads the working `vault.yaml`, so an uncommitted edit that loosens applies at the next ingest; the question only guards the commit | fixed: the settings in force, for the gate and every job that decides what gets in, cached with no git call per run (DR-0521); confirmed in round 2 |
| P2 | minor | the screen's diff and bare counts can hide a swap | fixed: each change by its full path, each list as taken off and added; the swap tested |
| P3 | minor | the re-check should compare HEAD's `vault.yaml`, not its commit id | fixed: HEAD's text compared; a commit of `vault.yaml` and one of another file, both tested |
| P4 | minor | how `set_rule`'s warnings reach the screen wasn't said | fixed: a named parameter, `warnings`, pinned in the signature test |
| P5 | minor | a pasted block of commands feeds line 2 into line 1's question in conhost | fixed: setup gives them one at a time |
| P6 | minor | the Settings page's loosening commands should say "in your own terminal" | fixed: the page and its template in Docs |
| P7 | minor | the switch to the `owner` fixture came after the question, leaving the suite red between steps | fixed: in the question's step |

Round 2, the reviewer's re-check of P1 at `540addb`: P1 resolved; one new major and two minors, folded in this
revision. The reviewer re-checks N1.

| Id | Severity | Finding | Status |
|---|---|---|---|
| N1 | major | `effective` took from the file only DR-0506's page list, so a pattern, an exclude, a tool removed or a source removed by hand would wait for a commit, though the gate applies each at once today | fixed: one rule widened to every change that only keeps more out, each checked against the gate and the jobs; a new pattern runs after HEAD's; DR-0506 amended knowingly, pages unchanged. Found while checking it: the doc screen read only redacted text, so a rule turned on could hide a keyword; it now reads the doc as written too |
| N2 | minor | messages and docs that send the owner to `vault.yaml` didn't say an edit applies once committed | fixed: the four refusals and #141's carry the notice; the Plaud example, `sources.md`, the Sync page's hint and the README say to commit |
| N3 | minor | a miss read `HEAD:./vault.yaml`, so a commit between the two reads could key the wrong text | fixed: `git show <id>:./vault.yaml` with the id read from `.git` |
