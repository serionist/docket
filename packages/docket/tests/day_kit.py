"""What the day pages' tests share: a vault whose ledger holds invented items, a meeting with its points and two
streams, written through the ledger's own writer as Claude via the CLI and dated from today, so every group of the
Ledger shows; the host repo's files the materials name; the meetings around it that Meetings' tests add; and a
signed-in client of the service over it. Invented people only: Ada Example and Ben Sample."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import copied, digest, empty_vault, indexed, make_config, read_only, shared
from docket_webkit import OURS, PORT, TOKEN, parse
from starlette.testclient import TestClient

from docket.ledger import store
from docket.service.app import create_app
from docket.service.sessions import COOKIE, Sessions
from docket.service.state import Settings
from docket.vault.config import VaultConfig
from docket.vault.vault import Vault

ZONE = ZoneInfo("Europe/Budapest")
SAME = {"Origin": OURS, "Sec-Fetch-Site": "same-origin"}
CHAT = "chat:C0TEST/1791000001.000100"
PLAN = "notes/plan.md"
SHEET = "notes/costs.xlsx"
LINK = "https://example.invalid/the-offer"


def today() -> date:
    return datetime.now(UTC).astimezone(ZONE).date()


def day(offset: int) -> str:
    return (today() + timedelta(days=offset)).isoformat()


def add(n: str, kind: str, title: str, who: str, stream: str, **fields: Any) -> dict[str, Any]:
    return {"op": "add", "as": n, "force": True, "fields": {"kind": kind, "title": title, "who": who,
                                                              "stream": stream, "source": CHAT, **fields}}  # fmt: skip


def seed() -> list[dict[str, Any]]:
    """Two streams; a meeting tomorrow with two points; seven items: overdue with a call, due today on the meeting,
    due this week, later, no due with materials, one done and one snoozed."""
    start = datetime.combine(today() + timedelta(days=1), datetime.min.time(), ZONE).replace(hour=10)
    end = start + timedelta(minutes=30)
    meeting = {"subject": "Pilot sync", "start": start.isoformat(), "end": end.isoformat(), "stream": "pilot"}
    quote = {"notes": "Asked twice.", "description": "The full quote with the menu"}
    return [
        {"op": "stream", "fields": {"name": "pilot", "status": "active"}},
        {"op": "stream", "fields": {"name": "ops", "status": "active"}},
        {"op": "meeting", "as": "sync", "fields": meeting},
        {"op": "point", "item": "$sync", "fields": {"text": "The venue's dates"}},
        {"op": "point", "item": "$sync", "fields": {"text": "Who books the room", "proposed": True}},
        add("dates", "reply", "Send Ada the dates", "Ada Example", "pilot", due=day(-2)),
        {"op": "triage", "item": "$dates", "verdict": "chase"},
        add("room", "task", "Book the room", "Ben Sample", "pilot", due=day(0), meeting="$sync"),
        add("venue", "decision", "Pick the venue", "Ada Example", "ops", due=day(3)),
        add("later", "task", "Plan the spring visit", "Ben Sample", "ops", due=day(30)),
        add("quote", "waiting", "Quote from the caterer", "Ben Sample", "ops", **quote),
        {"op": "material", "item": "$quote", "ref": PLAN, "label": "The plan"},
        {"op": "material", "item": "$quote", "ref": SHEET},
        {"op": "material", "item": "$quote", "ref": LINK, "label": "The offer"},
        add("old", "task", "Send the minutes", "Ada Example", "pilot"),
        {"op": "done", "item": "$old", "note": "sent in the thread"},
        add("nap", "task", "Renew the badge", "Ada Example", "ops"),
        {"op": "snooze", "item": "$nap", "until": day(5)},
    ]


RECORDING = "calls:rec-0100"
LONG = "Ask the venue whether the long hall can hold forty seats with the stage set up at the far end"


def at(days: int, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(today() + timedelta(days=days), datetime.min.time(), ZONE).replace(hour=hour, minute=minute)


def meeting(name: str, subject: str, days: int, hour: int, minutes: int = 30, **fields: Any) -> dict[str, Any]:
    start = at(days, hour)
    span = {"subject": subject, "start": start.isoformat(), "end": (start + timedelta(minutes=minutes)).isoformat()}
    return {"op": "meeting", "as": name, "fields": span | fields}


def meetings_week() -> list[dict[str, Any]]:
    """Meetings around seed()'s Pilot sync tomorrow, for Meetings' tests: a stand-up this morning with nothing on it,
    a budget review under way at noon, yesterday's supplier call with its recording and an open point, a cancelled and
    a declined one ahead, a retro twenty days back with a point still open, and one forty days back with none, which
    the window leaves out; and an item whose title is longer than a list shows."""
    return [
        meeting("standup", "Morning stand-up", 0, 9, 15),
        meeting(
            "budget",
            "Budget review",
            0,
            11,
            90,
            organiser="Ben Sample",
            event="cal:evt-budget",
            series="cal:series-budget",
            response="tentative",
            attendees=["Ben Sample", "Ada Example", "ben@northwind.example", "Dora Nobody"],
            where="Room 2",
            stream="ops",
        ),
        {"op": "point", "item": "$budget", "fields": {"text": "Ben: the spring numbers"}},
        meeting("supplier", "Supplier call", -1, 15, 60, stream="pilot"),
        {"op": "point", "item": "$supplier", "fields": {"text": "The offer in writing"}},
        {"op": "material", "item": "$supplier", "ref": RECORDING, "label": "recording"},
        {"op": "material", "item": "$supplier", "ref": "notes/plan.md", "label": "The plan"},
        meeting("off", "Cancelled sync", 2, 9, response="cancelled"),
        meeting("walk", "Venue walk", 3, 14, response="declined"),
        meeting("retro", "Spring retro", -20, 10),
        {"op": "point", "item": "$retro", "fields": {"text": "What the pilot taught us"}},
        meeting("ancient", "Kick-off", -40, 10),
        {
            "op": "add",
            "as": "long",
            "force": True,
            "fields": {"kind": "reply", "title": LONG, "who": "Ben Sample", "stream": "ops", "source": "chat:C0TEST/1"},
        },
    ]


def day_template(factory: pytest.TempPathFactory, config: VaultConfig | None = None) -> Path:
    """The day vault, built once per session and day: tests copy it, or share one copy that they only read."""
    made = config or make_config()
    return shared(lambda folder: _build_day_vault(folder, made), _day_key(made), factory)


def day_vault(tmp_path: Path, config: VaultConfig | None = None) -> Vault:
    """A copy of the day vault for this config."""
    made = config or make_config()
    return Vault.open(copied(tmp_path, lambda folder: _build_day_vault(folder, made), _day_key(made)))


@dataclass(frozen=True)
class OnTheDay:
    """A page's own vault: the day vault for this config with `then` run on it, built once per session and day as the
    day vault is. Tests copy it, or share one copy that they only read."""

    name: str
    then: Callable[[Vault], object]
    config: Callable[[], VaultConfig] = make_config

    def copy(self, tmp_path: Path) -> Vault:
        return Vault.open(copied(tmp_path, self._build, self._key()))

    def template(self, factory: pytest.TempPathFactory) -> Path:
        return shared(self._build, self._key(), factory)

    def _key(self) -> str:
        return f"{self.name}-{_day_key(self.config())}"

    def _build(self, folder: Path) -> Path:
        vault = day_vault(folder, self.config())
        self.then(vault)
        return vault.root


# The worker's read-only copy of each day's template, by the indexed template it copies.
_READ_TODAY: dict[Path, Path] = {}


def read_today(factory: pytest.TempPathFactory, template: Path) -> Vault:
    """The worker's one read-only copy, its index built, of a day template, which the tests that only read share: a
    write into it fails the run (DR-0531, DR-0779). Each test asks for it with the day's template, so a run that crosses
    midnight reads the next day's, as a test's own copy takes the next day's template."""
    made = indexed(factory, template)
    if made not in _READ_TODAY:
        _READ_TODAY[made] = read_only(made)
    return Vault.open(_READ_TODAY[made])


def _day_key(config: VaultConfig) -> str:
    # The seed is dated from today, so a run that crosses midnight builds the next day's.
    return f"day-vault-{today().isoformat()}-" + digest(config.dump())


def _build_day_vault(folder: Path, config: VaultConfig) -> Path:
    vault = empty_vault(folder, config)
    store.write(vault, seed(), by="claude", via="cli")
    notes = vault.home.top / "notes"
    notes.mkdir(exist_ok=True)
    (notes / "plan.md").write_text("# The plan\n\nBook the room first.\n", encoding="utf-8")
    (notes / "costs.xlsx").write_bytes(b"PK\x03\x04 invented")
    return vault.root


@contextmanager
def signed_in(vault: Vault, sessions: Sessions | None = None) -> Iterator[TestClient]:
    """A client of the service over this vault, holding a session cookie."""
    sessions = sessions or Sessions()
    app = create_app(Settings(port=PORT, vault=vault.root, token=lambda: TOKEN), sessions=sessions)
    with TestClient(app, base_url=OURS, follow_redirects=False) as client:
        started = sessions.redeem(sessions.new_code())
        assert started is not None
        client.cookies.set(COOKIE, started)
        yield client


def hidden(html: str, selector_class: str, n: int = 0) -> dict[str, str]:
    """The hidden fields of the nth form with this class, as the browser posts them."""
    found = parse(html).find_all("form", class_=selector_class)[n]
    return {i.attrs["name"]: i.attrs["value"] for i in found.find_all("input") if i.attrs.get("type") == "hidden"}
