---
title: Cut a recording's Whisper transcript when a later version gains a cut
issue: "#186"
status: draft
approved_by:
approved_at:
---

# Cut a recording's Whisper transcript when a later version gains a cut

The promotion that brings a new cut to a recording's family also cuts the family's Whisper transcript. The gate writes
the transcript's next version, its overlapping segments replaced by `[cut]`, in the same run and commit as the
recorder's. It is a cut of text, so it needs no Whisper, audio or key on the machine.

## Goal

| The issue's case | This plan |
|---|---|
| a later version adds a cut | the Whisper transcript's next version, cut, in the same run and commit; search no longer finds the stretch (DR-0590) |
| a later version removes nothing | nothing is written |
| a machine that doesn't ingest the source | nothing is written there; a clone that runs the source writes it at its next promotion (DR-0591) |
| a staged Whisper record | cut as it is promoted, and the only version of the family that promotion writes |
| due again | not by itself: the cut version holds nothing of the cut; `docket transcribe` redoes it from the cut audio when asked (DR-0592) |

Out of scope: the older versions in raw and history (#182's ground), and a family cut only by characters, which has
no time to cut at (its audio stays unplayed for the same reason, DR-0562).

## Design

### Where the cut follows (DR-0590)

In `Gate.promote`, between building the groups and writing the runs:

| Step | Today | Now |
|---|---|---|
| sweep, ready entries, groups | as today | as today |
| the follow | none | `_follow(config, groups)`: the families to follow, their ranges, the Whisper versions; a staged one is cut in its place |
| runs | `_promote_source(name, items)` | `_promote_source(name, items, extra)`: the versions from raw go at the end of the source's run, or make a run of their own |
| media | as today | as today, after the runs, so the transcript is cut no later than its audio is stored |

- One run, one commit: no point lies between the recorder's line and the Whisper version's, except a run split at
  16 MiB (DR-0488). A crash there leaves the recorder's entry staged, since staged files go only after the commit, and
  the next promotion follows it again.
- Not staged (DR-0596, superseding DR-0015). Staged, it would wait for the owner: everything from a mixed source does,
  as a recorder's is, and a version of a cut record does on any source (`Gate._by_hand`), so search would show the
  stretch until they classified it. No classifier value fits it, and staging adds no check and no crash safety. Only a
  cut of a version already in raw, or of a staged one promoted in the same call, goes in this way; nothing new from a
  source does.
- The code: `speech/follow.py` (new: the families, the ranges and the versions, from the gate's cut scan and staging,
  in memory), `cuts.place` (the cut, shared with `speech/job._record`), and the gate's `_follow`, which reads a Whisper
  transcript from raw only when its family has a range it doesn't hold.

### Which families, which ranges

A family is a recording and every rid under it, as media's (DR-0547). For a source this clone runs, the follow looks
at:

| Family | Why |
|---|---|
| one with a `[cut]` segment in raw (the rids the gate's cut scan, `_refresh_cuts`, finds on every call) and a Whisper transcript in raw | a cut that reached raw from anywhere: another clone's, an older Docket's, one from before this change |
| one with a transcript promoted now holding `[cut]` segments | the cut in its own promotion |
| one whose Whisper transcript is staged and promoted now | cut before it reaches raw |

It writes a version when placing the family's cuts on the Whisper transcript's latest version changes its segments,
so a family whose latest holds them already costs a comparison and writes nothing.

The ranges are the family's, as media's screen reads them (DR-0561): the release's cuts, and the `[cut]` segments of
every transcript of the family, staged ones with their time cuts made and those in raw, Whisper's own among them. They
are what `docket transcribe` cuts by (DR-0569). One thing differs: a record cut by characters, or a staged cut that
doesn't fit yet, adds nothing instead of refusing, so the times known are cut.

- Not the cut log's ranges for the new stored file: the audio is put again only at the source's next run, and search
  would show the stretch until then.
- Not the recorder version's own cuts alone: a release's cuts, and a cut on another transcript of the family, would be
  missed where the audio has them.

### The version

| Part | Rule |
|---|---|
| segments | every segment of the Whisper transcript's latest version (or of the staged one) that overlaps a range goes, `[cut]` ones included, each end of a range that lies outside the version's own `[cut]` segments padded by `cutter.PAD_MS`; a `[cut]` segment sits at each range, unpadded, merged |
| text | rendered from the segments |
| everything else | kept from the version it cuts: `via` still names the stored file it was heard from, `engine`, `lang`, `to`, `ts`, `fetched` |
| redaction | the rules in force, as for every promoted record |
| no change | when the segments come out the same, nothing is written |

- The pad: the audio is silenced 250 ms beyond each range (DR-0560). A version heard from audio that still held a new
  stretch can carry words said inside it in the segment just after it, so that segment goes when it overlaps the
  padded range. Each end is judged on its own: an end inside the version's own `[cut]` segments was silent in the audio
  it was heard from, or was padded when it was followed, so it isn't padded again. A range that grew on one side is
  padded on that side only, a fresh transcript is never cut further, and placing twice gives what placing once does.
- The marks sit at the ranges, as `_record` places them (#158's build), not widened over Whisper's own segments as
  `cuts.cut_segments` widens them. Every transcript's `[cut]` segments widen the audio's cut (DR-0561), so a widened
  or padded mark would silence audio the owner never cut and send it to be put again. A range after Whisper's last
  segment is marked, never refused.
- `cuts.place(segments, ranges, pad)` is the one cut. `_record` calls it with no pad, after dropping punctuation-only
  segments, since its audio was silent there already.

### What stops it (DR-0591)

A version that only removes text already in raw is the safe side, so only what would make writing it wrong stops it.

| Check | Then |
|---|---|
| the source isn't one this clone runs (its cursor file) | nothing |
| the source out of force, or private | nothing |
| a hold on the recording | nothing; a staged one waits with the hold |
| the cut version matches a never-store value | nothing |
| the family withheld, or a private decision on a record of it | it goes on: the version holds less of what raw holds already |
| the source only off | it goes on, as a staged one is kept |

- A staged Whisper record is still dropped by the sweep when its family is kept out (DR-0570): it holds text raw
  doesn't, so that rule stands.
- The cursor rule keeps a source's run files to the clones that run it, as `docket transcribe` does (DR-0569). A clone
  used only for search never writes one or spends the comparison, and two clones can race on a version only where
  both run the source, as they already can on the recorder's transcript.

### On each machine

| Clone | At its next promotion |
|---|---|
| runs the source, promotes the cut | writes the version in that promotion |
| runs it; the audio not downloaded, its key not here, no Whisper installed | the same: the cut is text |
| doesn't run it | writes nothing; it reads the earlier version until it pulls the one a clone that runs the source wrote |
| runs it, pulls a cut it didn't promote | writes it: the cut scan finds the family |
| two that run it, from the same raw | the same version: a function of the Whisper version, the ranges and the rules in force, so the same `v`; after the merge every reader's latest is that version, and the index counts it once |
| two that each promote a different cut before pulling | a version each, neither holding what its own clone cut; the next promotion cuts by both, except in the race under Risks |

### A staged Whisper record

- Ready and promoted now: cut by the family's ranges as it is promoted, in its place in the run. It is the family's
  only Whisper version that promotion writes: an earlier version in raw isn't followed beside it, so it can't hide the
  transcript the owner just classified.
- Pending (a mixed source, or a cut family) or held: it stays, and is cut when it is promoted; until then the version
  in raw is followed.
- `docket transcribe`'s step 4 already cuts by the ranges read then (DR-0569); this covers a cut promoted while it
  waits.

### Cost

| Part | What it reads |
|---|---|
| the cut scan | no file it doesn't read already: `_refresh_cuts` reads every run file's lines on every gate call and parses each line with a `[cut]`; it now keeps each rid's `[cut]` spans, and parses each machine transcript's line too (by its `via`, a substring test) to keep its latest marks |
| the families | each cut rid's owners, from the rid alone, for the sources this clone runs, with a machine transcript the scan saw |
| the check | in memory: the scan's spans, the release's cuts and staging's cuts against the Whisper transcript's latest marks; a family whose marks hold every range is passed over without a read |
| the read | only for a family with a range not held: its Whisper transcript's latest version at the index's places (DR-0446), then `cuts.place` |

- Families with a cut are the released and hand-cut recordings, and backfill releases add many; each costs a few set
  operations, not a read. A read happens once per new cut, since the version it writes holds the range from then on.
- The index is opened only when a family has a range not held, and closed before any write. With it stale for the
  source (after a pull, before the service's next update), that read loads the source's run files once, as media's
  screen does.
- A machine transcript's latest marks are its latest line's, by run order, whether that line holds a `[cut]` or not.

### Due again (DR-0592)

Not by itself. The service keeps DR-0571's rule: a recording with a Whisper transcript, staged or in raw, isn't queued
by itself.

- The followed version holds nothing of the cut. A new transcript from the audio put again only gives back the words of
  the segments a cut took whole beside it.
- A recut family's new Whisper transcript waits for the owner, as any version of a cut record does, so queuing it
  would hand the owner one more record to classify for every recut, and run one GPU job each.
- `docket transcribe <rid>` and the `transcribe` tool still redo it: the version names the earlier stored file, so
  DR-0569's same-file rule lets a run on the new file through.
- The issue proposed it. One condition in `speech.job.due` turns it on (Needs the maintainer).

### Beside #182

#182's plan has every gate call run its apply first; the follow runs inside `promote`, after the sweep.

| Place | #182 | This change |
|---|---|---|
| `Gate.promote` | the apply, first | one call, `_follow`, after the groups and before the runs |
| `Gate._promote_source` | unchanged | takes `extra` |
| `_refresh_cuts` | skips listed lines; run file caches by size and time | keeps the spans and marks it parses, through `follow.Seen.add`: a conflict there is a few lines |
| `stage`, `_sweep`, `RawLog`, `RawLookup` | skip and drop listed records | read only |
| `media/screen.py`, `media/reader.py` | refuse a listed rid's media; the state `forgotten` | read only |
| `speech/job.py` | `WHY` gains `forgotten` | `_record` through `cuts.place` |
| gate.py | its module among the imports | `speech.follow` and `cursors` among the imports; the first line cites DR-0596 |

- A forgotten family is gone or skipped before the follow looks: the cut scan doesn't name it, and it has no Whisper
  transcript to read, so nothing is written.
- Every input comes through readers #182 makes skip listed lines, so the follow needs no check of its own. A version a
  clone writes before it pulls a forget is listed with every version of its rid, by the rid's hash: skipped at once,
  removed by the next apply.
- Whichever merges second adds a test: a promotion touching a forgotten family writes no Whisper version.

## Doors and data

| Door or decision | Change |
|---|---|
| AGENTS.md's doors | none: the Whisper record's rid, engine, `via` and segments are as DR-0570 has them, and the version is an ordinary raw version in a run file of this machine |
| DR-0015, everything reaches raw through staging | superseded by DR-0596: everything from a source does; the gate's own cut of a version already in raw, or of one it promotes in the same call, goes in at promotion (Needs the maintainer) |
| DR-0570's bind, nothing else writes a `via` starting `docket transcribe` | holds: the followed version is a version of the machine transcript, its `via` copied |

Existing vaults: nothing moves. A family whose cut reached raw before the update is followed at the first promotion on
a clone that runs its source.

## Steps

1. `cuts.place(segments, ranges, pad)`, and `_record` through it with no pad. `test_cuts.py`: overlap as `cuts.py`
   reads it, the pad on an end outside the held marks only, a mark past the last segment, marks merged and unpadded,
   `[cut]` segments replaced, placing twice. `test_speech_record.py` runs unchanged.
2. `speech/follow.py`: what the scan keeps (`Seen`), the families, the ranges, the version.
   `test_whisper_follow.py`, on invented records.
3. The gate: `_follow`, `_promote_source`'s `extra`, the count in the log line (no rid). `test_whisper_follow.py` end
   to end (Tests, below). gate.py's first line cites DR-0596 already, in this plan's commit, since `docs check`
   refuses a cite of a superseded entry.
4. Docs.

## Tests

Invented data only: `docket_testkit` and `media_kit` records, a tone made at test time, the fake worker; the cut at
31:05 to 31:40 is the issue's, on an invented call.

| Test | Proves |
|---|---|
| a later version cuts 31:05 to 31:40 | one Whisper version in the same run file and commit as the recorder's; no segment of it overlaps the range, `[cut]` sits there; after an index update a search for a word said there finds neither transcript |
| the pad | a Whisper segment starting 100 ms after a new range goes and one starting 300 ms after stays; the mark sits at the range; a fresh transcript's segment 100 ms after a range it was cut by stays, and the next promotion writes nothing for it |
| a range extended on one side | a held range 31:05 to 31:40 grown to 31:42 keeps the segment from 30:50 to 31:04.9 on the held side, and drops one starting 100 ms past 31:42 |
| a family whose Whisper marks hold every range | passed over with no read of raw (a test seam counts the reads) |
| a later version with no new cut, or one whose cut lies inside the Whisper marks already | nothing written |
| the summary classified private and the transcript cut 31:05 to 31:40 in one review | the family withheld, a private decision on the summary, and the Whisper version written all the same |
| a cursor file without the source | the recorder's version promoted, no Whisper version |
| the audio an LFS pointer, no key here, no speech installed | the version written all the same |
| a ready staged Whisper record made before the cut, an earlier Whisper version in raw | promoted cut, the only Whisper version in the run; a pending one stays staged, the version in raw is followed, and the staged one is cut when promoted |
| the source private or out of force, a hold on the recording, the cut version matching a never-store value | nothing written |
| the source only off | written |
| a summary cut by characters beside the transcript cut by time | cut by the time range |
| a crash after the run file, before its commit | the next promotion commits the interrupted run with both lines and writes nothing more |
| a crash between the recorder's line and the Whisper version's: the run split in two files (`RUN_FILE_BYTES` made small), stopped after the first | the next promotion writes the Whisper version once; a third writes nothing |
| a cut promoted with the follow off (an older Docket, a test seam), staging then empty | the next promotion writes the Whisper version in a run of its own; the one after writes nothing |
| two clones through a bare remote, both running the source, the same cut promoted on each | the same `v`; after the merge every reader's latest is it, and the index counts one version |
| `docket transcribe` after the follow, on the audio put again | a new version staged, waiting for the owner |

## Docs

- DR-0590 to DR-0592, and DR-0596 superseding DR-0015, proposed, in this plan's commit.
- design.md: the gate's bullet that records go to raw only through staging names the cut DR-0596 lets in; the Speech
  section, step 4: a later cut of the family reaches the Whisper transcript at the next promotion.
- `speech/job.py`'s and `gate.py`'s module docstrings; the CHANGELOG under Unreleased.

## Risks

| Risk | Status |
|---|---|
| two writers race on one record's versions. `version()` hashes content only, so when one clone's cut lies inside another's, or a run file sorts before the version it cut (a clock behind, or the same minute and a machine name that sorts first), the cut both need equals a version already in raw, the raw log doesn't write it again, and the less-cut version stays latest. Nothing fixes it by itself: the follow reads the family at every promotion and writes nothing | accepted: it needs two clones running one source that promote cuts of one recording before a pull, or a follow in the minute the version it cuts was written, or a clock behind; the recorder's transcript has the same race; a later cut, or `docket transcribe` on the audio put again, makes a new latest |
| a cut takes a whole Whisper segment beside it, up to 30 s, and with the pad one that only touches 250 ms past a new range | accepted, as the recorder's transcript loses whole utterances (DR-0028); `docket transcribe` on the audio put again gives the words back |
| a cut promoted on another clone waits for this clone's next promotion | accepted: promotions are the owner's, from the Staging page or the CLI; the audio reads `recut` and plays nothing meanwhile |
| a family cut only by characters keeps the stretch in Whisper | out of scope, as its audio (DR-0562) |
| the follow slows promotion | Cost, above: set operations per cut family, and a read only for a family with a range its Whisper transcript doesn't hold |

## Needs the maintainer

| Question | Proposed |
|---|---|
| DR-0596 superseding DR-0015, his decided entry: everything from a source goes through staging, and the gate's own cut of a version in raw goes in at promotion | yes (DR-0590, DR-0596) |
| due again, as the issue proposed | no: the cut version holds nothing of the cut, and every recut would add a GPU job and a record to classify (DR-0592) |
| the cursor rule from the issue | kept: only a clone that runs the source writes (DR-0591) |

## While building

| What changed from the plan | Why |
|---|---|
| the follow reads no media screen: the ranges come from the scan and staging in memory, and the stops from the settings, the cursor file, the holds and the never-store lists | K1 left `Screen.family` out of the checks, and K8 asked for no read of a family that holds its ranges; the screen would load what neither needs |
| `PromoteResult.followed` counts the versions cut, and the promotion's log line says how many | the tests and the log need the count; no rid goes in either |
| the transcribe row is tested at the gate: a Whisper version staged after the follow waits for the owner | the full transcribe job needs audio and the fake worker for what the gate's own rule decides |
| the two-clone test runs the second clone under a machine name that sorts after the first | a follow in the same minute as the version it cut, by a machine whose name sorts first, lies before that version in run order; the Risks row and DR-0591 now name it |
| `speech/follow.py` imports the cutter as a module, not its pad by name | a process that imports the cutter first reaches the gate, and so the follow, through the vault package before the cutter is done; `test_media_cut.py`'s killed-parent test caught it |
| the gate imports the cursor file's module in `_follow`, where it reads it | the cursor module reaches the source contract, which imports the vault package, so a process that imports the contract first met it half made; CI's `test_source_new.py` caught it. `test_module_imports.py` now imports every module first, with the seven media modules whose cycles came before listed until they are fixed |

## Plan review

| Round | Finding | Severity | Status |
|---|---|---|---|
| 1 | K1: the follow refused whenever `Screen.family` refused, so a summary classified private beside a transcript cut in one review left the stretch in Whisper | major | fixed: only the source out of force or private, a hold, or the cut version matching a never-store value stops it; the cursor rule restated; a test |
| 1 | K2: a segment just after a range kept words said inside it | minor | fixed: a range the version doesn't hold yet is padded by `cutter.PAD_MS` for the drop, the mark at the range; a test |
| 1 | K3: the two-clone race lasts longer than said | minor | fixed: the Risks row and DR-0591 say nothing fixes it by itself, and that the recorder's transcript has the same race |
| 1 | K4: a missed cut waited for the audio to be put again, for good if it never was | minor | fixed: the stamp and the waiting-audio trigger dropped; every family the cut scan names with a Whisper transcript is followed at every promotion; Cost |
| 1 | K5: DR-0015 still said staging was the only way | minor | fixed: DR-0596 supersedes it; DR-0590's bind; gate.py's first line |
| 1 | K6: a staged Whisper version and a followed raw one in one promotion | minor | fixed: a family whose staged version is promoted gets only that one; the test holds an earlier version in raw |
| 2 | K1 to K6 re-checked | | resolved |
| 2 | K7: a range that grew on one side was padded on both, so a fresh transcript lost a segment on the held side | minor | fixed: each end is padded only when it lies outside the version's own marks; a test |
| 2 | K8: the follow read every cut family at every promotion | minor | fixed: the cut scan keeps each rid's spans and each machine transcript's latest marks; a family whose marks hold every range is passed over without a read |
