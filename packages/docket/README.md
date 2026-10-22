# docket

The Docket app: the vault, the service (web app, MCP endpoint, scheduler) and the `docket` command. The repo's
[README](../../README.md) says what Docket is.

| Command | What it does |
|---|---|
| `docket init` | set up a vault: asks where, who you are, this machine's name, the sources, and inside a git repo whether the vault is a folder of it (the default) or a repo of its own (`--storage folder\|repo`); every answer is also a flag, and `--yes` takes the defaults; `--no-sources` starts with none, for `docket source new` to add; the summary names each check a flag overrode (`--allow-shared`, `--remote-is-private`, `--allow-dirty-parent`); `--no-redact-credentials` and `--redact-phone` set redaction, and the summary prints it |
| `docket sync` | alone, a repo of its own: fetch and rebase onto its remote; a folder: says your own git moves it, and does nothing else |
| `docket sync start [source...]` | for `/docket:sync`: writes a sync folder in Docket's state folder, never in the vault, with `context.md` (each live source's window as `run_open` would open it, every open and snoozed item a line, the meetings from the earliest window to the fifth working day after today with their points by id, the streams) and each agent's prompt, and prints the folder and a line per agent; a name that isn't a live source is refused before any file; removes sync folders over 7 days old; works with the service down |
| `docket sync recordings <sync> <source>` | run as each source's agent is back: each of the source's recordings in raw, in its window, that no note covers (its minutes, a recording note, a thread summary or a legacy note) gets its files in the sync folder, the recording from raw in parts the Read tool takes whole, and its agent's prompt, and a line with the prompt's path; one with a record still in staging is skipped and said, and one given an agent in this sync gets no second |
| `docket sync reconcile <sync>` | drafts the sync's batch from its blocks, the recordings' too, into `draft.jsonl` in the sync folder, by fixed rules: closes, asks logged on the item they continue or added, invites, meetings, links, minutes, rollover, a `# prep` line with each prep meeting and its open points, streams, archive; an op the rules can't settle carries `unsure`, which the writer refuses, with its block line, what settling it needs (each candidate's line with its due and its latest log line, a point or an outcome it would change, whole, a meeting's points, a stream's line) and its alternatives as `#` lines; a recording's NOTES line with no note id still links the recording, and says so; the head line carries the draft's rev, 1; prints the RUN lines, the unsure lines by number, every merge, the lines dropped, the closes skipped, `no source reported a close` when no block holds a CLOSES line, the LEARNED lines and the prep meetings; writes nothing else |
| `docket sync settle <sync> <line>=<choice>... --rev <n>` | settles the draft's unsure lines in one call, by their numbers at the draft's rev `<n>`, on its head line: `keep` takes the `unsure` out, `alt` (`alt2`, ...) puts that alternative in its place, `keep+alt2` gives both, `drop` deletes the line; what goes with an op follows a keep; every choice is checked first, and a draft at another rev or a choice that doesn't apply changes nothing; rewrites only `draft.jsonl` at the next rev and prints it, the unsure lines left and the `# prep` line by their new numbers |
| `docket sync summary <sync>` | after the batch: the sync's summary from the ledger, a line per item, the source left out: the batch and its undo, from the sync's start to the next sync's, the calendar step's meetings said apart, what it closed and added, the outcomes written, the meetings and their prep to the fifth working day, what you owe due today or before, this week and whenever, invites, waits, meetings left over with open points, the runs that stopped short, each recording's note, staging; at most 30,000 characters, whenever cut first; reads only |
| `docket vault status` | which vault it found and how (`--vault`, `DOCKET_VAULT`, this folder or above, `./vault`, the default vault), where it lives and on which branch, records per source, runs, run files not committed yet, the last commit |
| `docket vault check` | whether the vault is intact: every raw file parses, nothing written once was changed, no ignore rule hides a vault path, the vault's own attributes apply, no doc rev is claimed by two commits; and each stream's STATUS.md against the ledger, read and never written: a hand edit, a file that lags (`stale; docket render`) or isn't rendered yet, a stream the ledger lacks; it also says how many calendar records wait for their meetings, which `docket ledger calendar` makes |
| `docket vault convert` | make a vault that is a repo of its own a folder of the repo around it, history included: a dry run first, then your yes; `--abort` undoes one that stopped before its commit |
| `docket staging list` | what waits for the gate, oldest first: `pending` waits for you or Claude, `ready` goes to raw at the next promotion |
| `docket staging show <ref>` | one staged record, its cuts, and the version in raw that was cut, when there is one; a transcript a segment a line, `@mm:ss-mm:ss Speaker: text`, the times a cut takes |
| `docket staging classify <ref> work\|private` | work makes it ready, `--cut 120-340` (characters) or `--cut 12:30-14:05` (a time) cuts a stretch; private drops it |
| `docket staging promote` | writes every ready record to the raw log, cut and redacted, one run per source, and commits; a record on hold stays in staging; then the calendar's event records it wrote become meetings, a line saying what changed, and a step that stops leaves them pending for the next promotion |
| `docket ingest <source> <folder>` | imports payloads fetched earlier through the source's mapper: the folder's `hold.txt` first, then its JSON files; every record goes to staging, nothing on hold; `--hold <file>` adds more ids to hold, `--final` tells the mapper not to wait for more, `--mapper-errors` prints the mapper's own errors |
| `docket source new <name>` | writes a source's skill, mapper and tests into `.claude/skills/sources/<name>/` of the repo that holds the vault, from Docket's blank template, or `--example` for one of the example sources (`plaud`, `slack`, `teams`, `outlook`, `calendar`; see Example sources), and adds the source to `vault.yaml` with `--sensitivity`, its `--tool`s and `--lookback-days`; never over a folder that exists; `--folder <folder>` adds a folder source instead; it commits `vault.yaml`, run on your yes |
| `docket render` | writes the people directory and the glossary from the entities, and each stream's STATUS.md frontmatter from the ledger, its body left as it is, at the paths `vault.yaml`'s `rendered` names in the repo that holds the vault; never commits them; leaves a file edited by hand and says so, and writes nothing into a repo anyone can read; exit 1 when a file was left or refused. It runs after each curate write too, and a stream's after each ledger write that changes it |
| `docket scan [source]` | reads a folder source's folder, read-only, and stages each new or changed document's text; every folder source turned on without a name; `--accept-gone` when most files the vault held are missing on purpose |
| `docket doc put <file or ->` | store a processed doc as its next rev, redacted first, saying on which lines; refused, with every problem and its line, when the frontmatter is wrong, an anchor doesn't resolve, the doc changed since the rev it carries, or it says something on the never-store keywords list (then only the lines are named) |
| `docket doc redact` | redact the docs already stored with the settings in force, as their next revs in one commit; once after upgrading, and again after turning a rule on or adding a pattern or a keyword; `--dry-run` first; a doc it can't redact is named and left, with exit 1, and so is a doc whose anchors hold a token this Docket doesn't read. Never run it on a machine with an older Docket than the docs were written with: it can write inside anchors it doesn't know |
| `docket doc read <locator>` | a doc, or a record and the doc lines that point at it; `--view raw` or `both` for the raw, `--around` for minutes, paragraphs or messages either side, `--rev` for an earlier revision |
| `docket decision list [--entity <id>] [--status <s>]` | each decision, newest first: proposed, current, superseded or rejected, with what it replaces, contradicts or supersedes and what replaced or contradicted it; only the Briefs page confirms or rejects one |
| `docket brief stale [--entity <id>]` | the briefs whose entity has records their last rev hadn't seen, or a line that cites a line that changed, and the orphans whose entity is gone; with `--entity`, that entity's state or that it has no brief yet |
| `docket doc verify <doc>` | each line of a doc next to the raw its anchors point at; `--line` for chosen lines |
| `docket doc stale` | the doc lines that cite a line of another doc that changed since, the corrections not applied yet, and anchors an older Docket's `docket doc redact` wrote inside, with the rev to restore them from |
| `docket search <query>` | hits grouped by recording or thread, each with its locator and the group's summary; `--since`, `--until`, `--source`, `--person`, `--entity`, `-k`; it asks the running service when it serves the vault, whose search brings the index up to date, and otherwise updates the index itself unless `--no-update` and searches by itself |
| `docket index rebuild` | build this machine's index from the vault, from scratch |
| `docket index update` | index what changed since; it rebuilds when it has to |
| `docket index status` | whether the index is there and current, its languages, counts, what is new, and its problems |
| `docket ledger snapshot` | the open and snoozed items, each with its source as a locator, a snooze whose day has come counted open; the meetings not archived from yesterday to six days on (`--since`, `--until`), or with an open point, with their points and each point's id; the streams; counts first. Each ledger read below reads the index's ledger, brought up to date first, or folds the op files when there is no index, so it works with the service down; `--json` gives the `ledger` tool's result, every field and every row |
| `docket ledger show <ref>` | one item, meeting or stream by its A- or M- number, its id or a stream's name: its fields, notes and materials, a meeting's points with their ids and its items, an item's counts (chased, snoozed, due moved, reopened, days open), and its history, each change with its day, who made it and how, an undone one marked; a number others held names them; `--all --json` gives every item, meeting and stream so, archived too, with the op files' stamp (the vault's root, each file's path from it, size and time) and the vault lock's file, for a script outside Docket |
| `docket ledger queue` | the open items to work through: the untriaged first, a snooze whose day has come among them, then by triage (discuss, claude, me, chase), each by due |
| `docket ledger list` | the items by `--who` (a part of the name), `--stream`, `--kind`, `--state open\|snoozed\|done\|dropped\|all` (open takes a snooze whose day has come, snoozed only those still asleep) and the day they opened (`--since`, `--until`), with counts over all of them: by state, overdue, chased and the median days to done; `-k` rows of text, 50 by default |
| `docket ledger meetings` | the meetings overlapping a window, today to six days on by default; minutes around a time find the meeting a recording was made in |
| `docket ledger suggest <M->...` | the open items worth raising in each meeting given, in order, each with why, an item for two meetings under each; it writes nothing |
| `docket ledger day` | your day, as the session start says it: each live source's last sync and the ones due, a status file a hand edit holds, the queue's counts with the discuss and claude rows, today's meetings still to come and the next working day's with their open points and items, then what is overdue or due today, replies owed, decisions and tasks due this week, invites, waits over three days and the streams not closed; an item listed once among those; at most 45 lines, each cut section keeping its count; `--json` gives every row |
| `docket ledger batch [file or -]` | the ledger's changes in one write, every one or none, as the `ledger_batch` tool makes them: a JSON array or JSON lines with `#` comments, such as a sync's draft; `--by user\|claude\|sync` unless an op says, `as` on an add, a meeting, a point or a point's item naming it for a later op's `$name`; `--dry-run` first, which lists every change it would refuse and exits 1 if any |
| `docket ledger calendar [--since DAY]` | the meetings caught up with the calendar's event records in raw, by sync: what a stopped step left pending first, sets included, then each record from DAY (14 days back by default), adding a meeting or joining the old one with its start and subject, never setting one, in writes of about 200 records, whole days each; `--dry-run` first names the joins and the near misses |
| `docket ledger import <file or ->` | a ledger brought in with its history from the op lines an export wrote, `via: import`, a month per commit; every old A- and M- number kept, Docket's own renumbered; what the ledger holds already is skipped, so a rerun adds nothing twice, and an op from elsewhere on its objects refuses it; `--dry-run` first counts items by kind and state, log entries, meetings, points and their roll chains, the sources `read` can't resolve and the old meetings `docket ledger calendar` would join after it, `--json` for an export to compare; `--revert` deletes every op file in one commit, until the old ledger retires, and every clone pulls that before it writes again |
| `docket import notes <folder>` | each markdown note in an existing folder becomes a legacy note that keeps its old path, so `doc read notes/x.md` or `notes/x.md@12:08` still finds it; the folder is only read; run it again to pick up changes; `--dry-run` first, `--as` for what old paths start with; says how many markers redaction added |
| `docket redaction` | which redaction rules run; `docket redaction set <credentials\|phone\|pattern name> on\|off` changes one and commits it; turning credentials off asks at your own console: your OS prompt, or a code typed back |

A command finds its vault from `--vault`, `DOCKET_VAULT`, the working folder upward, a `vault/` folder in it, or the
default the wizard recorded. A `<ref>` is a record's rid, or at least 6 characters of the key `staging list` shows.

Docket's commits skip your repo's hooks, in a folder of your repo and in a repo of its own, and so do `docket
sync`'s fetch and rebase: a formatter or a commit-msg rule can't rewrite what Docket writes once. Your own commits
and every push still run them, so git-lfs's pre-push uploads the media.

## In a folder of your repo

By default the vault is a folder of the repo you work in (`storage: folder` in `vault.yaml`). Docket commits there
locally, and your own git does the rest.

- Docket's commits hold only the vault's paths: nothing you staged or changed goes in. They skip your repo's hooks,
  and land on whichever branch is checked out; `docket vault status` warns when that isn't the remote's default.
- Docket never fetches, pulls, stashes or pushes your repo. Your push carries the vault, media included once git-lfs
  is set up (init and convert set it up, or say what to add to a pre-push hook of your own).
- Docket's own files sit under your repo's `.git/docket/<vault path>/`, never in your tree. If that `.git` goes
  missing, Docket refuses to write until it is back, and never makes one.
- Docket opens a folder vault only when the repo around it tracks its `vault.yaml`: a vault you moved into another
  repo opens once you commit its `vault.yaml` there.
- `staging/` (records waiting for the gate) is ignored, so `git clean -x` deletes it; a source's next sync pulls those
  records again.
- A clone without the audio: `git config lfs.fetchexclude "<vault path>/media/**"`.
- Init refuses a folder in a repo anyone can read, and warns when others commit to it.
- `docket vault convert` refuses a vault with a remote: another machine's clone of it would be overwritten by that
  machine's next pull of your repo. Bring those clones' commits in, delete them, remove the remote, then convert.

## The gate

Every record reaches the raw log through the gate. It reads the settings in force on every call: `vault.yaml` as
committed, with what an edit not committed yet only tightens (a never-store value added, a rule turned on, a new
pattern, a source made stricter, turned off or removed, an exclude added, a tool removed). The rest of an edit applies
once you commit it, and `docket vault status` says when one waits (DR-0521).

| `vault.yaml` | What it does |
|---|---|
| `sources.<name>.sensitivity` | `work` goes through, `mixed` waits in staging to be classified, `private` is dropped |
| `sources.<name>.mapper` | the source's mapper, relative to the repo that holds the vault: `.claude/skills/sources/<name>/mapper.py` |
| `sources.<name>.tools`, `media_folders` | the tools the capture hook posts for the source, and the folders under `~/` or in the repo `media_put` may take its media from; an old `media_hosts` key is read and ignored |
| `sources.<name>.lookback_days` | how many days before the last sync a live run reads back, a whole number from 0 to 30: `1` suits chat and mail; unset, 7 (the cursor file's own) |
| `never_store.people` | drops a record whose author, any recipient, or any id in its source's payload equals an entry: an exact address (`kid@example.invalid`, also inside `Kid <kid@example.invalid>`) or the source's user id (`U0KID`); case doesn't matter |
| `never_store.containers` | drops a record from a channel, chat or folder: its id, with or without the source's prefix (`slack:C0FAMILY` or `C0FAMILY`) |
| `never_store.keywords` | drops a record that says the words anywhere, title, text, attachment names or payload, as whole words: case, accents and curly apostrophes don't matter (`kovacs` matches `Kovács`, not `kovacsfalva`), and any punctuation may join the words; a keyword of two or more words also matches them joined by a case change or by nothing (`dora example` matches `dora-example`, `dora_example`, `DoraExample` and `doraexample`), while a one-word keyword never matches inside a word (`Max` leaves `maxRetries` alone); a name behind an encoding matches too: percent-encoded, once or wrapped again as a Safe Link does (`Dora%2520Example`), behind HTML entities, in Latin-1, or as `%u` and `\u` escapes |
| `redaction.credentials` | `true` (the default) redacts passwords and secrets after a label (also in bold, in a table cell, or on the next line of an HTML mail), API keys and tokens, and private key blocks; `builtin`, the key files written before held, reads as this one |
| `redaction.phone` | `false` (the default); `true` redacts phone numbers written with `+` or after a label, while a `+` amount with its currency (`+12 500 000 Ft`) stays |
| `redaction.patterns` | your own, none by default: each has a `name` (it shows in the marker), a `pattern`, a regular expression, and `enabled`, `true` unless you turn it off; a group named `secret` redacts only that part (`door code (?P<secret>\d+)`) |

- A hyphen or other punctuation in a keyword also matches a space: `check-up` drops "check up" too.
- A dropped record leaves no file and no log line, only a count.
- What is redacted becomes `[redacted:<name>]`, and a cut stretch becomes `[cut]`; the secret itself is kept nowhere.
- Names are never redacted. To keep a person, a topic or a chat out of the vault, list it under `never_store`: your
  own lists, empty until you add to them. A match drops the whole record, or refuses the whole doc.
- Processed docs and imported notes follow the never-store keywords list and the same redaction settings, line by
  line, so a marker never moves a line or an anchor; a person's name to keep out of docs goes under `keywords`.
  A doc is screened as written as well as redacted, so a keyword inside an address, a number or a password a rule
  redacts still refuses it, as a record holding it is dropped. `docket redaction set` changes a setting, and `docket
  doc redact` applies it to the docs already there.
- The ledger refuses an item's `source` or a material's `ref` that points into a container on `never_store.containers`:
  a Slack or Teams rid by its own text, any rid by the container and thread the index holds for it. The refusal names
  the field, never the value, for every writer and the import (DR-0690).
- Turning a rule off leaves the markers already stored, but a record a source sends again then lands as a new version
  holding the value, records you classified wait in staging again, and the next `docket import notes` writes a new
  rev of each note the rule matched. Turning credentials off stores passwords, keys and tokens as written, in raw,
  docs and git history; turning it back on doesn't take them out of raw.
- Staging lives in the vault's `staging/` folder, on this machine only, never committed.
- A source's name is lowercase letters, digits, dots, underscores and hyphens, and doesn't end in a dot: it names
  folders and files.
- A transcript is cut by time: each utterance the cut touches goes whole, and one `[cut]` takes its place. Any other
  text is cut by characters. A cut record keeps no payload.

### Claude acts with your OK

Claude releases, changes settings, confirms decisions and confirms old cuts by running the same commands you would,
after you say yes in chat (DR-0599). Docket doesn't check that yes: Claude Code's own prompt shows each such command
unless you allow it. Every commit such a command makes says who acted.

| Who | The commit's body |
|---|---|
| you, in your own terminal | nothing more |
| Claude, in Claude Code's shell | `by Claude, with the owner's OK in chat` |
| a page | `via page` |
| you, at your console, turning credentials off | `confirmed at the console` |

Two steps stay yours, at your own console: turning credentials redaction off (`docket redaction set credentials off`,
or an uncommitted edit of `vault.yaml` that a command would take in) and forgetting for real. Each shows what it does
and asks your OS prompt, or a code typed back where the machine has none (The OS prompt, below); Claude Code's shell, a
pipe or a redirect is refused, so run it in your own terminal, in Git Bash's own window through `winpty`. A no writes
nothing.

An edit of `vault.yaml` by hand is yours: what it only tightens applies at once, the rest once it is committed, by you
or by the next command that writes `vault.yaml`, which commits it on its own first (DR-0601).

### The OS prompt

Where this machine has one set up, the yes is the system's own prompt, which only you answer: Windows Hello, Touch ID
or your Mac's password, polkit (DR-0572 to DR-0574). The console code is asked only where there is none.

| Step | Asks |
|---|---|
| turning credentials redaction off, `docket forget --now` | your OS prompt, after `Press Enter` at the console; or the code |
| `docket media keys export` and `import`, `models key` | nothing more: your console only, since a passphrase or a key is shown or typed there |

| System | Prompt | Set up when | Otherwise |
|---|---|---|---|
| Windows 10 1709 and later | Windows Hello: a fingerprint, your PIN, or your face | Hello is set up for you | the code on a machine with neither a TPM nor a biometric device; refused on one with either, until you set Hello up or make the marker |
| macOS | Touch ID, an Apple Watch or your login password | always, on a Mac with a login password | refused if this Python can't use LocalAuthentication: make the marker |
| Linux | polkit: your password, or a fingerprint through PAM; at the console when no desktop agent runs, as over SSH | you installed Docket's action, once: the line `docket vault status` prints, `sudo install -m 0644 <file> /usr/share/polkit-1/actions/` | the code |

- `docket vault status` says which one this machine asks, and why. Nothing shows a prompt to find out.
- Tried on real hardware so far: Windows 11 with a PIN, in Windows Terminal and conhost. Face recognition, SSH, macOS
  and Linux follow the design and CI's checks, but no one has tried them on a real machine yet.
- Once a prompt is set up, its no is a no: a cancel, two minutes without an answer, or no desktop to show it on (SSH to
  Windows or a Mac) writes nothing, and the code is never asked instead. Run the command at the machine.
- After two minutes, or Ctrl+C, Windows may leave its Hello dialog on screen. Docket has already refused, and
  whatever you type into it changes nothing: cancel it.
- Windows Hello's face recognition proves you are there, not that you agreed: a prompt you didn't start may pass as
  soon as the camera sees you. For a yes you have to give, use a fingerprint or your PIN, or turn face recognition off.
- A prompt you didn't start is not yours: cancel it.

What it stops, and what it doesn't. A prompt says a person answered. It doesn't make Docket's own code tamper-proof: a
process running as you can still change Docket, or call its insides, on every system. What it ends is the easy way
round the console check, a pseudo-terminal of an agent's own that reads the code and types it back.

| System | What a process running as you can still do |
|---|---|
| Windows | start the prompt while you face the camera; type into the PIN box, or read the PIN as you type, if the dialog lets same-user input in; with UAC that doesn't ask, or a UAC bypass, make the marker |
| macOS | type into the password dialog, if its terminal has Accessibility and it knows your password; with passwordless sudo, make the marker |
| Linux | on X11, read or type into the agent's dialog; with passwordless sudo, add a polkit rule that says yes, remove the action or make the marker |

Root, or an admin with UAC that doesn't ask, is past every check Docket has.

#### The console-code marker

A file only an admin or root can make sends this machine back to the console code, even where a prompt is set up. It
is the way out of a refusal. It counts only when every folder on its path and the file are an admin's or root's and
nothing on the path is one you could replace or add a file to; `docket vault status` names the check it fails.

Windows, in an elevated PowerShell (UAC's own screen asks you first):

<!-- the Windows marker's lines -->
```powershell
$dir = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'Docket'
$admins = 'S-1-5-32-544'
if ((Test-Path $dir) -and (Get-Acl $dir).GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $admins) {
  Remove-Item -Recurse -Force $dir
}
New-Item -ItemType Directory -Force $dir | Out-Null
icacls $dir /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
New-Item -ItemType File -Force (Join-Path $dir 'console-code') | Out-Null
icacls $dir /setowner "*$admins" /T | Out-Null
```

- The lines remake a `Docket` folder that isn't the Administrators', take away the right Users inherit to add files
  there, and make the Administrators the owner, so a policy that leaves new files to their creator can't fail it
  silently.
- macOS: `sudo mkdir -p "/Library/Application Support/Docket"`, then `sudo touch "/Library/Application
  Support/Docket/console-code"`.
- Linux: `sudo mkdir -p /etc/docket`, then `sudo touch /etc/docket/console-code`.
- Remove it the same way, as an admin, to have the prompt again.

## Sources and their mappers

A source's skill and mapper live in your project, under `.claude/skills/sources/<source>/`; Docket runs the mapper and
checks what it returns (DR-0108, DR-0119). `docket source new <name> --vault <vault>` writes that folder and adds the
source to `vault.yaml` (DR-0166); the plugin's `/docket:setup` runs it for each source you choose.

- Without `--example` it writes Docket's blank template: a `SKILL.md` to fill, a mapper that sets every capture aside
  as `not-mapped-yet` until you write the mapping, and its tests. With `--example` it copies a reference source whole,
  one of the example sources below (DR-0609).
- It writes only into that folder of the repo around the vault, never through a link out of it or into the vault, and
  never over a folder that exists. It changes `vault.yaml` only when the file is as Docket wrote it, and commits it;
  otherwise it prints the lines to add under `sources:`. The folder is yours to commit.
- Without `--vault` it takes only a vault found from the project that holds it. One found through the default vault
  or `DOCKET_VAULT` can be another project's, so it is refused, with the `--vault` to pass.
- Claude Code doesn't load a `SKILL.md` from `.claude/skills/sources/` as a skill: `run_open` names it by its path
  and gives the run's steps, so a `SKILL.md` holds only the source's own calls, paging and limits (DR-0251). It
  carries a marker line, `<!-- docket: steps from run_open -->`, and a live run refuses one without it: after
  upgrading Docket, copy the reference's `SKILL.md` and `mapper.py` over yours.
- Run a source's tests where it lives: `python -m pytest .claude/skills/sources/<name>/tests`, one source folder at a
  time, or with `--import-mode=importlib`, since every source has a `tests/test_mapper.py`. The tests that go through
  Docket's own checks run only where `docket` can be imported (a Python environment Docket is installed in);
  elsewhere they are left out and the mapper's own tests still run.
- The mapper is `map_run(run)` in `mapper.py`: a run's captures and its manifest in, records out, as plain JSON.
- Docket runs it in its own process, for at most 300 seconds (90 in a live run) and 256 MiB of output. A failure
  stages nothing, and the run's captures are fetched again.
- Docket checks every rid, sorts a transcript's segments and renders its text, and computes every version: a mapper
  can't break ids or decide what counts as new.
- Changing a mapper: keep its output stable, since every record the lookback reads again gets a new version when it
  changes; pin golden versions in its tests, as the example does; pull the project on every machine before its next
  sync.

### Example sources

Five working sources ship in [examples/sources](../../examples/sources), each a skill, a mapper, the page scripts it
runs and its tests, tested in CI (DR-0608). `docket source new <name> --example --vault <vault>` copies one into your
project under its own name, with its sensitivity, tools and lookback in `vault.yaml`.

| Example | Route | Reads | In `vault.yaml` |
|---|---|---|---|
| `plaud` | MCP: Plaud's own server | recordings, their transcripts and Plaud's notes | `mixed` |
| `slack` | MCP: the Slack plugin's read tools, its search to find what to read | channels, DMs and their threads | `work` |
| `teams` | Claude in Chrome: Teams on the web, two fixed page scripts | one-on-one, group and meeting chats | `work`, a day back |
| `outlook` | Claude in Chrome: Outlook on the web, two fixed page scripts | mail in every folder, the online archive | `work`, a day back |
| `calendar` | Claude in Chrome: Outlook on the web's week view, one fixed page script | events, one record per occurrence | `work`, a week back |

- The copy is yours from then on: change its route as your workspace or tenant needs and keep its tests passing.
  Docket never updates it; a newer example is in the repo to compare with.
- The browser routes are for a tenant no MCP server reaches. Their page notes were learned on one tenant, and a quirk
  seen once says so. Where an MCP server reaches the source, start from the blank template instead.
- A session or agent holds one open run: opening another, of any source, drops the one before. Run sources one after
  another in a session, or each in its own agent.
- With two or more copied, `python -m pytest .claude/skills/sources` stops at collection, since every folder has a
  `tests/test_mapper.py`: run each folder's tests on its own, or all of them with `--import-mode=importlib`.

## Folder sources

A folder source is your own documents: Docket reads the folder itself, read-only, and keeps each file as text through
the same gate as every source (DR-0480). `docket source new documents --folder ~/Work` adds one; `docket scan`
reads it, and `docket staging promote` writes what it staged.

```yaml
sources:
  documents:
    sensitivity: work
    folder: ~/Work                  # or a path relative to the repo that holds the vault
    exclude: ["archive/*", "*.draft.md"]
```

- `folder` is under your home folder (`~/Work`) or relative to the repo, so it reads the same on every machine; an
  absolute path is refused. A machine whose folder is elsewhere names it in Docket's `config.yaml`, by the vault's
  folder and the source (DR-0490):

  ```yaml
  folders:
    D:/vaults/work:
      documents: E:/Work
  ```

- `exclude` takes fnmatch patterns on the path inside the folder, any case, and leaves out folders too
  (`archive/*`). Notes you also import with `docket import notes` are best left out here.
- It is `work`, with no mapper or tools: `mixed` would hold every page on the Staging page, and to store nothing from
  it, set `enabled: false`. Only the vault's primary machine scans.
- In a git repo the folder is listed through git's ignore rules, and git's index is never refreshed; elsewhere it is
  walked, dot folders and nested repos left out. Links and junctions inside it are left out, and so are Office lock
  files, git-lfs pointers, cloud placeholders whose bytes aren't on disk, and a path holding a never-store keyword.
- A folder inside it that can't be listed now (a permission, a share gone) keeps its files' last versions, and the
  report names it under `later`; exclude it if it should go.
- Reading a file moves its last-access time where the file system keeps one (NTFS and most Linux mounts, relatime):
  a scan reads a file only when its stat moved, and never writes to it.

| Format | Gives | Misses |
|---|---|---|
| pdf | a record per page, its text layer | scanned pages (no OCR), reading order across columns, form fields |
| docx | one record: paragraphs and tables in order, tracked insertions, then headers, footers, notes and comments | list numbers, deleted text, images, charts |
| pptx | a record per slide: its shapes and tables, then its speaker notes | charts, SmartArt, images |
| xlsx | a record per sheet: row n is paragraph n, `41: Widget \| 3 \| 4.50`, so `#41-80` is rows 41 to 80 | formulas without a saved value, comments, charts |
| md, txt | one record, UTF-8, UTF-16 by its BOM, else Windows-1252 | other encodings |

- A file's rid is `documents:<12 hex>`, from its path in the folder, and its parts are `/page3`, `/slide4`, `/sheet2`
  under it. A search hit names the file's path and the page, slide or `Costs!41:80`.
- Text is read in a child process: 120 s and 2 GiB a file (no memory cap on macOS), 64 MB, 2,000 pages, 1,000
  slides, 100 sheets, 10,000 rows and 256 columns, 4 MB of text. A file past a cap, damaged or protected is a record
  saying why, with no text; one that stops or crashes the reader is tried once more.
- A second scan reads only what changed: a stat each, a hash when the stat moved, the reader when the bytes did. A
  file deleted, moved or excluded gets one gone version that says since when; `docket doc read` shows it and the last
  version with text, and anchors on it still verify, as they do on a file that turned protected. A scan that would
  find more than half the files gone, and at least 20, stages nothing until `--accept-gone`.

## Live runs

A source syncs from any Claude Code session with the plugin: ask Claude to sync it, and it calls `run_open`, follows
the source's skill, and closes the run with `ingest` (DR-0240 to DR-0252). The plugin's capture hook hands Docket what
each of the source's tools returned, so Claude never retypes a payload. `/docket:sync` runs every live source this way,
each in an agent of its own from a sync folder (DR-0746), and writes what they found into the ledger in one batch,
which `docket sync reconcile` drafts, `docket sync settle` settles and `ledger_batch` writes from its file (DR-0772,
DR-0760, DR-0724); `docket sync summary` prints the list after it (DR-0761). Its agents check their locators in one
`read` with `more`, and its prep asks one `suggest` with `refs` (DR-0725); the plugin's README says how.

| Part | What |
|---|---|
| a run | held in the service's memory from `run_open` to `ingest`, tied to the session that opened it; nothing of it on disk |
| a dropped run | one that comes up short, fails its close, sits 30 minutes with no capture, or meets a service restart: nothing is staged, the mark stays, and the next sync fetches the window again |
| the window | from the latest mark any machine reached, less the source's `lookback_days` in `vault.yaml` (else the cursor's, 7 days), to now; `run_open(since=...)` starts it elsewhere |
| the cursor | `state/cursors-<machine>.yaml`, committed: the mark, the lookback, the ids to retry and the ids still waiting, ids and times only |
| a final run | `run_open(source, final=true)` for the ids that waited a week: fetched by id, no listing, the mark untouched |
| a folder | a live run reads a backfill folder only when `docket ingest` read it on this machine first |

- A sync of your own (a skill or a command in your project) runs each source the same way: `run_open("<source>")`
  opens the run and names the source's `SKILL.md`; Claude makes the reads it lists, which the capture hook hands to
  Docket; `ingest(run, captures=<n>, skipped=[...])` closes it. What your sync does with the records after that is its
  own.
- Each machine commits its cursor file when a whole run changes it: one commit per run, `Cursors: <source> on
  <machine>`, naming no id.
- A cursor file that doesn't read stops this machine's runs, naming the line; another machine's is left out with a
  note, since its marks only shorten a window.

## Hold lists

Records Docket must not stage yet, such as backfilled recordings with stretches you left out, are held by id in
`state/holds/<source>.txt` (DR-0121). Every ingest and the gate read it.

- Write `hold.txt` beside the backfill files before the first import, one id a line. `docket ingest` adds it to
  `state/holds/<source>.txt`, which is then the only truth, and commits it. Only a release takes an id off.
- A held record gets no record on any route; it is only counted.
- When an ingest stops because a held record waits in staging, delete the files it names, then run it again.
- `docket staging classify` refuses a held record, work or private, and `promote` leaves it waiting.
- A held record already in raw is named on every ingest; `docket forget --held` takes it out, by its versions.
- A recording's id in `never_store.containers` keeps it out for good, held or not.

### Releasing a held recording

A held recording goes in only with the cuts you said yes to (DR-0200, DR-0203 to DR-0206, DR-0600).

| Command | What |
|---|---|
| `docket hold list [<source>]` | the held ids; one marked `held again after its release` came back with a merge |
| `docket hold show <source> <id> --from <folder>` | the recording as its mapper reads it from the folder, a transcript one utterance a line with its times; writes nothing |
| `docket hold release <source> <id> --from <folder> --cut 12:30-14:05 [--keep <rid>]` | one recording, its cuts and what to keep as it reads |
| `docket hold release <source> --from <folder> --proposals <file>` | the proposals file `/docket:cuts` writes, outside any repo |

- Run it on your yes: in your own terminal, or let `/docket:cuts` show you the cuts in chat and run it after you say
  yes (DR-0600). Each recording shows each cut widened to whole utterances, its edges, and every record it keeps in
  full, then is released.
- A recording needs a transcript and at least one time cut that removes an utterance; a record without segments, such
  as the vendor's notes, is left out unless you keep it. Anything else stays held, with the reason.
- The release line goes to `state/releases/<source>.jsonl` and is committed before the hold comes off; then the
  versions you were shown are staged already cut, ready, and `docket staging promote` takes them to raw. Any other
  version under the id waits as pending for you to classify.
- A hold never comes off in git before its release line: a line whose commit failed is committed first, and a log
  or hold list an ignore rule hides stops the release with git's own error.
- The gate cuts a released recording by the same times wherever it is staged again, on any machine. Update Docket on
  every machine before your first release.
- A merge can bring a hold back: `hold list` and every ingest say so, and the next import with a hold file, or
  `docket hold release <source> <id> --from <folder>` (no cuts asked), takes it off; what that stages waits
  as pending. `hold show` refuses such an id: its cuts are on record.

## Media

`docket media put <rid> <path-or-url>`, or the `media_put` tool, keeps a record's audio or file beside it
(DR-0547 to DR-0551). It is encrypted as it is read, with a key made for that file alone, and waits in staging until
its record is in raw; the next `docket staging promote` stores it in `media/` in LFS.

| From | Taken when |
|---|---|
| a path | it is under one of the source's `media_folders`, or Docket's drop folder (`docket media drop` prints it), whose files are deleted once stored |
| a URL | https, to any host, every address it resolves to public; at most 3 redirects, each checked again (DR-0604) |

- A file is 512 MiB at most; `media.max_bytes` in Docket's `config.yaml` sets this machine's cap.
- `docket media folder add <source> <folder>` and `remove` change a source's folders and commit `vault.yaml`.
- Media follows its record: a version of it dropped (private, never-store, left out by a release) takes the media with
  it, and a held recording, or one whose transcript named a never-store value, gets none.
- A put needs this machine to have synced the source once: what the gate dropped is remembered per clone.

### Cut recordings' audio

A recording released with cuts, or whose transcript you cut by time, gets its audio cut the same way before it is
encrypted (DR-0559 to DR-0562): each stretch becomes silence, 250 ms wider each side, so every time in the transcript
still points at the same moment. It is stored as WebM Opus at 32 kbit/s, about 14 MB an hour; audio with nothing to
cut is stored as it came.

| Case | What happens |
|---|---|
| released with cuts, or a transcript cut by time | cut at the put, then stored with its record |
| cut after its audio was put | the staged audio goes; the run's `media` line names the recording, and its media step puts it again, cut |
| cut after its audio was stored | the stored file isn't played (`recut`); the run names it, and the new file takes its place, the old one's key deleted here once the new one is committed |
| held | no audio until the release, and a stored file isn't played; then the next run puts it, cut |
| a record cut by characters | no audio, and a stored file stays unplayed: no cut of the audio can match; the Recording page and `docket media status` say so |
| a version dropped for a never-store value or as private, its own transcript's or Whisper's | a stored file isn't played (`withheld`); a source only turned off still plays |
| decided by hand before Docket kept which way | no audio until `docket media confirm-cuts <rid>`, run on your yes that they were cuts, shows each decision against the versions cut by hand and marks them |

- The cutting runs in a child process with a memory cap and 15 minutes; uncut audio is plain only in a folder of
  Docket's private folder, removed when the put ends, or by the next gate call if the process died.
- A put is refused when the audio and its transcript may not share a timeline: the transcript runs past the audio's
  end, or the audio's first sound is more than 1.5 s from its first words. The put names the offset; nothing is
  stored.
- Each stored file's cuts are committed beside it in `state/media-cuts/<source>.jsonl`, times and its key id
  only, so another machine's line never vouches for this machine's file.

Keys never enter git. Each clone keeps its own, sealed in its git dir's Docket folder, with the seal in the system
keychain. Where no keychain answers (a server, CI), `docket media keys init --store file` keeps the seal in a file
only you can read, outside any repo. `docket media status` counts the keys and the media waiting, stored, without a
key here or not downloaded.

### Keys on a second machine, and the backup

Lose a clone's keys and its media is gone for good. `docket media keys export <file>`, in your own terminal, writes
every key of the clone into one new file, sealed with a passphrase it draws and shows once, after your OS prompt's yes
(or the code); type the passphrase's last four letters back and the file is kept. `docket media keys import <file>` on the other machine asks for the passphrase
and adds the keys it lacks. Neither runs in Claude Code's shell or through a pipe (DR-0550).

The newest export holds every key, so it is the backup. Keep the file and the passphrase in different places:

| Where | Keeps |
|---|---|
| a password manager | the passphrase, and the file too if it takes attachments |
| a USB stick | the file, off the network |
| a synced folder | the file, on each machine you use; nothing opens it without the passphrase |

`docket media status`, `docket vault status` and the session start count the keys no export holds yet: export again
after new media is stored. The count trusts that every export you made is still where you put it; Docket can't see a
lost stick. Export to a folder outside any repo: one inside a git work tree is refused, since a commit could take it.
The passphrase opens the file however you type it, with or without the dashes.

## Forgetting

`docket forget` takes a record out of the vault for good, on every clone: its lines, the doc lines built on it, its
audio and the audio's keys (DR-0577 to DR-0581, DR-0593 to DR-0595). Update Docket on every machine before your first
forget: an older one reads what a merge brings back until a newer one's gate call takes it out.

| Command | Takes |
|---|---|
| `docket forget <rid>...` | each rid and everything under it, the audio it is in with every key, and its Whisper transcripts |
| `docket forget --never-store [--source <name>]` | each record in raw a never-store list in force matches now |
| `docket forget --held [--source <name>]` | each record in raw under a hold not released, by its versions, so the release still lets its cut version in |
| `docket forget --check-rewrite <folder>` | nothing: checks the history rewrite a forget wrote leaves HEAD as it is, and names each branch to merge the forget into first |

- Without `--now` it only says what it would take: counts, run files and doc line numbers, never a doc's id or a
  line's text, so it runs in Claude Code's shell too. `--now` runs in your own terminal only, shows the ids, and
  forgets on your OS prompt's yes, or a code typed back where the machine has none.
- A forget writes `state/forgotten.jsonl`, hashes and op ids only, and commits it first. Every read, search and gate call obeys it
  at once, on every clone after its pull; the next gate call there takes the lines, audio and keys out too, and removes
  that machine's sync folders started before it, whose blocks can quote it.
- A folder file is taken by its content: move it out of the folder too, since a forget never writes there.
- A confirmed or rejected decision whose evidence was all forgotten keeps its title, and says so.
- A ledger op the list names keeps its place, number and history, and reads `[forgotten]` where its words were, in
  every read at once; the next gate call writes that into the op files, every machine's, and commits them.
- The forget names the ops to mask: one anchoring on what it takes, a material or a source naming it, and every op of
  an item or a meeting built on it, which stays as a shell with its number, its points' copies with it when their
  words were raised there. A copy rolled into a shell from a live meeting goes alone, and the chain after it keeps
  its words. The screens name each shell, an open one to drop (an item open, a meeting with an open point), each
  object that lost a link, and each open point left on a shell, by number only. Text naming a record in plain words,
  with no anchor or locator, is out of its reach.
- After a forget the ledger refuses text or a log on a shell, and a source, a ref or an anchor naming what was
  forgotten; a drop, an archive or a close with an outcome on a shell still goes, its note and outcome `[forgotten]`.
  A point the forget masked on a meeting it kept takes no text and makes no item; an answer there reads `[forgotten]`,
  it can still be dropped, and the rollover leaves it where it is.

What a forget can't reach, which every dry run names: key exports made before (make a new one right after, then
destroy the old), the LFS server's encrypted copies, chunks sent to an API provider, the source itself, and files
outside the vault that quote it.

The text stays in your repo's history: Docket never rewrites it. The forget writes `git filter-repo` files to its
private folder and prints how to use them, in order:

1. check them with `--check-rewrite`: it refuses while the rewrite would change HEAD, or a file outside the vault on
   any branch, and names each other branch that still holds what the forget took: merge the forget into it, or
   delete it, first;
2. back the repo up, then run filter-repo in a fresh `git clone --no-local`;
3. filter-repo rewrites every branch and tag there and removes the `origin` remote: add it back, force-push every
   branch and tag, and clone again on every machine.

- Your own files elsewhere in the repo keep what they say, but for a copy of a forgotten record's raw line.
- A branch only the remote has isn't in the clone and keeps the text: check it out first, or delete it there.
- The remote keeps the commits a force-push left behind until its host purges them, and forks keep theirs.
- Each folder holds every earlier forget's files on that clone: run only the newest.

## Processed docs

A processed doc's lines end with anchors: `@12:08` in a doc covering one record, `[r:<rid>@12:08]`, `[r:<rid>#2]` or
`[r:<rid>]` across records (DR-0198). A locator is a doc's id or path, `r:<rid>@12:08`, or a rid. The same three
operations are `docket.recall.doc_put`, `read` and `verify` in Python. A time resolves against the record's `segments`
field, which the source's mapper fills (DR-0111); a record without segments takes paragraph and whole-record anchors.

## Search

A query is words, `"quoted phrases"`, names and time words: `docket search "tízmillió forint" múlt héten --person
ben-sample`. Words match as written, accents or not, and through their stems in each of your languages; names expand to
their aliases; time words ("last week", "múlt héten", "yesterday", "2026-10") become a filter; `--person` and
`--entity` lift what mentions them and never hide the rest. In Python it is `docket.index.search`.

Search with key words (names, nouns, numbers), not the sentence: every word counts, however common, and a hit holding
every word ranks first, so a customer named in most of your notes still narrows the search. A doc, mail or recording
whose title leads with most of your words comes before one that only mentions them; a title's lead is its text up to
the first `: ` after at least three words (so `RE: ` or `Fwd: ` doesn't end it), else its first 12 words. `in no
chunk: <word>` names a word nothing in the vault holds, so no hit can hold every word.

A compound is found however it is written: `kickoff`, `kick-off` and `kick off` find each other, and so do
`e-mail`, `email` and `e-mailben`. A hit holding a word only split in two ("edit or" for `editor`) comes after the
hits holding as many of your words as written. A quoted phrase matches its words as written.

The index lives in `index/` in the vault, on each machine, never committed. `docket search` keeps it up to date, and
`docket index rebuild` makes it again from scratch.

Your languages are `owner.languages` in `vault.yaml`, as codes such as `en` and `hu`; English is always searched. Write
Norwegian as `"no"` in quotes, or YAML reads it as false. Adding or removing a language rebuilds the index the next
time you search.

## Measuring search

`docket eval questions.yaml` runs your own questions against your vault and prints recall at 10 and MRR per kind of
question and per language, then the questions it missed. Write the questions you really ask, with the locators a good
answer cites, and run it again after changing aliases, models or settings: a change that helps paraphrases and hurts
names shows in the table.

```yaml
schema_version: 1
asked: "2026-10-14T10:00:00+02:00"   # when you asked: time words like last week count from here
questions:
  - q: money up front for the translator        # the words search gets
    want: ["r:mail:<advance-1@example.invalid>#2"]
    kind: paraphrase                            # exact-name, paraphrase, inflection, typo or time-bound
    lang: en
    note: the mail says advance                 # optional
```

| Wanted | Written | A hit answers it when it is |
|---|---|---|
| a record | `r:<rid>` | on that record |
| a moment or a stretch | `r:<rid>@12:08`, `r:<rid>@12:08-12:40` | that record whole, or a window of it overlapping the stretch |
| paragraphs | `r:<rid>#2-4` | that record whole, or a part of it overlapping them |
| a doc, or its lines | `minutes:<slug>`, `minutes:<slug>#L7-9` | a section of it, whose lines overlap them when given |
| a thread or a recording | `thread:<rid>` | anything grouped under it |

- Copy locators from `docket search`'s output. A thread window answers the messages it holds, and a doc's section the
  raw its anchors point into.
- `-k 20` scores the top 20 instead of 10; `--json` prints the numbers and each question's ranks, to compare two runs.
- It reads the index as it is and writes nothing; when the index is behind the vault it says so, and `docket index
  update` brings it up to date.
- Keep the file in your own repo, never in Docket's: it holds your questions.
- With models on, it says what the vectors covered and how many questions ran without them; `--no-models` measures
  keyword search alone, so two runs show what the models add.

## Models

Models are optional: with them, search also finds what a question means, not only the words it shares, in English and
Hungarian alike, and a reranker orders its top hits by how well each answers the question. Keyword search works the
same without them. Both run on ONNX Runtime in a worker process of its own, on the CPU or an NVIDIA GPU.

| Model | Where | Licence |
|---|---|---|
| multilingual-e5-small, the embedder | every device | MIT |
| bge-reranker-v2-m3 (onnx-community's fp16 export), the reranker | an NVIDIA GPU: the top 50 | Apache-2.0; trained on data that includes MS MARCO, whose own terms are for non-commercial research |
| cross-encoder/mmarco-mMiniLMv2-L12-H384-v1, the reranker | the CPU: the top 30 | Apache-2.0; trained on mMARCO, a machine translation of MS MARCO, whose own terms are for non-commercial research |

| Command | Does |
|---|---|
| `docket models install [--device auto\|cpu\|cuda] [--no-reranker]` | builds the models' environment with uv, fetches the embedder's and the device's reranker's pinned files, checks the worker runs, times the reranker and turns models on for this machine; `--no-reranker` leaves the reranker out and removes its files |
| `docket models status [--check]` | the choice, the environment, the files, the reranker's timing or why it is off, each model's vectors in the vault's index, and the running service's worker with its reranks and the last lines it wrote to stderr; `--check` starts a worker once and says what it runs on |
| `docket models remove` | deletes the environment, the files and the choice; search goes on with keywords. On Windows the service's worker holds its files: models go off at once, and the remove asks you to stop the service and run it again |
| `docket models install --api [--no-reranker]` | chooses Voyage AI's API for this machine instead (Through Voyage AI's API, below); nothing is downloaded |
| `docket models key voyage [--forget]` | keeps the Voyage key in this machine's keychain, typed at your own console and never shown; `--forget` removes it, and this machine sends nothing from then on |
| `docket models allow voyage\|none [--vault]` | lets this vault's text go to Voyage AI, or stops it, in `vault.yaml`, and commits it |

- Downloads, once: 0.98 GB of model files for the CPU or 1.64 GB for CUDA, the reranker's 0.49 GB or 1.15 GB of them,
  and about 50 MB of packages for the CPU or 1.3 GB for CUDA (NVIDIA's CUDA 13 and cuDNN wheels, under NVIDIA's
  licences, in that environment only). `auto` takes CUDA when `nvidia-smi` lists a
  GPU; CUDA 13 needs a driver of R580 or later, and the install says when ONNX Runtime fell back to the CPU.
- The choice is this machine's, in your config's `models:` block, never in `vault.yaml`: each machine embeds with what
  it has. A block Docket can't read, or a config that isn't YAML, turns models off and says why.
- With these local models nothing leaves the machine. The files live under your user cache and data folders, never in
  the vault or a repo, and after the install nothing touches the network.
- Vectors need a Python whose SQLite can load extensions. uv's own Pythons can; some others can't (the one on
  GitHub's macOS runners, for one), and `docket models install` says so. `uv tool install --python-preference
  only-managed docket` installs Docket on uv's own.
- ONNX Runtime ships for Windows, Linux, and Macs with Apple silicon on macOS 14 or later. On an Intel Mac or an
  older macOS, `docket models install` says so and keyword search works as before.
- The service reads the choice when it starts: after an install, `docket service stop` and `docket service install`
  start it with the models. Until then `docket index update` embeds for it and says so.

Who embeds what:

| Where | Embeds |
|---|---|
| the service | what each index update added, in the background, a few seconds for a sync run's chunks on a CPU |
| `docket index update`, `docket index rebuild` | what has no vector, with a progress line; when the service runs for the vault with the same model it leaves that to the service |
| `docket search`, `docket eval` | only the query: `docket search` through the service when it serves the vault with the same model and its worker is up; otherwise, and always for `docket eval`, on a worker they start for themselves (a second or two, about 8 s for `docket eval` on a GPU, where its reranker runs); `--no-models` skips it |

A first pass over a year of chunks takes a minute or two on a GPU and half an hour or more on a CPU; search uses what is
there meanwhile, and a rebuild keeps every vector it had. When the worker is down or slow, search answers with keywords
alone and says `no vectors in this search:` and why.

The reranker:

- It sees the chunks behind the hits a search would return without it, then the next ones by meaning, up to 50 on a
  GPU and 30 on a CPU, and orders them: hits holding every word of the question first, and within each part the chunks
  a `--person` or `--entity` lifted, then the reranker's score.
- It has 0.75 s a search. Past that, with the worker down, or when it can't run, search keeps the order it gives
  without a reranker and says `not reranked:` and why; three searches past the budget in a row pause it for five
  minutes. `docket models status` counts reranks done and past the budget.
- The install times it on this machine. A GPU, or a CPU of about 8 cores or more, reranks within 0.6 s and the
  reranker is on; on a slower CPU it stays off, and the install and `docket models status` say the time.
  `docket models install --no-reranker` frees its files.
- `docket search` reranks as the service does when the service answers it. On its own (no service for the vault,
  `--no-update`) it reranks on the CPU only; on a GPU machine it goes without, and its line says so.
  `docket eval` reranks where the service does, with no time limit, and says how many questions went without it.

### Speech to text

`docket transcribe <rid>`, or the `transcribe` tool, turns a recording's stored audio into a transcript of its own with
Whisper, on this machine, and stages it at the gate beside the recorder's as `<rid>/whisper` (DR-0567 to DR-0571). It
is off until you install it.

| Command | Does |
|---|---|
| `docket models install --speech [--speech-model large-v3\|tiny] [--device auto\|cpu\|cuda]` | builds the speech environment with uv, fetches Whisper's pinned files (large-v3 3.09 GB, tiny 76 MB, both MIT) and times it once |
| `docket transcribe <rid> [--here] [--wait]` | with the service running for the vault, queues the job there and says its place; otherwise, or with `--here`, decodes, transcribes and stages it in this terminal, `--wait` waiting for a job running |
| `docket eval speech <file>` | each engine's transcripts against stretches you corrected by hand: word, character and name error rates, and the names each added; writes nothing |
| `docket models remove --speech` | deletes the speech environment, Whisper's files and the choice; a plain `docket models remove` takes them too |

- On an NVIDIA GPU it installs NVIDIA's cuBLAS 12 under NVIDIA's terms, about 1.2 GB of packages, and needs about
  4.5 GB of the GPU's memory while it transcribes, besides search's 2 to 3 GB: 8 GB runs both, 6 GB only by turns,
  and under 6 GB the install says to use the CPU. On an RTX 5090 an hour of audio takes about two and a half minutes.
- On a CPU an hour of audio takes about an hour, so it transcribes only when you ask.
- The service runs one job at a time, under the machine's lock, so a job in a terminal waits its turn. Each job's
  place and outcome show in `docket service status` and on the recording's page. After each index update, on CUDA
  only, it queues by itself the 20 newest recordings whose stored audio reads ok and that have no Whisper transcript,
  each once while it runs: a sync's audio, once promoted, gets its transcript. A file that staged nothing for good,
  such as one with no speech, is passed over after a restart too, so older recordings get their turn.
- The speech file `docket eval speech` reads is yours, kept outside any repo, which it refuses: `schema_version: 1`
  and `stretches`, each `at: "r:<rid>@mm:ss-mm:ss"`, the `text` as it was said and its `names`. A name it added of
  an entity a never-store list holds is counted as hidden, never shown.
- Only audio stored and cut by everything its records need is transcribed, and the checks run again before the
  transcript is staged: a hold, a never-store match or a new cut that came meanwhile stages nothing. Cut stretches
  read `[cut]`.
- The transcript carries the recording's people, its recipients and the recorder's speakers, so a never-store name
  drops it as it drops the recorder's. A never-store word Whisper hears drops it too, and from then on the stored
  audio isn't played.
- A Hungarian recording gets its people's and projects' names as a prompt. An entity a never-store list holds by any
  of its names, aliases or ids stays out whole, and so does a name a redaction rule would change.
- Nothing leaves the machine: the worker runs offline, and the prompt and the transcript are never logged. The choice
  is this machine's, in your config's `speech:` section.

### Where the two transcripts disagree

`docket reconcile spots <rid>` lists where a recording's transcript and Whisper's disagree on a name, a number or a
word, for Claude to decide in context (DR-0583, DR-0587, DR-0589). It writes nothing, and works from raw each time,
on the machine that ingests the recording's source.

| Command | Does |
|---|---|
| `docket reconcile spots <rid> [--json]` | at most 40 spots, names, then numbers, then words, each with both lines, both anchors and the entities near it |
| `docket reconcile list` | at most 10 of the newest 30 recordings with both transcripts that have spots to decide |

- Each spot is in one recorder line, its two texts widened until the recorder's stands once there, so it names one
  place.
- A number written two ways (`forty two` and `42`, `tízmillió` and `tíz millió`, `a hundred` and `100`) is no spot.
- None sits in a cut stretch, and Whisper's words cut there read `[cut]`. One whose texts, line or speaker name
  someone a never-store list holds, in any word order or by an alias, or that touches a redaction marker or cut words,
  is counted, never shown. A recording a never-store value or your decisions keep out gets none, its source off or
  on.
- Claude's fixes, the doc that keeps them and the reading they make come in the next releases.

### Through Voyage AI's API

A machine with no GPU and no time for CPU embedding can use Voyage AI instead: voyage-4 at 512 dimensions for search
by meaning, rerank-2.5-lite for the reranker's 50 pairs. It sends your vault's text off the machine, so it runs only
when three things hold at once, each set on your yes, the key in your own terminal:

| Lock | Set with | Off |
|---|---|---|
| this vault allows it: `api_provider: voyage` in `vault.yaml`, committed | `docket models allow voyage` | `docket models allow none` |
| this machine chose it, in your config's `models:` block | `docket models install --api` | `docket models remove`, at once |
| the key is in this machine's keychain | `docket models key voyage` | `docket models key voyage --forget`, at once |

What leaves this machine, to `api.voyageai.com` over HTTPS:

| What | When |
|---|---|
| a chunk's title and text | once, by the embedding pass after an index update, and again only when its text changes |
| a search's query | on each search, for its vector |
| a search's query and its top 50 candidates' text | on each search's rerank |

Before anything is sent, Docket screens it with the vault's settings in force:

- Only chunks of the index go, and the index never reads `staging/`.
- A chunk stays here when one of its records comes from a source that isn't in `vault.yaml` any more, or is private
  now; when a record or its text matches a never-store list; or when a redaction rule in force would change its text.
  Raw is written once, so a source you make private keeps its old records here.
- Turning a source off pauses its capture and leaves its stored records going to Voyage; making it private keeps them
  here.
- A query that holds a never-store value, or that a rule would redact, isn't sent: that search goes without vectors and
  the reranker, and says why without naming what matched.
- A setting changed since the last check stops the reranker until the next embedding pass has screened every chunk
  again.

Costs and limits:

- voyage-4 lists at $0.06 a million tokens and rerank-2.5-lite at $0.02, each with 200 million free tokens per account
  (Voyage's docs, read 17 Oct 2026). A first pass over a vault of 100,000 chunks is about 37 million tokens; a rerank
  about 25,000.
- `api_monthly_tokens` in your config's `models:` block caps what this machine sends in a month, 50 million by
  default; past it nothing is sent until the next month. `docket models status` shows the month's count.
- The free tokens and Voyage's first tier need a payment method on file with Voyage. A pass meeting Voyage's rate
  limit waits and goes on; a search never waits for it.
- In Voyage's dashboard, opt out of their storing and training on your data, which gives zero-day retention.

The key lives in this machine's keychain only, never in the vault or your config. The keychain keeps it out of
transcripts and files, not from a program running as you, which can ask the keychain as Docket does. On a vault used
from several machines, each machine's choice and key are its own instant offs; `api_provider: none`, committed, stops
them all. The rerank keeps its 0.75 s and its fallback to the order without it.

## Entities

An entity is one YAML file, `entities/<type>/<id>.yaml`, that tells search who or what a name means:

```yaml
id: northwind
type: customer
name: {en: Northwind, hu: Northwind}
aliases:
  - {text: Northwind Traders}
  - {text: Nortwin, kind: asr}
  - {text: Norton, kind: asr, needs: [ben-sample]}
related: {person: [ben-sample]}
ids: {email_domain: northwind.example}
```

| Field | What |
|---|---|
| `id`, `type` | the file's name and its folder: person, org, customer, project, site, device, product, topic, stream, meeting-series, document, repo, place or term |
| `name` | the name per language code |
| `aliases` | other ways it is written; `kind: asr` for how speech recognition mangles it (matched within an edit or two); `needs` when it counts only next to one of those entities |
| `ids` | a source's user id (`chat: U0EXAMPLE`), `email` or `email_domain`, so what they wrote counts as theirs |
| `related`, `status`, `role`, `note` | kept for the entity view; `status` is active or archived |

A file with a mistake still loads where it can; `docket index status` lists what was wrong (DR-0113).

### Changing entities

`docket curate add <file>` makes the changes a file lists, all in one commit or none, and logs each one so it can
be undone. Use it to seed entities from a people directory or a glossary you already keep:

```yaml
changes:
  - create: {id: cleo-test, type: person, name: {en: Cleo Test}, aliases: [{text: Cleo}], role: rights agent}
  - entity: northwind
    add: {aliases: [{text: Nortwin, kind: asr}], ids: {email_domain: northwind.example}}
```

| Command | Does |
|---|---|
| `docket curate add <file>` | creates entities and adds aliases, names, ids and related links; what an entity holds already is counted and skipped |
| `docket curate log` | every change, newest first, with the op id undo takes, and a change a stopped write left marked as not finished (the next write commits it); a value a never-store list holds now is hidden; `--entity` (as logged), `-n`, `--json` |
| `docket curate undo <op>...` | takes a change back out: an alias, name, id or link, or a created entity's file while it is as it was created |

- Each change is a line of `entities/_log/<yyyy-mm>-<machine>.jsonl`, committed with the file it changed (DR-0460).
- Docket writes an entity file whole, in the form above. It refuses a file that holds a comment, or reads with a
  problem, since the rewrite would lose it: take the comment out, fix the file, or make the change by hand
  (DR-0463).
- A value on your never-store lists, or one a redaction rule would change, is refused (DR-0464). A refusal names
  the change by its position (`change 3`), never the value (DR-0476).
- A hand edit of a file the change touches is committed on its own first.

### Rounds

A round is at most 15 cards Docket mines from what the index holds: names a speaker label, your notes, mail or chat
spell, joined with how speech recognition heard them and their Hungarian case forms, and ranked by how much search
misses today (chunks holding a spelling that a search for the name doesn't find).

| Command | Does |
|---|---|
| `docket curate round` | starts a round on this machine: the index brought up to date, the cards mined, your question set measured |
| `docket curate show` | the round's cards with their state, spellings, counts and snippets; `--json` |
| `docket curate accept <card>...` | as a card says: a new entity, or new aliases; `--type`, `--id` for one new entity |
| `docket curate reject <card>...` | the card's name and what only joined it go on the stoplist; a label or a name written on its own stays a candidate |
| `docket curate lift <key>...` | takes keys off the stoplist |
| `docket curate status` | whether a round is due (a week after the last on any machine), review minutes per week, the stoplist, your questions before and after; `--eval` measures again |

- Name a card by its id, or by its number with `--round <round>`; a round that isn't this machine's current one is
  refused.
- To change more than a new entity's type or id, write a changes file whose change names the card (`card: <id>`) and
  run `docket curate add`: the card's spellings you leave out are rejected as that entity's aliases.
- The round lives in `.git/docket/curate/batch.json` (a folder vault: the host's git dir), per machine, and can be
  deleted: decisions are in the curate log.
- Set `curate.questions` in Docket's config to your question file, and a round measures it before and after.
- On the machine `vault.yaml` names as primary, a round first applies tier 1's facts by itself: an address or a
  chat id that mail or chat writes beside a known full name (`Name <address>`, `Name <id>`), a name a system
  shows beside an id an entity lists, and a spelling two engines' transcripts agree on. `show` and the page list
  each with its undo, and what the store refused. A fact that fits two entities, a one-word name, or a spelling
  that may be a common word or someone else's name becomes a card; a sender shown with several people's names,
  such as a service's notification address, gives nothing.
- The web app's Curate page (`docket open /curate`) does the same in the browser: a round, each card opened
  beside the list with its snippets, accept, edit and reject, and undo and lift from the log.

## The service

One Docket process per user serves Claude and the web app on one 127.0.0.1 port, 8741 unless you choose another. It
runs its writes one at a time; the commands above keep writing directly, under the same vault lock.

| Command | What it does |
|---|---|
| `docket service install --vault <folder> [--port N]` | registers the service to start at your logon (a Task Scheduler task on Windows, a LaunchAgent on macOS, a systemd user unit on Linux), makes its token, starts it, and waits until it answers |
| `docket service status [--quiet]` | installed or not, running or not, the vault, the port, where the token lives, the PATH it runs with; `--quiet` is one line |
| `docket service stop` | stops it until the next `install` |
| `docket service uninstall` | stops it and removes the registration; the token and the config stay |
| `docket service token [--header] [--rotate]` | prints the token, or the header Claude Code's `headersHelper` reads; `--rotate` makes a new one and restarts the service |
| `docket open [PATH]` | opens the web app through a link good once and for a minute, and prints the link; it lands on Today, or with a path, such as the one its sign-in page names, there |
| `docket serve [--vault] [--port]` | runs the service in the foreground, where install can't register it (WSL without systemd) |

- Install from a tool environment (`uv tool install`), not a development checkout: the service runs whatever code
  its interpreter finds at its next start.
- Upgrading: `docket service stop`, upgrade Docket, `docket service install`. On Windows a running service holds its
  files open, and the upgrade can't replace them.
- Run `docket service install` again after your PATH changes: the service runs with the PATH of the shell that
  installed it, so it finds the git and git-lfs you use.
- The service checks its own port every 30 seconds. When nothing answers there it says so in its log and exits, and
  its registration starts it again: within 5 minutes on Windows, in seconds on macOS and Linux, at your next logon
  under the Run key (DR-0762).
- macOS: a vault under `~/Documents`, `~/Desktop`, `~/Downloads` or iCloud Drive needs the Python the service runs to
  have the Files and Folders permission (or Full Disk Access) in System Settings, Privacy & Security. Install names the
  Python when it can't read the vault; or move the vault elsewhere.
- `docket open` prints its link too: a snap or Flatpak browser (Ubuntu's default Firefox) can't open the page it
  writes in a hidden folder, so paste the link instead.

Claude Code connects through the Docket plugin, or by hand:

```json
{"type": "http", "url": "http://127.0.0.1:8741/mcp", "headersHelper": "docket service token --header", "timeout": 120000}
```

The plugin's session-start hook runs `docket hook session-start`, which prints the service's state, the vault's and
the port check as the JSON Claude Code reads (DR-0163), then your day as the service reads it (`GET /api/day`): whole
in a session whose own repo is the vault's, one line of counts in any other, and a line naming `docket ledger day`
when the service is down or slow (DR-0670 to DR-0672). Its capture hook runs `docket hook post-tool-use` after every
MCP call: with no run open it reads one small file and returns (DR-0242). Both are hidden from `docket --help`: they
are for the plugin, not for people.

What the service refuses, and what it doesn't:

- A request whose Host isn't this port on 127.0.0.1 or localhost, so another site that rebinds its name to
  127.0.0.1 gets nothing.
- A write from another origin or site, and any caller without the token (the pages take a session cookie instead).
  No answer carries a CORS header.
- A run's routes take only the token, never the browser's session, and a capture only from the session the run is
  bound to. Any program running as you could post into an open run, as it could write the vault; what it adds alone
  breaks the run's count and drops it (DR-0243).
- Another user of the machine can reach the port and see that Docket answers, and, while the service is down, could
  bind the port first. Any program running as you can read the token, from the keychain or the file.
- The log keeps no content: route templates, status codes and exception types, never your words.

## The web app

`docket open` signs your browser in to the service's own pages and lands on Today. In this version that is Today, the
Queue, the Ledger, Meetings, Search, Recording, Briefs, Sync, Staging, Curate and Settings; the navigation shows the
pages still to come, with the phase each arrives in.

The pages are moving, one by one, into a new app built in React; the day pages, Today, the Queue, the Ledger and
Meetings, are its first, then Search, the Recording page, Briefs, Staging and Curate. Its shell is the sidebar with
what waits at the gate and whether a sync is due, the theme and keys switches at its foot, the list and the open item
beside it; `/design` shows its parts on invented data. It comes built with Docket; nothing to install.

- Search runs the search Claude runs: the same words, names, time words and dates. Sources and a month narrow it;
  people and entities favour the hits that mention them, as Claude's boosts do, each from a menu over the results,
  with the dates beside them. Facet counts are within the top 200. The words it found are highlighted (DR-0775).
- A hit opens beside the results: the raw it came from, then the summary covering it with the lines pointing at it
  marked. Widen or narrow the raw with `[` and `]`; `v` checks each line of the summary against its raw.
- A search's URL holds the search tool's parameters (`query`, `sources`, `people`, `entities`, `since`, `until`,
  `k`) and the open hit (`hit`, `lines`, `around`, `verify`), so a bookmark or a link from Claude reopens it. Paste a
  locator from Claude's answer into the box to open it.
- Keys: `/` the box, `j` and `k` through the hits, carrying an open hit along, `Enter` or `o` to open one, `Esc` to
  close it, `c` to copy a locator, `?` for the rest. The keys switch at the sidebar's foot turns the single keys off.
- Nothing loads from the network: the app, its fonts and the icons come with Docket, and the pages work
  offline. Any browser from mid-2024 on; the app's pages need JavaScript.
- A session lasts 12 hours, and ends when the service restarts; the sign-in page then names the `docket open` command
  that brings you back to the page you were on.

### The Recording page

`/recording?rid=<rid>` puts a recording's summary beside its transcript, and plays its audio from any line
(DR-0545, DR-0776, DR-0777). It only reads.

| Part | Shows |
|---|---|
| Summary | the minutes or recording note search attaches, else a legacy note; the other docs on the call as links; each anchor a link that marks the lines it points at |
| Transcript | the one the rid names, else the one the summary covers; each line's time a link to that line; the call's other transcripts as links |
| Whisper's transcript | once raw holds it, beside the recorder's, a row per stretch of time; a cut stretch shows as cut in both |
| Audio | the browser's own player, when the stored audio reads ok; else why there is none: not stored yet, waiting at the gate, held, kept out by a never-store value, cut before its latest cuts, no key here, or not downloaded |

- Its URL holds `rid` (a recording's or a transcript's, or a locator such as `r:plaud:4f1c@02:30`), `doc` and `at`
  (`02:30` or `02:30-03:20`). A link that sets `at` lands on the first line it marks, scrolled into view.
- A line's time plays the audio from its start, and the address keeps the line. A cut stretch never plays.
- The audio comes from `/api/web/recording/audio`, in the browser's session only: the token never fetches it. It
  plays only while the audio reads ok, checked on every request; one file's decrypted bytes stay in memory a minute
  for the player's next range, answered exactly and never cached (`Cache-Control: no-store`). Nothing about it is
  logged but the route's name.
- Search's transcript hits, and its panel on a recording, link here at the hit's time.
- A held recording shows only that it is held, with a link to the holds on the Staging page.
- Keys: `?`.

### The Briefs page

`/briefs` lists the briefs, and opens one with the raw its lines cite beside it (DR-0780). The `brief` skill writes
briefs when you ask Claude; the page's one write is your pick on a decision (DR-0781).

| Part | Shows |
|---|---|
| The list | the decisions waiting for you first, then the briefs by entity type: each one's state (stale, current or orphaned), rev, how long since, flagged lines and new records; an orphan with the entity that holds its name now |
| A brief | its lines, a flagged one marked, each line that cites something a link; its anchors open the Recording page at their time, or the Search page |
| A line | in the panel beside the brief: the raw it cites, and the doc lines it was built from as they read now, with their own raw |
| Decisions | for a project, topic or stream: what waits for you, each with a decision it contradicts, and its buttons; then what is decided, newest first, what it replaced folded under it; then what you rejected |

- Its URL holds `brief` (`brief:<entity id>`), `line` and `type` (an entity type, or `orphaned`). A link that sets
  `line` ends in `#raw` and opens the line's raw.
- A decision's buttons: Confirm or Reject; for one that contradicts a decision, the new one replaces it, the old
  one stands, or not a contradiction. The decision as the page showed it is what counts: one that changed since,
  such as one you settled in another tab, or one whose earlier decision was replaced meanwhile, writes nothing, says
  why, and shows as it stands now. Only your session picks: the token never does.
- A brief whose entity a never-store list now holds shows only that it is hidden, and is counted on the list; a
  line a list now names shows only that it is hidden, and opens nothing.
- Keys: `j` and `k` through the briefs, or a brief's lines that cite, the open raw following; `o` opens; `Esc`
  closes the raw; `?`.

### The Today page

`/today` is the start page: `/` goes there, and so does `docket open` with no page. It reads your day as `docket ledger
day` does, in the order a morning reads it (DR-0773).

| Part | Shows |
|---|---|
| The next meeting | under way now, or the next to start today, else the next working day's first: its start, where and with whom, its open points and the items on it |
| Due | the items overdue, due today and back from a snooze, as the Ledger's rows: tick them for the act bar, or use the quick buttons and keys; the first 100, the rest on the Ledger |
| The rest | today's later meetings and the next working day's, never cancelled or declined ones, each with its first open points and items; then what you closed today |
| The bar | the day and its counts, and each live source's last sync, a due one marked as in the sidebar; each links to its runs on the Sync page |

- An item's row opens its panel as on the Ledger, a meeting's its panel as on Meetings; its URL holds `open`, `at`,
  `drop`, `text` and `suggest`, and no filter.
- It updates in place when the ledger changes or a sync closes, and when a meeting starts or ends or a sync falls due.
- Keys: the Ledger's, `j` and `k` through the meetings and items alike; the items' keys never act on a meeting.

### The Queue page

`/queue` lists the open items to work through, as `docket ledger queue` does, beside the one you open: the untriaged
first, those whose snooze ended leading them, then the items by their call, discuss, claude, me and chase, each by
due (DR-0666). It is the Ledger page's list, keys, act bar, item panel and new item over the queue's read, and what
`/docket:tasks` opens.

- Each call's group carries its stamp and what it means; `1` to `4` make the call on the selected row, which moves on
  to the next one, so a run of keys triages the untriaged in turn.
- Its URL holds `open`, `q`, `stream`, `kind`, `who` and `k`, as the Ledger's does; the Ledger's own `state`,
  `group` and `archived` are left out.
- The sidebar shows how many are untriaged; the page updates in place when the ledger changes, as the Ledger does.

### The Meetings page

`/meetings` lists your meetings by day, from today on, then the earlier days, newest first, each day by start, beside
the one you open (DR-0666, DR-0667). It reads two weeks either side of today, and an older meeting whose point is
still open.

| Part | Shows |
|---|---|
| A row | its time, now while it is under way, its subject and response, how many of its points and items are open; a cancelled or declined one, or one with nothing on it, dimmed |
| The meeting | when and where, the organiser and attendees, each linked to their brief when one is kept, your answer, the recording on the Recording page |
| Its points | each one's mark ticks it answered or open again; Answer, Edit, Drop, Make an item, and Keep or Remove on one Claude proposed, drawn in a dashed rule until kept; a rolled one shows where it went |
| The rest | Suggest items, each item worth raising added as a point in one click; the outcome and notes, saved as you leave them; the items on it, each opening on the Ledger; its materials and history |

- Its URL holds `open`, `q` (in a meeting's subject, people, points and notes), `stream` and `suggest`.
- A meeting that is over says its open points move on at the next sync; Claude's sync rolls them, never the page.
- Copy agenda copies the open points as text.
- Keys: `j` and `k` move, `Enter` opens, `Esc` closes, `u` or Ctrl+Z undo, `/` the search box, `?` for the rest; the
  items' keys never act on a meeting.

### The Ledger page

`/ledger` lists the ledger's items beside the one you open, and every button writes the change as one batch, by you,
through the page, as `docket ledger batch` would (DR-0664 to DR-0666, DR-0710, DR-0711). It is the old tasks app's
list, one click and one key at a time, and the first page of the web app's React shell.

| Part | Shows |
|---|---|
| The list | open items by due: snooze ended, overdue, today, this week, later, none; or by stream, kind, who or meeting. Snoozed by the day each comes back, closed by its day, or all; 100 rows, Show more for the next 100 |
| A row | its call, number, kind as a tab in the kind's own colour, title, meeting, who, stream, due and age; on hover or focus the verdicts, done and drop |
| Ticked rows | the act bar: a verdict, no call, done, drop with its why, snooze, reopen; each row the change doesn't take is skipped and named |
| The item | its call, its fields, saved as you leave each one, the meeting it hangs on with its points, notes, materials, where it came from, its counts and its history, newest first, Undo on each line that has one |

- Its URL holds `open`, `q` (every word, in an item's text and log), `stream`, `kind`, `who`, `state`, `group`,
  `archived` and `k`; `/ledger?who=Ada` is what a person's brief links to.
- After a change the page says what it wrote, with Undo; after an undo, Redo. A change the ledger refuses writes
  nothing and keeps what you typed, with the ledger's words.
- A material that is a path from your repo's top opens with its app or in its folder, on this machine, only for
  documents, images and media inside the repo; a text file opens in the panel. A URL opens as a link, a locator on
  the Search page.
- The page updates in place when the ledger changes, from the CLI, Claude or a sync, unless you are typing, have
  rows ticked, a dialog open or a save on its way; what you selected, ticked and typed stays.
- Leaving the item or the page with text typed and not saved asks first.
- Who, in the item, the new item and the filter, offers the names the ledger uses, the most used first, and your
  vault's people with their aliases once the index holds them (DR-0714): type to narrow them, the arrows and Enter to
  take one, Esc to close the list; a new name is always taken as typed.
- Keys: `j` and `k` move, the panel following once open; `x` or Space ticks; `1` to `4` discuss, claude, me, chase;
  `0` no call; `e` done; `Delete` or Backspace drop; `s` snooze; `u` or Ctrl+Z undo; `n` a new item; `/` the search
  box; `?` for the rest.

### The Sync page

`/sync` shows how syncs went without a log, what to do first (DR-0304, DR-0305, DR-0788). It only reads: nothing on
it opens, drops or closes a run.

| Part | Shows |
|---|---|
| Needs | a source `run_open` would refuse, in its words; a source no machine synced in a day, or never; ids due a final run; each with the words to paste into Claude Code |
| Failures | dropped runs until a later whole run of their source, ids skipped for a reason other than `later` while a cursor file holds them, ids a close expired in the last week, cursor files that don't read; each with what to do |
| Runs | open runs, read again every 5 seconds while one is open, then recent ones; a run's card opens beside them: captures, what was staged, the mark, what is owed, or why the run dropped |
| Cursors | each machine's mark, lookback, retry and waiting ids with their ages, and whether another machine's are still handed over |

- Its URL holds `source` (one source), `run` (a run's card, in the panel) and `k` (how many recent runs, up to 500).
- When the last open run closes, the page reads itself again, the sidebar's due mark too.
- Recent runs survive a restart: each ended run leaves one line in the vault's private folder, `sync/runs.jsonl`
  under `.git/docket/` (or the host's git dir for a folder vault). Ids and counts only, per machine, never committed;
  deleting it loses only the page's list.
- The page shows ids, counts, times and Docket's own messages, never a title or anything a source's tool returned.
- Keys: `j` and `k` through the runs, an open run following, `Enter` or `o` to open one, `Esc` to close it, `?`.

### The Staging page

`/staging` is what waits at the gate, worked in the browser through the same calls as `docket staging` (DR-0380,
DR-0382, DR-0383, DR-0784, DR-0785).

| Part | Shows and does |
|---|---|
| Ready | what goes at the next promotion, per source, with a Promote button per source and one for all |
| Waiting | each record by source, oldest first: kind, title, when, how long it waited, and why it waits; a record's text only once you open it |
| The open record | beside the list, as promotion would write it, redacted by today's rules: tick utterances or lines to cut, or type a range (`12:30-14:05`, `120-340`), Preview, then Work; or Private, which asks once more and drops it |
| Held | each source's held ids, and the commands that read and release one in your terminal, quoted, with `--from` and `--vault` filled |

- The page names the release's command, which you run or Claude runs on your yes; no page releases a hold (DR-0382).
- A record the CLI, Claude or another tab changed since you opened it isn't overwritten: the page says so and shows it
  as it stands. A refused cut, such as a range that doesn't read, keeps what you ticked and typed beside its words.
- A record that already has cuts opens with them ticked, and Work keeps them unless you untick them. Utterances its
  recording's release cuts show as gone, with no text.
- A typed time can carry milliseconds, `0:30.400`, as `--cut` takes it everywhere.
- Its URL holds `source`, `state`, `k`, the open record (`open`), what you previewed (`u`, `line`), typed cuts (`cut`),
  `picked=1` when you cleared a record's cuts on purpose, and the drop's question (`confirm=private`).
- Its writes take the browser's session only: the token never classifies or promotes.
- Keys: `j` and `k` through the records, an open record following, `Enter` or `o` to open one, `Esc` to close it,
  `?`. No key writes.

### The Curate page

`/curate` is this machine's curate round, worked in the browser as `docket curate` and the curate skill work it
(DR-0468, DR-0786).

| Part | Shows and does |
|---|---|
| The round | its cards, up to 15: new people and terms, and spellings search misses for entities you have, each with its state; Start a round, or a new one |
| The open card | beside the list: what it would write, its spellings, and up to three snippets with the spelling marked, each a link to Search; Accept, Edit or Reject |
| Edit | in the panel: for a new card its type, id, a name per language and role; the spellings to keep, each plain or asr; the rest are rejected as its aliases |
| Around it | what tier 1 applied by itself, your question set before and after, the forms matching leaves out, the log with Undo, the stoplist with Lift |

- Once you decide a card, the next open card opens.
- A card whose entity file changed since the page showed it, by another tab, the CLI or Claude, isn't overwritten: the
  page says so and shows it as it stands. A refused edit keeps what you typed beside its words.
- A value a never-store list holds shows as hidden, with no Undo or Lift.
- Its URL holds the open card (`open`) or its edit form (`edit`). Its writes take the browser's session only.
- Keys: `j` and `k` through the cards, an open card following, `Enter` or `o` to open one, `Esc` to close it, `?`.
  No key decides a card.

### The Settings page

`/settings` shows what the gate obeys and this machine's models (DR-0789, DR-0510). Its buttons make changes both
ways (DR-0601): a never-store value added or taken off, a redaction rule turned on or off, a source's sensitivity, a
source on or off. Turning credentials redaction off it names, for your own terminal; a new source, a media folder and
the api provider it names as commands.

| Part | Shows |
|---|---|
| Sources | each source's sensitivity, on or off, tools, mapper and whether the runner takes it; a folder source's folder, this machine's own folder and exclude; each change as the `vault.yaml` line that makes it, stricter first |
| Never store | each list's count and what it keeps out; the values only with `show=lists`; the line to add one |
| Redaction | each rule and pattern of yours, on or off, with the `docket redaction set` command that turns it the other way and the warning it prints; the block for a new pattern |
| Models | what `docket models status` says, the service's worker, the vectors; the commands to install, remove or restart |

- A change that lets more in is marked so on the page: what it lets in reaches git for good.
- While `vault.yaml` holds an edit that isn't in force, the page says so at the top.
- A rule turned on or off, a value taken off, or a source's sensitivity or on and off, is one click; private asks
  once more first,
  naming what waits from it in staging, which it drops. Turning a rule on names `docket doc redact` for the docs
  already stored. A click on a page older than `vault.yaml` writes nothing, and a click sent twice writes once.
- A `vault.yaml` with comments or a layout of your own is never rewritten: the page says so, and each change is a
  line to make by hand.
- Adding a never-store value asks once more first: the records it drops from staging now, and how many records and
  chunks already stored hold it, with their share of the index, so a word that would take half of everything shows
  it (DR-0509). They stay in raw; from the next sync, what it names is dropped before it is stored.
- The value goes in a write's body only: never in the address bar, an answer, the service's log or a commit
  message, which says `Config: never-store people, 1 added`. The field asks the browser to keep nothing and check no
  spelling (DR-0790). A value refused, such as an empty one, says why on its field and stays there to fix.
  Sending the same add twice adds it once.
- A never-store value is committed in `vault.yaml` as you write it, so it is in your repo's history and goes wherever
  the vault is pushed.
- A change made while the page was open (another tab, the CLI, an editor) refuses the click, says so, and the page
  shows `vault.yaml` as it stands; an add's question comes back counted again. An edit of `vault.yaml` not committed yet is committed on its own first, by the CLI too,
  and the question says so; one that turns credentials redaction off refuses the add until you commit or undo it.
- Keys: `?`.

The pages' browser tests run on CI. To run them here, against your own Chrome or Edge, with `npm ci` run once in
`packages/docket/web` for their accessibility checks:
`DOCKET_TEST_BROWSER=chrome uv run pytest packages/docket/tests/test_web_browser*.py` (or `msedge`).
