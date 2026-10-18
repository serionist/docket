# Changelog

What each release changes for the people who install and run Docket, in the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. A change users notice adds its line under
Unreleased in the same PR.

## Unreleased

### Added

- `docket forget <rid>...`, `--never-store` or `--held` takes records out of the vault for good, on every clone: their
  lines, the doc lines built on them, their audio and its keys. Without `--now` it only counts what it would take;
  `--now` runs in your own terminal, on your OS prompt or a code typed back. The text stays in your repo's history:
  the forget writes a `git filter-repo` rewrite for a fresh clone and never runs it. Update Docket on every machine
  before your first forget, and make a new key export right after one.
- `docket reconcile spots <rid>` lists where a recording's transcript and Whisper's disagree on a name, a number or a
  word: at most 40 spots, names and numbers first, each with both lines, none in a cut stretch or naming someone a
  never-store list holds. `docket reconcile list` shows the recordings with spots to decide. Both only read, on the
  machine that ingests the recording's source.
- The Recording page plays a recording's stored audio from any line, and shows Whisper's transcript beside the
  recorder's, a row per stretch of time, a cut stretch cut in both. The audio is decrypted on this machine for each
  request and never cached; audio that isn't stored, waits at the gate, is held, cut since, kept out by a never-store
  value or has no key here plays nothing, and the page says why.
- The `transcribe` tool queues a recording for Whisper in the service, one job at a time, and `docket transcribe`
  queues there too when the service runs; the job's place and outcome show in `docket service status` and on the
  recording's page. On an NVIDIA GPU the service transcribes the newest stored recordings by itself after each index
  update. `docket eval speech <file>` scores each engine's transcripts against stretches you corrected by hand: word,
  character and name error rates.
- Speech to text on your own machine: `docket models install --speech` sets up Whisper large-v3 (3.09 GB, MIT;
  `--speech-model tiny` for a quick try), and `docket transcribe <rid>` turns a recording's stored audio into a
  transcript of its own, staged at the gate beside the recorder's. On an NVIDIA GPU it installs NVIDIA's cuBLAS under
  NVIDIA's terms and needs about 4.5 GB of the GPU's memory; on a CPU an hour of audio takes about an hour. Only audio
  stored and cut by everything its records need is transcribed, cut stretches come back as `[cut]`, and the transcript
  carries the recording's people, so a never-store name drops it as it drops the recorder's. A never-store word
  Whisper hears drops it too, and the stored audio stops playing. Off until you install it.
- The OS prompt: where your machine has one set up, a release, a change that loosens `vault.yaml`, `docket media
  confirm-cuts` and `docket media keys export` ask Windows Hello, Touch ID or your Mac's password, or polkit, after
  you press Enter at the console; the code typed back is asked only where there is none. `docket vault status` says
  which this machine asks, and why. On Linux, install Docket's polkit action once with the line it prints. A
  console-code marker, which only an admin or root can make, sends a machine back to the code; the README gives the
  lines.
- Decisions on the Briefs page: a project's, topic's or stream's decisions newest first, what waits for you
  beside any decision it contradicts, and the buttons that confirm or reject it. Your click is the only way a
  decision becomes confirmed or rejected; a page left open while something changed writes nothing and shows
  the decision as it stands.
- The decision log: a doc per decision on a project, topic or stream, proposed by the plugin's `decision` skill
  when you ask and confirmed or rejected only by you, with a click on the Briefs page. `docket decision list`
  and the `decisions` tool say where each stands. A proposed decision never reads as decided, a brief's
  Decisions cite confirmed decisions only, and a decision another replaced or contradicted keeps its text and
  links both ways. A decision confirmed or rejected is final: a change is a new decision that replaces it.
- Briefs: a doc per person, project, customer or topic, `brief:<entity id>`, with what changed, the last
  conversations and the decisions, every line cited. The plugin's `brief` skill writes one when you ask; `docket brief
  stale` and the `briefs` tool say which are stale: records about the entity its last rev hadn't seen, a line citing a
  line that changed, or its entity gone. A brief is never written for an entity a never-store list holds, and never
  names a listed person.
- The Briefs page, `/briefs` in the web app: the briefs by entity type with what makes each stale, and a brief
  beside the raw its lines cite, the lines it was built from one click away. A brief or a line a never-store list
  now holds shows only that it is hidden.
- Voyage AI's API for search by meaning and the reranker, for a machine with no GPU and no time for CPU embedding:
  `docket models install --api`, then `docket models key voyage` and, in each vault, `docket models allow voyage`,
  all in your own terminal. It sends a vault's text off the machine only when the vault allows it in `vault.yaml`,
  the machine chose it and its key is in the keychain; only chunks that pass the vault's settings in force go, never
  anything staged, never-store, from a source now private or gone, or that a redaction rule would change. The
  README's "Through Voyage AI's API" says what leaves the machine and when. A vault that opts in can't be opened by an
  older Docket.
- The people directory and the glossary rendered from the entities: name them in `vault.yaml` under `rendered`, each
  with its kind (`people` or `glossary`) and its path in the repo that holds the vault, and commit it. Docket writes
  them after each curate write and on `docket render`, and never commits them. A file edited by hand, or a hand-kept
  one already at the path, is left as it is and reported; a repo anyone can read gets no file; a name a never-store list
  holds is left out. Update every machine before you add the key: an older Docket refuses a `vault.yaml` that has it.
- Stored audio that gains a cut later, from another release line or a transcript cut by hand, isn't played until
  the run puts it again, cut; the new file takes the old one's place in one commit, and the old file's key is
  deleted on this machine once the new one is committed. A stored recording put on hold isn't played either, nor
  one a record of which was cut by characters: no cut of the audio can match it, and the Recording page and
  `docket media status` say so.
- Cut recordings get their audio: a recording released with cuts, or whose transcript you cut by time, has the same
  stretches made silent before its audio is encrypted, so every transcript time still points at the right moment,
  and is stored as WebM Opus. Audio refused before is named in the run's `media` line and put again, cut. A put
  is refused when the audio and its transcript may not share a timeline. Recordings cut by hand before this
  release wait for `docket media confirm-cuts <rid>` in your own terminal. Cutting adds PyAV (FFmpeg) as a
  dependency.
- Media: `docket media put` and the `media_put` tool keep a record's audio or file, encrypted with a key of its own
  as it is read, beside its record until the record is in raw, then in `media/` in LFS. A path comes from a folder you
  name or Docket's drop folder, a URL only over https from a host you name; adding either asks at your console. A
  held recording, one cut or released with cuts, or one the gate dropped for a never-store value gets no media yet.
  Keys stay on this machine, in the system keychain or, by your choice, a private file; the Recording page says when
  the audio waits, isn't downloaded, or has no key here.
- Media keys on a second machine, and their backup: `docket media keys export <file>` writes every key of a clone
  into one file sealed with a passphrase it draws and shows once, and `docket media keys import <file>` adds them on
  another machine; both only in your own terminal. The newest export is the backup, and `docket media status`,
  `docket vault status` and the session start count the keys no export holds yet. A source with a media host or
  folder gets a media step in each run, and the Plaud skill says how to turn its audio on.
- The Recording page, `/recording` in the web app: a recording's summary beside its transcript, each anchor a link
  that marks the lines it points at, with the recording and the line in the address. Search's transcript hits open
  there. No audio is stored yet, and the page says so.
- The Settings page turns a redaction rule on, and makes a source mixed or private or turns it off, each with a
  button; making a source private asks once more first, naming what waits from it. Anything that lets more in stays
  a command or a line to change by hand.
- Never-store values added from the Settings page: type a name, a chat or a phrase, and Docket asks once more first,
  naming the records it drops from staging and how much of what is already stored holds it. The value is committed in
  `vault.yaml` and nowhere else: not the address bar, the log or the commit message.
- The Settings page, `/settings` in the web app: each source's settings, the never-store lists (their values only
  when you ask), redaction and this machine's models in one place, with every change named as the command to run or
  the `vault.yaml` line to change. No page ever makes a change that lets more in: turning a rule off, taking a
  never-store value off, or making a source looser stays at your console or in your editor.
- Tier 1 of curation: on the vault's primary machine, a round first applies by itself an address or a chat id
  that mail or chat writes beside a known full name, a name a system shows beside an id an entity lists, and a
  spelling two engines agree on, each logged and undoable; anything less certain becomes a card. A shared sender
  such as a notification address gives nothing, and what it gave while it looked like one person's is taken back.
  An author written `Name <id>` now counts as a mention of the entity listing the id; the index rebuilds once.
  `docket curate show` and its `--json` hide what a never-store list gains after a round, in what tier 1 applied and
  on the cards, as the page does.
- The Curate page, `/curate` in the web app: start a round with a click, open each card beside the list with the
  calls and mails it comes from, and accept, edit or reject it; undo a change or lift a rejection from the log
  below. A decision on an entity file that changed since the page showed it writes nothing.
- `/docket:curate` in the plugin: Claude runs a curate round with you, the cards in one table with its call and
  why, and decides each card only on your answer; it seeds entities from a people directory or glossary you point
  at on your yes. The session-start status tells Claude when a round is due, a week after the last.
- Curate rounds: `docket curate round` mines at most 15 cards from what the index holds (names your labels,
  notes and mail spell, joined with how transcripts misheard them), ranked by what search misses; `show`,
  `accept`, `reject`, `lift` and `status` work through them, every decision in the curate log, and a round
  measures your own question set before and after when `curate.questions` names it.
- Folder sources, your own documents: `docket source new documents --folder ~/Work` names a folder in `vault.yaml`,
  and `docket scan` reads its docx, xlsx, pptx, pdf, md and txt files, read-only, through the same gate as every
  source: a pdf by page, a deck by slide, a workbook by sheet with row n as paragraph n. A search hit names the file's
  path and its page or rows. A scan reads only what changed; a deleted, moved or excluded file keeps its earlier
  versions and stops being current. Only the vault's primary machine scans. Update every machine before adding one:
  an older Docket refuses a `vault.yaml` with `folder` or `exclude`.
- `docket curate add`, `docket curate log` and `docket curate undo`: entity changes from a file (seed your people
  and terms from what you keep), each logged in `entities/_log/` and committed with the file it changed, and each
  one undoable. Docket never rewrites an entity file that holds a comment or reads with a problem.
  `docket curate log`, its `--json` and what a write prints hide a value a never-store list gains after it was
  logged, as `docket curate show` does; op ids stay, so undo still takes them.
- Models, optional: `docket models install` sets up a multilingual embedder (multilingual-e5-small, on the CPU or an
  NVIDIA GPU) in a worker of its own, and search then also finds what a question means, not only its words. The
  service keeps the worker loaded and embeds what each sync adds; `docket models status` and `docket models remove`
  manage it, and `--no-models` on `docket search`, `docket eval` and `docket index update` keeps keyword search alone.
  Nothing leaves the machine.
- A reranker in the models' worker: bge-reranker-v2-m3 on an NVIDIA GPU, a small multilingual cross-encoder on a CPU
  fast enough for it, which orders search's top hits by how well each answers the question, hits holding every word
  first. It has 0.75 s a search, and search says when it went without. `docket models install` fetches and times it;
  `--no-reranker` leaves it out.
- `docket eval <file>` measures search with your own questions: each with the locators a good answer cites, its
  kind and its language. It prints recall at 10 and MRR per kind and per language, and the questions it missed, or
  JSON with `--json`, and writes nothing. The question file's format is described in the README.
- Live syncs through the service: ask Claude to sync a source, and it opens a run with the MCP tool `run_open`,
  follows the source's skill and closes the run with `ingest`. The plugin's capture hook hands Docket what the
  source's tools returned, so nothing is retyped, and a run that comes up short is dropped and fetched again. Update
  Docket before the plugin: an older `docket` doesn't know the hook's command.
- Each machine's sync cursor, committed in `state/cursors-<machine>.yaml`: where its syncs reached, and the ids still
  owed. A run starts from the furthest point any machine reached.
- Releasing held recordings: `docket hold list`, `docket hold show` and `docket hold release`. A held recording goes
  into staging only with the cuts you confirm, each one by typing back the code it shows at your own console, and
  leaves the hold list once its release is committed to `state/releases/<source>.jsonl`. The gate cuts a released
  recording on every machine; its vendor notes stay out unless you keep them. Update Docket on every machine before
  your first release: an older one doesn't read the release log.
- `/docket:cuts` in the plugin: Claude turns your words for each left-out stretch into time ranges on the transcript
  and hands you the release to run in your own terminal.
- The web app's first page, Search: the search Claude uses, with facets by source, person, entity and time, results
  grouped by recording or thread, and a panel that puts a hit's summary next to its raw and checks each line
  against it. Opened with `docket open`; light and dark, by keyboard, and offline.
- `docket open [PATH]` lands on the page you asked for.
- The Sync page: what to ask Claude to sync, with the words to paste; failed runs and skipped ids, each with what to do;
  the runs open and ended; each machine's cursor. Recent runs survive a restart, as ids and counts in the vault's
  private folder.
- The Staging page: what waits at the gate by source and age, with why it waits; open a record to read it as it will
  be written, check utterances or lines to cut, preview, then classify it work or drop it as private; promote what is
  ready. Held recordings are listed with the commands that release them in your own terminal.
- A time in `--cut` can carry milliseconds, such as `0:30.400`, so a cut on an utterance's own times is exact.
- The Claude Code plugin's first version: it registers the service as the MCP server `docket`, tells each session at
  its start whether Docket runs and what waits in staging, and brings `/docket:recall`, which answers from the raw with
  locators, and `/docket:setup`, which asks which sources to set up, creates the vault and writes each source's folder.
- `docket source new`: a source's skill, mapper and tests in your project, from Docket's blank template or, with
  `--example`, the Plaud reference, and the source added to `vault.yaml`.
- `docket init --no-sources`; init's summary names each check a flag overrode; `docket vault status` says which vault
  it found and how.
- The service: `docket serve`, and `docket service install` to start it at your logon on Windows, macOS and Linux.
  It serves Claude the MCP tools `search`, `read`, `verify` and `doc_put` on 127.0.0.1, behind a token from your
  keychain, and the same four under a REST API.
- `docket service stop`, `uninstall`, `status` and `token`, and `docket open`, which opens the web app through a link
  good once and for a minute.
- `docket init`: a wizard that sets up a vault anywhere, or nested in a working folder that is its own git repo, with
  an optional private remote.
- The vault's raw log: records written once per source and run, deduplicated, committed locally.
- `docket vault status` and `docket vault check`.
- The staging gate: every record reaches the raw log through it. Never-store lists and private sources drop records
  without a trace, mixed sources wait to be classified, and credentials and keys are redacted, phone numbers when you
  turn them on.
- `docket staging list`, `show`, `classify` (work or private, with cuts) and `promote`.
- Processed docs with anchors: `docket doc put` stores a summary only when every line points at raw that exists, and
  keeps each change as a revision; `docket doc read` and `docket doc verify` show a doc or a record next to its raw.
- `docket search`: full-text search over everything kept, in English and your other languages, with names, aliases,
  time words and boosts; hits come grouped by recording or thread, with their locators and summaries.
- `docket index rebuild | update | status`: the search index on each machine, built from the vault.
- Entity files under `entities/`: names, aliases and ids that search matches into everything kept.
- `docket import notes`: an existing notes folder becomes legacy notes that keep their old paths, so references like
  `notes/calls/x.md@12:08` still resolve, with the time placed in the recording a note covered, or in its transcript
  when the source keeps that as a record of its own, as Plaud does. `r:<recording>@12:08` reads that transcript too.
  A note whose head names no recording matches one of those by its title and day as well: run the import again, and
  each note that gains its recording is written as a new rev, counted under `covers`. Reading the transcript, as the
  Search panel does for a hit, names the legacy notes and doc lines on its recording, and reading the recording, or an
  old path with a time, lists the lines on both as well. A doc that puts a time on the recording is refused with the
  transcript's anchor to write instead.
- `docket ingest <source> <folder>`: imports payloads fetched earlier through the source's mapper, which lives in your
  project under `.claude/skills/sources/<source>/`; Docket runs it in its own process and checks every record.
- Hold lists: `state/holds/<source>.txt` keeps records out of staging and raw until a hold is released, on every
  route and in the gate.
- Time cuts on transcripts: each utterance a cut touches goes whole, and `[cut]` takes its place; a cut record keeps
  no payload.
- Plaud as the reference source, under `examples/sources/plaud`: a skill and a mapper to copy.
- A vault can be a folder of the repo you work in, the default inside a git repo: Docket commits only the vault's
  paths there, skips your hooks and never touches your remote. `docket init --storage folder|repo` chooses, and
  refuses a folder in a repo anyone can read. Docket opens a folder vault only when the repo around it tracks its
  `vault.yaml`, so a repo that lost its `.git` never has its vault committed into the repo above it.
- `docket sync`: a repo of its own fetches and rebases; a folder leaves it to your own git.
- `docket vault convert`: a vault that is a repo of its own becomes a folder of the repo around it, history
  included, after a dry run and your yes.
- `docket redaction` shows which redaction rules run, and `docket redaction set <rule> on|off` changes one;
  `docket init --no-redact-credentials` and `--redact-phone` choose at setup, and `/docket:setup` asks.
- `docket doc redact`: the docs already stored, redacted with the settings as they are now. Run it once on every
  vault after every machine runs this version, and again after turning a rule on or adding a pattern or a keyword.

### Changed

- A cut you make on a later version of a recording now reaches its Whisper transcript in the same promotion: the
  gate writes the transcript's next version with the stretch replaced by `[cut]`, so search, `read` and the
  Recording page stop showing it. It needs no Whisper or audio on the machine. A cut made before this version is
  followed at the first promotion after you update, on a machine that runs the recording's source.
- `docket media keys export` asks for your yes before it writes the file, as every key leaves the clone.
- On Windows, a machine with a TPM or a fingerprint or face reader where Windows Hello isn't set up for you now
  refuses a release or a loosening: set Hello up, or make the console-code marker. Update every machine before you
  rely on the prompt.
- A line of a brief, a decision or a correction can cite the doc line it was built from, `[d:<doc id>@<rev>#<n>]`,
  as `verify` gives it, and is flagged when that line changes: `docket doc read` says so beside the line, `docket doc
  stale` lists every flagged line, and the session start counts them. Update every machine before docs cite lines,
  never run `docket doc redact` on an older Docket, and run `docket doc redact --dry-run` once after updating to see
  any doc it would now leave: redaction no longer writes inside a line's anchors, and leaves whole a doc whose anchors
  hold something this Docket doesn't read. The index rebuilds once.
- Each write makes fewer git calls: it reads what HEAD names from the files in `.git` and asks git each question
  once. A doc put makes 7 calls instead of 12, a release 22 instead of 39, and an import of 200 notes 13 instead of
  217. Commits, their messages and what they hold are as before. A write refused because a file has a conflict now
  leaves the conflict as git left it.
- A search through the service on a vault that is a folder of your repo answers as fast as one in the command: it
  asked git where your repo is four times for each search, up to a second on Windows, and now asks once while the
  service runs. `docket search` no longer updates the index itself when the service answers, since the service's
  search does; when the service is still building the index, it says so and searches once the build is done.
- A redaction rule turned on, or a pattern added, no longer cuts another rule's match short: every rule reads the
  same text, so a pattern that holds a phone number or a password, such as a dial-in with its PIN or a database
  address, is redacted whole, and matches that overlap become one marker. A record whose redacted form changes is
  stored once more when its source sends it again. What a cut-short match already stored stays: for each of your own
  patterns, search the vault for the marker of the rule that cut it where the rest would follow, such as
  `[redacted:password]@`, `[redacted:phone] PIN` or `[redacted:tck]-`, and edit a doc that holds one; raw keeps what
  it stored.
- A doc that says something on the never-store keywords list only inside what a redaction rule hides, such as an
  address a pattern redacts, a phone number or a password, is refused now, as a record holding it is dropped. A doc
  redacted before still goes in again. `docket doc redact` names such a stored doc and leaves it for you to edit,
  where it redacted it before.
- A change that lets more into the vault asks you at your own console: `docket redaction set <rule> off` and `docket
  source new` show the change and wait for a code typed back, and are refused in Claude Code's shell, from a pipe or a
  redirect. A script that ran them now gets that refusal. The setup skill hands you each `docket source new` to run in
  your terminal.
- An edit of `vault.yaml` by hand applies at once only where it keeps more out; the rest applies once you commit it, and
  `docket vault status` says: vault.yaml has an edit that isn't in force until you commit it. A vault holding such an
  edit when you upgrade stops applying its looser part until then.
- An edit of `vault.yaml` not committed yet is committed on its own, as `Config: vault.yaml as edited`, before
  `docket redaction set`, `docket source new` or the Settings page commits a change to it, so each commit says what
  it did. An edit that lets more in than the committed file (a rule turned off, a source made looser, a never-store
  value taken off) is never committed this way unasked: a page refuses the change and writes nothing, and a command
  asks you about the edit at your own console, with a code typed back, or you commit or undo it yourself.
- A `vault.yaml` that doesn't read as YAML is named by its line and column and what was wrong there, no longer with
  the line's text, so a never-store value on it never shows in a page, a terminal or a log.
- Verifying a doc and writing one no longer load the raw log of each source it covers when the search index is
  current for that source: their anchors are checked against the records' own lines where the index says they are.
  On 50 MB of call recordings in one source, `verify` went from about 580 ms to 5 ms and a `doc_put` from about
  1.1 s to 0.3 s, the rest of it git. After a sync or a pull the index hasn't seen yet, they load the source as
  before.
- `docket import notes` checks every note's records through one read of the raw log: at their places when the index
  is current, else each source loaded once for the whole import, where it loaded a covered source once per note. 200
  notes over 50 MB of call recordings went from about 155 s to 9 s.
- `docket search` asks the running service when it serves the vault with the same model, so on a GPU machine its hits
  are reranked as Claude's are, with no worker to start. With no service for the vault, `--no-update` or
  `--no-models`, it searches by itself as before; the output reads the same either way.
- Reading a record no longer loads its source's whole raw log when the search index is current for that source: it
  reads the record's own lines where the index says they are. On 50 MB of call recordings in one source, a read went
  from about 750 ms to under 10 ms, and so did the check whether a `plaud:<path>` is a record. The index rebuilds
  once, at the first search or service start after the update; until then, and after a sync or a pull the index
  hasn't seen yet, a read loads the source whole as before.
- Reading an old note path, a doc or a record no longer parses every doc in the vault when the search index is current,
  and loads a source's raw once: `docket doc read notes/x.md@12:08` on a vault of 200 legacy notes went from 0.8 to
  0.5 s, and from 1.3 to 0.5 s with 800 docs. The index rebuilds once, at the first search or service start after
  the update; until then, and after a change to a doc the index hasn't seen yet, a read parses every doc once.
- Search keeps every word of a query, however common, so a customer named in most of your notes still narrows it; a
  doc, mail or recording whose title leads with most of your words comes first. `too common to search for` is gone,
  and `in no chunk: <word>` names a word nothing in the vault holds. Search with key words, not the sentence.
- Search finds a compound however it is written: `kickoff`, `kick-off` and `kick off` find each other, and so do
  `e-mail`, `email` and Hungarian's `e-mailben`. A hit holding a word only split in two ("edit or" for `editor`)
  comes after the hits holding as many of your words as written. The index rebuilds once, at the first search or
  service start after the update.
- A customer or person named in most of your notes is found in its Hungarian inflected forms too (`Acménél`,
  `Acmét`), and `acménél` in a query reads as that entity. A foreign name is found as Hungarian says it by its sound
  (`Sammel`, `Ryannel`, `Nimishsel`). A word that only stems like a name no longer counts as
  the name, in your notes or in a query: not `már` for Mark, and not the English `same` for Sam or `marked` and
  `market` for Mark. A name's inflected forms now count when a common word stems like it: `Markkal` however often
  `már` comes up, and `Bélával` however often `bele` does, as long as `Markkal` or `Bélával` is in at most twice as
  many of your notes as the name written bare. Short names' inflected forms now count too (`Évával`, `Évának`,
  `Evével`, `Edivel`, `Beával`), and inflected forms written without accents still do (`Zoltannak`, `Istvant`).
  An inflected form whose stem is too short or too common to tell it from a word (`annál` for Ann, `évet` for Eve,
  `edit` for Edi, `Evan` for Éva) doesn't count when it is in more than twice as many of your notes as the name; a
  colleague you name more than half as often as such a word still collides with it. Other inflected forms that are
  also words (`belát` for Béla, `Anna` for Ann) count as before until the word is in ten times as many notes. A name
  written only in a Hungarian possessive (`Markéhoz`) no longer counts on its own. The same one rebuild covers it.
- A source's `SKILL.md` now holds only the source's own calls; the run's steps come from `run_open`. After upgrading,
  copy the reference's `SKILL.md` and `mapper.py` over your project's copy: a live run refuses a `SKILL.md` without
  the new marker line. Plaud's reference pages transcripts by 100 utterances, and finalizes only the recordings a run
  fetched.
- Redaction is yours to set, rule by rule in `vault.yaml`: credentials (passwords, keys and tokens) on, phone numbers
  off, each of your own patterns on unless its `enabled` is false. Phone numbers are no longer redacted unless you
  turn them on: `docket redaction set phone on`. Names are never redacted; the never-store lists keep them out.
- Turning a rule off leaves the markers already stored, but a record a source sends again lands as a new version
  holding the value, records you classified wait in staging again, and the next `docket import notes` writes a new
  rev of each note the rule matched; every vault does this for phone numbers once, after this upgrade.
- Turning credentials off stores passwords, keys and tokens as written, in raw, docs and git history; turning it back
  on doesn't remove them from raw.
- `doc_put` and `docket doc put` redact a doc before storing it, line by line so no anchor moves, and say on which
  lines; a doc that says something on the never-store keywords list is refused, naming only the lines. The notes
  import redacts the same way and says how much.
- A redaction marker before a line's anchors is text, not a bad anchor.
- Update every machine first: an older Docket keeps redacting phone numbers while `vault.yaml` is in its old form,
  can't load it once Docket writes the new keys, writes docs unredacted, and refuses to put back a doc holding a
  marker.
- Docket's commits in a vault that is a repo of its own skip the repo's hooks, as a folder vault's do, and so do
  `docket sync`'s fetch and rebase: a write takes about 0.4 s less on Windows. Your own commits and every push still
  run them.
- Folder vaults stop running git-lfs's post-commit hook on Docket's commits. They were meant to skip the host's
  hooks, but git-lfs wrote its hooks into the empty folder Docket used for that.
- A promotion writes each source's records in the order they were staged. Before, two versions of one record staged
  before the same promotion could land in either order, and the older one then counted as current.
- A promotion past 16 MiB of a source's records writes them in several run files (`.2`, `.3` after the minute's
  name), in one commit, so no file reaches a host's file size limit. An older Docket reads them as any runs.
- A models' worker that fails its check or is stopped has its last lines in the error and in
  `docket models status --check` every time. Before, they could be missing when the error came before they were read.
- On Windows, the service starts git without a console of its own: each git call it makes takes about 30 ms
  instead of about 160 ms, so a doc put, a promote or a curate decision through the service is several times faster.
- On Windows, a command run from a shell with a console but no window (as Claude Code's is) starts git without
  the no-window flag again: each git call takes about 110 ms there instead of about 180 ms.
