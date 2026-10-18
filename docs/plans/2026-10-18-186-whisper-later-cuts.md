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
| a machine that doesn't ingest the source | nothing is written there; a clone that ingests it writes it (DR-0591) |
| a staged Whisper record | cut as it is promoted |
| due again | not by itself: the cut version holds nothing of the cut; `docket transcribe` redoes it from the cut audio when asked (DR-0592) |

Out of scope: the older versions in raw and history (#182's ground), and a family cut only by characters, which has
no time to cut at (its audio stays unplayed for the same reason, DR-0562).

## Design

### Where the cut follows (DR-0590)

In `Gate.promote`, between building the groups and writing the runs:

| Step | Today | Now |
|---|---|---|
| sweep, ready entries, groups | as today | as today |
| the follow | none | `_follow(config, groups)`: the families touched, their ranges, the Whisper versions; a staged one is cut in its place |
| runs | `_promote_source(name, items)` | `_promote_source(name, items, extra)`: the versions from raw go at the end of the source's run, or make a run of their own |
| media | as today | as today, after the runs, so the transcript is cut no later than its audio is stored |

- One run, one commit: no point lies between the recorder's line and the Whisper version's, except a run split at
  16 MiB (DR-0488). A crash there leaves the recorder's entry staged, since staged files go only after the commit, and
  the next promotion follows it again.
- Not staged. Staged, it would wait for the owner: everything from a mixed source does, as a recorder's is, and a
  version of a cut record does on any source (`Gate._by_hand`), so search would show the stretch until they classified
  it. This version holds nothing its earlier version didn't, so nothing waits.
- The screen is opened only when a trigger is there, and closed before any write, as `_promote_media` does. A
  promotion with no cut transcript, no staged Whisper record and no audio waiting costs what it did, and a read of the
  stamp.
- The code: `speech/follow.py` (new: the triggers, the checks, the ranges, the versions, over the screen),
  `cuts.place` (the cut, shared with `speech/job._record`), `media/staged.waiting_rids` (the rids of the audio waiting,
  read from the sidecars, without the sweep's deletes), and the gate's `_follow`.

### Which families, which ranges

A family is a recording and every rid under it, as media's (DR-0547). It is touched, for a source this machine
ingests, when:

| Trigger | Why |
|---|---|
| a transcript of it promoted now has `[cut]` segments | the cut itself, in its own promotion |
| its Whisper transcript is staged and promoted now | cut before it reaches raw |
| its audio waits in staging | a cut that reached raw on another clone, or from an older Docket, is followed when this clone puts the audio again; the audio reads `recut` until then |
| once per clone and source after the update, every family with a Whisper transcript in raw | a cut that reached raw before this change; a stamp in Docket's private folder, `whisper-follow`, names the sources done |

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
| segments | every segment of the Whisper transcript's latest version (or of the staged one) that overlaps a range goes, `[cut]` ones included; a `[cut]` segment sits at each range, merged |
| text | rendered from the segments |
| everything else | kept from the version it cuts: `via` still names the stored file it was heard from, `engine`, `lang`, `to`, `ts`, `fetched` |
| redaction | the rules in force, as for every promoted record |
| no change | when the segments come out the same, nothing is written |

- The marks sit at the ranges, as `_record` places them (#158's build), not widened over Whisper's own segments as
  `cuts.cut_segments` widens them. Every transcript's `[cut]` segments widen the audio's cut (DR-0561), so a widened
  mark would silence audio the owner never cut and send it to be put again. A range after Whisper's last segment is
  marked, never refused.
- `cuts.place(record, ranges)` is the one cut. `_record` calls it after dropping punctuation-only segments, so a fresh
  transcript and a followed one are cut alike, and placing twice gives what placing once does.

### On each machine (DR-0591)

The withheld marks the family checks read are each clone's own (DR-0547), so the gate cuts a Whisper transcript only
where this machine's cursor file names the source, as `docket transcribe` needs it (DR-0569).

| Clone | At its promotion |
|---|---|
| ingests the source, promotes the cut | writes the version in that promotion |
| ingests it, the audio not downloaded or its key not here, no Whisper installed | the same: the cut is text |
| doesn't ingest it | writes nothing; it reads the earlier version until it pulls the one a clone that ingests the source wrote |
| ingests it, pulls a cut it didn't promote | writes it at its next promotion touching the family, at the latest the one that stores the audio put again |
| two that ingest it, from the same raw | the same version: a function of the Whisper version, the ranges and the rules in force, so the same `v`; after the merge every reader's latest is that version, and the index counts it once |
| two that each promote a different cut before pulling | a version each, neither holding what its own clone cut; the next promotion touching the family cuts by both |

`Screen.family` runs first. A family withheld, a record of it matching a never-store value, a private decision on one,
or its source out of force or private: nothing is written, and a staged Whisper record is dropped by the sweep already
(DR-0570). A source only off doesn't stop it, as it keeps a staged one. A hold on the recording writes nothing, and a
staged one waits with the hold.

### A staged Whisper record

- Ready and promoted now: cut by the family's ranges as it is promoted, one version, in its place in the run.
- Pending (a mixed source, or a cut family) or held: it stays, and is cut when it is promoted.
- `docket transcribe`'s step 4 already cuts by the ranges read then (DR-0569); this only covers a cut promoted while it
  waits.

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
| `stage`, `_sweep`, `_refresh_cuts`, `RawLog`, `RawLookup` | skip and drop listed records | read only |
| `media/screen.py`, `media/reader.py` | refuse a listed rid's media; the state `forgotten` | read only |
| `speech/job.py` | `WHY` gains `forgotten` | `_record` through `cuts.place` |
| gate.py's imports | its module | `speech.follow`, `cursors` |

- A forgotten family is gone or skipped before the follow looks, so it finds no Whisper transcript and writes nothing.
- Every input comes through readers #182 makes skip listed lines, so the follow needs no check of its own. A version a
  clone writes before it pulls a forget is listed with every version of its rid, by the rid's hash: skipped at once,
  removed by the next apply.
- Whichever merges second adds a test: a promotion touching a forgotten family writes no Whisper version.

## Doors and data

| Door or decision | Change |
|---|---|
| AGENTS.md's doors | none: the Whisper record's rid, engine, `via` and segments are as DR-0570 has them, and the version is an ordinary raw version in a run file of this machine |
| DR-0015, everything reaches raw through staging | the gate's own cut of a version already in raw goes in at promotion without staging, as a release's cuts are made there; nothing new from a source goes this way (Needs the maintainer) |
| DR-0570's bind, nothing else writes a `via` starting `docket transcribe` | holds: the followed version is a version of the machine transcript, its `via` copied |

Existing vaults: nothing moves. The once-per-clone pass follows any family whose cut reached raw before the update,
whether or not its audio was put again since.

## Steps

1. `cuts.place`, and `_record` through it. `test_cuts.py`: overlap as `cuts.py` reads it, a mark past the last
   segment, marks merged, `[cut]` segments replaced, placing twice. `test_speech_record.py` runs unchanged.
2. `media/staged.waiting_rids` and `speech/follow.py`: the triggers, the checks, the ranges, the version.
   `test_whisper_follow.py`, over a screen built from invented records.
3. The gate: `_follow`, `_promote_source`'s `extra`, the stamp, the count in the log line (no rid).
   `test_whisper_follow.py` end to end (Tests, below).
4. Docs.

## Tests

Invented data only: `docket_testkit` and `media_kit` records, a tone made at test time, the fake worker; the cut at
31:05 to 31:40 is the issue's, on an invented call.

| Test | Proves |
|---|---|
| a later version cuts 31:05 to 31:40 | one Whisper version in the same run file and commit as the recorder's; no segment of it overlaps the range, `[cut]` sits there; after an index update a search for a word said there finds neither transcript |
| a later version with no new cut, or one whose cut lies inside the Whisper marks already | nothing written |
| a cursor file without the source | the recorder's version promoted, no Whisper version |
| the audio an LFS pointer, no key here, no speech installed | the version written all the same |
| a ready staged Whisper record made before the cut | promoted cut, one version; a pending one stays staged and is cut when promoted |
| the family withheld, a never-store value on a record of it, a hold on the recording | nothing written |
| the source only off | written |
| a summary cut by characters beside the transcript cut by time | cut by the time range |
| a crash after the run file, before its commit | the next promotion commits the interrupted run with both lines and writes nothing more |
| a crash between the recorder's line and the Whisper version's: the run split in two files (`RUN_FILE_BYTES` made small), stopped after the first | the next promotion writes the Whisper version once; a third writes nothing |
| a cut promoted with the follow off (an older Docket, a test seam), then the audio put again | the promotion that stores the audio writes the Whisper version, its run committed before the media commit |
| the once-per-clone pass | a family cut before the update is followed at the first promotion; the second reads only the stamp |
| two clones through a bare remote, both ingesting, the same cut promoted on each | the same `v`; after the merge every reader's latest is it, and the index counts one version |
| `docket transcribe` after the follow, on the audio put again | a new version staged, waiting for the owner |

## Docs

- DR-0590 to DR-0592, proposed, in this plan's commit.
- design.md's Speech section, step 4: a later cut of the family reaches the Whisper transcript at the promotion that
  brings it.
- `speech/job.py`'s and `gate.py`'s module docstrings; the CHANGELOG under Unreleased.

## Risks

| Risk | Status |
|---|---|
| two clones each promote a different cut before pulling, and one clone's ranges hold the other's: the merged cut equals a version already in raw, the raw log doesn't write it again, and the version with fewer cuts can stay latest by run order | accepted: it needs two clones ingesting one source and two cuts of one recording before a pull; a later cut, or `docket transcribe` once the audio is put again, makes a new latest |
| a cut takes a whole Whisper segment, up to 30 s, beside it | accepted, as for the recorder's transcript (DR-0028); `docket transcribe` on the audio put again gives the words back |
| a cut promoted elsewhere waits for this clone's next promotion touching the family, at the latest its next put of the audio | accepted: the audio reads `recut` and plays nothing meanwhile |
| a family cut only by characters keeps the stretch in Whisper | out of scope, as its audio (DR-0562) |
| the follow slows promotion | the screen opens only with a trigger, raw is read at the index's places (DR-0446), and the full pass runs once per clone |
| clock skew between two machines puts a later run file before an earlier one | not new: it holds for every record's versions across machines |

## Needs the maintainer

| Question | Proposed |
|---|---|
| a version written at promotion without staging, against DR-0015's wording | yes: it is the gate's cut of a version already in raw, and nothing new from a source goes this way (DR-0590) |
| due again, as the issue proposed | no: the cut version holds nothing of the cut, and every recut would add a GPU job and a record to classify (DR-0592) |
| the cursor rule from the issue | kept: only a clone that ingests the source writes (DR-0591) |

## Plan review

Not reviewed yet: `review-plan` writes this section.
