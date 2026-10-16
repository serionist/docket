"""The Settings page over HTTP, signed in (DR-0505, DR-0506, DR-0510 to DR-0512): each source, the never-store lists
with their values only on request, redaction with the commands and warnings, this machine's models, every change named
as its command or its line, and nothing written."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from docket_testkit import sh
from docket_webkit import Element, a11y_problems, html_problems, parse
from settings_kit import edit_config, settings_template, settings_vault
from staging_kit import signed_in
from starlette.testclient import TestClient

from docket import index, userconfig
from docket.index import vectors
from docket.index.build import vector_counts
from docket.vault import redaction
from docket.vault.config import NeverStore, Redaction, RedactionPattern
from docket.vault.vault import Vault
from docket.web import settings_view
from docket.web.templating import PAGE_CSP


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


def page(client: TestClient, path: str = "/settings", status: int = 200) -> str:
    answer = client.get(path)
    assert answer.status_code == status, answer.text
    assert answer.headers["Content-Security-Policy"] == PAGE_CSP
    assert answer.headers["Cache-Control"] == "no-store"
    assert not html_problems(answer.text), path
    assert not a11y_problems(answer.text), path
    return answer.text


def card(html: str, card_id: str) -> str:
    return parse(html).find("li", id=card_id).text()


def said(html: str) -> str:
    """The page's main text as a reader gets it: entities read, white space folded."""
    return parse(html).find("main").text()


def raw(element: Element) -> str:
    """An element's own text as written, line breaks kept: a pre's."""
    return "".join(c for c in element.children if isinstance(c, str))


def test_the_page_names_each_source_with_its_settings_and_writes_nothing(client: TestClient, vault: Vault) -> None:
    before = (vault.root / "vault.yaml").read_bytes()
    head = sh(vault.root, "git", "rev-parse", "HEAD")
    html = page(client)
    lead = parse(html).find("p", class_="lead").text()
    assert lead.startswith("4 sources, 4 on; 4 never-store values; credentials redacted, phone numbers not; models off")
    calls = card(html, "source-calls")
    assert "mixed" in calls
    assert "list_calls, get_call" in calls
    assert ".claude/skills/sources/calls/mapper.py" in calls
    memo = card(html, "source-memo")
    assert "work" in memo
    assert "has no mapper in vault.yaml" in memo
    documents = card(html, "source-documents")
    assert "docs" in documents
    assert "archive/*" in documents
    assert "folder" in documents
    # The page makes no change: no form posts, and the file and its history are as they were.
    assert not parse(html).find_all("form")
    assert (vault.root / "vault.yaml").read_bytes() == before
    assert sh(vault.root, "git", "rev-parse", "HEAD") == head


def test_every_change_to_a_source_is_its_line_stricter_first(client: TestClient) -> None:
    html = page(client)
    changes = parse(html).find("li", id="source-calls").find_all("li", class_="change")
    named = [(c.find("span", class_="change-what").text(), "looser" in c.attrs["class"]) for c in changes]
    assert named == [("Make it private", False), ("Turn it off", False), ("Make it work", True)]
    assert [c.find("code", class_="paste-text").text() for c in changes] == [
        "sensitivity: private",
        "enabled: false",
        "sensitivity: work",
    ]
    assert "lets more in" in changes[2].text()
    documents = parse(html).find("li", id="source-documents")
    # A folder source is work only: it can only be turned off.
    assert [c.find("span", class_="change-what").text() for c in documents.find_all("li", class_="change")] == [
        "Turn it off"
    ]
    entry = raw(documents.find("pre", class_="entry"))
    assert yaml.safe_load(f"sources:\n{entry}")["sources"]["documents"]["folder"] == "docs"


def test_the_never_store_values_show_only_when_asked(client: TestClient, vault: Vault) -> None:
    hidden = page(client)
    for value in ("Cleo Test", "Dora #2", "chat:garden", "Project: Phoenix"):
        assert value not in hidden
    assert "2 values" in card(hidden, "list-people")
    toggle = parse(hidden).find("a", class_="show-values")
    assert (toggle.attrs["href"], toggle.text()) == ("/settings?show=lists", "Show the values")
    shown = page(client, "/settings?show=lists")
    people = parse(shown).find("li", id="list-people")
    values = [v.text() for v in people.find_all("span", class_="value")]
    assert values == ["Cleo Test", "Dora #2"]
    lines = [c.text() for c in parse(shown).find_all("code", class_="line")]
    # Each line pastes as one value: quoted where YAML would read it otherwise.
    assert [yaml.safe_load(line) for line in lines] == [
        ["Cleo Test"],
        ["Dora #2"],
        ["chat:garden"],
        ["Project: Phoenix"],
    ]
    toggle = parse(shown).find("a", class_="show-values")
    assert (toggle.attrs["href"], toggle.text()) == ("/settings", "Hide the values")
    assert "committed in vault.yaml as you write it" in shown


def test_redaction_names_each_rule_with_its_command_and_what_it_does(client: TestClient, vault: Vault) -> None:
    html = page(client)
    codes = [c.text() for c in parse(html).find_all("code", class_="paste-text")]
    root = str(vault.root)
    assert f'docket redaction set credentials off --vault "{root}"' in codes
    assert f'docket redaction set phone on --vault "{root}"' in codes
    assert f'docket redaction set "ticket-id" off --vault "{root}"' in codes
    text = said(html)
    assert redaction.CREDENTIALS_OFF in text
    assert redaction.TURNED_ON in text
    assert "Names are never redacted" in text
    assert "TCK-\\d{6}" in text
    edit_config(vault, redaction=Redaction(credentials=False, patterns=[RedactionPattern(name="cost$", pattern="x")]))
    off = page(client)
    assert "Credentials are not redacted: " + redaction.CREDENTIALS_OFF in said(off)
    codes = [c.text() for c in parse(off).find_all("code", class_="paste-text")]
    # A name a shell would act on stays its placeholder, and is listed beside.
    assert f'docket redaction set "<pattern>" off --vault "{root}"' in codes
    assert "<pattern> is cost$" in said(off)


def test_a_hand_edited_vault_yaml_is_named_and_each_rule_shows_its_line(client: TestClient, vault: Vault) -> None:
    path = vault.root / "vault.yaml"
    mine = path.read_text(encoding="utf-8").replace("redaction:\n", "redaction:   # mine\n", 1)
    path.write_text(mine, encoding="utf-8", newline="\n")
    html = page(client)
    assert "has comments or a layout of your own, so it is never rewritten" in said(html)
    assert "the command prints the line instead: phone: true, under redaction:." in said(html)
    assert path.read_text(encoding="utf-8") == mine


def test_content_in_settings_renders_escaped(client: TestClient, vault: Vault) -> None:
    edit_config(
        vault,
        never_store=NeverStore(people=["<script>alert(1)</script>"]),
        redaction=Redaction(patterns=[RedactionPattern(name="tag", pattern="<img src=x onerror=alert(1)>")]),
    )
    html = page(client, "/settings?show=lists")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html


def test_this_machines_folder_and_models_come_from_its_config(client: TestClient, vault: Vault) -> None:
    userconfig.set_section("folders", {str(vault.root): {"documents": str(vault.root.parent / "elsewhere")}})
    html = page(client)
    assert str(vault.root.parent / "elsewhere") in card(html, "source-documents")
    assert "off (docket models install turns them on)" in html
    assert "docket models install --no-reranker" in html
    assert "provider: none" in html
    userconfig.set_section("models", {"provider": "api"})
    assert "names the api provider, which isn't there yet, so models are off" in said(page(client))
    userconfig.set_section("folders", {str(vault.root): {"documents": "relative/path"}})
    assert "must be an absolute folder" in page(client)


def test_a_bad_show_and_a_vault_that_doesnt_load_are_the_page_s_own_errors(client: TestClient, vault: Vault) -> None:
    html = page(client, "/settings?show=everything", status=400)
    assert "show is lists" in html
    (vault.root / "vault.yaml").write_text("owner: [\n", encoding="utf-8")
    broken = page(client, status=400)
    assert "not valid YAML" in broken


def test_a_failure_answers_the_error_page_naming_no_value(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object) -> object:
        raise RuntimeError("Dora #2")

    monkeypatch.setattr(settings_view, "build", broken)
    answer = client.get("/settings?show=lists")
    assert answer.status_code == 500
    assert "Something failed in Docket" in said(answer.text)
    assert "Dora #2" not in answer.text


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
    path = vault.root / "vault.yaml"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    at = next(i for i, line in enumerate(lines) if line.strip() == "- Cleo Test")
    indent = lines[at][: len(lines[at]) - len(lines[at].lstrip())]
    lines.insert(at + 1, f"{indent}- @dora.secret\n")
    path.write_text("".join(lines), encoding="utf-8", newline="\n")
    where = f"found character '@' that cannot start any token at line {at + 2}, column {len(indent) + 3}"
    for route in ("/settings", "/settings?show=lists", "/staging", "/curate"):
        answer = client.get(route)
        assert answer.status_code == 400, route
        assert "dora.secret" not in answer.text, route
        assert where in said(answer.text), route
