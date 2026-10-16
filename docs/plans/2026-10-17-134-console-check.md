---
title: Ask at the owner's console before the CLI loosens the gate
issue: "#134"
status: draft
approved_by:
approved_at:
---

# Ask at the owner's console before the CLI loosens the gate

A CLI change to `vault.yaml` that `loosens()` flags is made only after the owner types back a code at their own
console. The release's check does the asking, moved to one module both use, and `settings.rewrite` is the one place
that asks; a stricter change asks nothing, as today.

## Goal

| Part | The issue's "done when" | This plan |
|---|---|---|
| Which | the loosening commands listed | every change `rewrite` makes for the CLI that `loosens(before, after)` flags: today `docket redaction set <rule> off`, `docket source new` adding a source or changing its entry, and `docket source new --folder` adding one or changing its folder; a command added later is asked by default |
| Console | each asks as a release does: refused under `CLAUDECODE`, a pipe or a redirect, the code typed back | the release's check, code and question, moved to `docket/console.py` and called by both |
| Scripts | a documented way for the owner's scripts, if one is needed, that an agent can't reach | none: whatever a script running as the owner holds, an agent running as the owner reads too; the README says so |
| Tests | each command refuses under `CLAUDECODE=1` and from a pipe, and goes ahead at a console | per command: `CLAUDECODE=1`, a real pipe into a child process, the owner fixture typing the code back; a real terminal on Linux and macOS |

- In scope: `docket/console.py`; the question in `rewrite`; the three commands' output; the setup skill handing its
  loosening commands to the owner; the door line, the glossary, the README; the tests.
- Out of scope:
  - `docket init`: it writes a new vault's first `vault.yaml`, with nothing stored and no gate yet to loosen;
  - `docket vault convert`: it moves where the vault lives, not what it lets in, and asks its own question;
  - a never-store value taken off: no command does it (DR-0508), so it is a hand edit;
  - `docket staging classify`: it answers the gate's own question for one record and changes no setting;
  - an OS prompt (Windows Hello, Touch ID, polkit), deferred with the release's (DR-0382).

## Design

### Which changes ask

One rule at the one writer: `rewrite(via="cli")` asks when `loosens(before, after)` is true, `before` being the file
as it stands, hand edits included.

| Command | Change | Asks |
|---|---|---|
| `docket redaction set <rule> off` | credentials, phone or a pattern turned off | yes |
| `docket redaction set <rule> on` | turned on | no |
| `docket source new <name>` | a source added; or an entry already in `vault.yaml`, whose folder is gone, given a looser sensitivity, other tools or the mapper | yes |
| `docket source new <name> --folder <folder>` | a folder source added, or its folder changed | yes |
| either, giving the entry `vault.yaml` already has | nothing | no |
| a command added later that writes through `rewrite` | whatever `loosens()` says | by default |

- No second list: `loosens()` is DR-0506's allowlist, so a key added later counts as a loosening for the CLI too
  until a decision moves it onto the list.
- A stricter change asks nothing: the setup skill still turns phone redaction on, and the Settings page tightens as
  before.

### The question

The release's question, moved and shared, not copied.

| Step | What |
|---|---|
| refuse | the change loosens and `console.problem()` finds `CLAUDECODE` set, or no console on stdin and stdout: refused before anything is written, whatever the file's form |
| by hand | a file not in Docket's form gets its line printed, as today, and no code: nothing is written |
| show | the diff of `vault.yaml` in Docket's form, the never-store lists left out and a changed list said by its count; then the command's own warnings, such as credentials off's |
| ask | `Type KXQM to make this change, or n to leave vault.yaml as it is:`, a code drawn fresh |
| write | under the vault lock, only when the file's sha256 is the one asked about; else nothing, and "vault.yaml changed while you were asked; run the command again" |
| commit | as today, `Config: redaction phone off`, with `confirmed at the console` in the body |

- Asked outside the vault lock, as the release asks: the service's ingest and captures never wait on a person.
- `edit` runs once, so the change written is the change shown.
- The re-check uses `file_hash` and `StaleError` from #73's PR 2, which refuse a page's stale write (DR-0507).
- A no, a wrong code or the end of input leaves `vault.yaml` as it is and exits 1, as a hand-edited file does today;
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

None needed, and none an agent can't reach.

- A script of the owner's runs as the owner: a key in the keychain, a file or a variable it reads, an agent running
  as the owner reads too, so a path for scripts would be the agent's path.
- Loosening is a setup-time change, rare enough for the console or a hand edit.
- The human-only factor is an OS prompt, which DR-0382 and DR-0506 already defer.

### A hand edit of vault.yaml

It stays possible, and it is the owner's own console edit.

- The gate obeys the file as it stands and never refuses one because it loosens.
- The take-in from #73's PR 2 commits an uncommitted edit on its own, `Config: vault.yaml as edited`, unasked; the
  command's own change is then held to `loosens()` against the file as edited.
- Claude's edit of the file passes Claude Code's permission prompt, with its diff. The skills forbid a loosening edit
  and give the owner the line instead, as the Settings page does.

### The skills

- setup: the sources the user picks become one block of `docket source new` commands for the owner's own terminal,
  each showing what it adds and asking for its code; Claude still runs `docket init`, and turns a redaction rule on.
  "Either can change later" says turning one off asks at the owner's console.
- setup and cuts: never run a loosening command, edit `vault.yaml` to loosen, wrap a command in a console of your
  own or clear `CLAUDECODE`; auto mode changes nothing.

### Decided

Taken under the autonomy grant, each proposed for the maintainer's review.

| Decided | Not taken | Entry |
|---|---|---|
| ask in `rewrite` for every CLI change `loosens()` flags | a list of commands, or a check per command in `cli.py`: a later command loosens unasked, and a Python caller of `set_rule` skips it | DR-0517 |
| adding a source asks, and setup hands the command over | sources added turned off, turned on at the console: a new command and DR-0506's list moved, to save one command per source | DR-0517 |
| init and convert left outside | asking there too: init has no gate yet, and convert lets no record in | DR-0517 |
| one console module for the release and the loosening | a copy in `settings.py`: two checks to keep in step, on three systems | DR-0518 |
| the diff without never-store values, asked outside the lock, the hash checked again | asking under the lock: the service waits on a person; the whole file shown: a value in the scrollback | DR-0519 |
| `confirmed at the console` in the commit's body | no mark: git log can't tell a confirmed loosening from a hand edit taken in | DR-0519 |
| no way around the question, for scripts either | a token or a variable for scripts: Claude reads what a script reads | DR-0520 |
| a hand edit stays the owner's, unasked | asking about it at the next command: the owner is asked about their own edit, and one of Claude's blocks every later change; refusing it at the gate: every write stops | DR-0520 |

The block's last id stays free for what the build decides.

## Doors and data

| Door | What this plan does |
|---|---|
| the gate's loosening (DR-0506) | amended in the PR whose title names it: the CLI makes a loosening only at the owner's console, with a code typed back (DR-0517, `cost: one-way`); AGENTS.md's line says so |
| the release's yes (DR-0202, DR-0382) | its check moves to `docket/console.py`, unchanged: the same refusals, prompt, code and answers; `release()`'s signature and the scans stay |
| `vault.yaml` (DR-0021) and its keys | unchanged: no key added, the same Docket form |
| the plugin's skill names | unchanged; the text of setup and cuts changes |

- Users' data: nothing new on disk, nothing migrates. A confirmed loosening's commit carries one more body line.
- An owner's script that ran `docket redaction set <rule> off` or `docket source new` now gets the console refusal;
  `CHANGELOG.md` says so under Changed.

## Steps

One PR, built on #73's PR 2 (`file_hash`, `StaleError`, the take-in): started after it merges, or rebased onto it.
Its title, "Ask at the owner's console before the CLI loosens the gate", names the door.

1. `docket/console.py` from `release.py`'s console part; `release.py` calls it. The `Owner` class and the `owner`
   fixture move from `test_release.py` to `conftest.py`, patching `docket.console`. Tests: `test_console.py` (the
   release's three console cases, moved); `test_release.py` otherwise unchanged and passing.
2. `rewrite`: the refusal, the screen, the question, the hash checked again, the body line; `Rewritten.declined`;
   `set_rule`'s warnings reach the screen. Test: `test_vault_settings.py`.
3. The three commands' output for a refusal and a no; `docket source new` removing its folder. Tests:
   `test_cli_redaction.py`, `test_source_new.py`, `test_cli_scan.py`.
4. `test_cli.py` and the other cases that add a source only to set up answer through `owner`. Test: the suite, with
   `CLAUDECODE` cleared, as `conftest.py` does.
5. The scans; the child-process and terminal cases. Tests: `test_settings_scan.py`, `test_console.py`.
6. The skills, the door, the glossary, the README, the design, the changelog, the Plaud example's `SKILL.md`. Test:
   `test_plugin_files.py`.

## Tests

On temp vaults from the test kit, with invented sources and patterns (`calls`, `memo`, `ticket-id`); temp config,
state and models folders, the failing keyring, `DOCKET_VAULT` unset, no service.

| File | What it proves |
|---|---|
| `test_console.py` | the null device, a pipe and no stream are no console; both ends a console and no `CLAUDECODE` asks; the end of input stops; codes are four fresh letters. Each of the three commands run as `python -m docket` with a `y` piped in: exit 2, the console refusal, `vault.yaml` and HEAD unchanged. On Linux and macOS, `redaction set phone off` in a pseudo-terminal from `pty.openpty`: the code read off the screen and typed back, the change committed |
| `test_vault_settings.py` | a CLI change that loosens: refused under `CLAUDECODE=1` and without a console, the file's bytes and HEAD unchanged; a no, a wrong code and the end of input write nothing; the code writes and commits with `confirmed at the console`; a file changed while asked writes nothing; a stricter change asks nothing; a hand-edited file at a console gets its line and no code, and under `CLAUDECODE` the refusal; given a never-store value, the screen never holds it; `rewrite`'s parameters pinned |
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
- `AGENTS.md`, the gate's loosening door: every other change is named, never made by a page, route or tool, and the
  CLI makes it only at the owner's console with a code typed back (DR-0517).
- Glossary, loosening: made at the owner's console with a code typed back, or by hand, never by a page.
- `packages/docket/index.yaml`'s reading list gains `docket/console.py` (contract: the console's yes, for the release
  and the loosening).
- `packages/docket/README.md`: the `docket redaction set` and `docket source new` rows say a loosening asks at your
  console; two lines on scripts and hand edits.
- `docs/design.md`, the Settings bullet: the CLI's loosening asks at the console.
- `CHANGELOG.md` under Changed.
- `plugin/skills/setup/SKILL.md`, `plugin/skills/cuts/SKILL.md`, `examples/sources/plaud/SKILL.md`.

## Risks

| Risk | Status |
|---|---|
| setup in Claude Code needs the owner at a terminal for each source | accepted: the point of the issue; one command and one code per source, once |
| Claude edits `vault.yaml` to loosen under auto mode, with no prompt, and the take-in commits it | accepted: the owner's own settings; the skills forbid it; the commit says `vault.yaml as edited`, never `confirmed at the console` |
| any process running as the user passes on purpose: a pseudo-terminal of its own, `CLAUDECODE` cleared | accepted, as for the release (DR-0202): a deliberate act the skills forbid |
| another agent's shell doesn't set `CLAUDECODE` | accepted: most run commands on pipes, which the check refuses; one that drives a terminal passes, as with the release |
| an owner's script that loosened now fails | accepted: the refusal says why and where; `CHANGELOG.md` names it |
| `vault.yaml` changes while the owner reads the screen | resolved: the hash checked again under the lock |
| a never-store value on the screen or in the scrollback | resolved: the diff leaves the lists out |
| the terminal case is slow or flaky | resolved: one case with a deadline, never on Windows |
| #73's PR 2 and this change both edit `rewrite` | accepted: this one builds on it and rebases |

## Plan review

Not reviewed yet: `review-plan` writes it.
