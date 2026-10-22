"""Adding a never-store value from the Settings page (DR-0508, DR-0509, DR-0602): two form posts, the second only after
a view naming what the value drops now and what already stored holds it; the list only grows; the value travels in the
posts' bodies alone, never in a URL, the log or a commit message. Invented people only: Dora Example, Cleo Test."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import cant_place, digest, git, make_record, sh, shared
from docket_webkit import OURS, a11y_problems, html_problems, parse
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
from docket.web.templating import PAGE_CSP

SAME = {"Origin": OURS, "Sec-Fetch-Site": "same-origin"}
ADD = "/settings/never-store"
NAME = "Dora Example"
SUBJECT = "Config: never-store people, 1 added"


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


def shown(vault: Vault) -> str:
    return file_hash((vault.root / CONFIG_FILE).read_bytes())


def ask(client: TestClient, value: str, name: str = "people", status: int = 200) -> str:
    """The first post: the confirm view, checked as every page is."""
    answer = client.post(ADD, data={"list": name, "value": value, "shown": "from-the-page"}, headers=SAME)
    assert answer.status_code == status, answer.text
    assert answer.headers["Content-Security-Policy"] == PAGE_CSP
    assert answer.headers["Cache-Control"] == "no-store"
    assert answer.headers["Referrer-Policy"] == "same-origin"
    assert not html_problems(answer.text)
    assert not a11y_problems(answer.text)
    return answer.text


def confirm(client: TestClient, html: str) -> Response:
    """The second post, with the fields the confirm view's form holds."""
    form = parse(html).find("form", class_="confirm-form")
    fields = {i.attrs["name"]: i.attrs["value"] for i in form.find_all("input")}
    return client.post(ADD, data=fields, headers=SAME)


def said(html: str) -> str:
    return parse(html).find("main").text()


def commits(vault: Vault, subject: str = SUBJECT) -> int:
    return sh(vault.root, "git", "log", "--format=%s").splitlines().count(subject)


def staged_text(vault: Vault) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in (vault.root / "staging").rglob("*.json"))


def test_the_add_field_asks_the_browser_to_keep_and_check_nothing(client: TestClient, vault: Vault) -> None:
    html = client.get("/settings").text
    for name in ("people", "containers", "keywords"):
        form = parse(html).find("li", id=f"list-{name}").find("form")
        field = form.find("input", name="value")
        wanted = {"autocomplete": "off", "spellcheck": "false", "autocapitalize": "off", "autocorrect": "off"}
        assert {k: field.attrs[k] for k in wanted} == wanted
        assert field.attrs["maxlength"] == "200"
        hidden = {i.attrs["name"]: i.attrs["value"] for i in form.find_all("input", type="hidden")}
        assert hidden == {"list": name, "shown": shown(vault)}


def test_an_add_asks_once_more_then_writes_naming_only_the_list(
    client: TestClient, vault: Vault, log_file: Path
) -> None:
    html = ask(client, NAME)
    text = said(html)
    assert "It drops 1 record waiting in staging now." in text
    assert "Already stored, at least 1 record and 1 of 2 chunks (50.0%) hold it." in text
    assert "From the next sync, a record it names is dropped before it is stored" in text
    assert "Docs that say it" not in text
    assert commits(vault) == 0
    answer = confirm(client, html)
    assert answer.status_code == 303
    assert answer.headers["location"] == "/settings?done=listed-people-1"
    assert VaultConfig.load(vault.root / CONFIG_FILE).never_store.people[-1] == NAME
    assert sh(vault.root, "git", "log", "-1", "--format=%B").strip() == f"{SUBJECT}\n\nvia page"
    # The sweep ran: what waited naming her is gone, and the rest waits.
    assert NAME not in staged_text(vault)
    assert "Ben Sample" in staged_text(vault)
    done = client.get(answer.headers["location"]).text
    assert "Added one value to people, and committed vault.yaml" in said(done)
    # Nowhere else: not the commit messages, the URL, the page without show=lists, nor the service's log.
    assert "Dora" not in sh(vault.root, "git", "log", "--format=%B")
    assert "Dora" not in str(answer.headers)
    assert NAME not in client.get("/settings").text
    assert "Dora" not in log_file.read_text(encoding="utf-8")
    assert "POST /settings/never-store" in log_file.read_text(encoding="utf-8")


def test_the_confirm_sent_twice_adds_once(client: TestClient, vault: Vault) -> None:
    html = ask(client, NAME)
    assert confirm(client, html).headers["location"] == "/settings?done=listed-people-1"
    again = confirm(client, html)
    assert again.status_code == 303
    assert again.headers["location"] == "/settings?done=listed-people-0"
    assert commits(vault) == 1
    assert "held that already, so nothing changed" in said(client.get(again.headers["location"]).text)


def test_a_value_the_list_holds_skips_the_question(client: TestClient, vault: Vault) -> None:
    answer = client.post(ADD, data={"list": "people", "value": "  CLEO TEST ", "shown": shown(vault)}, headers=SAME)
    assert answer.status_code == 303
    assert answer.headers["location"] == "/settings?done=listed-people-0"
    assert commits(vault) == 0


def test_a_keyword_names_the_docs_and_warns_when_it_takes_much(client: TestClient) -> None:
    text = said(ask(client, "lunch", name="keywords"))
    assert "at least 2 records and 2 of 2 chunks (100.0%) hold it" in text
    assert "That is 100% of everything indexed" in text
    assert "Docs that say it stay as they are" in text
    calm = said(ask(client, "zeppelin", name="keywords"))
    assert "Nothing already stored holds it, of 2 chunks." in calm
    assert "Nothing waiting in staging names it now." in calm


def test_a_file_changed_since_the_view_asks_again_with_fresh_counts(client: TestClient, vault: Vault) -> None:
    html = ask(client, NAME)
    form = parse(html).find("form", class_="confirm-form")
    fields = {i.attrs["name"]: i.attrs["value"] for i in form.find_all("input")}
    before = (vault.root / CONFIG_FILE).read_bytes()
    answer = client.post(ADD, data=fields | {"shown": "0" * 64}, headers=SAME)
    assert answer.status_code == 409
    assert "vault.yaml changed since you saw this, so nothing was written" in said(answer.text)
    fresh = parse(answer.text).find("form", class_="confirm-form").find("input", name="shown")
    assert fresh.attrs["value"] == shown(vault)
    assert (vault.root / CONFIG_FILE).read_bytes() == before


def test_a_hand_edited_file_takes_the_add_in_place(client: TestClient, vault: Vault) -> None:
    path = vault.root / CONFIG_FILE
    mine = path.read_text(encoding="utf-8").replace("never_store:\n", "never_store:   # mine\n", 1)
    path.write_text(mine, encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qam", "A comment of mine")
    assert parse(ask(client, NAME)).find_all("form", class_="confirm-form")
    answer = client.post(
        ADD, data={"list": "people", "value": NAME, "shown": shown(vault), "confirmed": "1"}, headers=SAME
    )
    assert answer.status_code == 303
    assert "never_store:   # mine\n" in path.read_text(encoding="utf-8")
    assert NAME in VaultConfig.load(path).never_store.people


def test_an_add_docket_can_t_place_gets_the_line_and_is_left_as_it_is(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    cant_place(monkeypatch)
    path = vault.root / CONFIG_FILE
    mine = path.read_text(encoding="utf-8").replace("never_store:\n", "never_store:   # mine\n", 1)
    path.write_text(mine, encoding="utf-8", newline="\n")
    answer = client.post(
        ADD, data={"list": "people", "value": NAME, "shown": shown(vault), "confirmed": "1"}, headers=SAME
    )
    assert answer.status_code == 200
    assert "add this line under never_store: people: by hand" in said(answer.text)
    assert path.read_text(encoding="utf-8") == mine


@pytest.mark.parametrize(
    ("fields", "said_back"),
    [
        ({"list": "people", "value": "   "}, "never empty"),
        ({"list": "people", "value": "Dora\x01Example"}, "no control characters"),
        ({"list": "people", "value": "x" * 201}, "at most 200 characters"),
        ({"list": "domains", "value": "example.invalid"}, "people, containers or keywords"),
        ({"value": NAME}, "people, containers or keywords"),
    ],
)
def test_a_value_or_list_the_form_can_t_hold_writes_nothing(
    client: TestClient, vault: Vault, fields: dict[str, str], said_back: str
) -> None:
    before = (vault.root / CONFIG_FILE).read_bytes()
    answer = client.post(ADD, data=fields | {"confirmed": "1", "shown": shown(vault)}, headers=SAME)
    assert answer.status_code == 400
    assert said_back in said(answer.text)
    assert "Dora" not in said(answer.text)
    assert (vault.root / CONFIG_FILE).read_bytes() == before


def test_a_lock_another_process_holds_is_the_page_s_error(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_state, "LOCK_SECONDS", 0.2)
    before = (vault.root / CONFIG_FILE).read_bytes()
    fields = {"list": "people", "value": NAME, "shown": shown(vault)}
    with Vault.open(vault.root).lock:
        asked = client.post(ADD, data=fields, headers=SAME)
        added = client.post(ADD, data=fields | {"confirmed": "1"}, headers=SAME)
    for answer in (asked, added):
        assert answer.status_code == 400
        assert "another docket process holds" in answer.text
    assert (vault.root / CONFIG_FILE).read_bytes() == before


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Origin": "http://evil.example"}, 403),
        ({"Origin": "null", "Sec-Fetch-Site": "same-origin"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({}, 403),
        (SAME, 200),
    ],
)
def test_every_post_passes_the_guard(client: TestClient, headers: dict[str, str], status: int) -> None:
    answer = client.post(ADD, data={"list": "people", "value": NAME, "shown": "x"}, headers=headers)
    assert answer.status_code == status


def test_a_post_without_a_session_is_refused_and_a_get_is_405(client: TestClient, vault: Vault) -> None:
    assert client.get(ADD).status_code == 405
    client.cookies.clear()
    answer = client.post(
        ADD, data={"list": "people", "value": NAME, "shown": shown(vault), "confirmed": "1"}, headers=SAME
    )
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
    assert "can't be counted: there is no index yet." in said(ask(client, NAME))
    monkeypatch.setattr(settings_routes, "reach", busy)
    text = said(ask(client, NAME))
    assert "can't be counted: the index is busy being brought up to date" in text
    assert "It drops 1 record waiting in staging now." in text


def test_a_body_that_isn_t_a_form_is_refused(client: TestClient, vault: Vault) -> None:
    assert client.post(ADD, json={"list": "people", "value": NAME}, headers=SAME).status_code == 415
    form = SAME | {"Content-Type": "application/x-www-form-urlencoded"}
    big = client.post(ADD, content=b"value=" + b"x" * 70_000, headers=form)
    assert (big.status_code, big.text) == (413, "a form post is at most 64 KB")
    assert client.post(ADD, content=b"list=people&value=\xff", headers=form).status_code == 400
    assert commits(vault) == 0


def test_an_uncommitted_edit_that_lets_more_in_is_refused_at_both_steps(client: TestClient, vault: Vault) -> None:
    path = vault.root / CONFIG_FILE
    edited = path.read_text(encoding="utf-8").replace("credentials: true", "credentials: false", 1)
    path.write_text(edited, encoding="utf-8", newline="\n")
    at = sh(vault.root, "git", "rev-parse", "HEAD")
    asked = client.post(ADD, data={"list": "people", "value": NAME, "shown": shown(vault)}, headers=SAME)
    added = client.post(
        ADD, data={"list": "people", "value": NAME, "shown": shown(vault), "confirmed": "1"}, headers=SAME
    )
    for answer in (asked, added):
        assert answer.status_code == 400
        assert "vault.yaml has an edit that isn't in force until you commit it" in said(answer.text)
        assert NAME not in answer.text
    assert path.read_text(encoding="utf-8") == edited
    assert sh(vault.root, "git", "rev-parse", "HEAD") == at


def test_a_stricter_uncommitted_edit_is_named_and_goes_in_first(client: TestClient, vault: Vault) -> None:
    path = vault.root / CONFIG_FILE
    stricter = path.read_text(encoding="utf-8").replace("phone: false", "phone: true", 1)
    path.write_text(stricter, encoding="utf-8", newline="\n")
    html = ask(client, NAME)
    assert "Your edit of vault.yaml that isn't committed yet goes in first, as a commit of its own." in said(html)
    assert confirm(client, html).status_code == 303
    assert sh(vault.root, "git", "log", "-2", "--format=%s").splitlines() == [SUBJECT, "Config: vault.yaml as edited"]
