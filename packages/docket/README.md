# docket

The Docket app: the vault, the service (web app, MCP endpoint, scheduler) and the `docket` command. The repo's
[README](../../README.md) says what Docket is.

| Command | What it does |
|---|---|
| `docket init` | set up a vault: asks where, who you are, this machine's name, the sources, and inside a git repo whether the vault is a folder of it (the default) or a repo of its own (`--storage folder\|repo`); every answer is also a flag, and `--yes` takes the defaults; `--no-sources` starts with none, for `docket source new` to add; the summary names each check a flag overrode (`--allow-shared`, `--remote-is-private`, `--allow-dirty-parent`); `--no-redact-credentials` and `--redact-phone` set redaction, and the summary prints it |
| `docket sync` | a repo of its own: fetch and rebase onto its remote; a folder: says your own git moves it, and does nothing else |
| `docket vault status` | which vault it found and how (`--vault`, `DOCKET_VAULT`, this folder or above, `./vault`, the default vault), where it lives and on which branch, records per source, runs, run files not committed yet, the last commit |
| `docket vault check` | whether the vault is intact: every raw file parses, nothing written once was changed, no ignore rule hides a vault path, the vault's own attributes apply, no doc rev is claimed by two commits |
| `docket vault convert` | make a vault that is a repo of its own a folder of the repo around it, history included: a dry run first, then your yes; `--abort` undoes one that stopped before its commit |
| `docket staging list` | what waits for the gate, oldest first: `pending` waits for you or Claude, `ready` goes to raw at the next promotion |
| `docket staging show <ref>` | one staged record, its cuts, and the version in raw that was cut, when there is one |
| `docket staging classify <ref> work\|private` | work makes it ready, `--cut 120-340` (characters) or `--cut 12:30-14:05` (a time) cuts a stretch; private drops it |
| `docket staging promote` | writes every ready record to the raw log, cut and redacted, one run per source, and commits; a record on hold stays in staging |
| `docket ingest <source> <folder>` | imports payloads fetched earlier through the source's mapper: the folder's `hold.txt` first, then its JSON files; every record goes to staging, nothing on hold; `--hold <file>` adds more ids to hold, `--final` tells the mapper not to wait for more, `--mapper-errors` prints the mapper's own errors |
| `docket source new <name>` | writes a source's skill, mapper and tests into `.claude/skills/sources/<name>/` of the repo that holds the vault, from Docket's blank template, or `--example` for a reference source (Plaud), and adds the source to `vault.yaml` with `--sensitivity` and its `--tool`s; never over a folder that exists; `--folder <folder>` adds a folder source instead |
| `docket scan [source]` | reads a folder source's folder, read-only, and stages each new or changed document's text; every folder source turned on without a name; `--accept-gone` when most files the vault held are missing on purpose |
| `docket doc put <file or ->` | store a processed doc as its next rev, redacted first, saying on which lines; refused, with every problem and its line, when the frontmatter is wrong, an anchor doesn't resolve, the doc changed since the rev it carries, or it says something on the never-store keywords list (then only the lines are named) |
| `docket doc redact` | redact the docs already stored with the settings as they are now, as their next revs in one commit; once after upgrading, and again after turning a rule on or adding a pattern or a keyword; `--dry-run` first; a doc it can't redact is named and left, with exit 1 |
| `docket doc read <locator>` | a doc, or a record and the doc lines that point at it; `--view raw` or `both` for the raw, `--around` for minutes, paragraphs or messages either side, `--rev` for an earlier revision |
| `docket doc verify <doc>` | each line of a doc next to the raw its anchors point at; `--line` for chosen lines |
| `docket search <query>` | hits grouped by recording or thread, each with its locator and the group's summary; `--since`, `--until`, `--source`, `--person`, `--entity`, `-k`; it updates the index first unless `--no-update` |
| `docket index rebuild` | build this machine's index from the vault, from scratch |
| `docket index update` | index what changed since; it rebuilds when it has to |
| `docket index status` | whether the index is there and current, its languages, counts, what is new, and its problems |
| `docket import notes <folder>` | each markdown note in an existing folder becomes a legacy note that keeps its old path, so `doc read notes/x.md` or `notes/x.md@12:08` still finds it; the folder is only read; run it again to pick up changes; `--dry-run` first, `--as` for what old paths start with; says how many markers redaction added |
| `docket redaction` | which redaction rules run; `docket redaction set <credentials\|phone\|pattern name> on\|off` changes one and commits it |

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

Every record reaches the raw log through the gate. It reads `vault.yaml` on every call, so an edit applies at once.

| `vault.yaml` | What it does |
|---|---|
| `sources.<name>.sensitivity` | `work` goes through, `mixed` waits in staging to be classified, `private` is dropped |
| `sources.<name>.mapper` | the source's mapper, relative to the repo that holds the vault: `.claude/skills/sources/<name>/mapper.py` |
| `sources.<name>.tools`, `media_hosts` | the tools the capture hook posts for the source, and the hosts `media_put` may fetch from; the service reads them |
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
  `docket redaction set` changes a setting, and `docket doc redact` applies it to the docs already there.
- Turning a rule off leaves the markers already stored, but a record a source sends again then lands as a new version
  holding the value, records you classified wait in staging again, and the next `docket import notes` writes a new
  rev of each note the rule matched. Turning credentials off stores passwords, keys and tokens as written, in raw,
  docs and git history; turning it back on doesn't take them out of raw.
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

- `exclude` takes fnmatch patterns on the path inside the folder, any case. Notes you also import with `docket import
  notes` are best left out here.
- It is `work`, with no mapper or tools: `mixed` would hold every page on the Staging page, and to store nothing from
  it, set `enabled: false`. Only the vault's primary machine scans.
- In a git repo the folder is listed through git's ignore rules, and git's index is never refreshed; elsewhere it is
  walked, dot folders and nested repos left out. Links and junctions inside it are left out, and so are Office lock
  files, git-lfs pointers, cloud placeholders whose bytes aren't on disk, and a path holding a never-store keyword.

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
  version with text, and anchors on it still verify. A scan that would find more than half the files gone, and at
  least 20, stages nothing until `--accept-gone`.

## Live runs

A source syncs from any Claude Code session with the plugin: ask Claude to sync it, and it calls `run_open`, follows
the source's skill, and closes the run with `ingest` (DR-0240 to DR-0252). The plugin's capture hook hands Docket what
each of the source's tools returned, so Claude never retypes a payload.

| Part | What |
|---|---|
| a run | held in the service's memory from `run_open` to `ingest`, tied to the session that opened it; nothing of it on disk |
| a dropped run | one that comes up short, fails its close, sits 30 minutes with no capture, or meets a service restart: nothing is staged, the mark stays, and the next sync fetches the window again |
| the window | from the latest mark any machine reached, less 7 days (`lookback_days`), to now; `run_open(since=...)` starts it elsewhere |
| the cursor | `state/cursors-<machine>.yaml`, committed: the mark, the lookback, the ids to retry and the ids still waiting, ids and times only |
| a final run | `run_open(source, final=true)` for the ids that waited a week: fetched by id, no listing, the mark untouched |
| a folder | a live run reads a backfill folder only when `docket ingest` read it on this machine first |

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

- Downloads, once: 0.98 GB of model files for the CPU or 1.64 GB for CUDA, the reranker's 0.49 GB or 1.15 GB of them,
  and about 50 MB of packages for the CPU or 1.3 GB for CUDA (NVIDIA's CUDA 13 and cuDNN wheels, under NVIDIA's
  licences, in that environment only). `auto` takes CUDA when `nvidia-smi` lists a
  GPU; CUDA 13 needs a driver of R580 or later, and the install says when ONNX Runtime fell back to the CPU.
- The choice is this machine's, in your config's `models:` block, never in `vault.yaml`: each machine embeds with what
  it has. A block Docket can't read, or a config that isn't YAML, turns models off and says why.
- Nothing leaves the machine. The files live under your user cache and data folders, never in the vault or a repo,
  and after the install nothing touches the network.
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
| `docket search`, `docket eval` | only the query, on a worker they start for themselves (a second or two, about 8 s for `docket eval` on a GPU, where its reranker runs); `--no-models` skips it |

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
- `docket search` reranks on the CPU only; on a GPU machine the service reranks, and the command's line says so.
  `docket eval` reranks where the service does, with no time limit, and says how many questions went without it.

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
| `docket curate log` | every change, newest first, with the op id undo takes, and a change a stopped write left marked as not finished (the next write commits it); `--entity`, `-n`, `--json` |
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
the port check as the JSON Claude Code reads (DR-0163). Its capture hook runs `docket hook post-tool-use` after every
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

`docket open` signs your browser in to the service's own pages. In this version that is Search, Sync and Staging;
the navigation shows the pages still to come, with the phase each arrives in.

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

### The Sync page

`/sync` shows how syncs went without a log, what to do first (DR-0300 to DR-0306). It only reads: nothing on it opens,
drops or closes a run.

| Part | Shows |
|---|---|
| Needs | a source `run_open` would refuse, in its words; a source no machine synced in a day, or never; ids due a final run; each with the words to paste into Claude Code |
| Failures | dropped runs until a later whole run of their source, ids skipped for a reason other than `later` while a cursor file holds them, ids a close expired in the last week, cursor files that don't read; each with what to do |
| Runs | open runs, refreshed every 5 seconds while one is open, then recent ones: captures, what was staged, the mark, what is owed, or why the run dropped |
| Cursors | each machine's mark, lookback, retry and waiting ids with their ages, and whether another machine's are still handed over |

- Its URL holds `source` (one source), `run` (a run's card at the top) and `k` (how many recent runs, up to 500).
- Recent runs survive a restart: each ended run leaves one line in the vault's private folder, `sync/runs.jsonl`
  under `.git/docket/` (or the host's git dir for a folder vault). Ids and counts only, per machine, never committed;
  deleting it loses only the page's list.
- The page shows ids, counts, times and Docket's own messages, never a title or anything a source's tool returned.

### The Staging page

`/staging` is what waits at the gate, worked in the browser through the same calls as `docket staging` (DR-0380 to
DR-0384).

| Part | Shows and does |
|---|---|
| Ready | what goes at the next promotion, per source, with a Promote button per source and one for all |
| Waiting | each record by source, oldest first: kind, title, when, how long it waited, and why it waits; a record's text only once you open it |
| The open record | as promotion would write it, redacted by today's rules: check utterances or lines to cut, or type a range (`12:30-14:05`, `120-340`), Preview, then Work; or Private, which asks once more and drops it |
| Held | each source's held ids, and the commands that read and release one in your terminal, quoted, with `--from` and `--vault` filled |

- Releases stay at your console: the page names the command, and no page releases a hold (DR-0382).
- A record the CLI, Claude or another tab changed since you opened it isn't overwritten: the page says so and shows it
  as it stands.
- A record that already has cuts opens with them checked, and Work keeps them unless you uncheck them. Utterances its
  recording's release cuts show as gone, with no text.
- A typed time can carry milliseconds, `0:30.400`, as `--cut` takes it everywhere.
- Its URL holds `source`, `state`, `k`, the open record (`open`), what you checked (`u`, `line`), typed cuts (`cut`),
  `picked=1` when you cleared a record's cuts on purpose, and the drop's question (`confirm=private`).
- Keys: `j` and `k` through the records, `Enter` to open one, `Esc` to close it, `?`. No key writes.

The pages' browser test runs on CI. To run it here, against your own Chrome or Edge:
`DOCKET_TEST_BROWSER=chrome uv run pytest packages/docket/tests/test_web_browser.py` (or `msedge`).
