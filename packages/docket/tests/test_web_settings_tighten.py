"""Turning a redaction rule on, and making a source stricter or turning it off, from the Settings page (DR-0506,
DR-0507, DR-0509): one post each, a source made private only after a view naming what waits from it; each through
vault.yaml's one writer and `loosens()`, refused when the page is older than the file, no write when sent twice.
Invented records only: Ada Example, Ben Sample."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from docket_testkit import git, make_record, sh
from docket_webkit import OURS, a11y_problems, html_problems, parse
from httpx2 import Response
from settings_kit import edit_config, settings_template, settings_vault
from staging_kit import signed_in
from starlette.testclient import TestClient

from docket.vault import redaction, settings
from docket.vault.config import CONFIG_FILE, Redaction, RedactionPattern, SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.records import RawRecord
from docket.vault.vault import Vault
from docket.web.templating import PAGE_CSP

SAME = {"Origin": OURS, "Sec-Fetch-Site": "same-origin"}
RULE = "/settings/redaction"
SOURCE = "/settings/source"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return settings_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    """The Settings test vault with two memos waiting at the gate, ready, since memo is a work source."""
    found = settings_vault(template, tmp_path)
    Gate(found).stage([memo(1, "Day rates."), memo(2, "Lunch on Friday.")])
    return found


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with signed_in(vault) as found:
        yield found


def memo(n: int, text: str) -> RawRecord:
    return make_record(n, rid=f"memo:m-{n}", source="memo", kind="memo", container=f"memo:m-{n}", text=text)


def shown(vault: Vault) -> str:
    return settings.file_hash((vault.root / CONFIG_FILE).read_bytes())


def config(vault: Vault) -> VaultConfig:
    return VaultConfig.load(vault.root / CONFIG_FILE)


def said(html: str) -> str:
    return parse(html).find("main").text()


def subjects(vault: Vault) -> list[str]:
    return sh(vault.root, "git", "log", "--format=%s").splitlines()


def states(vault: Vault) -> list[str]:
    return sorted(e.staged.state for e in Gate(vault).list().entries if e.staged.record.source == "memo")


def checked(answer: Response) -> str:
    assert answer.headers["Content-Security-Policy"] == PAGE_CSP
    assert answer.headers["Cache-Control"] == "no-store"
    assert answer.headers["Referrer-Policy"] == "same-origin"
    assert not html_problems(answer.text)
    assert not a11y_problems(answer.text)
    return answer.text


def test_a_rule_is_turned_on_in_one_post_and_twice_is_one_write(client: TestClient, vault: Vault) -> None:
    form = parse(client.get("/settings").text).find("input", name="rule").parent
    assert form is not None
    fields = {i.attrs["name"]: i.attrs["value"] for i in form.find_all("input")}
    assert fields == {"rule": "phone", "shown": shown(vault)}
    answer = client.post(RULE, data=fields, headers=SAME)
    assert answer.status_code == 303
    assert answer.headers["location"] == "/settings?done=phone-on"
    assert config(vault).redaction.phone
    assert sh(vault.root, "git", "log", "-1", "--format=%B").strip() == "Config: redaction phone on\n\nvia page"
    again = client.post(RULE, data=fields, headers=SAME)
    assert again.headers["location"] == "/settings?done=phone-on"
    assert subjects(vault).count("Config: redaction phone on") == 1
    done = said(checked(client.get(answer.headers["location"])))
    assert "Phone numbers are redacted from now on. Run docket doc redact" in done


def test_a_pattern_is_turned_on_and_its_name_stays_out_of_the_url(client: TestClient, vault: Vault) -> None:
    edit_config(vault, redaction=Redaction(patterns=[RedactionPattern(name="ticket-id", pattern="TCK", enabled=False)]))
    git(vault.root, "commit", "-qam", "the pattern off, by hand")
    answer = client.post(RULE, data={"rule": "ticket-id", "shown": shown(vault)}, headers=SAME)
    assert answer.headers["location"] == "/settings?done=pattern-on"
    assert config(vault).redaction.patterns[0].enabled
    assert subjects(vault)[0] == "Config: redaction ticket-id on"


def test_a_source_made_mixed_or_turned_off_is_one_post_and_the_sweep_runs(client: TestClient, vault: Vault) -> None:
    assert states(vault) == ["ready", "ready"]
    mixed = client.post(SOURCE, data={"source": "memo", "act": "mixed", "shown": shown(vault)}, headers=SAME)
    assert mixed.headers["location"] == "/settings?done=memo-mixed"
    assert config(vault).sources["memo"].sensitivity == "mixed"
    # Nobody classified them, and memo now asks: they wait again.
    assert states(vault) == ["pending", "pending"]
    assert "memo is mixed: each record from it waits on the Staging page" in said(
        client.get(mixed.headers["location"]).text
    )
    off = client.post(SOURCE, data={"source": "memo", "act": "off", "shown": shown(vault)}, headers=SAME)
    assert off.headers["location"] == "/settings?done=memo-off"
    assert not config(vault).sources["memo"].enabled
    assert states(vault) == ["pending", "pending"]
    assert subjects(vault)[:2] == ["Config: source memo off", "Config: source memo mixed"]


def test_a_source_made_private_asks_once_more_naming_what_waits(client: TestClient, vault: Vault) -> None:
    asked = client.post(SOURCE, data={"source": "memo", "act": "private", "shown": "from-the-page"}, headers=SAME)
    assert asked.status_code == 200
    text = said(checked(asked))
    assert "It drops 2 records of memo waiting in staging now." in text
    assert "From the next sync, nothing from memo is stored" in text
    assert config(vault).sources["memo"].sensitivity == "work"
    form = parse(asked.text).find("form", class_="confirm-form")
    fields = {i.attrs["name"]: i.attrs["value"] for i in form.find_all("input")}
    answer = client.post(SOURCE, data=fields, headers=SAME)
    assert answer.headers["location"] == "/settings?done=memo-private"
    assert config(vault).sources["memo"].sensitivity == "private"
    assert states(vault) == []
    assert client.post(SOURCE, data=fields, headers=SAME).headers["location"] == "/settings?done=memo-private"
    assert subjects(vault).count("Config: source memo private") == 1


def test_a_page_older_than_the_file_writes_nothing(client: TestClient, vault: Vault) -> None:
    before = (vault.root / CONFIG_FILE).read_bytes()
    stale = "0" * 64
    rule = client.post(RULE, data={"rule": "phone", "shown": stale}, headers=SAME)
    mixed = client.post(SOURCE, data={"source": "memo", "act": "mixed", "shown": stale}, headers=SAME)
    for answer in (rule, mixed):
        assert answer.status_code == 409
        assert "vault.yaml changed since the page showed it, so nothing was written" in said(checked(answer))
    private = client.post(
        SOURCE, data={"source": "memo", "act": "private", "shown": stale, "confirmed": "1"}, headers=SAME
    )
    assert private.status_code == 409
    assert "vault.yaml changed since you saw this, so nothing was written" in said(private.text)
    assert (vault.root / CONFIG_FILE).read_bytes() == before


@pytest.mark.parametrize(
    ("fields", "said_back"),
    [
        ({"source": "memo", "act": "work"}, "a page makes a source mixed or private, or turns it off"),
        ({"source": "memo", "act": "on"}, "a page makes a source mixed or private, or turns it off"),
        ({"source": "attic", "act": "off"}, "no source attic in vault.yaml"),
        ({"source": "../x", "act": "off"}, "source is a source's name from vault.yaml"),
        ({"source": "documents", "act": "mixed"}, "the change doesn't fit vault.yaml"),
    ],
)
def test_a_change_pages_don_t_make_is_refused_and_writes_nothing(
    client: TestClient, vault: Vault, fields: dict[str, str], said_back: str
) -> None:
    before = (vault.root / CONFIG_FILE).read_bytes()
    answer = client.post(SOURCE, data=fields | {"shown": shown(vault), "confirmed": "1"}, headers=SAME)
    assert answer.status_code == 400
    assert said_back in said(answer.text)
    assert (vault.root / CONFIG_FILE).read_bytes() == before


def test_a_looser_sensitivity_by_a_crafted_post_is_refused(client: TestClient, vault: Vault) -> None:
    edit_config(vault, sources={**config(vault).sources, "memo": SourceConfig(sensitivity="private")})
    git(vault.root, "commit", "-qam", "memo private by hand")
    before = (vault.root / CONFIG_FILE).read_bytes()
    answer = client.post(SOURCE, data={"source": "memo", "act": "mixed", "shown": shown(vault)}, headers=SAME)
    assert answer.status_code == 400
    assert "a page makes a source only stricter" in said(answer.text)
    rules = client.post(RULE, data={"rule": "nope", "shown": shown(vault)}, headers=SAME)
    assert rules.status_code == 400
    assert "no redaction rule by that name" in said(rules.text)
    assert (vault.root / CONFIG_FILE).read_bytes() == before


def test_an_uncommitted_edit_that_lets_more_in_is_refused_at_each_step(client: TestClient, vault: Vault) -> None:
    path = vault.root / CONFIG_FILE
    edited = path.read_text(encoding="utf-8").replace("credentials: true", "credentials: false", 1)
    path.write_text(edited, encoding="utf-8", newline="\n")
    at = sh(vault.root, "git", "rev-parse", "HEAD")
    answers = [
        client.post(RULE, data={"rule": "phone", "shown": shown(vault)}, headers=SAME),
        client.post(SOURCE, data={"source": "memo", "act": "off", "shown": shown(vault)}, headers=SAME),
        client.post(SOURCE, data={"source": "memo", "act": "private", "shown": shown(vault)}, headers=SAME),
    ]
    for answer in answers:
        assert answer.status_code == 400
        assert settings.UNCOMMITTED_LOOSENING in said(answer.text)
    assert path.read_text(encoding="utf-8") == edited
    assert sh(vault.root, "git", "rev-parse", "HEAD") == at


def test_a_hand_edited_file_shows_no_button_and_a_post_gets_the_line(client: TestClient, vault: Vault) -> None:
    path = vault.root / CONFIG_FILE
    mine = path.read_text(encoding="utf-8").replace("sources:\n", "sources:   # mine\n", 1)
    path.write_text(mine, encoding="utf-8", newline="\n")
    page = parse(client.get("/settings").text)
    assert not page.find_all("input", name="rule")
    assert not page.find_all("input", name="act")
    rule = client.post(RULE, data={"rule": "phone", "shown": shown(vault)}, headers=SAME)
    off = client.post(SOURCE, data={"source": "memo", "act": "off", "shown": shown(vault)}, headers=SAME)
    private = client.post(SOURCE, data={"source": "memo", "act": "private", "shown": shown(vault)}, headers=SAME)
    assert "change it there: phone: true, under redaction:." in said(rule.text)
    assert "change it there: enabled: false, under sources.memo." in said(off.text)
    assert "sensitivity: private" in said(private.text)
    assert not parse(private.text).find_all("form")
    assert path.read_text(encoding="utf-8") == mine


@pytest.mark.parametrize(("headers", "status"), [({"Origin": "http://evil.example"}, 403), ({}, 403), (SAME, 303)])
def test_every_post_passes_the_guard(client: TestClient, vault: Vault, headers: dict[str, str], status: int) -> None:
    assert client.post(RULE, data={"rule": "phone", "shown": shown(vault)}, headers=headers).status_code == status
    off = {"source": "chat", "act": "off", "shown": shown(vault)}
    assert client.post(SOURCE, data=off, headers=headers).status_code == status


def test_a_get_is_405_and_a_body_that_isn_t_a_form_is_refused(client: TestClient, vault: Vault) -> None:
    for route in (RULE, SOURCE):
        assert client.get(route).status_code == 405
        assert client.post(route, json={"rule": "phone"}, headers=SAME).status_code == 415
    assert redaction.CREDENTIALS in config(vault).redaction.model_dump()
