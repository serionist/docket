# docket

The Docket app: the vault, the service (web app, MCP endpoint, scheduler) and the `docket` command. The repo's
[README](../../README.md) says what Docket is.

| Command | What it does |
|---|---|
| `docket init` | set up a vault: asks where, who you are, this machine's name, the sources, and inside a git repo whether the vault is a folder of it (the default) or a repo of its own (`--storage folder\|repo`); every answer is also a flag, and `--yes` takes the defaults; `--no-sources` starts with none, for `docket source new` to add; the summary names each check a flag overrode (`--allow-shared`, `--remote-is-private`, `--allow-dirty-parent`) |
| `docket sync` | a repo of its own: fetch and rebase onto its remote; a folder: says your own git moves it, and does nothing else |
| `docket vault status` | which vault it found and how (`--vault`, `DOCKET_VAULT`, this folder or above, `./vault`, the default vault), where it lives and on which branch, records per source, runs, run files not committed yet, the last commit |
| `docket vault check` | whether the vault is intact: every raw file parses, nothing written once was changed, no ignore rule hides a vault path, the vault's own attributes apply, no doc rev is claimed by two commits |
| `docket vault convert` | make a vault that is a repo of its own a folder of the repo around it, history included: a dry run first, then your yes; `--abort` undoes one that stopped before its commit |
| `docket staging list` | what waits for the gate, oldest first: `pending` waits for you or Claude, `ready` goes to raw at the next promotion |
| `docket staging show <ref>` | one staged record, its cuts, and the version in raw that was cut, when there is one |
| `docket staging classify <ref> work\|private` | work makes it ready, `--cut 120-340` (characters) or `--cut 12:30-14:05` (a time) cuts a stretch; private drops it |
| `docket staging promote` | writes every ready record to the raw log, cut and redacted, one run per source, and commits; a record on hold stays in staging |
| `docket ingest <source> <folder>` | imports payloads fetched earlier through the source's mapper: the folder's `hold.txt` first, then its JSON files; every record goes to staging, nothing on hold; `--hold <file>` adds more ids to hold, `--final` tells the mapper not to wait for more, `--mapper-errors` prints the mapper's own errors |
| `docket source new <name>` | writes a source's skill, mapper and tests into `.claude/skills/sources/<name>/` of the repo that holds the vault, from Docket's blank template, or `--example` for a reference source (Plaud), and adds the source to `vault.yaml` with `--sensitivity` and its `--tool`s; never over a folder that exists |
| `docket doc put <file or ->` | store a processed doc as its next rev; refused, with every problem and its line, when the frontmatter is wrong, an anchor doesn't resolve, or the doc changed since the rev it carries |
| `docket doc read <locator>` | a doc, or a record and the doc lines that point at it; `--view raw` or `both` for the raw, `--around` for minutes, paragraphs or messages either side, `--rev` for an earlier revision |
| `docket doc verify <doc>` | each line of a doc next to the raw its anchors point at; `--line` for chosen lines |
| `docket search <query>` | hits grouped by recording or thread, each with its locator and the group's summary; `--since`, `--until`, `--source`, `--person`, `--entity`, `-k`; it updates the index first unless `--no-update` |
| `docket index rebuild` | build this machine's index from the vault, from scratch |
| `docket index update` | index what changed since; it rebuilds when it has to |
| `docket index status` | whether the index is there and current, its languages, counts, what is new, and its problems |
| `docket import notes <folder>` | each markdown note in an existing folder becomes a legacy note that keeps its old path, so `doc read notes/x.md` or `notes/x.md@12:08` still finds it; the folder is only read; run it again to pick up changes; `--dry-run` first, `--as` for what old paths start with |

A command finds its vault from `--vault`, `DOCKET_VAULT`, the working folder upward, a `vault/` folder in it, or the
default the wizard recorded. A `<ref>` is a record's rid, or at least 6 characters of the key `staging list` shows.

## In a folder of your repo

By default the vault is a folder of the repo you work in (`storage: folder` in `vault.yaml`). Docket commits there
locally, and your own git does the rest.

- Docket's commits hold only the vault's paths: nothing you staged or changed goes in. They skip your repo's hooks,
  and land on whichever branch is checked out; `docket vault status` warns when that isn't the remote's default.
- Docket never fetches, pulls, stashes or pushes your repo. Your push carries the vault, media included once git-lfs
  is set up (init and convert set it up, or say what to add to a pre-push hook of your own).
- Docket's own files sit under your repo's `.git/docket/<vault path>/`, never in your tree.
- `staging/` (records waiting for the gate) is ignored, so `git clean -x` deletes it; a source's next sync pulls those
  records again.
- A clone without the audio: `git config lfs.fetchexclude "<vault path>/media/**"`.
- Init refuses a folder in a repo anyone can read, and warns when others commit to it.
- `docket vault convert` refuses a vault with a remote: another machine's clone of it would be overwritten by that
  machine's next pull of your repo. Bring those clones' commits in, delete them, remove the remote, then convert.

## The gate

Every record reaches the raw log through the gate. It reads `vault.yaml` on every call, so an edit applies at once.

| `vault.yaml` | What it does |
|---|---|
| `sources.<name>.sensitivity` | `work` goes through, `mixed` waits in staging to be classified, `private` is dropped |
| `sources.<name>.mapper` | the source's mapper, relative to the repo that holds the vault: `.claude/skills/sources/<name>/mapper.py` |
| `sources.<name>.tools`, `media_hosts` | the tools the capture hook posts for the source, and the hosts `media_put` may fetch from; the service reads them |
| `never_store.people` | drops a record whose author, any recipient, or any id in its source's payload equals an entry: an exact address (`kid@example.invalid`, also inside `Kid <kid@example.invalid>`) or the source's user id (`U0KID`); case doesn't matter |
| `never_store.containers` | drops a record from a channel, chat or folder: its id, with or without the source's prefix (`slack:C0FAMILY` or `C0FAMILY`) |
| `never_store.keywords` | drops a record that says the words anywhere, title, text, attachment names or payload, as whole words: case, accents and curly apostrophes don't matter (`kovacs` matches `Kovács`, not `kovacsfalva`), and any punctuation may join the words; a keyword of two or more words also matches them joined by a case change or by nothing (`dora example` matches `dora-example`, `dora_example`, `DoraExample` and `doraexample`), while a one-word keyword never matches inside a word (`Max` leaves `maxRetries` alone); a name behind an encoding matches too: percent-encoded, once or wrapped again as a Safe Link does (`Dora%2520Example`), behind HTML entities, in Latin-1, or as `%u` and `\u` escapes |
| `redaction.builtin` | `true` (the default) redacts passwords and secrets after a label (also in bold, in a table cell, or on the next line of an HTML mail), API keys and tokens, private key blocks, and phone numbers written with `+` or after a label; a `+` amount with its currency (`+12 500 000 Ft`) stays |
| `redaction.patterns` | your own: each has a `name` (it shows in the marker) and a `pattern`, a regular expression; a group named `secret` redacts only that part (`door code (?P<secret>\d+)`) |

- A hyphen or other punctuation in a keyword also matches a space: `check-up` drops "check up" too.
- A dropped record leaves no file and no log line, only a count.
- What is redacted becomes `[redacted:<name>]`, and a cut stretch becomes `[cut]`; the secret itself is kept nowhere.
- Staging lives in the vault's `staging/` folder, on this machine only, never committed.
- A source's name is lowercase letters, digits, dots, underscores and hyphens, and doesn't end in a dot: it names
  folders and files.
- A transcript is cut by time: each utterance the cut touches goes whole, and one `[cut]` takes its place. Any other
  text is cut by characters. A cut record keeps no payload.

## Sources and their mappers

A source's skill and mapper live in your project, under `.claude/skills/sources/<source>/`; Docket runs the mapper and
checks what it returns (DR-0108, DR-0119). `docket source new <name> --vault <vault>` writes that folder and adds the
source to `vault.yaml` (DR-0166); the plugin's `/docket:setup` runs it for each source you choose.

- Without `--example` it writes Docket's blank template: a `SKILL.md` to fill, a mapper that sets every capture aside
  as `not-mapped-yet` until you write the mapping, and its tests. With `--example` it copies a reference source whole:
  in this version Plaud, [examples/sources/plaud](../../examples/sources/plaud) (DR-0167).
- It writes only into that folder of the repo around the vault, never through a link out of it or into the vault, and
  never over a folder that exists. It changes `vault.yaml` only when the file is as Docket wrote it, and commits it;
  otherwise it prints the lines to add under `sources:`. The folder is yours to commit.
- Without `--vault` it takes only a vault found from the project that holds it. One found through the default vault
  or `DOCKET_VAULT` can be another project's, so it is refused, with the `--vault` to pass.
- Claude Code doesn't load a `SKILL.md` from `.claude/skills/sources/` as a skill: whatever runs the source reads it
  by its path (DR-0168).
- Run a source's tests where it lives: `python -m pytest .claude/skills/sources/<name>/tests`, one source folder at a
  time, or with `--import-mode=importlib`, since every source has a `tests/test_mapper.py`. The tests that go through
  Docket's own checks run only where `docket` can be imported (a Python environment Docket is installed in);
  elsewhere they are left out and the mapper's own tests still run.
- The mapper is `map_run(run)` in `mapper.py`: a run's captures and its manifest in, records out, as plain JSON.
- Docket runs it in its own process, for at most 300 seconds and 256 MiB of output. A failure stages nothing, and the
  run's captures are fetched again.
- Docket checks every rid, sorts a transcript's segments and renders its text, and computes every version: a mapper
  can't break ids or decide what counts as new.
- Changing a mapper: keep its output stable, since every record the lookback reads again gets a new version when it
  changes; pin golden versions in its tests, as the example does; pull the project on every machine before its next
  sync.

## Hold lists

Records Docket must not stage yet, such as backfilled recordings with stretches you left out, are held by id in
`state/holds/<source>.txt` (DR-0121). Every ingest and the gate read it.

- Write `hold.txt` beside the backfill files before the first import, one id a line. `docket ingest` adds it to
  `state/holds/<source>.txt`, which is then the only truth, and commits it. Only a release takes an id off.
- A held record gets no record on any route; it is only counted.
- When an ingest stops because a held record waits in staging, delete the files it names, then run it again.
- `docket staging classify` refuses a held record, work or private, and `promote` leaves it waiting.
- A held record already in raw is named on every ingest; it waits for `docket forget`.
- A recording's id in `never_store.containers` keeps it out for good, held or not.

### Releasing a held recording

A held recording goes in only with the cuts you confirm (DR-0200 to DR-0206).

| Command | What |
|---|---|
| `docket hold list [<source>]` | the held ids; one marked `held again after its release` came back with a merge |
| `docket hold show <source> <id> --from <folder>` | the recording as its mapper reads it from the folder, a transcript one utterance a line with its times; writes nothing |
| `docket hold release <source> <id> --from <folder> --cut 12:30-14:05 [--keep <rid>]` | one recording, its cuts and what to keep as it reads |
| `docket hold release <source> --from <folder> --proposals <file>` | the proposals file `/docket:cuts` writes, outside any repo |

- Run the release in your own terminal: it refuses Claude Code's shell, a pipe and a redirect. Each recording shows
  each cut widened to whole utterances, its edges, and every record it keeps in full; it is released only when you
  type back the code it shows. `n` keeps it held, `q` stops.
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
  `docket hold release <source> <id> --from <folder>` (one code, no cuts asked), takes it off; what that stages waits
  as pending. `hold show` refuses such an id: its cuts are on record.

## Processed docs

A processed doc's lines end with anchors: `@12:08` in a doc covering one record, `[r:<rid>@12:08]`, `[r:<rid>#2]` or
`[r:<rid>]` across records (DR-0110). A locator is a doc's id or path, `r:<rid>@12:08`, or a rid. The same three
operations are `docket.recall.doc_put`, `read` and `verify` in Python. A time resolves against the record's `segments`
field, which the source's mapper fills (DR-0111); a record without segments takes paragraph and whole-record anchors.

## Search

A query is words, `"quoted phrases"`, names and time words: `docket search "tízmillió forint" múlt héten --person
ben-sample`. Words match as written, accents or not, and through their stems in each of your languages; names expand to
their aliases; time words ("last week", "múlt héten", "yesterday", "2026-10") become a filter; `--person` and
`--entity` lift what mentions them and never hide the rest. In Python it is `docket.index.search`.

The index lives in `index/` in the vault, on each machine, never committed. `docket search` keeps it up to date, and
`docket index rebuild` makes it again from scratch.

Your languages are `owner.languages` in `vault.yaml`, as codes such as `en` and `hu`; English is always searched. Write
Norwegian as `"no"` in quotes, or YAML reads it as false. Adding or removing a language rebuilds the index the next
time you search.

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
| `docket open [PATH]` | opens the web app through a link good once and for a minute, and prints the link; with a path, such as the one its sign-in page names, it lands there |
| `docket serve [--vault] [--port]` | runs the service in the foreground, where install can't register it (WSL without systemd) |

- Install from a tool environment (`uv tool install`), not a development checkout: the service runs whatever code
  its interpreter finds at its next start.
- Upgrading: `docket service stop`, upgrade Docket, `docket service install`. On Windows a running service holds its
  files open, and the upgrade can't replace them.
- Run `docket service install` again after your PATH changes: the service runs with the PATH of the shell that
  installed it, so it finds the git and git-lfs you use.
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
the port check as the JSON Claude Code reads (DR-0163). It is hidden from `docket --help`: it is for the plugin, not
for people.

What the service refuses, and what it doesn't:

- A request whose Host isn't this port on 127.0.0.1 or localhost, so another site that rebinds its name to
  127.0.0.1 gets nothing.
- A write from another origin or site, and any caller without the token (the pages take a session cookie instead).
  No answer carries a CORS header.
- Another user of the machine can reach the port and see that Docket answers, and, while the service is down, could
  bind the port first. Any program running as you can read the token, from the keychain or the file.
- The log keeps no content: route templates, status codes and exception types, never your words.

## The web app

`docket open` signs your browser in to the service's own pages. In this version that is Search; the navigation shows
the pages still to come, with the phase each arrives in.

- Search runs the search Claude runs: the same words, names, time words and dates. Sources and a month narrow it;
  people and entities favour the hits that mention them, as Claude's boosts do. Facet counts are within the top 200.
- A hit opens beside the results: its summary, with the lines pointing at it marked, next to the raw it came from.
  Widen or narrow the raw with `[` and `]`; `v` checks each line of the summary against its raw.
- A search's URL holds the search tool's parameters (`query`, `sources`, `people`, `entities`, `since`, `until`,
  `k`) and the open hit (`hit`, `lines`, `around`, `verify`), so a bookmark reopens it. Paste a locator from Claude's
  answer into the box to open it.
- Keys: `/` the box, `j` and `k` through the hits, `Enter` to open one, `Esc` to close it, `c` to copy a locator,
  `?` for the rest. The keys dialog turns the single keys off.
- Nothing loads from the network: htmx, the fonts and the icons come with Docket, and the pages work offline. Any
  browser from mid-2024 on.
- A session lasts 12 hours, and ends when the service restarts; the sign-in page then names the `docket open` command
  that brings you back to the page you were on.

The pages' browser test runs on CI. To run it here, against your own Chrome or Edge:
`DOCKET_TEST_BROWSER=chrome uv run pytest packages/docket/tests/test_web_browser.py` (or `msedge`).
