"""What the Staging page's tests share: a vault with a mixed `calls` source and a work `memo` source, records staged
through the gate, and a signed-in client of the service over it. Invented people only: Ada Example and Ben Sample; the
phone number is from the fictional +1 555 01xx range."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docket_testkit import empty_vault, make_config, make_record
from docket_webkit import OURS, PORT, TOKEN
from starlette.testclient import TestClient

from docket.service.app import create_app
from docket.service.sessions import COOKIE, Sessions
from docket.service.state import Settings
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

PHONE = "+1 555 0142"
# Two utterances overlap (Ben starts before Ada ends), and one has no length.
TALK: list[dict[str, Any]] = [
    {"start_ms": 0, "end_ms": 12_000, "speaker": "Ada Example", "text": "Morning, Ben."},
    {"start_ms": 12_000, "end_ms": 30_400, "speaker": "Ben Sample", "text": "First the weekend plans."},
    {"start_ms": 30_000, "end_ms": 45_700, "speaker": "Ada Example", "text": "The cabin by the lake, yes."},
    {"start_ms": 45_700, "end_ms": 45_700, "speaker": "Ben Sample", "text": "Right."},
    {"start_ms": 46_000, "end_ms": 60_000, "speaker": "Ben Sample", "text": "Now the offer: ten million."},
]
MAIL = "Hello Ben,\n\nThe offer stands at 10 million.\nCall me on the usual number.\nUntil Friday.\nAda"


def config() -> VaultConfig:
    made = make_config()
    made.sources["memo"] = SourceConfig()
    return made


def transcript(native: str = "rec-1", **fields: Any) -> RawRecord:
    base: dict[str, Any] = {
        "rid": f"calls:{native}/transcript",
        "source": "calls",
        "kind": "transcript",
        "container": f"calls:{native}",
        "title": "Supplier call",
        "segments": TALK,
        "ts": datetime(2026, 10, 7, 8, 0, tzinfo=UTC),
    }
    return make_record(1, **(base | fields))


def mail(native: str = "mail-1", **fields: Any) -> RawRecord:
    base: dict[str, Any] = {
        "rid": f"calls:{native}",
        "source": "calls",
        "kind": "mail",
        "container": "calls:inbox",
        "title": "The offer",
        "author": "ada@example.invalid",
        "text": MAIL,
        "ts": datetime(2026, 10, 8, 9, 0, tzinfo=UTC),
    }
    return make_record(2, **(base | fields))


def memo(native: str = "m-1", **fields: Any) -> RawRecord:
    base: dict[str, Any] = {
        "rid": f"memo:{native}",
        "source": "memo",
        "kind": "memo",
        "container": f"memo:{native}",
        "title": "Day rates",
        "text": "Northwind's day rate is 900.",
        "ts": datetime(2026, 10, 9, 7, 0, tzinfo=UTC),
    }
    return make_record(3, **(base | fields))


def staging_vault(tmp_path: Path, records: list[RawRecord] | None = None) -> Vault:
    vault = empty_vault(tmp_path, config())
    Gate(vault).stage([transcript(), mail(), memo()] if records is None else records)
    return vault


def key_of(vault: Vault, rid: str) -> str:
    return next(e.key for e in Gate(vault).list().entries if e.staged.record.rid == rid)


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
