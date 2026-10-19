"""The Staging page's parts on invented records, with no I/O (DR-0380 to DR-0384): the URL both ways, the cuts a
selection makes, a record shown as promotion would write it, the list and its reasons, and the held part's commands."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from staging_kit import MAIL, PHONE, TALK, mail, memo, transcript
from starlette.datastructures import QueryParams

from docket.errors import DocketError
from docket.vault.config import Redaction
from docket.vault.cuts import CUT_MARKER, Cut
from docket.vault.gate import Listing, Shown, Why
from docket.vault.holds import Holds
from docket.vault.records import RawRecord
from docket.vault.redact import Redactor
from docket.vault.releaselog import LoggedCut, Release, Releases, fold
from docket.vault.staging import Entry, Staged, key_of
from docket.web.staging_view import (
    DEFAULT_K,
    StagingState,
    StagingStateError,
    build_held,
    build_list,
    build_panel,
    canonical,
    chosen_cuts,
    done_line,
    read_record,
    seen,
    stored,
    waited,
    why_line,
)

NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
CREDENTIALS = Redactor.from_config(Redaction())
WITH_PHONE = Redactor.from_config(Redaction(phone=True))
OPENED = "c0ffee00" * 4


def state(query: str) -> StagingState:
    return StagingState.read(QueryParams(query))


def entry(record: RawRecord, **fields: Any) -> Entry:
    staged = Staged(state=fields.pop("state", "pending"), staged_at=NOW - timedelta(days=2), record=record, **fields)
    return Entry(Path("staging") / record.source / f"{key_of(record.rid, record.v)}.json", staged)


def shown(record: RawRecord, why: Why = "mixed", **fields: Any) -> Shown:
    return Shown(entry(record, **fields), None, why)


# -- the URL -------------------------------------------------------------------------------------------------------


def test_the_url_is_written_back_in_one_canonical_form() -> None:
    read = state(f"cut=30:02-31:12&u=3&k={DEFAULT_K}&open={OPENED}&u=1-2&source=calls&u=3&state=pending&x=1")
    assert read.url() == f"/staging?source=calls&state=pending&open={OPENED}&u=1-2&u=3&cut=30%3A02-31%3A12"
    assert state(read.url().partition("?")[2]) == read
    # Typed cuts split on commas and spaces, as the field takes them.
    assert state(f"open={OPENED}&cut=0:10-0:20,+1:00-1:05").cut == ("0:10-0:20", "1:00-1:05")
    # Selections and the drop's question only with an open record; done only where a post put it.
    assert state("u=1&line=2&cut=1-4&confirm=private").url() == "/staging"
    assert state("done=work-3f9a1c2e4b5d").url() == "/staging"
    assert state("done=work-3f9a1c2e4b5d").url(done=True) == "/staging?done=work-3f9a1c2e4b5d"
    assert canonical(f"http://127.0.0.1:1/staging/panel?u=2&open={OPENED}") == f"/staging?open={OPENED}&u=2"
    assert canonical("http://127.0.0.1:1/search?query=x") is None
    assert canonical("http://127.0.0.1:1/staging?k=0") is None
    # picked says an empty selection is the user's; with anything selected it says nothing, and it needs a record.
    assert state(f"open={OPENED}&picked=1").url() == f"/staging?open={OPENED}&picked=1"
    assert state(f"open={OPENED}&picked=1&u=2").url() == f"/staging?open={OPENED}&u=2"
    assert state("picked=1").url() == "/staging"
    assert state(f"open={OPENED}").chosen().url() == f"/staging?open={OPENED}&picked=1"


def test_a_typed_cut_that_doesnt_read_stays_for_the_panel_to_name() -> None:
    read = state(f"open={OPENED}&u=2&cut=12.30-14.05")
    assert (read.u, read.cut) == (((2, 2),), ("12.30-14.05",))
    panel = build_panel(shown(transcript()), CREDENTIALS, state(f"open={OPENED}&u=2&cut=12:30"))
    assert panel.problem is not None and panel.problem.startswith("cut 12:30: a cut is <start>-<end>")
    assert panel.utterances is not None and [u.n for u in panel.utterances if u.checked] == [2]
    assert panel.typed == "12:30" and not panel.can_work


@pytest.mark.parametrize(
    ("query", "names"),
    [
        ("source=Calls!", "source"),
        ("state=done", "state"),
        ("k=0", "k"),
        ("k=1001", "k"),
        ("k=\N{SUPERSCRIPT TWO}", "k"),
        ("open=3f9a", "open"),
        (f"open={OPENED}&u=0", "u"),
        (f"open={OPENED}&u=9-4", "u"),
        (f"open={OPENED}&line=x", "line"),
        (f"open={OPENED}&cut=" + "1" * 41, "cut"),
        (f"open={OPENED}&picked=yes", "picked"),
        ("confirm=yes", "confirm"),
        ("done=work-xyz", "done"),
    ],
)
def test_a_value_the_url_cant_hold_is_named(query: str, names: str) -> None:
    with pytest.raises(StagingStateError, match=rf"^{names}"):
        state(query)


# -- the cuts a selection makes --------------------------------------------------------------------------------------


def test_checked_utterances_cut_at_their_segments_exact_milliseconds() -> None:
    reading = read_record(transcript(), CREDENTIALS)
    # 2 and 3 overlap, so they make one cut; typed beside them, a stretch of its own.
    cuts = chosen_cuts(reading, state(f"open={OPENED}&u=2-3&cut=0:50-0:55"))
    assert cuts == [Cut(start=12_000, end=45_700, unit="ms"), Cut(start=50_000, end=55_000, unit="ms")]


def test_an_utterance_with_no_length_is_a_one_millisecond_cut_not_an_error() -> None:
    reading = read_record(transcript(), CREDENTIALS)
    assert chosen_cuts(reading, state(f"open={OPENED}&u=4")) == [Cut(start=45_700, end=45_701, unit="ms")]


def test_checked_lines_cut_the_staged_texts_characters_lines_next_to_each_other_as_one() -> None:
    reading = read_record(mail(), CREDENTIALS)
    lines = MAIL.split("\n")
    start = len(lines[0]) + 1 + len(lines[1]) + 1
    cuts = chosen_cuts(reading, state(f"open={OPENED}&line=3-4&line=6"))
    assert [MAIL[c.start : c.end] for c in cuts] == [f"{lines[2]}\n{lines[3]}", "Ada"]
    assert cuts[0].start == start
    # An empty line alone cuts nothing.
    assert chosen_cuts(reading, state(f"open={OPENED}&line=2")) == []


def test_a_selection_that_doesnt_fit_the_record_is_refused() -> None:
    talk, text = read_record(transcript(), CREDENTIALS), read_record(mail(), CREDENTIALS)
    for reading, query, message in [
        (talk, "u=6", "u names utterance 6, and this transcript has 5"),
        (text, "u=1", "u picks utterances, and this record has no transcript"),
        (talk, "line=1", "line picks lines of a text, and this record is a transcript"),
        (text, "line=7", "line names line 7, and this text has 6"),
    ]:
        with pytest.raises(StagingStateError, match=message):
            chosen_cuts(reading, state(f"open={OPENED}&{query}"))


def test_typed_character_cuts_are_refused_where_the_rules_changed_the_texts_length() -> None:
    record = mail(text=f"Call me on {PHONE}.\nUntil Friday.")
    assert chosen_cuts(read_record(record, CREDENTIALS), state(f"open={OPENED}&cut=0-4")) == [Cut(start=0, end=4)]
    redacted = read_record(record, WITH_PHONE)
    assert redacted.changed and not redacted.same_length
    with pytest.raises(StagingStateError, match="changed this text's length since it was staged"):
        chosen_cuts(redacted, state(f"open={OPENED}&cut=0-4"))
    # Checked lines still land: they are the staged text's lines.
    start = record.text.index("Until")
    assert chosen_cuts(redacted, state(f"open={OPENED}&line=2")) == [Cut(start=start, end=len(record.text))]


def test_lines_the_rules_join_take_no_character_cut() -> None:
    # A key block from before the credential rule was on goes as one marker, so the lines join. Assembled here, so the
    # repo's secret scan finds no literal.
    block = "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIBOgIBAAJBAKj34\n-----END RSA " + "PRIVATE KEY-----"
    record = mail(text=f"Here it is:\n{block}\nUntil Friday.")
    reading = read_record(record, CREDENTIALS)
    assert reading.lines is None
    with pytest.raises(StagingStateError, match="join lines of this text"):
        chosen_cuts(reading, state(f"open={OPENED}&line=1"))
    panel = build_panel(shown(record), CREDENTIALS, state(f"open={OPENED}"))
    assert panel.lines is None and "MIIBOgIBAAJBAKj34" not in panel.text
    assert any(n.startswith("The redaction rules join lines of this text") for n in panel.notes)
    with_line = build_panel(shown(record), CREDENTIALS, state(f"open={OPENED}&line=1"))
    assert with_line.problem is not None and not with_line.can_work
    # A stored cut on such a text can't be shown as lines: it is typed whole.
    assert stored(reading, [Cut(start=0, end=4)]) == ((), (), ("0-4",))


def test_a_cut_the_record_cant_take_is_the_previews_problem() -> None:
    panel = build_panel(shown(mail()), CREDENTIALS, state(f"open={OPENED}&cut=0:10-0:20"))
    assert panel.problem is not None and "no segments to cut by time" in panel.problem
    assert panel.preview is None and not panel.can_work


def test_seen_names_what_the_panel_showed() -> None:
    staged = Staged(state="ready", staged_at=NOW, classified_by="claude", cuts=[Cut(start=1, end=4)], record=memo())
    assert seen(staged) == {"shown_state": "ready", "shown_by": "claude", "shown_cuts": "char:1-4"}


# -- the panel -------------------------------------------------------------------------------------------------------


def test_the_panel_shows_a_record_as_promotion_would_write_it_and_says_so() -> None:
    record = mail(title=f"Call {PHONE}", text=f"Ring {PHONE}\nor mail.")
    panel = build_panel(shown(record), WITH_PHONE, state(f"open={OPENED}"))
    assert panel.title == "Call [redacted:phone]"
    assert panel.lines is not None and panel.lines[0].text == "Ring [redacted:phone]"
    assert panel.notes and "redaction rules changed since this was staged" in panel.notes[0]
    assert PHONE not in str(panel)
    plain = build_panel(shown(record), CREDENTIALS, state(f"open={OPENED}"))
    assert plain.notes == [] and PHONE in plain.title


def test_a_transcript_s_people_read_as_its_speakers_and_a_message_s_as_its_recipients() -> None:
    talk = build_panel(shown(transcript(to=["Ada Example", "Ben Sample"])), CREDENTIALS, state(f"open={OPENED}"))
    assert ("speakers", "Ada Example, Ben Sample") in talk.facts
    note = build_panel(shown(mail(to=["ben@example.invalid"])), CREDENTIALS, state(f"open={OPENED}"))
    assert ("to", "ben@example.invalid") in note.facts


def test_the_preview_names_the_utterances_that_go_an_overlapping_one_included() -> None:
    panel = build_panel(shown(transcript()), CREDENTIALS, state(f"open={OPENED}&u=2"))
    assert panel.utterances is not None
    assert [u.n for u in panel.utterances if u.going] == [2, 3]
    assert [u.n for u in panel.utterances if u.checked] == [2]
    assert panel.preview is not None and panel.preview.summary == "Removes 2 utterances: 00:12-00:31."
    assert panel.work_label == "Work, with 1 cut"
    text = build_panel(shown(mail()), CREDENTIALS, state(f"open={OPENED}&line=3"))
    assert text.preview is not None and text.preview.text is not None
    assert "10 million" not in text.preview.text and CUT_MARKER in text.preview.text


def test_a_selection_the_record_cant_take_is_the_panels_problem_and_offers_no_work() -> None:
    panel = build_panel(shown(transcript()), CREDENTIALS, state(f"open={OPENED}&cut=1-4"))
    assert panel.problem is not None and "cut a time" in panel.problem
    assert not panel.can_work and panel.preview is None


def test_an_utterance_with_no_length_at_the_end_can_be_cut() -> None:
    talk = [*TALK, {"start_ms": 60_000, "end_ms": 60_000, "speaker": "Ada Example", "text": "Bye."}]
    panel = build_panel(shown(transcript(segments=talk)), CREDENTIALS, state(f"open={OPENED}&u=6"))
    assert panel.problem is None and panel.can_work
    assert panel.preview is not None and panel.preview.summary == "Removes 1 utterance: 01:00-01:01."


def test_a_cut_record_opens_with_its_cuts_checked_so_work_keeps_them() -> None:
    # Claude cut utterance 2 and, by a typed range, 0:50 to 0:55; nothing is in the URL.
    cuts = [Cut(start=12_000, end=30_400, unit="ms"), Cut(start=50_000, end=55_000, unit="ms")]
    record = shown(transcript(), "classified", state="ready", classified_by="claude", cuts=cuts)
    panel = build_panel(record, CREDENTIALS, state(f"open={OPENED}"))
    assert panel.utterances is not None
    assert [u.n for u in panel.utterances if u.checked] == [2]
    assert panel.typed == "0:50-0:55"
    assert panel.work_label == "Work, with 2 cuts"
    assert panel.preview is not None and panel.preview.summary.startswith("Removes 3 utterances")
    # The checks and the typed range make exactly the stored cuts.
    reading = read_record(record.entry.staged.record, CREDENTIALS)
    assert chosen_cuts(reading, state(f"open={OPENED}&u=2&cut=0:50-0:55")) == cuts
    # Unchecked on purpose and previewed: the empty selection is the user's.
    bare = build_panel(record, CREDENTIALS, state(f"open={OPENED}&picked=1"))
    assert bare.utterances is not None and not any(u.checked for u in bare.utterances)
    assert bare.work_label == "Work, whole"


def test_a_cut_that_covers_part_of_an_utterance_keeps_the_rest_typed_exactly() -> None:
    cut = Cut(start=10_000, end=35_000, unit="ms")
    reading = read_record(transcript(), CREDENTIALS)
    u, line, typed = stored(reading, [cut])
    assert (u, line, typed) == (((2, 2),), (), ("0:10-0:12", "0:30.400-0:35"))
    selection = StagingState(open=OPENED, u=u, cut=typed)
    assert chosen_cuts(reading, selection) == [cut]
    # Lines a character cut covers are checked, with the break between them; a part of a line is typed.
    text = read_record(mail(), CREDENTIALS)
    lines = MAIL.split("\n")
    start = len(lines[0]) + 1 + len(lines[1]) + 1
    whole = Cut(start=start, end=start + len(lines[2]) + 1 + len(lines[3]))
    assert stored(text, [whole]) == ((), ((3, 4),), ())
    part = Cut(start=start, end=start + 5)
    assert stored(text, [part]) == ((), (), (f"{start}-{start + 5}",))


def test_a_version_staged_before_its_release_shows_what_the_release_cuts() -> None:
    release = (Cut(start=12_000, end=30_400, unit="ms"),)
    record = Shown(entry(transcript()), None, "released", release)
    panel = build_panel(record, CREDENTIALS, state(f"open={OPENED}"))
    assert panel.utterances is not None
    assert [u.n for u in panel.utterances if u.released] == [2, 3]
    assert all(u.going for u in panel.utterances if u.released)
    assert any("release cuts 2 utterances" in n for n in panel.notes)
    # A preview cuts by the release too, as classification and promotion do.
    previewed = build_panel(record, CREDENTIALS, state(f"open={OPENED}&u=5"))
    assert previewed.preview is not None and previewed.preview.summary == "Removes 1 utterance: 00:46-01:00."
    assert [u.n for u in previewed.utterances or [] if u.going] == [2, 3, 5]


def test_a_record_on_hold_shows_neither_its_title_nor_its_text() -> None:
    panel = build_panel(shown(transcript(), "on-hold"), CREDENTIALS, state(f"open={OPENED}"))
    assert (panel.title, panel.text, panel.utterances, panel.lines, panel.facts) == ("", "", None, None, [])
    assert "Supplier call" not in str(panel) and "weekend" not in str(panel)
    assert not panel.can_work


# -- the list --------------------------------------------------------------------------------------------------------


def test_the_list_counts_titles_and_reasons() -> None:
    # The phone sits across the 80th character: cut before it is redacted, a part of it would show.
    long_title = "x" * 72 + " " + PHONE
    records = [transcript(), mail(title=long_title), memo(), transcript("rec-9", title="Held back call")]
    entries = [entry(records[0]), entry(records[1]), entry(records[2], state="ready"), entry(records[3])]
    why: dict[str, Why] = dict(zip([e.key for e in entries], ["mixed", "by-hand", "work", "on-hold"], strict=True))
    view = build_list(Listing(entries, [], why), WITH_PHONE, StagingState(), NOW, held_ids=2, held_sources=["calls"])
    assert view.lead == (
        "4 records wait at the gate: 2 for you to classify, 1 ready to promote, 1 on hold; 2 recordings held"
    )
    rows = {r.rid: r for g in view.groups for r in g.rows}
    title = rows["calls:mail-1"].title
    assert title.endswith("\N{HORIZONTAL ELLIPSIS}") and len(title) <= 80
    assert title.startswith("x" * 72 + " [redac")
    assert not any(part in title for part in ("+1", "555", "0142"))
    assert PHONE not in str(view)
    assert rows["calls:rec-9/transcript"].title == "" and rows["calls:rec-9/transcript"].on_hold
    assert rows["calls:mail-1"].line == "an earlier version was decided by hand: work or private?"
    assert rows["calls:rec-1/transcript"].waited == "2 days"
    assert view.ready == [("memo", 1)]
    assert [c.label for c in view.source_chips] == ["All sources", "calls", "memo"]
    narrowed = build_list(Listing(entries, [], why), CREDENTIALS, state("state=ready"), NOW)
    assert [r.rid for g in narrowed.groups for r in g.rows] == ["memo:m-1"]
    assert build_list(Listing(entries, [], why), CREDENTIALS, state("k=2"), NOW).more == "/staging?k=52"
    assert build_list(Listing([], [], {}), CREDENTIALS, StagingState(), NOW).lead == "Nothing waits at the gate"


def test_each_reason_has_its_line() -> None:
    staged = Staged(state="ready", staged_at=NOW, classified_by="user", cuts=[Cut(start=1, end=2)], record=memo())
    lines = {why: why_line(why, staged) for why in ("on-hold", "held-back", "released", "by-hand", "mixed", "work")}
    assert lines["on-hold"].startswith("on hold: it stops every ingest of memo")
    assert why_line("classified", staged) == "classified work by you, with 1 cut: it goes at the next promotion"


def test_waited_and_done_read_plainly() -> None:
    assert [waited(timedelta(seconds=s)) for s in (10, 600, 7200, 3 * 86400)] == [
        "under a minute",
        "10 min",
        "2 h",
        "3 days",
    ]
    assert done_line("work-3f9a1c2e4b5d") == "3f9a1c2e4b5d classified work: it goes to raw at the next promotion."
    assert (
        done_line("promoted-12-1")
        == "Promoted: 12 records written to raw and committed; 1 held back, each with its problem in the list."
    )
    assert done_line("promoted-2-0-stopped") == (
        "Promoted: 2 records written to raw and committed. Meetings not made: the calendar step stopped, and its "
        "records wait for the next promotion."
    )
    assert done_line(None) is None


# -- held recordings -------------------------------------------------------------------------------------------------


def granted(native: str) -> Releases:
    when = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    line = Release(id=native, cuts=[LoggedCut(start=1000, end=2000)], keep=[], at=when, machine="laptop-1")
    return Releases({"calls": fold([line])})


def test_the_held_commands_are_quoted_and_name_the_vault() -> None:
    held = Holds({"calls": frozenset({"rec-cut-01", "rec-cut-03"})})
    folders = {"calls": [r"C:\Users\ada\My Exports\calls"]}
    out = build_held(held, granted("rec-cut-03"), StagingState(), folders=folders, root=r"D:\vaults\work vault",
                     on_hold={"calls": 1})  # fmt: skip
    assert [(h.source, [i.id for i in h.ids], h.on_hold) for h in out] == [("calls", ["rec-cut-01", "rec-cut-03"], 1)]
    first, again = out[0].ids
    where = r'--from "C:\Users\ada\My Exports\calls" --vault "D:\vaults\work vault"'
    assert [c.text for c in first.commands] == [
        f'docket hold show calls "rec-cut-01" {where}',
        f'docket hold release calls "rec-cut-01" {where} --cut <mm:ss-mm:ss>',
    ]
    assert again.again == "released 2026-10-14 09:00 UTC on laptop-1, and held again after a merge"
    assert [c.text for c in again.commands] == [f'docket hold release calls "rec-cut-03" {where}']
    assert out[0].unfilled == []
    assert out[0].paste == "Go through my held calls recordings with /docket:cuts."


def test_a_value_a_shell_would_act_on_stays_a_placeholder_listed_beside() -> None:
    held = Holds({"calls": frozenset({"rec-1"}), "memo": frozenset()})
    folders = {"calls": ["/home/ada/$HOME/calls", "/srv/two"]}
    (out,) = build_held(held, Releases(), StagingState(), folders=folders, root="/srv/100%", on_hold={})
    assert out.ids[0].commands[0].text == 'docket hold show calls "rec-1" --from "<folder>" --vault "<vault>"'
    assert out.unfilled == [("folder", "/home/ada/$HOME/calls"), ("folder", "/srv/two"), ("vault", "/srv/100%")]
    assert build_held(held, Releases(), state("source=memo"), folders={}, root="/v", on_hold={}) == []


# Each kind of character a copied command can't carry as it is: the double quote, $, the backquote, % and ! that
# shells expand, a control character, two backslashes, a trailing backslash, and the curly quotes PowerShell reads as
# a quote, after which a held id would run as code.
NOT_CARRIED = ['"', "$", "`", "%", "!", "\x07", "\\\\", "\u201c", "\u201d", "\u201e", ";", "'", "&", "|", "(", "#"]


@pytest.mark.parametrize("bad", NOT_CARRIED)
@pytest.mark.parametrize("where", ["id", "folder", "vault"])
def test_each_character_a_shell_reads_leaves_its_value_a_placeholder(bad: str, where: str) -> None:
    value = {"id": f"x{bad}y", "folder": f"/srv/ex{bad}ports", "vault": f"/v/wo{bad}rk"}[where]
    native = value if where == "id" else "rec-1"
    folder = value if where == "folder" else "/srv/exports"
    root = value if where == "vault" else "/v/work"
    held = Holds({"calls": frozenset({native})})
    (out,) = build_held(held, Releases(), StagingState(), folders={"calls": [folder]}, root=root, on_hold={})
    command = out.ids[0].commands[1].text
    assert value not in command and f'"<{where}>"' in command
    assert out.unfilled == [(where, value)]


def test_a_trailing_backslash_leaves_its_value_a_placeholder() -> None:
    held = Holds({"calls": frozenset({"rec-1"})})
    (out,) = build_held(held, Releases(), StagingState(), folders={"calls": ["C:\\exports\\"]}, root="/v", on_hold={})
    assert '--from "<folder>"' in out.ids[0].commands[0].text


def test_letters_of_any_script_are_carried_as_they_are() -> None:
    folder = "C:\\Users\\ada\\\u00c1rv\u00edzt\u0171r\u0151 t\u00fck\u00f6r"
    held = Holds({"calls": frozenset({"felv\u00e9tel-1"})})
    (out,) = build_held(held, Releases(), StagingState(), folders={"calls": [folder]}, root="/v/w", on_hold={})
    assert out.ids[0].commands[0].text == f'docket hold show calls "felv\u00e9tel-1" --from "{folder}" --vault "/v/w"'
    assert out.unfilled == []


def test_reading_a_state_from_a_form_mapping() -> None:
    assert StagingState.read({"source": "calls"}).source == "calls"
    with pytest.raises(DocketError):
        StagingState.read({"source": "no such!"})
