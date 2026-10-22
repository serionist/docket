"""Adding a never-store value from the Settings page (DR-0509, DR-0791, DR-0790): two JSON writes, the add only after
the question naming what the value drops now and what already stored holds it; the list only grows; the value travels
in the writes' bodies alone, never in a URL, an answer, an error, the log or a commit message. Invented people only:
Dora Example, Cleo Test."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from docket_testkit import cant_place, digest, git, make_record, sh, shared
from docket_webkit import OURS
from httpx2 import Response
from settings_kit import settings_template, settings_vault
from staging_kit import signed_in
from starlette.testclient import TestClient

from docket import index
from docket.errors import DocketError
from docket.service import logs
from docket.service import state as service_state
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.gate import Gate
from docket.vault.records import RawRecord
from docket.vault.settings import file_hash
from docket.vault.vault import Vault
from docket.web import settings as settings_routes

SAME = {"Origin": OURS, "Sec-Fetch-Site": "same-origin"}
ADD = "/api/web/settings/never-store"
NAME = "Dora Example"
SUBJECT = "Config: never-store people, 1 added"
# Each case of the line an add Docket can't place shows, which the page writes from the value it holds:
# web/src/test/never-store-lines.json, read by the Vitest of the same rule too.
LINES = Path(__file__).parents[1] / "web" / "src" / "test" / "never-store-lines.json"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The Settings test vault with a memo by Dora stored and indexed, and another of hers and one by Cleo staged:
    built once per session, its project returned; tests copy it."""
    settings = settings_template(tmp_path_factory)

    def build(folder: Path) -> Path:
        found = settings_vault(settings, folder)
        run = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        stored = [memo(1, NAME, "Lunch with the supplier on Friday."), memo(2, "Ben Sample", "Lunch menu attached.")]
        found.write_run("memo", stored, run)
        index.rebuild(found)
        Gate(found).stage([memo(3, NAME, "Dinner plans."), memo(4, "Ben Sample", "Day rates.")])
        return found.root.parent

    return shared(build, "settings-add-" + digest(str(settings.resolve())), tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return settings_vault(template, tmp_path)


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with signed_in(vault) as found:
        yield found


@pytest.fixture
def log_file(tmp_path: Path) -> Iterator[Path]:
    root = logging.getLogger()
    levels = {name: logging.getLogger(name).level for name in ("", "docket", *logs.LIBRARIES)}
    path = tmp_path / "log" / "service.log"
    handler = logs.configure(path)
    yield path
    root.removeHandler(handler)
    handler.close()
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


def memo(n: int, author: str, text: str) -> RawRecord:
    return make_record(
        n, rid=f"memo:m-{n}", source="memo", kind="memo", container=f"memo:m-{n}", author=author, text=text
    )


def token(client: TestClient) -> str:
    """vault.yaml's token, as the page's read gave it."""
    found: str = client.get("/api/web/settings").json()["token"]
    return found


def write(client: TestClient, act: str, value: str, name: str = "people", **more: Any) -> Response:
    body = {"act": act, "name": name, "value": value, "token": more.pop("token", None) or token(client), **more}
    return client.post(ADD, json=body, headers=SAME)


def ask(client: TestClient, value: str, name: str = "people") -> dict[str, Any]:
    """The first write: the add's question, never the value."""
    answer = write(client, "ask", value, name)
    assert answer.status_code == 200, answer.text
    assert value not in answer.text
    question: dict[str, Any] = answer.json()["question"]
    return question


def commits(vault: Vault, subject: str = SUBJECT) -> int:
    return sh(vault.root, "git", "log", "--format=%s").splitlines().count(subject)


def staged_text(vault: Vault) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in (vault.root / "staging").rglob("*.json"))


def test_an_add_asks_once_more_then_writes_naming_only_the_list(
    client: TestClient, vault: Vault, log_file: Path
) -> None:
    question = ask(client, NAME)
    assert question["name"] == "people"
    lines = " ".join(question["lines"])
    assert "It drops 1 record waiting in staging now." in lines
    assert "Already stored, at least 1 record and 1 of 2 chunks (50.0%) hold it." in lines
    assert "From the next sync, a record it names is dropped before it is stored" in lines
    assert "Docs that say it" not in lines
    assert "committed in vault.yaml as you typed it" in question["commits"]
    assert commits(vault) == 0
    answer = write(client, "add", NAME, token=question["token"])
    assert answer.status_code == 200
    assert answer.json()["notice"].startswith("Added one value to people, and committed vault.yaml")
    assert VaultConfig.load(vault.root / CONFIG_FILE).never_store.people[-1] == NAME
    assert sh(vault.root, "git", "log", "-1", "--format=%B").strip() == f"{SUBJECT}\n\nvia page"
    # The sweep ran: what waited naming her is gone, and the rest waits.
    assert NAME not in staged_text(vault)
    assert "Ben Sample" in staged_text(vault)
    # Nowhere else: not the commit messages, the answer, the page without show=lists, nor the service's log.
    assert "Dora" not in sh(vault.root, "git", "log", "--format=%B")
    assert "Dora" not in answer.text and "Dora" not in str(answer.headers)
    assert NAME not in client.get("/api/web/settings").text
    assert "Dora" not in log_file.read_text(encoding="utf-8")
    # The log names the route, never its body.
    assert "POST /settings/never-store 200" in log_file.read_text(encoding="utf-8")


def test_the_add_sent_twice_adds_once(client: TestClient, vault: Vault) -> None:
    question = ask(client, NAME)
    first = write(client, "add", NAME, token=question["token"])
    assert first.json()["notice"].startswith("Added one value to people")
    again = write(client, "add", NAME, token=question["token"])
    assert again.status_code == 200
    assert again.json()["notice"] == "The people list held that already, so nothing changed."
    assert commits(vault) == 1


def test_a_value_the_list_holds_skips_the_question(client: TestClient, vault: Vault) -> None:
    answer = write(client, "ask", "  CLEO TEST ")
    assert answer.status_code == 200
    assert answer.json() == {
        "notice": "The people list held that already, so nothing changed.",
        "warning": None,
        "question": None,
        "by_hand": False,
    }
    assert commits(vault) == 0


def test_a_keyword_names_the_docs_and_warns_when_it_takes_much(client: TestClient) -> None:
    broad = ask(client, "lunch", name="keywords")
    assert "at least 2 records and 2 of 2 chunks (100.0%) hold it" in " ".join(broad["lines"])
    assert broad["broad"].startswith("That is 100% of everything indexed")
    assert "Docs that say it stay as they are" in " ".join(broad["lines"])
    calm = " ".join(ask(client, "zeppelin", name="keywords")["lines"])
    assert "Nothing already stored holds it, of 2 chunks." in calm
    assert "Nothing waiting in staging names it now." in calm


def test_a_file_changed_since_the_question_writes_nothing_and_answers_409(client: TestClient, vault: Vault) -> None:
    question = ask(client, NAME)
    path = vault.root / CONFIG_FILE
    changed = path.read_text(encoding="utf-8").replace("phone: false", "phone: true", 1)
    path.write_text(changed, encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qam", "phone on, by hand")
    before = path.read_bytes()
    answer = write(client, "add", NAME, token=question["token"])
    assert answer.status_code == 409
    assert answer.json()["error"].startswith("vault.yaml changed since you saw this, so nothing was written")
    assert NAME not in answer.text
    assert path.read_bytes() == before
    # Asked again, the question carries the file's token as it stands now.
    assert ask(client, NAME)["token"] == token(client)


def test_a_token_from_another_service_process_or_none_names_no_file(client: TestClient, vault: Vault) -> None:
    before = (vault.root / CONFIG_FILE).read_bytes()
    for made_up in ("0" * 64, "", file_hash(before)):
        answer = write(client, "add", NAME, token=made_up or "x")
        assert answer.status_code == 409, made_up
    assert (vault.root / CONFIG_FILE).read_bytes() == before


def test_a_hand_edited_file_takes_the_add_in_place(client: TestClient, vault: Vault) -> None:
    path = vault.root / CONFIG_FILE
    mine = path.read_text(encoding="utf-8").replace("never_store:\n", "never_store:   # mine\n", 1)
    path.write_text(mine, encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qam", "A comment of mine")
    assert ask(client, NAME)["lines"]
    assert write(client, "add", NAME).status_code == 200
    assert "never_store:   # mine\n" in path.read_text(encoding="utf-8")
    assert NAME in VaultConfig.load(path).never_store.people


def test_an_add_docket_can_t_place_says_so_with_no_value_and_is_left_as_it_is(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    cant_place(monkeypatch)
    path = vault.root / CONFIG_FILE
    mine = path.read_text(encoding="utf-8").replace("never_store:\n", "never_store:   # mine\n", 1)
    path.write_text(mine, encoding="utf-8", newline="\n")
    answer = write(client, "add", NAME)
    assert answer.status_code == 200
    said = answer.json()
    assert (said["by_hand"], said["notice"]) == (True, None)
    assert said["warning"].endswith("add this line under never_store: people: by hand.")
    assert NAME not in answer.text
    assert path.read_text(encoding="utf-8") == mine


def test_the_line_the_page_writes_for_a_hand_add_reads_back_as_the_value() -> None:
    """The page writes the line itself, so the value never comes back from the service: single-quoted, its quotes
    doubled, which YAML reads as the one value whatever it holds; the Vitest holds the page to the same lines."""
    cases = json.loads(LINES.read_text(encoding="utf-8"))
    assert len(cases) >= 6
    for case in cases:
        assert yaml.safe_load(case["line"]) == [case["value"]], case


@pytest.mark.parametrize(
    ("body", "said_back"),
    [
        ({"act": "add", "name": "people", "value": "   "}, "never empty"),
        ({"act": "add", "name": "people", "value": "Dora\x01Example"}, "no control characters"),
        ({"act": "ask", "name": "people", "value": "Dora " + "x" * 201}, "at most 200 characters"),
        ({"act": "add", "name": "domains", "value": "Dora Example"}, "people, containers or keywords"),
        ({"act": "add", "name": "Dora Example", "value": "Dora Example"}, "people, containers or keywords"),
    ],
)
def test_a_value_or_list_the_write_can_t_hold_writes_nothing_and_never_says_it(
    client: TestClient, vault: Vault, body: dict[str, str], said_back: str
) -> None:
    before = (vault.root / CONFIG_FILE).read_bytes()
    answer = client.post(ADD, json=body | {"token": token(client)}, headers=SAME)
    assert answer.status_code == 400
    assert said_back in answer.json()["error"]
    assert "Dora" not in answer.text
    assert (vault.root / CONFIG_FILE).read_bytes() == before


@pytest.mark.parametrize(
    "body",
    [
        {"act": "add", "name": "people", "value": ["Dora Example"], "token": "t"},
        {"act": "add", "name": "people", "value": 17, "token": "Dora Example"},
        {"act": "keep", "name": "people", "value": "Dora Example", "token": "t"},
        {"act": "add", "name": "people", "value": "Ben", "token": "t", "also": "Dora Example"},
        {"act": "add", "name": "people", "token": "t", "Dora Example": "1"},
    ],
)
def test_a_body_that_doesn_t_read_is_refused_and_never_echoes_what_it_held(
    client: TestClient, vault: Vault, body: dict[str, object]
) -> None:
    before = (vault.root / CONFIG_FILE).read_bytes()
    answer = client.post(ADD, json=body, headers=SAME)
    assert answer.status_code == 422
    assert "Dora" not in answer.text
    assert answer.json()["detail"]
    assert (vault.root / CONFIG_FILE).read_bytes() == before


def test_a_lock_another_process_holds_is_refused_with_its_words(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_state, "LOCK_SECONDS", 0.2)
    before = (vault.root / CONFIG_FILE).read_bytes()
    held = token(client)
    with Vault.open(vault.root).lock:
        asked = write(client, "ask", NAME, token=held)
        added = write(client, "add", NAME, token=held)
    for answer in (asked, added):
        assert answer.status_code == 400
        assert "another docket process holds" in answer.text
        assert NAME not in answer.text
    assert (vault.root / CONFIG_FILE).read_bytes() == before


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Origin": "http://evil.example"}, 403),
        ({"Origin": "null", "Sec-Fetch-Site": "same-origin"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({}, 403),
        ({**SAME, "Authorization": "Bearer t0ken-for-tests-only"}, 403),
        (SAME, 200),
    ],
)
def test_every_write_passes_the_guard_and_takes_the_session_only(
    client: TestClient, headers: dict[str, str], status: int
) -> None:
    answer = client.post(ADD, json={"act": "ask", "name": "people", "value": NAME, "token": "x"}, headers=headers)
    assert answer.status_code == status


def test_a_write_without_a_session_or_as_a_form_is_refused(client: TestClient, vault: Vault) -> None:
    assert client.get(ADD).status_code == 405
    form = client.post(ADD, data={"act": "add", "name": "people", "value": NAME, "token": "x"}, headers=SAME)
    assert form.status_code == 415
    client.cookies.clear()
    answer = client.post(ADD, json={"act": "add", "name": "people", "value": NAME, "token": "x"}, headers=SAME)
    assert answer.status_code == 401
    assert commits(vault) == 0


def test_what_the_index_can_t_say_is_said_and_the_question_still_asked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def none(*_args: object) -> object:
        raise DocketError("there is no index yet; run docket index rebuild")

    def busy(*_args: object) -> object:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(settings_routes, "reach", none)
    assert "can't be counted: there is no index yet." in " ".join(ask(client, NAME)["lines"])
    monkeypatch.setattr(settings_routes, "reach", busy)
    lines = " ".join(ask(client, NAME)["lines"])
    assert "can't be counted: the index is busy being brought up to date" in lines
    assert "It drops 1 record waiting in staging now." in lines


def test_an_uncommitted_edit_that_lets_more_in_is_refused_at_both_steps(client: TestClient, vault: Vault) -> None:
    path = vault.root / CONFIG_FILE
    edited = path.read_text(encoding="utf-8").replace("credentials: true", "credentials: false", 1)
    path.write_text(edited, encoding="utf-8", newline="\n")
    at = sh(vault.root, "git", "rev-parse", "HEAD")
    held = token(client)
    for act in ("ask", "add"):
        answer = write(client, act, NAME, token=held)
        assert answer.status_code == 400
        assert "vault.yaml has an edit that isn't in force until you commit it" in answer.json()["error"]
        assert NAME not in answer.text
    assert path.read_text(encoding="utf-8") == edited
    assert sh(vault.root, "git", "rev-parse", "HEAD") == at


def test_a_stricter_uncommitted_edit_is_named_and_goes_in_first(client: TestClient, vault: Vault) -> None:
    path = vault.root / CONFIG_FILE
    stricter = path.read_text(encoding="utf-8").replace("phone: false", "phone: true", 1)
    path.write_text(stricter, encoding="utf-8", newline="\n")
    question = ask(client, NAME)
    assert (
        "Your edit of vault.yaml that isn't committed yet goes in first, as a commit of its own." in question["lines"]
    )
    assert write(client, "add", NAME, token=question["token"]).status_code == 200
    assert sh(vault.root, "git", "log", "-2", "--format=%s").splitlines() == [SUBJECT, "Config: vault.yaml as edited"]
