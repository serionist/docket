# Changelog

What each release changes for the people who install and run Docket, in the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. A change users notice adds its line under
Unreleased in the same PR.

## Unreleased

### Added

- Models, optional: `docket models install` sets up a multilingual embedder (multilingual-e5-small, on the CPU or an
  NVIDIA GPU) in a worker of its own, and search then also finds what a question means, not only its words. The
  service keeps the worker loaded and embeds what each sync adds; `docket models status` and `docket models remove`
  manage it, and `--no-models` on `docket search`, `docket eval` and `docket index update` keeps keyword search alone.
  Nothing leaves the machine.
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
