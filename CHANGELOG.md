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
