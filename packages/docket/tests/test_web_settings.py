"""The Settings page's read over HTTP, signed in (DR-0510, DR-0511, DR-0601, DR-0789): each source, the never-store
lists with their values only on request, redaction with the commands and warnings, this machine's models, every
change a button, its command or its line, nothing written, and nothing the page never showed."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from docket_testkit import indexed, read_only, sh
from settings_kit import edit_config, settings_template, settings_vault
from staging_kit import signed_in
from starlette.testclient import TestClient

from docket import index, userconfig
from docket.index import vectors
from docket.index.build import vector_counts
from docket.vault import redaction
from docket.vault.config import CONFIG_FILE, NeverStore, Redaction, RedactionPattern, SourceConfig, VaultConfig
from docket.vault.settings import file_hash
from docket.vault.vault import Vault
from docket.web import settings_view
from docket.web.display import FAILED

PAGE = "/api/web/settings"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return settings_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return settings_vault(template, tmp_path)


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with signed_in(vault) as found:
        yield found


@pytest.fixture(scope="module")
def seen(tmp_path_factory: pytest.TempPathFactory, template: Path) -> Vault:
    """One copy, its index built as the page's first read would build it, for the tests that only read: a write into
    it fails the run (DR-0531)."""
    return Vault.open(read_only(indexed(tmp_path_factory, template / "vault")))


@pytest.fixture
def reader(seen: Vault) -> Iterator[TestClient]:
    with signed_in(seen) as found:
        yield found


def read(client: TestClient, query: str = "", status: int = 200) -> dict[str, Any]:
    answer = client.get(f"{PAGE}?{query}" if query else PAGE)
    assert answer.status_code == status, answer.text
    found: dict[str, Any] = answer.json()
    return found


def source(found: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in found["sources"] if s["name"] == name)


def facts(entry: dict[str, Any]) -> dict[str, str]:
    return {f["name"]: f["value"] for f in entry["facts"]}


def test_the_page_names_each_source_with_its_settings_and_writes_nothing(reader: TestClient, seen: Vault) -> None:
    before = (seen.root / CONFIG_FILE).read_bytes()
    head = sh(seen.root, "git", "rev-parse", "HEAD")
    found = read(reader)
    assert found["canonical"] == "/settings"
    assert found["lead"].startswith(
        "4 sources, 4 on; 4 never-store values; credentials redacted, phone numbers not; models off"
    )
    assert (found["machine"], found["primary"]) == ("test-box", "test-box")
    calls = source(found, "calls")
    assert (calls["sensitivity"], calls["enabled"], calls["folder"]) == ("mixed", True, False)
    assert facts(calls)["Tools"] == "list_calls, get_call"
    assert facts(calls)["Mapper"] == ".claude/skills/sources/calls/mapper.py"
    memo = source(found, "memo")
    assert memo["sensitivity"] == "work"
    assert "has no mapper in vault.yaml" in memo["problem"]
    documents = source(found, "documents")
    assert (documents["folder"], facts(documents)["Folder"], facts(documents)["Exclude"]) == (True, "docs", "archive/*")
    assert {c["act"] for s in found["sources"] for c in s["changes"]} <= {"work", "mixed", "private", "off", "on"}
    assert (seen.root / CONFIG_FILE).read_bytes() == before
    assert sh(seen.root, "git", "rev-parse", "HEAD") == head


def test_each_change_to_a_source_is_a_button_and_a_looser_one_says_so(reader: TestClient) -> None:
    found = read(reader)
    changes = source(found, "calls")["changes"]
    assert [(c["act"], c["what"], c["looser"]) for c in changes] == [
        ("private", "Make it private", False),
        ("off", "Turn it off", False),
        ("work", "Make it work", True),
    ]
    assert changes[2]["effect"].startswith("lets more in")
    # A folder source is work only: it can only be turned off.
    documents = source(found, "documents")
    assert [c["act"] for c in documents["changes"]] == ["off"]
    assert yaml.safe_load(f"sources:\n{documents['entry']}")["sources"]["documents"]["folder"] == "docs"


def test_the_never_store_values_show_only_when_asked(reader: TestClient) -> None:
    hidden = reader.get(PAGE)
    for value in ("Cleo Test", "Dora #2", "chat:garden", "Project: Phoenix"):
        assert value not in hidden.text
    lists = {n["name"]: n for n in hidden.json()["lists"]}
    assert (lists["people"]["count"], lists["people"]["values"]) == (2, [])
    assert lists["people"]["ask"] == "A name, as a mail, a chat or a recording writes it"
    assert hidden.json()["shown"] is False
    shown = read(reader, "show=lists")
    assert (shown["canonical"], shown["shown"]) == ("/settings?show=lists", True)
    people = next(n for n in shown["lists"] if n["name"] == "people")
    assert [v["value"] for v in people["values"]] == ["Cleo Test", "Dora #2"]
    lines = [v["line"] for n in shown["lists"] for v in n["values"]]
    # Each line pastes as one value: quoted where YAML would read it otherwise.
    assert [yaml.safe_load(line) for line in lines] == [
        ["Cleo Test"],
        ["Dora #2"],
        ["chat:garden"],
        ["Project: Phoenix"],
    ]


def test_the_read_sends_nothing_the_page_never_showed(reader: TestClient, seen: Vault) -> None:
    """The lines a change makes and where they go, a list's example, a rule's command beside its button, and
    vault.yaml's own hash: none of them was on the page, so none is in its JSON."""
    found = read(reader, "show=lists")
    assert all(set(c) == {"act", "what", "effect", "looser"} for s in found["sources"] for c in s["changes"])
    assert all("example" not in n for n in found["lists"])
    assert "Dora Example" not in str(found)
    assert [r["name"] for r in found["rules"] if r["button"] and r["command"] is not None] == []
    plain = file_hash((seen.root / CONFIG_FILE).read_bytes())
    assert plain not in reader.get(f"{PAGE}?show=lists").text
    assert len(found["token"]) == 64
    # The token is the same for the same file, so a write from this read is checked against it.
    assert read(reader)["token"] == found["token"]


def test_redaction_names_each_rule_with_its_command_and_what_it_does(client: TestClient, vault: Vault) -> None:
    found = read(client)
    rules = {r["name"]: r for r in found["rules"]}
    root = str(vault.root)
    assert rules["credentials"]["command"] == f'docket redaction set credentials off --vault "{root}"'
    # Each rule turns either way with a button, but credentials off, which stays a command for your own terminal.
    assert [r["name"] for r in found["rules"] if r["button"]] == ["phone", "ticket-id"]
    assert (rules["credentials"]["button"], rules["credentials"]["note"]) == (False, redaction.CREDENTIALS_OFF)
    assert rules["phone"]["note"] == redaction.TURNED_ON
    assert found["names"].startswith("Names are never redacted")
    assert rules["ticket-id"]["what"] == r"TCK-\d{6}"
    assert found["credentials_off"] is None
    twins = [RedactionPattern(name="cost$", pattern="x"), RedactionPattern(name="cost$", pattern="y")]
    edit_config(vault, redaction=Redaction(credentials=False, patterns=twins))
    off = read(client)
    assert off["credentials_off"] == redaction.CREDENTIALS_OFF
    commands = [r["command"] for r in off["rules"] if r["command"]]
    # A name a shell would act on stays its placeholder, and is listed beside.
    assert f'docket redaction set "<pattern>" off --vault "{root}"' in commands
    assert {"name": "pattern", "value": "cost$"} in off["unfilled"]


def test_a_hand_edited_vault_yaml_is_named_and_keeps_its_buttons(client: TestClient, vault: Vault) -> None:
    path = vault.root / CONFIG_FILE
    mine = path.read_text(encoding="utf-8").replace("redaction:\n", "redaction:   # mine\n", 1)
    path.write_text(mine, encoding="utf-8", newline="\n")
    found = read(client)
    assert "each change below edits it in place and keeps them" in found["form"]
    assert [r for r in found["rules"] if r["button"]]
    assert path.read_text(encoding="utf-8") == mine


def test_content_in_settings_is_sent_as_text(client: TestClient, vault: Vault) -> None:
    edit_config(
        vault,
        never_store=NeverStore(people=["<script>alert(1)</script>"]),
        redaction=Redaction(patterns=[RedactionPattern(name="tag", pattern="<img src=x onerror=alert(1)>")]),
    )
    answer = client.get(f"{PAGE}?show=lists")
    assert answer.headers["content-type"] == "application/json"
    people = next(n for n in answer.json()["lists"] if n["name"] == "people")
    assert people["values"][0]["value"] == "<script>alert(1)</script>"


def test_this_machines_folder_and_models_come_from_its_config(reader: TestClient, seen: Vault) -> None:
    userconfig.set_section("folders", {str(seen.root): {"documents": str(seen.root.parent / "elsewhere")}})
    found = read(reader)
    assert facts(source(found, "documents"))["Here"] == str(seen.root.parent / "elsewhere")
    models = found["models"]
    assert {"name": "Models", "value": "off (docket models install turns them on)"} in models["rows"]
    assert "docket models install --no-reranker" in [c["text"] for c in models["commands"]]
    assert models["off_line"] == "provider: none"
    assert models["config_path"] == str(userconfig.config_path())
    userconfig.set_section("models", {"provider": "api", "embedder": "large"})
    rows = str(read(reader)["models"]["rows"])
    assert "names an embedder the api provider doesn't have (voyage-4), so models are off" in rows
    userconfig.set_section("folders", {str(seen.root): {"documents": "relative/path"}})
    assert "must be an absolute folder" in read(reader)["folders_problem"]


def test_a_bad_show_and_a_vault_that_doesnt_load_are_refused_with_their_words(client: TestClient, vault: Vault) -> None:
    assert "show is lists" in read(client, "show=everything", status=400)["error"]
    assert read(client, "done=listed-people-1")["notice"].startswith("Added one value to people")
    assert read(client, "done=listed-people-1")["canonical"] == "/settings"
    (vault.root / CONFIG_FILE).write_text("owner: [\n", encoding="utf-8")
    assert "not valid YAML" in read(client, status=400)["error"]


def test_a_failure_answers_500_naming_no_value(reader: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object) -> object:
        raise RuntimeError("Dora #2")

    monkeypatch.setattr(settings_view, "build", broken)
    answer = reader.get(f"{PAGE}?show=lists")
    assert (answer.status_code, answer.json()) == (500, {"error": FAILED})


def test_the_vectors_are_counted_where_an_index_reads_and_left_out_where_none_does(
    template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = settings_vault(template, tmp_path)
    assert vector_counts(vault) == []
    index.rebuild(vault)
    # An index with no model's table yet.
    assert vector_counts(vault) == []

    def locked(*_args: object) -> tuple[int, int]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(vectors, "tables", lambda _conn: [vectors.VecTable("e5", "e5-small", "r1", 64, "query: ")])
    monkeypatch.setattr(vectors, "coverage", locked)
    assert vector_counts(vault) == []


def test_a_vault_yaml_that_doesnt_parse_names_the_place_never_the_line(client: TestClient, vault: Vault) -> None:
    # A hand edit: a handle added as it is written in chat, which YAML refuses as a plain value.
    path = vault.root / CONFIG_FILE
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    at = next(i for i, line in enumerate(lines) if line.strip() == "- Cleo Test")
    indent = lines[at][: len(lines[at]) - len(lines[at].lstrip())]
    lines.insert(at + 1, f"{indent}- @dora.secret\n")
    path.write_text("".join(lines), encoding="utf-8", newline="\n")
    where = f"found character '@' that cannot start any token at line {at + 2}, column {len(indent) + 3}"
    for route in (PAGE, f"{PAGE}?show=lists", "/api/web/staging", "/api/web/curate", "/api/web/sync"):
        answer = client.get(route)
        assert answer.status_code == 400, route
        assert "dora.secret" not in answer.text, route
        assert where in answer.json()["error"], route


def test_an_edit_not_in_force_is_said_at_the_top_and_credentials_off_is_for_your_own_terminal(
    client: TestClient, vault: Vault
) -> None:
    assert read(client)["waiting"] is None
    edit_config(vault, never_store=NeverStore())
    found = read(client)
    assert found["waiting"] == "vault.yaml has an edit that isn't in force until you commit it."
    assert "Cleo" not in str(found)
    # Said once, at the top: a source's own problem doesn't repeat it, and one added by hand waits for its commit.
    assert str(found).count("isn't in force until you commit it") == 1
    sources = VaultConfig.load(vault.root / CONFIG_FILE).sources
    edit_config(vault, sources={**sources, "diary": SourceConfig(sensitivity="private")})
    assert source(read(client), "diary")["problem"] == "not in force until you commit vault.yaml"


def test_the_page_path_answers_the_apps_document_and_the_read_takes_the_session_only(client: TestClient) -> None:
    for path in ("/settings", "/settings?show=lists"):
        page = client.get(path)
        assert page.status_code == 200
        assert '<div id="root"></div>' in page.text
    assert client.get(PAGE, headers={"Authorization": "Bearer x"}).status_code == 401
    client.cookies.clear()
    assert client.get(PAGE).status_code == 401
