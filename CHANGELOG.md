# Changelog

What each release changes for the people who install and run Docket, in the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. A change users notice adds its line under
Unreleased in the same PR.

## Unreleased

### Added

- `docket init`: a wizard that sets up a vault anywhere, or nested in a working folder that is its own git repo, with
  an optional private remote.
- The vault's raw log: records written once per source and run, deduplicated, committed locally.
- `docket vault status` and `docket vault check`.
- The staging gate: every record reaches the raw log through it. Never-store lists and private sources drop records
  without a trace, mixed sources wait to be classified, and credentials, keys and phone numbers are redacted.
- `docket staging list`, `show`, `classify` (work or private, with cuts) and `promote`.
- Processed docs with anchors: `docket doc put` stores a summary only when every line points at raw that exists, and
  keeps each change as a revision; `docket doc read` and `docket doc verify` show a doc or a record next to its raw.
