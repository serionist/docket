from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from docket.index.languages import resolve
from docket.index.timewords import read_time

TZ = ZoneInfo("Europe/Budapest")
# Wednesday 14 October 2026, 10:00 in Budapest.
NOW = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
BOTH = resolve(["en", "hu"]).languages


def local(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=TZ).astimezone(UTC)


@pytest.mark.parametrize(
    ("query", "since", "until"),
    [
        ("today", "2026-10-14", "2026-10-15"),
        ("ma", "2026-10-14", "2026-10-15"),
        ("yesterday", "2026-10-13", "2026-10-14"),
        ("tegnap", "2026-10-13", "2026-10-14"),
        ("this week", "2026-10-12", "2026-10-19"),
        ("ezen a héten", "2026-10-12", "2026-10-19"),
        ("last week", "2026-10-05", "2026-10-12"),
        ("múlt héten", "2026-10-05", "2026-10-12"),
        ("mult heten", "2026-10-05", "2026-10-12"),
        ("előző héten", "2026-10-05", "2026-10-12"),
        ("this month", "2026-10-01", "2026-11-01"),
        ("ebben a hónapban", "2026-10-01", "2026-11-01"),
        ("last month", "2026-09-01", "2026-10-01"),
        ("múlt hónapban", "2026-09-01", "2026-10-01"),
        ("this year", "2026-01-01", "2027-01-01"),
        ("idén", "2026-01-01", "2027-01-01"),
        ("last year", "2025-01-01", "2026-01-01"),
        ("tavaly", "2025-01-01", "2026-01-01"),
        ("2026-10-12", "2026-10-12", "2026-10-13"),
        ("2026-10", "2026-10-01", "2026-11-01"),
        ("2026-12", "2026-12-01", "2027-01-01"),
    ],
)
def test_each_time_word_is_a_range_in_the_owner_s_zone(query: str, since: str, until: str) -> None:
    reading = read_time(query, BOTH, NOW, TZ)
    assert reading.range is not None
    assert (reading.range.since, reading.range.until) == (local(since), local(until))
    assert reading.rest.strip() == ""


def test_last_n_days_counts_back_from_now() -> None:
    for query in ("last 3 days", "past 3 days", "az elmúlt 3 napban", "elmúlt 3 nap"):
        reading = read_time(query, BOTH, NOW, TZ)
        assert reading.range is not None
        assert (NOW - reading.range.since).days == 3 and reading.range.until == NOW


def test_a_week_across_the_clock_change_starts_at_local_midnight() -> None:
    # The clocks go back on Sunday 25 October 2026, so the week of 19 October lasts 169 hours.
    reading = read_time("last week", BOTH, datetime(2026, 10, 28, 9, 0, tzinfo=UTC), TZ)
    assert reading.range is not None
    assert reading.range.since == datetime(2026, 10, 18, 22, 0, tzinfo=UTC)
    assert reading.range.until == datetime(2026, 10, 25, 23, 0, tzinfo=UTC)


def test_last_month_and_last_week_across_a_year_boundary() -> None:
    now = datetime(2027, 1, 6, 9, 0, tzinfo=UTC)
    month = read_time("last month", BOTH, now, TZ).range
    week = read_time("last week", BOTH, now, TZ).range
    assert month is not None and (month.since, month.until) == (local("2026-12-01"), local("2027-01-01"))
    assert week is not None and (week.since, week.until) == (local("2026-12-28"), local("2027-01-04"))


def test_the_rest_keeps_other_words_and_the_words_that_set_the_range() -> None:
    reading = read_time("pallet labels múlt héten, at the harbour", BOTH, NOW, TZ)
    assert reading.words == ["múlt héten"]
    assert reading.rest.split() == ["pallet", "labels", ",", "at", "the", "harbour"]


def test_the_longest_phrase_wins_and_several_give_their_span() -> None:
    assert read_time("ezen a héten", BOTH, NOW, TZ).words == ["ezen a héten"]
    reading = read_time("yesterday and today", BOTH, NOW, TZ)
    assert reading.range is not None
    assert (reading.range.since, reading.range.until) == (local("2026-10-13"), local("2026-10-15"))


def test_a_language_outside_the_owner_s_has_no_time_words() -> None:
    reading = read_time("tegnap", resolve(["en"]).languages, NOW, TZ)
    assert reading.range is None and reading.rest.strip() == "tegnap"


def test_no_time_words_and_an_impossible_date() -> None:
    reading = read_time("pallet 2026-13-40 labels", BOTH, NOW, TZ)
    assert reading.range is None and reading.words == []
    assert "2026-13-40" in reading.rest
    assert read_time("last days", BOTH, NOW, TZ).range is None
    assert read_time("last few days", BOTH, NOW, TZ).range is None
