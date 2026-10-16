"""The Sync page's rules on invented facts and a fixed clock (DR-0303 to DR-0305): the URL, what is due, what failed
and what to do, the run cards and the cursors."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from starlette.datastructures import QueryParams

from docket.service import history
from docket.service.history import Counts, Mark, RunLine, SetAside, Staged
from docket.service.runs import OpenRun
from docket.vault import cursors
from docket.web import sync_view
from docket.web.sync_view import Facts, SourceFacts, SyncState, SyncStateError

NOW = datetime(2026, 10, 15, 9, 12, tzinfo=UTC)
TODAY = NOW.date()
TZ = ZoneInfo("Europe/Budapest")
RUN = "run-20261015T080000-0123456789abcdef"


def facts(**fields: Any) -> Facts:
    base: dict[str, Any] = {
        "now": NOW,
        "tz": TZ,
        "machine": "laptop-1",
        "sources": [SourceFacts("memo", enabled=True, live=True)],
        "own": {},
        "own_problem": None,
        "others": {},
        "others_notes": [],
        "open_runs": [],
        "lines": [],
    }
    return Facts(**(base | fields))


def a_line(run: str = RUN, source: str = "memo", ended: datetime = NOW, **fields: Any) -> RunLine:
    base: dict[str, Any] = {
        "run": run,
        "source": source,
        "opened": ended - timedelta(minutes=3),
        "ended": ended,
        "outcome": "closed",
        "kind": history.CLOSED,
    }
    if fields.get("kind", history.CLOSED) != history.CLOSED:
        base["outcome"] = "dropped"
    return RunLine.model_validate(base | fields)


def an_open_run(**fields: Any) -> OpenRun:
    base: dict[str, Any] = {
        "id": "run-20261015T090000-00000000000000aa",
        "source": "memo",
        "final": False,
        "backfill": False,
        "bound": True,
        "closing": False,
        "captures": 2,
        "bytes": 3000,
        "budget": 400 * 1024,
        "opened": NOW - timedelta(minutes=12),
        "last": NOW - timedelta(minutes=1),
        "drops_at": NOW + timedelta(minutes=29),
        "short": None,
        "since": NOW - timedelta(days=7),
        "until": NOW - timedelta(minutes=12),
        "told_to_skip": 3,
        "held": 1,
        "handed": Counts(retry=1, waiting=0),
    }
    return OpenRun(**(base | fields))


def marked(hours: float, **fields: Any) -> cursors.Cursor:
    return cursors.Cursor(mark=NOW - timedelta(hours=hours), **fields)


def handed(
    own: cursors.Cursors, others: dict[str, cursors.Cursors], *, final: bool = False, held: frozenset[str] = frozenset()
) -> cursors.Handover:
    """What run_open would hand a run of memo now, by its own rules."""
    theirs = {m: found["memo"] for m, found in others.items() if "memo" in found}
    return cursors.handover(own.get("memo"), theirs, NOW, held, lambda _ids: set(), final=final)


# -- the URL -----------------------------------------------------------------------------------------------------------


def test_the_url_reads_and_writes_back_one_canonical_form() -> None:
    state = SyncState.read(QueryParams("k=40&run=" + RUN + "&source=memo&utm=x"))
    assert (state.source, state.run, state.k) == ("memo", RUN, 40)
    assert state.url() == f"/sync?source=memo&run={RUN}&k=40"
    assert SyncState.read(QueryParams("source=&k=")).url() == "/sync"
    assert SyncState(source="memo").url(sync_view.RUNS) == "/sync/runs?source=memo"
    assert sync_view.canonical("http://127.0.0.1:8750/sync?k=40&source=memo") == "/sync?source=memo&k=40"
    assert sync_view.canonical("http://127.0.0.1:8750/search?query=x") is None
    assert sync_view.canonical("http://127.0.0.1:8750/sync?k=0") is None


@pytest.mark.parametrize(
    ("query", "error"),
    [
        ("source=Not A Name", "source is a source's name"),
        ("run=run-1", "run is a run's id"),
        ("k=0", "k is a whole number from 1 to 500"),
        ("k=501", "k is a whole number"),
        ("k=x", "k is a whole number"),
        ("k=²", "k is a whole number"),
    ],
)
def test_a_value_the_url_cant_hold_names_its_parameter(query: str, error: str) -> None:
    with pytest.raises(SyncStateError, match=error):
        SyncState.read(QueryParams(query))


def test_ages_sizes_and_durations_read_plainly() -> None:
    assert [sync_view.ago(NOW - d, NOW) for d in (timedelta(seconds=5), timedelta(minutes=4), timedelta(hours=26))] == [
        "just now",
        "4 min ago",
        "26 h ago",
    ]
    assert sync_view.ago(NOW - timedelta(days=3), NOW) == "3 days ago"
    assert sync_view.ago(NOW + timedelta(minutes=5), NOW) == "just now"
    assert [
        sync_view.took(NOW, NOW + d) for d in (timedelta(seconds=40), timedelta(minutes=3), timedelta(hours=2))
    ] == [
        "40 s",
        "3 min",
        "2 h 0 min",
    ]
    assert (sync_view.kb(0), sync_view.kb(1), sync_view.kb(187_392)) == ("0 KB", "1 KB", "183 KB")


# -- what needs a session (DR-0304) ------------------------------------------------------------------------------------


def test_a_source_is_due_at_a_day_and_not_before() -> None:
    fresh, _ = sync_view.needs(facts(own={"memo": marked(23)}), None)
    assert fresh == []
    stale, paste_all = sync_view.needs(facts(own={"memo": marked(26)}), None)
    assert [(n.source, n.kind, n.line) for n in stale] == [("memo", "due", "due: last synced 26 h ago, on laptop-1")]
    assert stale[0].paste == 'Sync memo with Docket: run_open("memo") and follow its steps.'
    assert paste_all is None
    never, _ = sync_view.needs(facts(), None)
    assert never[0].line == "due: never synced"


def test_another_machines_newer_mark_counts_and_one_ahead_of_this_clock_doesnt() -> None:
    other = {"desktop-2": {"memo": marked(2)}}
    assert sync_view.needs(facts(own={"memo": marked(30)}, others=other), None)[0] == []
    ahead = {"desktop-2": {"memo": marked(-5)}}
    [due], _ = sync_view.needs(facts(own={"memo": marked(30)}, others=ahead), None)
    assert due.line == "due: last synced 30 h ago, on laptop-1"


def test_what_needs_you_comes_first_and_a_running_source_isnt_due() -> None:
    sources = [
        SourceFacts("memo", enabled=True, live=True, refused="memo/SKILL.md is from before live runs: copy ..."),
        SourceFacts("chat", enabled=True, live=True),
        SourceFacts("mail", enabled=True, live=True),
        SourceFacts("notes", enabled=False, live=True),
        SourceFacts("files", enabled=True, live=False),
    ]
    found, paste_all = sync_view.needs(facts(sources=sources, open_runs=[an_open_run(source="mail")]), None)
    assert [(n.source, n.kind) for n in found] == [("memo", "you"), ("chat", "due")]
    assert found[0].paste is None
    assert paste_all is None
    only, _ = sync_view.needs(facts(sources=sources), "chat")
    assert [n.source for n in only] == ["chat"]


def test_a_final_run_due_is_its_own_need_or_rides_on_a_due_sync() -> None:
    owed = cursors.Handover(
        waiting={"w1": cursors.Waiting("w1", "summary", TODAY - timedelta(days=9))},
        retry={"r1": cursors.Retry("r1", "gone", TODAY - timedelta(days=8))},
    )
    sources = [SourceFacts("memo", enabled=True, live=True, final=owed)]
    [final], _ = sync_view.needs(facts(sources=sources, own={"memo": marked(1)}), None)
    assert (final.kind, final.line) == ("final", "2 ids due a final run, the oldest since 6 Oct")
    assert final.paste == 'Run Docket\'s final run for memo: run_open("memo", final=true).'
    [both], _ = sync_view.needs(facts(sources=sources, own={"memo": marked(30)}), None)
    assert both.kind == "due"
    assert both.line == "due: last synced 30 h ago, on laptop-1; 2 ids due a final run, the oldest since 6 Oct"
    assert both.paste is not None
    assert "final" not in both.paste


def test_several_sources_due_get_one_line_to_paste() -> None:
    sources = [SourceFacts(n, enabled=True, live=True) for n in ("chat", "memo")]
    found, paste_all = sync_view.needs(facts(sources=sources), None)
    assert len(found) == 2
    assert paste_all == (
        'Sync chat, memo with Docket, one source at a time: run_open("chat"), then run_open("memo"), following each '
        "one's steps."
    )


# -- failures (DR-0305) ------------------------------------------------------------------------------------------------


def test_every_kind_has_what_to_do() -> None:
    kinds = {*history.DROPS, "cursor", "rate-limited", "too-large", "gone", "retry", "expired", "held-in-raw"}
    assert kinds | {"own-cursor", "other-cursor"} == set(sync_view.ADVICE)
    assert set(history.DROPS) | {history.CLOSED} == set(sync_view.ENDED)


def test_a_drop_is_a_failure_until_a_later_whole_close_of_its_source() -> None:
    lines = [
        a_line("run-20261015T070000-00000000000000a1", kind=history.COUNT, ended=NOW - timedelta(hours=3), why="c1"),
        a_line("run-20261015T080000-00000000000000a2", ended=NOW - timedelta(hours=2)),
        a_line("run-20261015T083000-00000000000000a3", kind=history.MAPPER, ended=NOW - timedelta(hours=1), why="m1"),
        a_line("run-20261015T084000-00000000000000a4", kind=history.MAPPER, ended=NOW - timedelta(minutes=30)),
        a_line("run-20261015T085000-00000000000000a5", "chat", kind=history.IDLE, ended=NOW - timedelta(minutes=5)),
    ]
    found = sync_view.failures(facts(lines=list(reversed(lines))), None)
    # The count drop came before the whole close at 07:12, so it is no longer a failure.
    assert [(f.kind, f.source, f.what, f.run, f.severity) for f in found] == [
        (history.IDLE, "chat", "idle", lines[4].run, "warn"),
        (history.MAPPER, "memo", "2 runs; the newest: mapper", lines[3].run, "error"),
    ]
    assert found[1].todo == sync_view.ADVICE[history.MAPPER]
    assert found[1].when == lines[3].ended
    assert [f.kind for f in sync_view.failures(facts(lines=lines), "chat")] == [history.IDLE]


def test_a_cursor_problem_stays_until_a_clean_close() -> None:
    problem = a_line(
        "run-20261015T070000-00000000000000b1",
        ended=NOW - timedelta(hours=3),
        cursor_problem="a lock; the mark didn't move, and the next run fetches the window again",
    )
    [found] = sync_view.failures(facts(lines=[problem]), None)
    assert (found.kind, found.what, found.todo) == ("cursor", problem.cursor_problem, sync_view.ADVICE["cursor"])
    clean = a_line("run-20261015T080000-00000000000000b2", ended=NOW - timedelta(hours=1))
    assert sync_view.failures(facts(lines=[clean, problem]), None) == []


def test_expiries_and_held_ids_in_raw_stay_a_week() -> None:
    recent = a_line(
        ended=NOW - timedelta(days=6),
        expired=["x1", "x2"],
        expired_count=2,
        held_in_raw=["h1"],
        held_in_raw_count=1,
    )
    old = a_line("run-20261001T080000-00000000000000c1", ended=NOW - timedelta(days=8), expired=["x9"], expired_count=1)
    found = sync_view.failures(facts(lines=[recent, old]), None)
    assert [(f.kind, f.what, f.ids) for f in found] == [
        ("expired", "2 ids expired at this close", ["x1", "x2"]),
        ("held-in-raw", "1 held id already in raw", ["h1"]),
    ]
    single = a_line(ended=NOW, expired=["x3"], expired_count=1, held_in_raw=["h1", "h2"], held_in_raw_count=2)
    assert [f.what for f in sync_view.failures(facts(lines=[single]), None)] == [
        "1 id expired at this close",
        "2 held ids already in raw",
    ]


def test_retry_entries_other_than_later_are_failures_with_the_day_they_leave() -> None:
    own = {
        "memo": cursors.Cursor(
            retry={
                "r1": cursors.Retry("r1", "rate-limited", TODAY - timedelta(days=2)),
                "r2": cursors.Retry("r2", "later", TODAY - timedelta(days=40)),
                "r3": cursors.Retry("r3", "odd-reason", TODAY),
                # Past its month, and held: run_open hands neither over, so neither is a failure.
                "r7": cursors.Retry("r7", "gone", TODAY - timedelta(days=40)),
                "r8": cursors.Retry("r8", "rate-limited", TODAY),
            }
        )
    }
    others = {
        "desktop-2": {"memo": marked(24, retry={"r4": cursors.Retry("r4", "rate-limited", TODAY)})},
        # A retired machine: its mark is over 30 days old, so its file asks for nothing.
        "old-box": {"memo": marked(24 * 40, retry={"r6": cursors.Retry("r6", "rate-limited", TODAY)})},
    }
    sources = [SourceFacts("memo", enabled=True, live=True, owed=handed(own, others, held=frozenset({"r8"})))]
    found = sync_view.failures(facts(own=own, others=others, sources=sources), None)
    assert [(f.kind, f.what, f.ids, f.todo) for f in found] == [
        (
            "odd-reason",
            "1 id in retry since 15 Oct, on laptop-1; the oldest leaves on 14 Nov",
            ["r3"],
            sync_view.ADVICE["retry"],
        ),
        (
            "rate-limited",
            "2 ids in retry since 13 Oct, on desktop-2, laptop-1; the oldest leaves on 12 Nov",
            ["r1", "r4"],
            sync_view.ADVICE["rate-limited"],
        ),
    ]
    assert sync_view.failures(facts(own=own, sources=sources), "chat") == []


def test_cursor_files_that_dont_read_are_failures() -> None:
    found = sync_view.failures(
        facts(own_problem="state/cursors-laptop-1.yaml: line 2: an unknown key", others_notes=["the other one"]), None
    )
    assert [(f.kind, f.severity) for f in found] == [("own-cursor", "error"), ("other-cursor", "warn")]
    assert (
        found[0].what == "this machine's cursor file doesn't read: state/cursors-laptop-1.yaml: line 2: an unknown key"
    )


# -- the runs ----------------------------------------------------------------------------------------------------------


def test_an_open_run_says_how_far_it_got_and_when_it_drops() -> None:
    state = SyncState()
    card = sync_view._open_card(an_open_run(), NOW, TZ, state)  # pyright: ignore[reportPrivateUsage]
    assert (card.state, card.word, card.badges, card.took, card.why) == ("open", "open", [], "12 min", None)
    assert card.facts == [
        "2 captures, 3 KB of 400 KB",
        "last capture 1 min ago",
        "drops at 11:41 without a capture",
        "window 8 Oct 11:12 to 15 Oct 11:00",
        "told to skip 3, held 1, handed 1 to retry and 0 waiting",
    ]
    assert card.link == "/sync?run=run-20261015T090000-00000000000000aa"
    unbound = sync_view._open_card(  # pyright: ignore[reportPrivateUsage]
        an_open_run(bound=False, captures=1, last=None, drops_at=NOW + timedelta(seconds=30), since=None, final=True),
        NOW,
        TZ,
        state,
    )
    assert unbound.badges == ["final", "not bound yet"]
    assert unbound.facts[:2] == [
        "1 capture, 3 KB of 400 KB",
        "drops at 11:12:30 unless the capture hook ties it to its session",
    ]
    closing = sync_view._open_card(  # pyright: ignore[reportPrivateUsage]
        an_open_run(closing=True, drops_at=None, short="a body past the cap", backfill=True), NOW, TZ, state
    )
    assert closing.badges == ["backfill", "closing"]
    assert closing.why == "short: a body past the cap; its close will drop it"
    assert not any(f.startswith("drops at") for f in closing.facts)


def test_a_closed_run_shows_its_counts_and_a_dropped_one_its_reason() -> None:
    whole = a_line(
        captures=12,
        bytes=187_392,
        since=NOW - timedelta(days=7),
        until=NOW - timedelta(minutes=3),
        skipped={"later": 2, "rate-limited": 1},
        staged=Staged(pending=3, skipped=6, on_hold=1),
        dropped={"never-store": 1},
        set_aside=[SetAside(id="s1", reason="no-text"), SetAside(id=None, reason="empty")],
        set_aside_count=2,
        waiting=["w1"],
        waiting_count=1,
        mark=Mark(before=None, after=NOW - timedelta(minutes=3), moved=True),
        owed=Counts(retry=1, waiting=1),
        expired=["x1"],
        expired_count=21,
        held_in_raw=["h1"],
        held_in_raw_count=1,
        final=True,
    )
    card = sync_view._line_card(whole, TZ, SyncState(run=RUN))  # pyright: ignore[reportPrivateUsage]
    assert (card.state, card.word, card.badges, card.took, card.focused) == (
        "closed",
        "closed",
        ["final"],
        "3 min",
        True,
    )
    assert card.facts == [
        "12 captures, 183 KB",
        "staged 3 pending, 0 ready; 6 already staged or in raw",
        "on hold 1",
        "dropped at intake: never-store 1",
        "set aside 2, waiting 1",
        "mark moved to 15 Oct 11:09",
        "owes 1 to retry, 1 waiting",
        "expired 21",
        "skipped: later 2, rate-limited 1",
        "window 8 Oct 11:12 to 15 Oct 11:09",
    ]
    assert [(lst.title, lst.ids, lst.more) for lst in card.lists] == [
        ("set aside", ["s1 (no-text)", "(no id) (empty)"], 0),
        ("waiting", ["w1"], 0),
        ("expired", ["x1"], 20),
        ("held but in raw", ["h1"], 0),
    ]
    bare = sync_view._line_card(a_line(captures=1, bytes=10), TZ, SyncState())  # pyright: ignore[reportPrivateUsage]
    assert bare.facts == [
        "1 capture, 1 KB",
        "staged 0 pending, 0 ready; 0 already staged or in raw",
        "mark stays",
        "owes 0 to retry, 0 waiting",
    ]
    dropped = a_line(kind=history.COUNT, why="the manifest counts 5 capture(s) and 4 arrived", backfill=True)
    card = sync_view._line_card(dropped, TZ, SyncState())  # pyright: ignore[reportPrivateUsage]
    assert (card.state, card.word, card.why, card.lists, card.badges) == (
        "dropped",
        "count",
        dropped.why,
        [],
        ["backfill"],
    )


# -- the cursors -------------------------------------------------------------------------------------------------------


def test_each_machine_shows_its_mark_and_entries_with_ages_and_leaving_days() -> None:
    own = {
        "memo": cursors.Cursor(
            mark=NOW - timedelta(hours=26),
            retry={
                "r1": cursors.Retry("r1", "gone", TODAY - timedelta(days=8)),
                "r2": cursors.Retry("r2", "later", TODAY - timedelta(days=9)),
            },
            waiting={"w1": cursors.Waiting("w1", "summary", TODAY - timedelta(days=2))},
        )
    }
    others = {
        "desktop-2": {"memo": cursors.Cursor(mark=NOW - timedelta(days=29))},
        "old-box": {
            "memo": cursors.Cursor(
                mark=NOW - timedelta(days=30), retry={"r9": cursors.Retry("r9", "gone", TODAY - timedelta(days=10))}
            )
        },
        "fast-box": {"memo": cursors.Cursor(mark=NOW + timedelta(hours=2))},
        "nobody": {"chat": cursors.Cursor()},
    }
    sources = [SourceFacts("memo", enabled=True, live=True, final=handed(own, others, final=True))]
    [block] = sync_view.cursor_blocks(facts(own=own, others=others, sources=sources), ["memo"])
    assert block.window_since == NOW - timedelta(hours=26) - timedelta(days=7)
    assert block.notes == ["the mark of fast-box is ahead of this machine's clock, so it is left out"]
    this, desktop, fast, old = block.rows
    assert (this.machine, this.this, this.mark_age, this.lookback, this.handed) == (
        "laptop-1",
        True,
        "26 h ago",
        7,
        None,
    )
    assert [(e.id, e.why, e.age, e.leaves, e.final_due) for e in this.retry] == [
        ("r2", "later", "9 days", None, False),
        ("r1", "gone", "8 days", TODAY - timedelta(days=8) + cursors.EXPIRES, True),
    ]
    assert [(e.id, e.why, e.final_due) for e in this.waiting] == [("w1", "summary", False)]
    assert desktop.handed == "its sets are handed over"
    assert old.handed == "its sets aren't handed over: its mark is 30 days old or none"
    # A retired machine's entry is no final run's: run_open(final=true) wouldn't hand it over.
    assert [(e.id, e.final_due) for e in old.retry] == [("r9", False)]
    assert (fast.ahead, desktop.ahead) == (True, False)
    [empty] = sync_view.cursor_blocks(facts(others=others), ["chat"])
    assert [(r.machine, r.mark, r.mark_age) for r in empty.rows] == [("nobody", None, "")]
    broken = sync_view.cursor_blocks(facts(own_problem="line 2"), ["memo"])[0]
    assert (broken.problem, broken.window_since, broken.rows) == ("line 2", None, [])


def test_a_long_set_shows_its_first_entries_and_how_many_more(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_view, "SHOWN", 2)
    waiting = {f"w{n}": cursors.Waiting(f"w{n}", "summary", TODAY - timedelta(days=n)) for n in range(5)}
    [block] = sync_view.cursor_blocks(facts(own={"memo": cursors.Cursor(waiting=waiting)}), ["memo"])
    assert ([e.id for e in block.rows[0].waiting], block.rows[0].more) == (["w4", "w3"], 3)


# -- the whole page ----------------------------------------------------------------------------------------------------


def test_the_page_puts_its_parts_together() -> None:
    lines = [
        a_line(f"run-20261015T0{n}0000-00000000000000d{n}", ended=NOW - timedelta(hours=9 - n)) for n in range(1, 4)
    ]
    lines.reverse()
    open_run = an_open_run(source="chat")
    view = sync_view.build(
        facts(
            sources=[SourceFacts("memo", enabled=True, live=True), SourceFacts("chat", enabled=True, live=True)],
            lines=lines,
            open_runs=[open_run],
            own={"memo": marked(30)},
            others={"desktop-2": {"files": cursors.Cursor()}},
            history_problem="PermissionError",
        ),
        SyncState(k=2),
    )
    assert view.lead == "1 source due a sync; 1 run open; last whole run 6 h ago"
    assert [(c.label, c.url, c.on) for c in view.chips] == [
        ("All", "/sync?k=2", True),
        ("chat", "/sync?source=chat&k=2", False),
        ("files", "/sync?source=files&k=2", False),
        ("memo", "/sync?source=memo&k=2", False),
    ]
    assert [r.id for r in view.recent] == [lines[0].run, lines[1].run]
    assert view.more == "/sync?k=22"
    assert [r.id for r in view.open_runs] == [open_run.id]
    assert view.notes == ["Recent runs aren't kept across a restart: PermissionError"]
    assert (view.live, view.refresh, view.focus, view.as_of) == (True, "/sync/runs?k=2", None, NOW)
    assert view.live_sources == 2
    assert [b.source for b in view.cursors] == ["chat", "files", "memo"]


def test_a_run_in_the_url_opens_its_card_at_the_top() -> None:
    line = a_line()
    open_run = an_open_run()
    both = facts(lines=[line], open_runs=[open_run], own={"memo": marked(1)})
    view = sync_view.build(both, SyncState(run=RUN))
    assert view.focus is not None
    assert (view.focus.id, view.focus.focused, view.recent) == (RUN, True, [])
    view = sync_view.build(both, SyncState(run=open_run.id))
    assert view.focus is not None
    assert (view.focus.state, view.open_runs) == ("open", [])
    unknown = "run-20261015T000000-ffffffffffffffff"
    view = sync_view.build(both, SyncState(run=unknown))
    assert view.focus is None
    assert view.notes == [f"Run {unknown} isn't open, nor among the 500 the history keeps."]


def test_one_source_narrows_every_part_and_an_unknown_one_says_so() -> None:
    lines = [a_line(source="chat"), a_line("run-20261015T070000-00000000000000e1", ended=NOW - timedelta(hours=2))]
    view = sync_view.build(facts(lines=lines, open_runs=[an_open_run(source="chat")]), SyncState(source="memo"))
    assert [r.source for r in view.recent] == ["memo"]
    assert (view.open_runs, [b.source for b in view.cursors]) == ([], ["memo"])
    assert view.lead.startswith("1 source due a sync; 0 runs open")
    view = sync_view.build(facts(), SyncState(source="nothing"))
    assert view.notes == [
        "No source nothing on this page: vault.yaml lists no tools for it, and nothing synced it here."
    ]
    assert (view.cursors, view.needs) == ([], [])


def test_a_folder_source_is_named_as_one_read_by_docket_scan() -> None:
    sources = [SourceFacts("memo", enabled=True, live=True), SourceFacts("documents", enabled=True, live=False,
                                                                         folder=True)]  # fmt: skip
    view = sync_view.build(facts(sources=sources), SyncState())
    assert view.notes == ["Folder sources, read by docket scan and never synced here: documents."]
    view = sync_view.build(facts(sources=sources), SyncState(source="documents"))
    assert view.notes == [
        "documents is a folder source: docket scan documents reads its folder, so it never syncs here."
    ]
    assert view.needs == []


@pytest.mark.parametrize(
    ("sources", "lead"),
    [
        ([], "nothing needs a session; 0 runs open; no whole run here yet"),
        (
            [SourceFacts("memo", enabled=True, live=True, refused="no")] * 2,
            "2 need you first; 0 runs open; no whole run here yet",
        ),
        (
            [
                SourceFacts(
                    "memo",
                    enabled=True,
                    live=True,
                    final=cursors.Handover(waiting={"w": cursors.Waiting("w", "x", date(2026, 10, 1))}),
                )
            ],
            "1 due a final run; 0 runs open; no whole run here yet",
        ),
    ],
)
def test_the_lead_line_says_what_matters_first(sources: list[SourceFacts], lead: str) -> None:
    assert sync_view.build(facts(sources=sources, own={"memo": marked(1)}), SyncState()).lead == lead
