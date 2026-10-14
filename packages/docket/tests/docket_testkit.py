"""Builders the app's tests share: invented configs and records, a fixture vault, docs, and a command runner."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from docket.vault.config import Owner, SourceConfig, VaultConfig
from docket.vault.records import RawRecord, version
from docket.vault.vault import Vault

# The fixture vault's records: a call over an hour long, a recording in a vendor's own shape, a chat thread, two
# side messages, and a mail whose rid holds @, % and ].
REC = "calls:rec-0042"
VENDOR = "vendor:rec-0007"
THREAD = [f"chat:C0TEST/{1791000000 + n}.000100" for n in (1, 2, 3)]
SIDE = [f"chat:C0TEST/{1791000000 + n}.000100" for n in (4, 5)]
MAIL = "mail:<50%off]x@example.invalid>"
CALL_SEGMENTS: list[dict[str, Any]] = [
    {"start_ms": 0, "end_ms": 4000, "speaker": "Ben Sample", "text": "Morning, Cleo."},
    {"start_ms": 3000, "end_ms": 9000, "speaker": "Cleo Test", "text": "Morning. Shall we start with the consultant?"},
    # Nobody speaks from 0:09 to 0:20.
    {"start_ms": 20000, "end_ms": 35000, "speaker": "Ben Sample", "text": "The consultant is 10 million forints."},
    {"start_ms": 728000, "end_ms": 740500, "speaker": "Ben Sample", "text": "So ten million, for the whole quarter."},
    {
        "start_ms": 870000,
        "end_ms": 892000,
        "speaker": "Cleo Test",
        "text": "Please send the offer in writing by Friday.",
    },
    # The last to start isn't the last to end: the call runs to 1:02:10.
    {"start_ms": 3690000, "end_ms": 3730000, "speaker": "Cleo Test", "text": "Thanks both, talking over the goodbyes."},
    {"start_ms": 3700000, "end_ms": 3725000, "speaker": "Ben Sample", "text": "Bye for now."},
]
MAIL_TEXT = "Hello Ben,\r\n\r\nThe offer stands at 10 million.\r\nUntil Friday.\r\n \N{NO-BREAK SPACE}\r\nCleo\r\n"


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


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 14, hour, minute, tzinfo=UTC)


def fixture_runs() -> list[tuple[str, str, list[RawRecord]]]:
    """The fixture's raw log as (source, run, records): one run per source at 10:00, and an edit at 11:00."""
    call = make_record(
        rid=REC, source="calls", kind="recording", container=None, title="Supplier call", text="Supplier call",
        segments=CALL_SEGMENTS,
    )  # fmt: skip
    # A recording whose transcript starts a second and a half in: a vendor's own shape, mapped to the core's.
    utterances = [{"t": 1.5, "w": "hello"}, {"t": 4.0, "w": "the price is fine"}]
    mapped = [{"start_ms": int(u["t"] * 1000), "end_ms": int(u["t"] * 1000) + 500, "text": u["w"]} for u in utterances]
    vendor = make_record(
        rid=VENDOR, source="vendor", kind="recording", container=None, segments=mapped, native={"utt": utterances}
    )
    thread = [make_record(n, thread=THREAD[0], text=f"thread message {n}") for n in (1, 2, 3)]
    side = [make_record(n, container="chat:C0SIDE", text=f"side message {n}") for n in (4, 5)]
    mail = make_record(
        rid=MAIL, source="mail", kind="mail", container="mail:thread-1", title="The offer",
        author="cleo@example.invalid", text=MAIL_TEXT,
    )  # fmt: skip
    edited = make_record(3, thread=THREAD[0], text="thread message 3, edited")
    first = "2026-10-14T1000-test-box"
    return [
        ("calls", first, [call]),
        ("vendor", first, [vendor]),
        ("chat", first, [*thread, *side]),
        ("mail", first, [mail]),
        ("chat", "2026-10-14T1100-test-box", [edited]),
    ]


def write_raw(root: Path) -> None:
    """The fixture's run files under root/raw, written directly: no git, no lock, so a module can share them."""
    for source, run, records in fixture_runs():
        path = root / "raw" / source / "2026" / "10" / f"{run}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [r.model_copy(update={"v": version(r), "run": run}).to_json() for r in records]
        path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8", newline="\n")


def fixture_vault(tmp_path: Path) -> Vault:
    """A vault with the fixture's raw log committed in one commit."""
    vault = Vault.init(tmp_path / "vault", make_config(), "test-box")
    write_raw(vault.root)
    vault.commit([], "Raw: the fixture")
    return vault


def template_vault(factory: pytest.TempPathFactory) -> Path:
    """The fixture vault built once for a test module, under the same isolated git settings conftest gives each test
    (a module's fixtures run before them), so each test copies it instead of paying for git init and a commit."""
    home = factory.mktemp("template-home")
    (home / "gitconfig").write_text("", encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
        mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
        mp.setenv("DOCKET_CONFIG_DIR", str(home / "docket"))
        mp.setenv("DOCKET_MACHINE", "test-box")
        for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            mp.delenv(name, raising=False)
        return fixture_vault(factory.mktemp("template")).root


def copy_vault(template: Path, tmp_path: Path) -> Vault:
    target = tmp_path / "vault"
    shutil.copytree(template, target)
    return Vault.open(target)


def note(
    doc_id: str = "recording-note:2026-10-14-supplier-call",
    covers: list[str] | None = None,
    lines: list[str] | None = None,
    **fields: str,
) -> str:
    """A doc's text: the frontmatter from the id, covers and any other fields given, then a heading and the lines."""
    head = [f"id: {doc_id}", f"type: {doc_id.partition(':')[0]}"]
    head += [f"{k}: {v}" for k, v in fields.items()]
    head += ["covers:", *(f"  - {rid}" for rid in (covers if covers is not None else [REC]))]
    if "by" not in fields:
        head.append("by: claude-test, sync 2026-10-14T16:07")
    body = lines if lines is not None else ["- Ben put the consultant at 10 million forints. @12:08"]
    return "---\n" + "\n".join(head) + "\n---\n\n# A note\n\n" + "\n".join(body) + "\n"
