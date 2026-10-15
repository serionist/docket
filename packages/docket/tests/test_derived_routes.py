"""Every route a doc takes into the vault passes redaction (DR-0195): one invented credential and phone number through
the command line, REST, MCP, put_many, the notes import, a take-in and the pass, then the vault's files and its whole
history searched for both. Secret-shaped strings are assembled from parts, as in test_redact.py."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from docket_testkit import configure, copy_vault, git, note, sh, template_vault
from mcp import Client
from starlette.testclient import TestClient

from docket.cli import main
from docket.imports.notes import import_notes
from docket.service.app import create_app
from docket.service.state import Service, Settings
from docket.service.tools import create_mcp
from docket.vault.docstore import DocStore
from docket.vault.vault import Vault

SECRET = "hunter2-" + "Example"
PHONE = "+1 555 010 0188"
SAID = f"the password: {SECRET}, and Cleo is on {PHONE}"
TOKEN = "t0ken-for-tests-only"  # noqa: S105 - an invented token
PORT = 8741


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_vault(tmp_path_factory)


def doc(slug: str) -> str:
    return note(f"recording-note:2026-10-14-{slug}", lines=[f"- {SAID} @12:08"])


OLDER = "Doc: brief:older rev 1"


def files(vault: Vault) -> str:
    """The vault's files as they are now, as one text."""
    return "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in vault.root.rglob("*")
        if p.is_file() and ".git" not in p.parts
    )


def committed(vault: Vault, value: str) -> set[str]:
    """The commits whose tree or message holds the value, but the one that plays an older Docket, which wrote its doc
    unredacted: the pass redacts the tip, and history keeps that rev until forget."""
    root = vault.root
    commits = sh(root, "git", "rev-list", "--all").split()
    older = sh(root, "git", "log", "--all", "--format=%H", f"--grep={OLDER}").split()
    found = {
        line.split(":", 1)[0] for line in sh(root, "git", "grep", "-F", "-l", value, *commits, check=False).split()
    }
    found |= {sha for sha in commits if value in sh(root, "git", "log", "-1", "--format=%B", sha)}
    return found - set(older)


def through_every_route(vault: Vault, tmp_path: Path) -> None:
    root = vault.root
    # The command line.
    draft = tmp_path / "draft.md"
    draft.write_text(doc("cli"), encoding="utf-8")
    assert main(["doc", "put", str(draft), "--vault", str(root)]) == 0
    # REST.
    app = create_app(Settings(port=PORT, vault=root, token=lambda: TOKEN, store="the system keychain"))
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        answer = client.post("/api/docs", content=doc("rest"), headers={"Authorization": f"Bearer {TOKEN}"})
        assert answer.status_code == 200, answer.text
    # MCP.
    service = Service(Settings(port=PORT, vault=root, token=lambda: TOKEN))

    async def put() -> bool:
        async with Client(create_mcp(service)) as mcp:
            return (await mcp.call_tool("doc_put", {"text": doc("mcp")})).is_error

    try:
        assert not asyncio.run(put())
    finally:
        service.writer.close()
    # put_many, as the import uses it.
    [many] = DocStore(Vault.open(root)).put_many([doc("many")], "Import notes: 1 new from notes")
    assert not isinstance(many, Exception)
    # The notes import.
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "old.md").write_text(f"# Old\n\n{SAID}\n", encoding="utf-8")
    import_notes(Vault.open(root), notes)
    # A take-in: a hand edit to a doc, then a put.
    hand = root / "processed" / "recording-note" / "2026" / "10" / "2026-10-14-cli.md"
    edited = hand.read_text(encoding="utf-8").replace("@12:08", f"and again: password: {SECRET}x @12:08")
    hand.write_text(edited, encoding="utf-8", newline="\n")
    # The put is stale, so it is refused, but the hand edit goes in first.
    assert main(["doc", "put", str(draft), "--vault", str(root)]) == 1
    # The pass: a doc written and committed as an older Docket would have.
    older = root / "processed" / "brief" / "older.md"
    older.parent.mkdir(parents=True, exist_ok=True)
    text = doc("older").replace("recording-note:2026-10-14-older", "brief:older")
    older.write_text(text.replace("type: recording-note", "type: brief"), encoding="utf-8", newline="\n")
    git(root, "add", "-A", "processed")
    git(root, "commit", "-qm", OLDER)
    assert main(["doc", "redact", "--vault", str(root)]) == 0


def test_with_phone_on_neither_value_reaches_the_vault(template: Path, tmp_path: Path) -> None:
    vault = copy_vault(template, tmp_path)
    configure(vault.root, phone=True)
    through_every_route(Vault.open(vault.root), tmp_path)
    now = files(vault)
    assert "[redacted:password]" in now
    assert "[redacted:phone]" in now
    assert SECRET not in now
    assert PHONE not in now
    assert committed(vault, SECRET) == committed(vault, PHONE) == set()


def test_with_the_defaults_the_credential_reaches_nothing_and_the_number_is_kept(
    template: Path, tmp_path: Path
) -> None:
    vault = copy_vault(template, tmp_path)
    through_every_route(vault, tmp_path)
    assert SECRET not in files(vault)
    assert committed(vault, SECRET) == set()
    assert PHONE in files(vault)
