---
title: Ask at the owner's console before the CLI loosens the gate
issue: "#134"
status: draft
approved_by:
approved_at:
---

# Ask at the owner's console before the CLI loosens the gate

A CLI change to `vault.yaml` that `loosens()` flags, or an uncommitted edit of it that loosens and that a CLI write
would take in, is made only after the owner types back a code at their own console. The release's check does the
asking, moved to one module both use, and `settings.rewrite` is the one place that asks; a stricter change asks
nothing, as today.

## Goal

| Part | The issue's "done when" | This plan |
|---|---|---|
| Which | the loosening commands listed | every change `rewrite` makes for the CLI that `loosens(before, after)` flags: today `docket redaction set <rule> off`, `docket source new` adding a source or changing its entry, and `docket source new --folder` adding one or changing its folder; a command added later is asked by default. Also an uncommitted edit that loosens, which #141 refuses to take in: a CLI write at a console asks instead |
| Console | each asks as a release does: refused under `CLAUDECODE`, a pipe or a redirect, the code typed back | the release's check, code and question, moved to `docket/console.py` and called by both |
| Scripts | a documented way for the owner's scripts, if one is needed, that an agent can't reach | nothing to build: whatever a script running as the owner holds, an agent running as the owner reads too; the README says there is no way around the question |
| Tests | each command refuses under `CLAUDECODE=1` and from a pipe, and goes ahead at a console | per command: `CLAUDECODE=1`, a real pipe into a child process, the owner fixture typing the code back; a real terminal on Linux and macOS |

- In scope: `docket/console.py`; the question in `rewrite`, for the command's change and for the take-in of an
  uncommitted edit; the three commands' output; the setup skill handing its loosening commands to the owner; the
  door line, the glossary, the README; the tests.
- Out of scope:
  - the issue's Scripts row: nothing to build (Scripts, below);
  - a never-store value taken off: nothing to build, since no command does it (DR-0508); it is a hand edit;
  - `docket init`: it writes a new vault's first `vault.yaml`, with nothing stored and no gate yet to loosen;
  - `docket vault convert`: it moves where the vault lives, not what it lets in, and asks its own question;
  - `docket staging classify`: it answers the gate's own question for one record and changes no setting;
  - a page taking in an uncommitted edit that loosens: it keeps #141's refusal;
  - an OS prompt (Windows Hello, Touch ID, polkit), for the release and the loosening together: an issue of its own.

## Design

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

- No second list: `loosens()` is DR-0506's allowlist, so a key added later counts as a loosening for the CLI too
  until a decision moves it onto the list.
- A stricter change with nothing loosening to take in asks nothing: the setup skill still turns phone redaction on,
  and the Settings page tightens as before.

### The question

The release's question, moved and shared, not copied. One question per command, whether the edit, the change or both
loosen.

| Step | What |
|---|---|
| refuse | something loosens and `console.problem()` finds `CLAUDECODE` set, or no console on stdin and stdout: refused before anything is written or committed, whatever the file's form; the message says which, the edit or the change |
| by hand | a file not in Docket's form gets its line printed, as today, and no code: nothing is written or taken in |
| show | for each part that loosens, the uncommitted edit against HEAD's file first, then the command's change: a diff of the settings in Docket's form, the never-store lists left out and a changed list said by its count; then the command's own warnings, such as credentials off's |
| ask | `Type KXQM to make this change, or n to leave vault.yaml as it is:`, one code drawn fresh for both parts |
| write | under the vault lock, only when the file's sha256 and HEAD's `vault.yaml` are the ones asked about; else nothing, and "vault.yaml changed while you were asked; run the command again" |
| commit | as today, `Config: vault.yaml as edited` for the edit and `Config: redaction phone off` for the change, each part the owner confirmed with `confirmed at the console` in its body |

- Asked outside the vault lock, as the release asks: the service's ingest and captures never wait on a person.
- `edit` runs once, so the change written is the change shown.
- The re-check uses `file_hash` and `StaleError` from #141, which refuse a page's stale write (DR-0507).
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

The refusal says where to run it, as the release's does: your own terminal with nothing piped in or out, in Git
Bash's own window through `winpty`.

### Scripts

Nothing to build: none is needed, and none could be out of an agent's reach.

- A script of the owner's runs as the owner: a key in the keychain, a file or a variable it reads, an agent running
  as the owner reads too, so a path for scripts would be the agent's path.
- Loosening is a setup-time change, rare enough for the console or a hand edit.
- The human-only factor is an OS prompt, which DR-0382 and DR-0506 already defer to an issue of its own.

### A hand edit of vault.yaml

It stays possible, and it is the owner's own console edit; only Docket's commit of it is weighed.

- The gate obeys the file as it stands and never refuses one because it loosens. The owner's own `git commit` of it
  is theirs.
- Before a write, #141's take-in weighs an uncommitted edit by `loosens(HEAD's config, the file's config)`. One that
  doesn't loosen is taken in unasked, `Config: vault.yaml as edited`, as #141 has it.
- One that loosens is asked about before a CLI write, a stricter one too, with the command's change in the same
  question (The question, above); on the yes it is taken in with `confirmed at the console`. A page keeps refusing it.
- Claude's edit of the file passes Claude Code's permission prompt with its diff, and under auto mode too the next
  Docket write in Claude's shell is refused, never committed silently. The skills forbid a loosening edit and give the
  owner the line instead, as the Settings page does.

### The skills

- setup: the sources the user picks become one block of `docket source new` commands for the owner's own terminal,
  each showing what it adds and asking for its code; Claude still runs `docket init`, and turns a redaction rule on.
  "Either can change later" says turning one off asks at the owner's console.
- setup and cuts: never run a loosening command, edit `vault.yaml` to loosen, wrap a command in a console of your
  own or clear `CLAUDECODE`; auto mode changes nothing. A refusal naming an uncommitted edit goes to the owner as it
  stands.

### Decided

Taken under the autonomy grant, each proposed for the maintainer's review.

| Decided | Not taken | Entry |
|---|---|---|
| ask in `rewrite` for every CLI change `loosens()` flags | a list of commands, or a check per command in `cli.py`: a later command loosens unasked, and a Python caller of `set_rule` skips it | DR-0517 |
| a CLI write at a console asks about an uncommitted edit that loosens, where #141 refuses; a page keeps refusing | #141's refusal at a console too: the owner who made the edit commits it by hand to go on, when the same question would do | DR-0517 |
| adding a source asks, and setup hands the command over | sources added turned off, turned on at the console: a new command and DR-0506's list moved, to save one command per source | DR-0517 |
| init and convert left outside | asking there too: init has no gate yet, and convert lets no record in | DR-0517 |
| one console module for the release and the loosening | a copy in `settings.py`: two checks to keep in step, on three systems | DR-0518 |
| one question and one code for the edit and the change | two questions: two codes for one command, and a no to the second leaves the first committed | DR-0519 |
| the diff without never-store values, asked outside the lock, the file and HEAD checked again | asking under the lock: the service waits on a person; the whole file shown: a value in the scrollback | DR-0519 |
| `confirmed at the console` in each confirmed commit's body | no mark: git log can't tell which config commits the owner confirmed | DR-0519 |
| no way around the question, for scripts either | a token or a variable for scripts: Claude reads what a script reads | DR-0520 |
| a hand edit stays the owner's; only Docket's commit of it is weighed | asking about every hand edit: one that keeps more out needs no yes; refusing a loosened file at the gate: every write stops | DR-0520 |

The block's last id stays free for what the build decides.

## Doors and data

| Door | What this plan does |
|---|---|
| the gate's loosening (DR-0506) | amended in the PR whose title names it: the CLI makes a loosening, or takes in an edit that loosens, only at the owner's console with a code typed back (DR-0517, `cost: one-way`); AGENTS.md's line says so |
| the take-in (DR-0507, #141) | its refusal of an uncommitted loosening edit stays for pages, and for the CLI without a console; at a console the CLI asks instead. DR-0507's take-in line gains that, with `docs set` in this PR |
| the release's yes (DR-0202, DR-0382) | its check moves to `docket/console.py`, unchanged: the same refusals, prompt, code and answers; `release()`'s signature and the scans stay |
| `vault.yaml` (DR-0021) and its keys | unchanged: no key added, the same Docket form |
| the plugin's skill names | unchanged; the text of setup and cuts changes |

- Users' data: nothing new on disk, nothing migrates. A confirmed commit carries one more body line.
- An owner's script that ran `docket redaction set <rule> off` or `docket source new` now gets the console refusal;
  `CHANGELOG.md` says so under Changed.

## Steps

One PR, built on #141 (#73's PR 2: `file_hash`, `StaleError`, the take-in and its refusal of an edit that loosens),
started after #141 merges. Its title, "Ask at the owner's console before the CLI loosens the gate", names the door.

1. `docket/console.py` from `release.py`'s console part; `release.py` calls it. The `Owner` class and the `owner`
   fixture move from `test_release.py` to `conftest.py`, patching `docket.console`. Tests: `test_console.py` (the
   release's three console cases, moved); `test_release.py` otherwise unchanged and passing.
2. `rewrite`: the refusal, the screen in two parts, the question, the file and HEAD checked again, the body lines;
   the take-in asking where #141 refuses, for `via="cli"` only; `Rewritten.declined`; `set_rule`'s warnings reach the
   screen. Test: `test_vault_settings.py`.
3. The three commands' output for a refusal and a no; `docket source new` removing its folder. Tests:
   `test_cli_redaction.py`, `test_source_new.py`, `test_cli_scan.py`.
4. `test_cli.py` and the other cases that add a source only to set up answer through `owner`. Test: the suite, with
   `CLAUDECODE` cleared, as `conftest.py` does.
5. The scans; the child-process and terminal cases. Tests: `test_settings_scan.py`, `test_console.py`.
6. The skills, the door, DR-0507's line, the glossary, the README, the design, the changelog, the Plaud example's
   `SKILL.md`. Test: `test_plugin_files.py`.

## Tests

On temp vaults from the test kit, with invented sources and patterns (`calls`, `memo`, `ticket-id`); temp config,
state and models folders, the failing keyring, `DOCKET_VAULT` unset, no service.

| File | What it proves |
|---|---|
| `test_console.py` | the null device, a pipe and no stream are no console; both ends a console and no `CLAUDECODE` asks; the end of input stops; codes are four fresh letters. Each of the three commands run as `python -m docket` with a `y` piped in: exit 2, the console refusal, `vault.yaml` and HEAD unchanged. On Linux and macOS, `redaction set phone off` in a pseudo-terminal from `pty.openpty`: the code read off the screen and typed back, the change committed |
| `test_vault_settings.py`, the change | a CLI change that loosens: refused under `CLAUDECODE=1` and without a console, the file's bytes and HEAD unchanged; a no, a wrong code and the end of input write nothing; the code writes and commits with `confirmed at the console`; a file changed while asked writes nothing; a stricter change asks nothing; a hand-edited file at a console gets its line and no code, and under `CLAUDECODE` the refusal; given a never-store value, the screen never holds it; `rewrite`'s parameters pinned |
| `test_vault_settings.py`, the take-in | an uncommitted edit that loosens, before `redaction set phone on`: under `CLAUDECODE=1` refused, nothing committed; at a console one question showing the edit, the code gives two commits, the take-in's with `confirmed at the console`, the change's without; a no commits nothing. An edit that takes a never-store value off: its list counted, the value never on the screen. The edit and `phone off` both loosening: one code, two commits, each with the line. A HEAD commit made while asked writes nothing. A HEAD with no `vault.yaml`, or one that doesn't parse, refused at a console too. An edit that doesn't loosen taken in unasked, as #141 has it |
| `test_web_settings_add.py` | #141's page refusal of an uncommitted loosening edit, unchanged |
| `test_cli_redaction.py` | credentials, phone and a pattern turned off: refused under `CLAUDECODE=1`, saying where to run it; at the console the credentials warning comes before the code; turned on stays unasked |
| `test_source_new.py`, `test_cli_scan.py` | `source new` and `source new --folder`, a new source and a changed entry: refused under `CLAUDECODE=1` with no folder left and `vault.yaml` unchanged; a no removes the folder; the code adds and commits; the same entry again asks nothing |
| `test_settings_scan.py` | only `release.py` and `settings.py` import `docket.console`; the direct-write row runs over the whole package, with `Vault.init` and convert the only writers of `vault.yaml` outside `rewrite` |
| `test_release.py` | its cases unchanged, through the shared fixture |
| `test_plugin_files.py` | setup hands `docket source new` over, its flags real; setup and cuts say never to run a loosening command or clear `CLAUDECODE` |

- Coverage stays at the `docket` floor of 100 %; `_console_fd`'s two branches keep their `pragma`, one per system.
- The terminal case runs only where `pty` exists, read with a deadline; Windows proves its side through
  `GetConsoleMode` on the null device and a pipe, as the release's tests do.

## Docs

- DR-0517 to DR-0520, added with this plan in `packages/docket/index.d/`.
- DR-0507's take-in line, with `docs set`: a CLI write at the owner's console asks instead of refusing (DR-0517).
- `AGENTS.md`, the gate's loosening door: every other change is named, never made by a page, route or tool, and the
  CLI makes it, or takes in an edit that makes it, only at the owner's console with a code typed back (DR-0517).
- Glossary, loosening: made at the owner's console with a code typed back, or by hand, never by a page.
- `packages/docket/index.yaml`'s reading list gains `docket/console.py` (contract: the console's yes, for the release
  and the loosening).
- `packages/docket/README.md`: the `docket redaction set` and `docket source new` rows say a loosening asks at your
  console; two lines on scripts and hand edits, the uncommitted edit among them.
- `docs/design.md`, the Settings bullet: the CLI's loosening asks at the console.
- `CHANGELOG.md` under Changed.
- `plugin/skills/setup/SKILL.md`, `plugin/skills/cuts/SKILL.md`, `examples/sources/plaud/SKILL.md`.

## Risks

| Risk | Status |
|---|---|
| setup in Claude Code needs the owner at a terminal for each source | accepted: the point of the issue; one command and one code per source, once |
| Claude edits `vault.yaml` to loosen under auto mode, with no prompt | resolved with #141: no Docket write commits it unasked; in Claude's shell the next CLI write is refused, and at the owner's console it is shown and asked. The owner's own `git commit` stays theirs |
| an uncommitted loosening edit blocks Claude's stricter writes too, such as setup turning phone redaction on | accepted: the refusal names the edit, and the owner answers it once at a console, or commits or undoes it |
| any process running as the user passes on purpose: a pseudo-terminal of its own, `CLAUDECODE` cleared | accepted, as for the release (DR-0202): a deliberate act the skills forbid |
| another agent's shell doesn't set `CLAUDECODE` | accepted: most run commands on pipes, which the check refuses; one that drives a terminal passes, as with the release |
| an owner's script that loosened now fails | accepted: the refusal says why and where; `CHANGELOG.md` names it |
| `vault.yaml` or its last commit changes while the owner reads the screen | resolved: both checked again under the lock |
| a never-store value on the screen or in the scrollback | resolved: the diffs leave the lists out |
| the terminal case is slow or flaky | resolved: one case with a deadline, never on Windows |
| #141 and this change both edit `rewrite` | resolved: this one starts after #141 merges |

## Plan review

Not reviewed yet: `review-plan` writes it.
