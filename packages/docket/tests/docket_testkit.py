"""Builders the app's tests share: invented configs and records, a fixture vault, docs, and a command runner."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from docket.vault.config import NeverStore, Owner, SourceConfig, VaultConfig
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


def consuming_project(tmp_path: Path, mappers: dict[str, str]) -> Vault:
    """A git repo with a mapper per source under .claude/skills/sources/, and a vault nested in it whose vault.yaml
    names each mapper; each source is mixed."""
    project = tmp_path / "project"
    project.mkdir()
    sh(project, "git", "init", "-q", "-b", "main")
    config = make_config()
    for source, code in mappers.items():
        write_mapper(project, source, code)
        config.sources[source] = SourceConfig(sensitivity="mixed", mapper=f".claude/skills/sources/{source}/mapper.py")
    return Vault.init(project / "vault", config, "test-box")


def write_mapper(project: Path, source: str, code: str) -> Path:
    path = project / ".claude" / "skills" / "sources" / source / "mapper.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8", newline="\n")
    return path


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


# -- the index fixture ------------------------------------------------------------------------------------------------

INDEX_FIXTURE = Path(__file__).parent / "fixtures" / "index-vault"
# The index fixture's clock: Wednesday 14 October 2026, so "last week" is 5 to 11 October.
INDEX_NOW = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
CALL = "calls:rec-0100"
SECOND = "calls:rec-0100-second"
FLEET = "calls:rec-0101"
PARENT = "chat:C0OPS/1791400000.000100"
OFFER = "mail:<offer-1@northwind.example>"


def index_config(languages: list[str] | None = None) -> VaultConfig:
    return VaultConfig(
        owner=Owner(
            name="Ada Example",
            emails=["ada@example.invalid"],
            languages=languages or ["en", "hu"],
            time_zone="Europe/Budapest",
        ),
        primary_machine="test-box",
        sources={
            "calls": SourceConfig(sensitivity="mixed"),
            "chat": SourceConfig(),
            "mail": SourceConfig(),
            "cal": SourceConfig(),
        },
    )


def fixture_records(source: str) -> list[RawRecord]:
    lines = (INDEX_FIXTURE / "records" / f"{source}.jsonl").read_text(encoding="utf-8").splitlines()
    return [RawRecord.model_validate(json.loads(line)) for line in lines if line.strip()]


def build_index_vault(root: Path) -> Vault:
    """The index fixture as a vault: its records written through write_run, its entity files copied in, its docs put
    through doc_put in file-name order. Built under the caller's git settings."""
    from docket import recall

    vault = Vault.init(root / "vault", index_config(), "test-box")
    for source in ("cal", "calls", "chat", "mail"):
        vault.write_run(source, fixture_records(source), datetime(2026, 10, 14, 7, 0, tzinfo=UTC))
    shutil.copytree(INDEX_FIXTURE / "entities", vault.root / "entities", dirs_exist_ok=True)
    vault.commit(["entities"], "Entities")
    for path in sorted((INDEX_FIXTURE / "docs").glob("*.md")):
        recall.doc_put(vault, path.read_text(encoding="utf-8"))
    return vault


LOGICAL_TABLES = (
    "meta", "run_files", "records", "record_versions", "segments", "docs", "doc_files", "doc_covers", "doc_anchors",
    "entities", "entity_files", "aliases", "alias_terms", "entity_links", "external_ids", "problems",
)  # fmt: skip


def logical_dump(db: Path) -> dict[str, list[tuple[Any, ...]]]:
    """An index's rows keyed by what they mean, not by row id: chunks by owner and ordinal, the FTS5 index as its
    words per chunk. Two indexes with the same dump answer every query the same."""
    conn = sqlite3.connect(db)
    try:
        keys = {row[0]: (row[1], row[2]) for row in conn.execute("SELECT id, owner, ordinal FROM chunks")}
        out = {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall()) for t in LOGICAL_TABLES}  # noqa: S608
        out["chunks"] = sorted(tuple(row[1:]) for row in conn.execute("SELECT * FROM chunks"))
        out["mentions"] = sorted((keys[c], e, h, a) for c, e, h, a in conn.execute("SELECT * FROM mentions"))
        conn.execute("CREATE VIRTUAL TABLE temp.words USING fts5vocab(main, chunks_fts, 'instance')")
        out["fts"] = sorted((t, keys[d], c, o) for t, d, c, o in conn.execute("SELECT * FROM temp.words"))
        return out
    finally:
        conn.close()


def template_with(factory: pytest.TempPathFactory, make: Callable[[Path], Vault]) -> Path:
    """A vault built once for a test module, under the isolated git settings conftest gives each test; tests copy it."""
    home = factory.mktemp("template-home")
    (home / "gitconfig").write_text("", encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
        mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
        mp.setenv("DOCKET_CONFIG_DIR", str(home / "docket"))
        mp.setenv("DOCKET_MACHINE", "test-box")
        for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            mp.delenv(name, raising=False)
        return make(factory.mktemp("template")).root


def index_template(factory: pytest.TempPathFactory) -> Path:
    """The index fixture built once for a test module; tests copy it."""
    return template_with(factory, build_index_vault)


# An invented notes folder, as an existing setup would have it: notes per source, a clash of stems, a note naming two
# recordings, one naming a recording not in the raw log yet, one with its own frontmatter, CRLF with a BOM, a
# credential, Latin-1, files that aren't notes, a folder git ignores, and two the never-store keywords catch: one by
# its text, one by a folder name that sorts first, so a path logged before the screen would show.
NEVER_STORE_KEYWORDS = ["dora example", "project falcon"]
NOTES: dict[str, bytes] = {
    "notes/0-people/dora-example/2026-09-01-catch-up.md": b"# Catch-up\n\nAll fine.\n",
    "notes/calls/2026-09-10-supplier-call.md": (
        b"# Supplier call\n\nSource: call recorder `rec-0042`, 45 min.\n\n## What was said\n\n"
        b"- Ben put the consultant at 10 million forints (12:08).\n- The follow-up is `rec-0099`.\n"
    ),
    "notes/calls/2026-09-12-two-parts.md": b"# Two parts\n\nRecorded as calls:rec-0042 and vendor:rec-0007.\n",
    "notes/calls/2026-09-14-later.md": b"# A later call\n\nSource: `calls:rec-0050`, not fetched yet.\n",
    "notes/calls/2026-10-14-supplier-call.md": b"# Supplier call again\n\nNo recorder id here.\n",
    "notes/chat/2026-09-10-supplier-call.md": b"# Chat about the supplier\n\nBen and Cleo agreed on Friday.\n",
    "notes/falcon.md": b"# Plans\n\nProject Falcon starts in spring.\n",
    "notes/latin1.md": b"caf\xe9\n",
    "notes/mail/2026-09-11-offer.md": (
        b"\xef\xbb\xbf# The offer\r\n\r\nCleo sent the offer.\r\npassword: hunter2-Example\r\n"
    ),
    "notes/own-frontmatter.md": b"---\ntags: [old]\n---\n# With its own frontmatter\n",
    "notes/photo.jpg": b"\xff\xd8\xff\xe0",
    "notes/private/diary.md": b"# Kept out of git\n",
    "notes/raw.json": b"{}\n",
    "notes/árvíztűrő-jegyzet.md": "# Árvíztűrő tükörfúrógép\n".encode(),
}
UNTRACKED_NOTE = "notes/calls/2026-09-13-untracked.md"
ADA = ("-c", "user.name=Ada Example", "-c", "user.email=ada@example.invalid")


def make_notes_repo(root: Path) -> Path:
    """The invented notes folder in a git repo of its own, committed, with one note not committed yet; returns the
    notes folder."""
    repo = root / "work"
    for rel, data in NOTES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (repo / ".gitignore").write_text("notes/private/\n", encoding="utf-8")
    sh(repo, "git", "init", "-q", "-b", "main")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", *ADA, "commit", "-q", "-m", "notes")
    (repo / UNTRACKED_NOTE).write_text("# Not committed yet\n\nA new note.\n", encoding="utf-8")
    return repo / "notes"


def isolated_git(mp: pytest.MonkeyPatch, home: Path) -> None:
    """The git and Docket settings conftest gives each test, for a fixture built once per module."""
    (home / "gitconfig").write_text("", encoding="utf-8")
    mp.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
    mp.setenv("DOCKET_CONFIG_DIR", str(home / "docket"))
    mp.setenv("DOCKET_MACHINE", "test-box")
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        mp.delenv(name, raising=False)


def screening(vault: Vault) -> Vault:
    """The vault with the fixture's never-store keywords in its settings, in memory and in vault.yaml."""
    vault.config = vault.config.model_copy(update={"never_store": NeverStore(keywords=NEVER_STORE_KEYWORDS)})
    (vault.root / "vault.yaml").write_text(vault.config.dump(), encoding="utf-8", newline="\n")
    return vault


def notes_template(factory: pytest.TempPathFactory) -> Path:
    """The invented notes repo built once for a module; returns the repo (its notes folder is notes/)."""
    with pytest.MonkeyPatch.context() as mp:
        isolated_git(mp, factory.mktemp("notes-home"))
        return make_notes_repo(factory.mktemp("notes")).parent


def imported_template(factory: pytest.TempPathFactory, vault_template: Path, repo_template: Path) -> Path:
    """A vault after importing the invented notes folder, and the folder beside it, built once for a module: returns
    the folder holding both (vault/ and work/)."""
    from docket.imports.notes import import_notes

    base = factory.mktemp("imported")
    with pytest.MonkeyPatch.context() as mp:
        isolated_git(mp, factory.mktemp("imported-home"))
        vault = screening(copy_vault(vault_template, base))
        shutil.copytree(repo_template, base / "work")
        import_notes(vault, base / "work" / "notes")
    return base
