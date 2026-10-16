"""Time words in a query, such as "last week" or "múlt héten", turned into a range in the owner's time zone.

The phrases come from a table per language (`languages.py`), used for the owner's languages, and match on folded words,
so `mult heten` typed without accents works. Weeks start on Monday. ISO dates (`2026-10-12`, `2026-10`) work in any
language. Several time words give the span covering them all.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from docket.index.languages import Language, TimeWord
from docket.plaintext import WORD, fold, nfc

ISO = re.compile(r"(?<![\w-])(\d{4})-(\d{2})(?:-(\d{2}))?(?![\w-])")


@dataclass(frozen=True)
class Range:
    """From `since` up to, not including, `until`; both in UTC."""

    since: datetime
    until: datetime


@dataclass(frozen=True)
class TimeReading:
    range: Range | None
    words: list[str]
    rest: str


def _midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(UTC)


def _month(year: int, month: int) -> date:
    return date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def day_range(day: date, tz: ZoneInfo) -> Range:
    return Range(_midnight(day, tz), _midnight(day + timedelta(days=1), tz))


def span_range(span: str, n: int, now: datetime, tz: ZoneInfo) -> Range:
    today = now.astimezone(tz).date()
    monday = today - timedelta(days=today.weekday())
    if span == "today":
        return day_range(today, tz)
    if span == "yesterday":
        return day_range(today - timedelta(days=1), tz)
    if span == "this-week":
        return Range(_midnight(monday, tz), _midnight(monday + timedelta(days=7), tz))
    if span == "last-week":
        return Range(_midnight(monday - timedelta(days=7), tz), _midnight(monday, tz))
    if span == "this-month":
        return Range(_midnight(_month(today.year, today.month), tz), _midnight(_month(today.year, today.month + 1), tz))
    if span == "last-month":
        return Range(_midnight(_month(today.year, today.month - 1), tz), _midnight(_month(today.year, today.month), tz))
    if span == "this-year":
        return Range(_midnight(date(today.year, 1, 1), tz), _midnight(date(today.year + 1, 1, 1), tz))
    if span == "last-year":
        return Range(_midnight(date(today.year - 1, 1, 1), tz), _midnight(date(today.year, 1, 1), tz))
    return Range(now.astimezone(UTC) - timedelta(days=n), now.astimezone(UTC))


def _match(phrase: TimeWord, folded: list[str], i: int) -> int | None:
    """The number a phrase matched at position i (0 when it has none), or None when it doesn't match there."""
    if i + len(phrase.words) > len(folded):
        return None
    number = 0
    for want, got in zip(phrase.words, folded[i : i + len(phrase.words)], strict=False):
        if want == "#":
            if not got.isdigit():
                return None
            number = int(got)
        elif want != got:
            return None
    return number


def read_time(text: str, languages: Sequence[Language], now: datetime, tz: ZoneInfo) -> TimeReading:
    """The range a query's time words set, the words that set it, and the query without them."""
    ranges: list[Range] = []
    said: list[str] = []
    for m in ISO.finditer(text):
        year, month = int(m.group(1)), int(m.group(2))
        try:
            if m.group(3):
                ranges.append(day_range(date(year, month, int(m.group(3))), tz))
            else:
                ranges.append(Range(_midnight(date(year, month, 1), tz), _midnight(_month(year, month + 1), tz)))
        except ValueError:
            continue
        said.append(m.group(0))
    text = ISO.sub(lambda m: " " if m.group(0) in said else m.group(0), text)
    found = list(WORD.finditer(nfc(text)))
    folded = [fold(m.group(0)) for m in found]
    phrases = sorted((p for lang in languages for p in lang.time_words), key=lambda p: -len(p.words))
    keep = [True] * len(found)
    i = 0
    while i < len(found):
        for phrase in phrases:
            number = _match(phrase, folded, i)
            if number is not None:
                ranges.append(span_range(phrase.span, number, now, tz))
                said.append(" ".join(m.group(0) for m in found[i : i + len(phrase.words)]))
                keep[i : i + len(phrase.words)] = [False] * len(phrase.words)
                i += len(phrase.words)
                break
        else:
            i += 1
    rest = nfc(text)
    for m, kept in reversed(list(zip(found, keep, strict=True))):
        if not kept:
            rest = rest[: m.start()] + " " + rest[m.end() :]
    span = Range(min(r.since for r in ranges), max(r.until for r in ranges)) if ranges else None
    return TimeReading(span, said, rest)
