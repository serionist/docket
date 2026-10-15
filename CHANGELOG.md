# Changelog

What each release changes for the people who install and run Docket, in the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. A change users notice adds its line under
Unreleased in the same PR.

## Unreleased

### Added

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
  without a trace, mixed sources wait to be classified, and credentials, keys and phone numbers are redacted.
- `docket staging list`, `show`, `classify` (work or private, with cuts) and `promote`.
- Processed docs with anchors: `docket doc put` stores a summary only when every line points at raw that exists, and
  keeps each change as a revision; `docket doc read` and `docket doc verify` show a doc or a record next to its raw.
- `docket search`: full-text search over everything kept, in English and your other languages, with names, aliases,
  time words and boosts; hits come grouped by recording or thread, with their locators and summaries.
- `docket index rebuild | update | status`: the search index on each machine, built from the vault.
- Entity files under `entities/`: names, aliases and ids that search matches into everything kept.
- `docket import notes`: an existing notes folder becomes legacy notes that keep their old paths, so references like
  `notes/calls/x.md@12:08` still resolve, with the time placed in the recording a note covered.
- `docket ingest <source> <folder>`: imports payloads fetched earlier through the source's mapper, which lives in your
  project under `.claude/skills/sources/<source>/`; Docket runs it in its own process and checks every record.
- Hold lists: `state/holds/<source>.txt` keeps records out of staging and raw until a hold is released, on every
  route and in the gate.
- Time cuts on transcripts: each utterance a cut touches goes whole, and `[cut]` takes its place; a cut record keeps
  no payload.
- Plaud as the reference source, under `examples/sources/plaud`: a skill and a mapper to copy.
- A vault can be a folder of the repo you work in, the default inside a git repo: Docket commits only the vault's
  paths there, skips your hooks and never touches your remote. `docket init --storage folder|repo` chooses, and
  refuses a folder in a repo anyone can read.
- `docket sync`: a repo of its own fetches and rebases; a folder leaves it to your own git.
- `docket vault convert`: a vault that is a repo of its own becomes a folder of the repo around it, history
  included, after a dry run and your yes.
