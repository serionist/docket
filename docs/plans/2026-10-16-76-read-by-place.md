---
title: Read a record by its place in the index, not by loading its raw log
issue: "#76"
status: draft
approved_by:
approved_at:
---

# Read a record by its place in the index, not by loading its raw log

A read of a record seeks to its line when the index is current for the record's source, and loads the source whole
only when it isn't. On 50 MB of invented Plaud raw that takes a read from about 780 ms to under 10 ms. The raw log's
format doesn't change; the index rebuilds once.

## Goal

`read` loads a source's whole raw log to find one line, about 15 ms per MB, so a year of calls makes every read of a
recording, and the rid check of a `plaud:<old path>` locator, take seconds.

Measured on an invented vault: one source of 50.1 MB (354 recordings, each a recording, a transcript of 600 segments
and a summary), 177 run files, one legacy note, the index current; best of five, in process, on the maintainer's
desktop (Windows 11):

| Read | Today | By place |
|---|---|---|
| `read r:<transcript>` | 767 ms | about 8 ms |
| `read r:<recording>` (lists its transcript's lines, DR-0440) | 808 ms | about 8 ms |
| `read plaud:<old path>` | 782 ms | about 6 ms |
| the rid check alone (`RawLookup.versions` today) | 771 ms | about 5 ms |

"By place" is summed from a sketch of its parts on the same vault, not from built code:

| Part | Time |
|---|---|
| open the index | 1.1 ms |
| the source's run files against the index (`os.scandir`, 177 files) | 3.7 ms; 10 ms at 5,000 files, 54 ms at 20,000 |
| a rid's places | 0.2 ms |
| the transcript's line, 141 KB, read and validated | 1.2 ms |
| the recording's line | 0.05 ms |
| what is under a recording: the query and its transcript's line | 1.9 ms |

Today's cost grows with the source's megabytes; by place it grows with the source's run files and the size of the
records read. The doc questions are #46's and cost what they cost there, a hash of every doc file.

- In scope: each version's byte offset in the index; `read` taking a record, its versions, what is under it, its
  recordings, its neighbours and the rid checks from their places when the source is current; the fallback; both
  routes through every read test; the 50 MB fixture and its timed test.
- Out of scope: `verify` and `doc_put` (Decided below); the raw log's other readers (the gate's `rawlog.read`,
  ingest's `rids()`); `docket index status`, which already counts new and changed runs.

## Design

| Module | Change |
|---|---|
| `index/schema.py` | `record_versions` gains `offset`; indexes on `records (container)` and `records (source, path)`; schema 7 |
| `index/build.py` | `read_run` reads bytes and gives each line its offset; a line that isn't UTF-8 and a record outside its source's folder are problems |
| `index/lookup.py` | `ReadIndex`: the index opened once per read in one read transaction, asked about docs (DR-0321, as now) and about a source's raw |
| `vault/spans.py` | `RawLookup` takes an optional `Places` (a protocol, so `vault` imports nothing from `index`); per source it reads lines at their places, else loads as today; `has(rid)` |
| `recall.py` | `_Call` hands its index to `RawLookup`; the rid checks call `has` |

### The place

`record_versions (rid, v, path, line, offset)`: where the version's line starts in its run file, in bytes (DR-0445).

- The build reads each run file in binary and splits at `\n`: the same lines text mode with `newline="\n"` gives,
  since UTF-8 never puts a `\n` byte inside a character. A read opens the file in binary, seeks, reads to the next `\n`.
- CRLF: Docket writes run files with `\n`, and the vault's `.gitattributes` keeps them LF on checkout (DR-0142). A
  file with CRLF anyway (an override, a hand copy) reads the same on both routes: the line runs to its `\n`, `\r`
  included, and JSON skips it.
- `line` stays, for problems and `locate`. Schema 7 makes every index stale, so the next search, service start or
  `docket index update` rebuilds it. #68's plan takes 7 in its fifth PR; whichever merges second takes 8.

### When a place is trusted

Per source, at the call's first question about it, inside the call's one read transaction (DR-0446):

| Check | Fails when |
|---|---|
| the index opens as this Docket writes it | no index, another schema or languages, a file that isn't one, an update holding it past 50 ms |
| the run files under `raw/<source>/` are those `run_files` lists, each with its size and modification time | a run not indexed yet (a sync, a pull), a run gone, a file written again (`docket forget`, DR-0014) |
| no problem in those files, and no record of the source in another source's folder | a line that doesn't load, isn't UTF-8 or holds another source's record (only a hand edit makes these) |
| each line read by place holds the rid and version the index holds | the file changed under the read |

- Any failure loads that source whole for the rest of the call, as today; other sources keep their places. A source the
  load route fails on takes that route, so it fails the same way.
- Read never writes, updates or waits for the index (DR-0321); search, the service and `docket index update` keep it
  current. Docs and raw are checked apart: a new run doesn't send doc questions to the parse route, and a changed doc
  doesn't send raw questions to the load route.
- The listing is `os.scandir` with `DirEntry.stat()`, filtered as `parse_run_file` filters for the load and the update.
  On Windows the size and time come with the listing: seven times faster than `Path.stat` on each file.
- No hash: hashing a run file reads every byte, the cost this removes. Size and modification time are what update
  already trusts to follow runs (DR-0115). Run files are written once (DR-0014): cuts are made in staging, and a release
  writes a new run file through the gate.

### What a read asks about raw

The index narrows, today's code decides on the lines read, as #46 does for docs (DR-0447):

| Read asks | From the index | Then |
|---|---|---|
| is this a rid in the raw log (`<source>:<path>` locators, a transcript's recordings) | a `record_versions` row in the source's folder | nothing read |
| a record's versions: how many, and the latest | its `record_versions` rows there, in run order then line | each line read at its offset |
| the transcripts under a record (DR-0370, DR-0440) | `records` rows of the source naming it as thread or container, of kind transcript, other than itself | each latest version read, kept by `over()` and `is_transcript()` |
| a message's neighbours (`around` on a record without segments) | `records` rows of the source with its thread, else its container, by `(ts, rid)` to the millisecond: its own millisecond and n rows either side, each end widened to its whole millisecond | read, sorted by `(ts, rid)` as today, cut to the window |

- Nothing in a read still needs the whole source.
- One `(rid, v)` in two machines' run files is two rows, as it is two lines today, so `versions` counts the same.
- DR-0370, DR-0391 and DR-0440 bind what is under a record to the raw log of its own source, never the index. DR-0447
  refines that: the records still come from the raw log, read where a current index says they are, and what the bind
  guards against, a transcript stored since the last update, sends the source to the load route.

### Decided

Under the autonomy grant, each proposed for the maintainer's review.

| Decided | Not taken | Entry |
|---|---|---|
| a byte offset per version in `record_versions` | the line alone: a read would scan the file up to it; a table of its own; offset and length: a line ends at its `\n` | DR-0445 |
| per source: run files as indexed by name, size and time, no problem among them, each line holding its rid and version; else the source loads whole for the call | a hash per run file: reads every byte; one check for all of `raw/`: a chat run would send Plaud reads to the load route; places for indexed runs plus a load of the new ones: two routes merged in run order on every question, for a state the service ends in seconds | DR-0446 |
| the build reports a line that isn't UTF-8 and a record outside its source's folder as problems | leave them: the place route would answer where the load route fails | DR-0446 |
| read never updates the index for new runs | update first: a read would write and wait behind a rebuild | DR-0446 |
| the index narrows the rid checks, versions, transcripts, recordings and neighbours; today's code decides on lines read | answers from the index's own columns (`records`, `segments`): a second reading of a record; superseding DR-0370, DR-0391 and DR-0440: what they decide stands | DR-0447 |
| `verify` and `doc_put` keep loading the source | the same `Places` now: a line each, but neither is in the issue, and `doc_put` checks a write against the log itself; a follow-up if wanted | - |

## Doors and data

None of AGENTS.md's one-way doors: the raw log's files, names and lines stay as they are (DR-0014, DR-0020), nothing in
the vault is written, and the `read` tool's parameters and results don't change (DR-0150). The index is a per-machine
cache (DR-0115); schema 7 rebuilds it once on each machine.

| Where | After the upgrade |
|---|---|
| `docket search`, the service's start, `docket index update` | finds schema 6 and rebuilds |
| `docket doc read`, the MCP `read` tool, the Search page's panel | load the source whole until that rebuild, as today |

## Steps

1. The place: `read_run` in binary with offsets, `record_versions.offset`, the two `records` indexes, the two new
   problems, schema 7. Tests: `test_index_build.py`, `test_index_update.py`.
2. `ReadIndex` in `lookup.py`: one connection per read, the doc check as now, the raw check per source, the place
   queries. Tests: `test_index_lookup.py`.
3. `RawLookup` with `Places` and `has`; `recall._Call` passes its index. Tests: `test_spans.py`, `test_recall.py`,
   `test_recall_legacy.py`.
4. The fixture and the timed test; the measured numbers into this plan. Test: `test_recall_perf.py`.
5. The docs below, then `finish-work`.

## Tests

On temp vaults with temp config and state dirs, the failing keyring and `DOCKET_VAULT` unset, as `conftest.py` sets
them; invented names only.

- The issue's same-answer test: `read_route`'s `index` route also fails the test when `RawLookup` loads a source
  whole while `recall.read` runs, so every read test in `test_recall.py` and `test_recall_legacy.py` answers raw by
  place, and both routes give the same result. `verify` in those modules is left alone.
- `test_index_build.py`: every `record_versions` offset reads back a line with its rid and v, in the fixture and in a
  run file with CRLF lines; a line that isn't UTF-8 and a record in another source's folder are problems, and the rest
  is indexed.
- `test_index_update.py`: runs added, then an update, equal a rebuild (`LOGICAL_TABLES` holds `record_versions`, so
  offsets are compared); a schema 6 index rebuilds.
- `test_index_lookup.py`: a source is current after an update; not after a new run in it, while another source stays
  current; not after a run touched, grown or removed, or a problem line in it; the check never writes the index file.
- `test_spans.py`: for every rid of the index fixture and the Plaud records, `versions`, `has`, `transcripts`,
  `timed`, `recordings` and `neighbours` give the same by place as loaded, with one `(rid, v)` in two runs, a record in
  two runs and two messages in one millisecond; a line rewritten in place with its size and time put back
  (`os.utime`), and a run deleted after the check, send the source to the load route with the same answer.
- `test_recall_perf.py`, marked `perf` (DR-0430): `big_raw()` in `docket_testkit.py` writes invented Plaud raw, 50 MB
  when timed and 5 MB under coverage, where nothing is timed; `read r:<transcript>`, `read r:<recording>`, `read
  plaud:<old path>` and the rid check each take under 100 ms by place, and the load route's times print beside them.
  The 50 MB vault takes about 14 s to write and index here.
- Coverage stays at the `docket` floor of 100%.

## Docs

- Entries in `packages/docket/index.d/`, added with this plan: DR-0445 (the place), DR-0446 (when a place is trusted),
  DR-0447 (the index narrows, today's code decides).
- `docs/design.md`: the index table's `records` row says each version keeps its byte offset; the raw record section
  says `read` takes a record at its place when the index is current for its source.
- Docstrings: `recall.py` ("A read loads each source's raw once"), `spans.py`, `lookup.py`.
- `CHANGELOG.md`, Changed: reading a record no longer loads its source's whole raw log when the index is current; the
  index rebuilds once.

## Risks

| Risk | Status |
|---|---|
| The run-file check grows with a source's runs, about 2 ms per 1,000 here | accepted: 10 ms at 5,000 runs, an hourly source's year, against 15 ms per MB loaded |
| After a pull or a command-line sync, reads load the source whole until something updates the index | accepted: as today; the service updates after its writes and before each search |
| On Windows a listing's size and time can lag a file still open for writing | accepted: run files arrive by rename, and a line read by place must hold its rid and version |
| A read's transaction holds an update's commit a few ms longer than #46's | accepted: against the update's 5 s busy timeout |
| The narrowing answers differently from the load | resolved: both routes run every read test, and `test_spans.py` compares each question on every rid |
| `ts` in the index is to the millisecond, the sort to the microsecond | resolved: the window takes whole milliseconds and today's sort decides |
| The 50 MB fixture slows the gate | accepted: about 14 s, only in the timed run alone after the rest (DR-0430); 5 MB under coverage |

## Plan review

Not reviewed yet.
