"""Each machine's cursor file (DR-0245) and the rules a run follows (DR-0246, DR-0247), on invented ids and times."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from docket_testkit import copy_vault, sh, template_vault

from docket.errors import DocketError
from docket.vault import cursors
from docket.vault.cursors import Close, Cursor, Handover, Retry, Waiting
from docket.vault.vault import Vault

NOW = datetime(2026, 10, 15, 9, 12, tzinfo=UTC)
TODAY = NOW.date()
A = "of_00c0ffee00c0ffee00c0ffee00c0ffee"
B = "of_00decaf000decaf000decaf000decaf0"
C = "of_00beef0000beef0000beef0000beef00"
GOOD = """\
# Where this machine's syncs reached. Docket writes it after each whole run; ids and times only.
schema_version: 1
sources:
  memo:
    mark: "2026-10-15T09:12:00Z"
    lookback_days: 7
    retry:
      - {id: "of_00c0ffee00c0ffee00c0ffee00c0ffee", reason: "later", since: "2026-10-14"}
    waiting:
      - {id: "of_00decaf000decaf000decaf000decaf0", for: "summary", since: "2026-10-13"}
"""


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_vault(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return copy_vault(template, tmp_path)


def days(n: int) -> date:
    return TODAY - timedelta(days=n)


# -- the file ----------------------------------------------------------------------------------------------------------


def test_the_file_reads_and_dumps_back_the_same() -> None:
    read = cursors.parse(GOOD, "f")
    memo = read["memo"]
    assert memo.mark == NOW
    assert memo.retry == {A: Retry(A, "later", date(2026, 10, 14))}
    assert memo.waiting == {B: Waiting(B, "summary", date(2026, 10, 13))}
    assert cursors.dump(read) == GOOD


def test_an_empty_file_and_one_with_no_sources_hold_no_cursor() -> None:
    assert cursors.parse("", "f") == {}
    assert cursors.parse("schema_version: 1\n", "f") == {}
    assert cursors.dump({}) == cursors.HEADER + "schema_version: 1\n"
    bare = cursors.dump({"memo": Cursor()})
    assert cursors.parse(bare, "f") == {"memo": Cursor()}


def test_an_id_with_a_quote_or_a_backslash_round_trips() -> None:
    odd = 'id"with\\odd'
    text = cursors.dump({"memo": Cursor(retry={odd: Retry(odd, "gone", TODAY)})})
    assert cursors.parse(text, "f")["memo"].retry[odd].id == odd


@pytest.mark.parametrize(
    ("text", "line", "why"),
    [
        ("schema_version: 2\n", 1, "schema_version is 1"),
        ("sources: {}\n", 1, "schema_version is 1"),
        ("schema_version: 1\ncolour: blue\n", 2, "an unknown key"),
        ("schema_version: 1\nschema_version: 1\n", 2, "a key given twice"),
        ("schema_version: 1\nsources:\n  memo:\n    mark: 2026-10-15T09:12:00Z\n", 4, "must be quoted"),
        ('schema_version: 1\nsources:\n  memo:\n    mark: "noon"\n', 4, "not a time"),
        ('schema_version: 1\nsources:\n  memo:\n    mark: "2026-10-15T09:12:00"\n', 4, "needs its zone"),
        ("schema_version: 1\nsources:\n  memo:\n    lookback_days: 0\n", 4, "lookback_days is 1 to"),
        ("schema_version: 1\nsources:\n  memo:\n    lookback_days: seven\n", 4, "lookback_days is expected"),
        ("schema_version: 1\nsources:\n  Memo:\n    lookback_days: 7\n", 4, "a source's name"),
        ("schema_version: 1\nsources:\n  memo: [1]\n", 3, "a map is expected"),
        ("schema_version: 1\nsources:\n  memo:\n    retry: 3\n", 4, "a list is expected"),
        ("schema_version: 1\nsources:\n  memo:\n    retry:\n      - {id: x}\n", 5, "id, reason and since"),
        ("schema_version: 1\nsources:\n  memo:\n    waiting:\n      - {id: x}\n", 5, "id, for and since"),
        (
            'schema_version: 1\nsources:\n  memo:\n    retry:\n      - {id: "a b", reason: "x", since: "2026-10-14"}\n',
            5,
            "not an id",
        ),
        (
            'schema_version: 1\nsources:\n  memo:\n    retry:\n      - {id: 12, reason: "x", since: "2026-10-14"}\n',
            5,
            "an id is expected",
        ),
        (
            "schema_version: 1\nsources:\n  memo:\n    retry:\n"
            '      - {id: "a", reason: "two words", since: "2026-10-14"}\n',
            5,
            "short token",
        ),
        (
            'schema_version: 1\nsources:\n  memo:\n    retry:\n      - {id: "a", reason: "x", since: "soon"}\n',
            5,
            "not a date",
        ),
        (
            'schema_version: 1\nsources:\n  memo:\n    retry:\n      - {id: [a], reason: x, since: "2026-10-14"}\n',
            5,
            "an id is expected",
        ),
        ("schema_version: 1\n? [a]\n: b\n", 2, "a key is expected"),
        ("- 1\n", 1, "a map is expected"),
    ],
)
def test_a_bad_file_fails_closed_by_line_number(text: str, line: int, why: str) -> None:
    with pytest.raises(DocketError) as e:
        cursors.parse(text, "state/cursors-test-box.yaml")
    assert f"state/cursors-test-box.yaml: line {line}" in str(e.value)
    assert why in str(e.value)


def test_broken_yaml_and_a_conflict_marker_are_named_by_line() -> None:
    with pytest.raises(DocketError, match="line 3 isn't valid YAML"):
        cursors.parse("schema_version: 1\nsources: [\n", "f")
    with pytest.raises(DocketError, match="isn't valid YAML"):
        cursors.parse("schema_version: 1\n\t\x00", "f")
    conflicted = GOOD.replace("    lookback_days: 7\n", "<<<<<<< HEAD\n    lookback_days: 7\n=======\n>>>>>>> other\n")
    with pytest.raises(DocketError, match="line 6 is a merge conflict's marker; keep one side"):
        cursors.parse(conflicted, "f")


def test_reading_this_machines_file_fails_closed_and_another_machines_is_left_out(vault: Vault) -> None:
    state = vault.root / "state"
    state.mkdir(exist_ok=True)
    assert cursors.read(vault.root, "test-box") == {}
    (state / "cursors-test-box.yaml").write_text("schema_version: 1\nbad: 1\n", encoding="utf-8")
    with pytest.raises(DocketError, match=r"state/cursors-test-box\.yaml: line 2"):
        cursors.read(vault.root, "test-box")
    (state / "cursors-test-box.yaml").write_bytes(b"\xff\xfe")
    with pytest.raises(DocketError, match="isn't UTF-8"):
        cursors.read(vault.root, "test-box")
    (state / "cursors-laptop.yaml").write_text(GOOD, encoding="utf-8")
    (state / "cursors-desktop.yaml").write_text("schema_version: 9\n", encoding="utf-8")
    (state / "notes.txt").write_text("not a cursor\n", encoding="utf-8")
    others, notes = cursors.read_others(vault.root, "test-box")
    assert set(others) == {"laptop"}
    assert notes == ["the cursor file of cursors-desktop.yaml doesn't read, so its marks and sets are left out"]
    with pytest.raises(DocketError, match="isn't a machine's name"):
        cursors.file_path(vault.root, "Bad Name")


def test_no_state_folder_means_no_other_machine() -> None:
    assert cursors.read_others(Path("no-such-vault"), "test-box") == ({}, [])


def _link(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        pytest.skip("this system can't make a symlink here")


def test_a_linked_file_or_folder_is_refused(vault: Vault, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text(GOOD, encoding="utf-8")
    (vault.root / "state").mkdir(exist_ok=True)
    _link(elsewhere, vault.root / "state" / "cursors-test-box.yaml")
    with pytest.raises(DocketError, match="is a link"):
        cursors.read(vault.root, "test-box")


def test_a_linked_state_folder_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    real = tmp_path / "real-state"
    real.mkdir()
    _link(real, root / "state")
    with pytest.raises(DocketError, match="state/ is a link"):
        cursors.read(root, "test-box")
    assert cursors.read_others(root, "test-box") == ({}, [])


def test_save_writes_through_a_rename_and_commits_alone_naming_no_id(vault: Vault) -> None:
    cursor = Cursor(mark=NOW, retry={A: Retry(A, "later", TODAY)})
    commit = cursors.save(vault, "memo", cursor)
    assert commit is not None
    assert sh(vault.root, "git", "log", "-1", "--format=%s").strip() == "Cursors: memo on test-box"
    assert A not in sh(vault.root, "git", "log", "-1", "--format=%B")
    assert sh(vault.root, "git", "show", "--name-only", "--format=", "HEAD").split() == ["state/cursors-test-box.yaml"]
    assert cursors.read(vault.root, "test-box") == {"memo": cursor}
    assert cursors.save(vault, "memo", cursor) is None
    # Another source keeps the first.
    cursors.save(vault, "chat", Cursor(mark=NOW))
    assert set(cursors.read(vault.root, "test-box")) == {"memo", "chat"}
    assert not list((vault.home.tmp_dir).glob("cursors-*"))


def test_save_commits_a_file_a_failed_commit_left_on_disk(vault: Vault) -> None:
    path = cursors.file_path(vault.root, "test-box")
    path.parent.mkdir(exist_ok=True)
    path.write_text(cursors.dump({"memo": Cursor(mark=NOW)}), encoding="utf-8")
    assert cursors.save(vault, "memo", Cursor(mark=NOW)) is not None
    assert sh(vault.root, "git", "status", "--porcelain") == ""


def test_two_machines_edits_to_different_sources_merge_cleanly(vault: Vault, tmp_path: Path) -> None:
    cursors.save(vault, "memo", Cursor(mark=NOW))
    other = tmp_path / "other"
    sh(tmp_path, "git", "clone", "-q", str(vault.root), str(other))
    sh(other, "git", "config", "user.name", "Ben Sample")
    sh(other, "git", "config", "user.email", "ben@example.invalid")
    theirs = cursors.dump({"memo": Cursor(mark=NOW), "chat": Cursor(mark=NOW)})
    (other / "state" / "cursors-test-box.yaml").write_text(theirs, encoding="utf-8")
    sh(other, "git", "commit", "-qam", "theirs")
    cursors.save(vault, "memo", Cursor(mark=NOW + timedelta(hours=1)))
    sh(
        vault.root,
        "git",
        "-c",
        "user.name=Ada Example",
        "-c",
        "user.email=ada@example.invalid",
        "pull",
        "-q",
        "--no-rebase",
        "--no-edit",
        str(other),
        "HEAD",
    )
    merged = cursors.read(vault.root, "test-box")
    assert merged["memo"].mark == NOW + timedelta(hours=1)
    assert merged["chat"].mark == NOW


# -- the window --------------------------------------------------------------------------------------------------------


def test_the_window_starts_at_the_furthest_mark_less_this_machines_lookback() -> None:
    own = Cursor(mark=NOW - timedelta(days=3), lookback_days=2)
    others = {"laptop": Cursor(mark=NOW - timedelta(days=1)), "desktop": Cursor()}
    found = cursors.window(own, others, NOW)
    assert found.since == NOW - timedelta(days=3)
    assert found.until == NOW
    assert found.default_since == found.since
    assert found.notes == []


def test_with_no_mark_the_window_is_the_lookback_from_now_and_since_starts_it_elsewhere() -> None:
    assert cursors.window(None, {}, NOW).since == NOW - timedelta(days=7)
    deeper = cursors.window(None, {}, NOW, since=NOW - timedelta(days=60))
    assert deeper.since == NOW - timedelta(days=60)
    assert deeper.default_since == NOW - timedelta(days=7)


def test_a_mark_ahead_of_this_clock_is_left_out_and_named() -> None:
    own = Cursor(mark=NOW + timedelta(hours=1))
    others = {"laptop": Cursor(mark=NOW + timedelta(days=2)), "desktop": Cursor(mark=NOW - timedelta(days=1))}
    found = cursors.window(own, others, NOW)
    assert found.since == NOW - timedelta(days=8)
    assert found.notes == [
        "the mark of this machine is ahead of this machine's clock, so it is left out",
        "the mark of laptop is ahead of this machine's clock, so it is left out",
    ]


# -- what a run is handed ----------------------------------------------------------------------------------------------


def none_stored(natives: object) -> set[str]:
    return set()


def test_handover_gives_this_machines_sets_less_held_and_expired_ids() -> None:
    own = Cursor(
        retry={
            A: Retry(A, "later", days(90)),
            B: Retry(B, "gone", days(31)),
            C: Retry(C, "rate-limited", days(2)),
        },
        waiting={"held-1": Waiting("held-1", "summary", days(1)), "w1": Waiting("w1", "summary", days(1))},
    )
    handed = cursors.handover(own, {}, NOW, frozenset({"held-1"}), none_stored)
    assert set(handed.retry) == {A, C}
    assert set(handed.waiting) == {"w1"}


def test_another_machines_entries_come_while_its_mark_is_under_a_month_old() -> None:
    laptop = Cursor(
        mark=NOW - timedelta(days=29),
        retry={"r-stored": Retry("r-stored", "later", days(1)), "r-new": Retry("r-new", "later", days(1))},
        waiting={
            "w-theirs": Waiting("w-theirs", "summary", days(1)),
            "w-new": Waiting("w-new", "summary", days(1)),
            "held-2": Waiting("held-2", "x", days(1)),
        },
    )
    retired = Cursor(mark=NOW - timedelta(days=30), waiting={"w-old": Waiting("w-old", "summary", days(40))})
    unmarked = Cursor(waiting={"w-none": Waiting("w-none", "summary", days(1))})
    own = Cursor(waiting={"w-theirs": Waiting("w-theirs", "transcript", days(3))})
    asked: list[set[str]] = []

    def stored(natives: object) -> set[str]:
        wanted = set(natives)  # pyright: ignore[reportArgumentType]
        asked.append(wanted)
        return {"r-stored"} & wanted

    handed = cursors.handover(
        own, {"laptop": laptop, "retired": retired, "unmarked": unmarked}, NOW, frozenset({"held-2"}), stored
    )
    assert set(handed.retry) == {"r-new"}
    assert handed.waiting == {
        "w-theirs": Waiting("w-theirs", "transcript", days(3)),
        "w-new": Waiting("w-new", "summary", days(1)),
    }
    assert asked == [{"r-stored", "r-new"}]
    # A new laptop with no cursor of its own picks up what the other one left.
    fresh = cursors.handover(None, {"laptop": laptop}, NOW, frozenset(), none_stored)
    assert set(fresh.waiting) == {"w-theirs", "w-new", "held-2"}


def test_a_final_run_is_handed_only_what_waited_a_week() -> None:
    own = Cursor(
        retry={
            "later-old": Retry("later-old", "later", days(20)),
            "gone-old": Retry("gone-old", "gone", days(8)),
            "fresh": Retry("fresh", "rate-limited", days(2)),
        },
        waiting={"week": Waiting("week", "summary", days(7)), "new": Waiting("new", "summary", days(6))},
    )
    handed = cursors.handover(own, {}, NOW, frozenset(), none_stored, final=True)
    assert set(handed.retry) == {"gone-old"}
    assert set(handed.waiting) == {"week"}


# -- a close -----------------------------------------------------------------------------------------------------------


def facts(**changes: object) -> Close:
    base: dict[str, object] = {
        "opened": NOW,
        "window_since": NOW - timedelta(days=7),
        "default_since": NOW - timedelta(days=7),
        "final": False,
        "captures": 3,
        "kept": frozenset[str](),
        "handed": Handover(),
        "skipped": [],
        "records": frozenset[str](),
        "waiting": [],
        "set_aside": frozenset[str](),
        "held": frozenset[str](),
    }
    base.update(changes)
    return Close(**base)  # pyright: ignore[reportArgumentType]


def test_a_whole_close_moves_the_mark_to_its_open_time() -> None:
    closed = cursors.close(Cursor(mark=NOW - timedelta(days=1)), facts(), TODAY)
    assert closed.moved
    assert closed.cursor.mark == NOW


@pytest.mark.parametrize(
    "changes",
    [
        {"captures": 0},
        {"final": True},
        {"window_since": NOW - timedelta(days=2)},
        {"window_since": None},
    ],
)
def test_the_mark_stays_on_no_captures_a_final_close_or_a_narrower_window(changes: dict[str, object]) -> None:
    before = NOW - timedelta(days=1)
    closed = cursors.close(Cursor(mark=before, lookback_days=3), facts(**changes), TODAY)
    assert not closed.moved
    assert closed.cursor.mark == before
    assert closed.cursor.lookback_days == 3


def test_retry_gains_skipped_ids_and_a_re_skip_takes_the_newer_reason() -> None:
    old = Cursor(retry={A: Retry(A, "rate-limited", days(5))})
    handed = Handover(retry={C: Retry(C, "later", days(9))})
    closed = cursors.close(
        old,
        facts(
            skipped=[(A, "gone"), (B, "later"), (C, "later"), (None, "rate-limited"), ("held-3", "x")],
            handed=handed,
            held=frozenset({"held-3"}),
        ),
        TODAY,
    )
    assert closed.cursor.retry == {
        A: Retry(A, "gone", days(5)),
        B: Retry(B, "later", TODAY),
        C: Retry(C, "later", days(9)),
    }


def test_retry_loses_reached_ids_held_ids_and_expired_ones_but_never_later() -> None:
    old = Cursor(
        retry={
            "by-record": Retry("by-record", "later", days(1)),
            "by-waiting": Retry("by-waiting", "later", days(1)),
            "by-aside": Retry("by-aside", "later", days(1)),
            "held-4": Retry("held-4", "later", days(1)),
            "old-later": Retry("old-later", "later", days(200)),
            "old-gone": Retry("old-gone", "gone", days(30)),
            "skipped-again": Retry("skipped-again", "later", days(2)),
        }
    )
    closed = cursors.close(
        old,
        facts(
            records=frozenset({"by-record/summary", "skipped-again"}),
            waiting=[("by-waiting", "summary"), ("skipped-again", "summary")],
            set_aside=frozenset({"by-aside"}),
            held=frozenset({"held-4"}),
            skipped=[("skipped-again", "later")],
        ),
        TODAY,
    )
    assert set(closed.cursor.retry) == {"old-later", "skipped-again"}
    assert closed.expired == ["old-gone"]


def test_waiting_keeps_an_id_reported_again_and_drops_one_that_got_its_record() -> None:
    old = Cursor(
        waiting={
            "partial": Waiting("partial", "summary", days(2)),
            "done": Waiting("done", "summary", days(2)),
            "untouched": Waiting("untouched", "summary", days(2)),
            "held-5": Waiting("held-5", "summary", days(2)),
        }
    )
    closed = cursors.close(
        old,
        facts(
            records=frozenset({"partial/transcript", "done", "done/summary"}),
            waiting=[("partial", "summary")],
            handed=Handover(waiting=dict(old.waiting)),
            held=frozenset({"held-5"}),
        ),
        TODAY,
    )
    assert closed.cursor.waiting == {
        "partial": Waiting("partial", "summary", days(2)),
        "untouched": Waiting("untouched", "summary", days(2)),
    }


def test_waiting_gains_reports_less_skipped_held_and_what_the_run_was_told_to_skip() -> None:
    handed = Handover(
        waiting={"theirs": Waiting("theirs", "summary", days(4)), "was-kept": Waiting("was-kept", "x", days(1))}
    )
    closed = cursors.close(
        None,
        facts(
            kept=frozenset({"kept-1", "kept-2/summary", "was-kept"}),
            handed=handed,
            waiting=[
                ("kept-1", "fetch"),
                ("kept-2", "summary"),
                ("was-kept", "summary"),
                ("skipped-1", "fetch"),
                ("held-6", "fetch"),
                ("theirs", "summary"),
                ("new", "fetch"),
            ],
            skipped=[("skipped-1", "later")],
            held=frozenset({"held-6"}),
        ),
        TODAY,
    )
    assert closed.cursor.waiting == {
        "was-kept": Waiting("was-kept", "summary", days(1)),
        "theirs": Waiting("theirs", "summary", days(4)),
        "new": Waiting("new", "fetch", TODAY),
    }
    assert set(closed.cursor.retry) == {"skipped-1"}


def test_a_final_close_removes_what_waited_a_week_whatever_it_gave() -> None:
    old = Cursor(
        waiting={"week": Waiting("week", "summary", days(8)), "young": Waiting("young", "summary", days(2))},
    )
    handed = Handover(waiting={"week": old.waiting["week"], "foreign": Waiting("foreign", "summary", days(9))})
    closed = cursors.close(
        old,
        facts(final=True, handed=handed, waiting=[("week", "summary"), ("foreign", "summary"), ("young", "summary")]),
        TODAY,
    )
    assert closed.cursor.waiting == {"young": Waiting("young", "summary", days(2))}
    assert closed.expired == ["week"]
    assert not closed.moved


def test_covers_matches_an_id_and_what_sits_under_it_only() -> None:
    assert cursors.covers("a", ["a"])
    assert cursors.covers("a", ["a/summary"])
    assert not cursors.covers("a", ["ab", "b/a"])


# -- code review, round 1 ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("native", ["of_￾", "of_￿", "of_\ud800", "a b", ""])
def test_an_id_yaml_cant_read_back_is_not_writable(native: str) -> None:
    assert not cursors.writable(native)


def test_a_writable_id_reads_back() -> None:
    odd = "of_é\U0001f600/summary"
    assert cursors.writable(odd)
    text = cursors.dump({"memo": Cursor(retry={odd: Retry(odd, "later", TODAY)})})
    assert cursors.parse(text, "f")["memo"].retry[odd].id == odd


def test_a_character_yaml_cant_hold_is_named_by_its_line() -> None:
    text = 'schema_version: 1\nsources:\n  memo:\n    retry:\n      - {id: "of_￾", reason: "x", since: "2026-10-14"}\n'
    with pytest.raises(DocketError, match="f: line 5 isn't valid YAML"):
        cursors.parse(text, "f")


def test_a_close_leaves_out_an_id_the_file_cant_hold() -> None:
    closed = cursors.close(None, facts(skipped=[("of_￾", "later"), (A, "later")], waiting=[("of_￿", "summary")]), TODAY)
    assert set(closed.cursor.retry) == {A}
    assert closed.cursor.waiting == {}
    assert cursors.parse(cursors.dump({"memo": closed.cursor}), "f")["memo"] == closed.cursor


def test_a_later_id_skipped_again_for_another_reason_starts_its_month_today() -> None:
    old = Cursor(retry={A: Retry(A, "later", days(35)), B: Retry(B, "later", days(35))})
    handed = Handover(retry={C: Retry(C, "later", days(40))})
    closed = cursors.close(old, facts(skipped=[(A, "rate-limited"), (B, "later"), (C, "gone")], handed=handed), TODAY)
    assert closed.cursor.retry == {
        A: Retry(A, "rate-limited", TODAY),
        B: Retry(B, "later", days(35)),
        C: Retry(C, "gone", TODAY),
    }
    assert closed.expired == []
