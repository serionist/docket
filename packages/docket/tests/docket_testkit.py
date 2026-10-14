"""Builders the app's tests share: invented configs and records, and a command runner."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docket.vault.config import Owner, SourceConfig, VaultConfig
from docket.vault.records import RawRecord


def sh(cwd: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False)
    if check and proc.returncode != 0:
        raise AssertionError(f"{args}: {proc.stderr or proc.stdout}")
    return proc.stdout


def make_config(**owner: Any) -> VaultConfig:
    return VaultConfig(
        owner=Owner(
            name=owner.get("name", "Ada Example"),
            emails=owner.get("emails", ["ada@example.invalid"]),
            languages=["en", "hu"],
            time_zone="Europe/Budapest",
        ),
        primary_machine="test-box",
        sources={"chat": SourceConfig(), "calls": SourceConfig(sensitivity="mixed")},
    )


def make_record(n: int = 1, **fields: Any) -> RawRecord:
    base: dict[str, Any] = {
        "rid": f"chat:C0TEST/{1791000000 + n}.000100",
        "source": "chat",
        "kind": "message",
        "container": "chat:C0TEST",
        "ts": datetime(2026, 10, 14, 9, 30, n % 60, tzinfo=UTC),
        "author": "U0ADA",
        "text": f"message {n}",
        "fetched": datetime(2026, 10, 14, 12, 0, tzinfo=UTC),
        "via": "test",
    }
    base.update(fields)
    return RawRecord.model_validate(base)
