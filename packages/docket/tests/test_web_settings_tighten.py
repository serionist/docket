"""Turning a redaction rule on or off, setting a source's sensitivity, turning it on or off, and taking a never-store
value off, from the Settings page (DR-0509, DR-0601, DR-0602, DR-0790): one JSON write each, a source made private
only after its question naming what waits from it; each through vault.yaml's one writer, credentials off refused,
refused when the page is older than the file, no write when sent twice. Invented records only: Ada Example, Ben
Sample."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import cant_place, digest, git, make_record, sh, shared
from docket_webkit import OURS
from httpx2 import Response
from settings_kit import edit_config, settings_template, settings_vault
from staging_kit import signed_in
from starlette.testclient import TestClient

from docket.vault import settings
from docket.vault.config import CONFIG_FILE, Redaction, RedactionPattern, SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

SAME = {"Origin": OURS, "Sec-Fetch-Site": "same-origin"}
PAGE = "/api/web/settings"
RULE = "/api/web/settings/redaction"
ADD = "/api/web/settings/never-store"
SOURCE = "/api/web/settings/source"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The Settings test vault with two memos waiting at the gate, ready, since memo is a work source: built once per
    session, its project returned; tests copy it."""
    settings = settings_template(tmp_path_factory)

    def build(folder: Path) -> Path:
        found = settings_vault(settings, folder)
        Gate(found).stage([memo(1, "Day rates."), memo(2, "Lunch on Friday.")])
        return found.root.parent

    return shared(build, "settings-tighten-" + digest(str(settings.resolve())), tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return settings_vault(template, tmp_path)


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with signed_in(vault) as found:
        yield found


def memo(n: int, text: str) -> RawRecord:
    return make_record(n, rid=f"memo:m-{n}", source="memo", kind="memo", container=f"memo:m-{n}", text=text)


def token(client: TestClient) -> str:
    found: str = client.get(PAGE).json()["token"]
    return found


def config(vault: Vault) -> VaultConfig:
    return VaultConfig.load(vault.root / CONFIG_FILE)


def subjects(vault: Vault) -> list[str]:
    return sh(vault.root, "git", "log", "--format=%s").splitlines()


def states(vault: Vault) -> list[str]:
    return sorted(e.staged.state for e in Gate(vault).list().entries if e.staged.record.source == "memo")


def post(client: TestClient, path: str, **body: Any) -> Response:
    return client.post(path, json=body, headers=SAME)


def said(answer: Response) -> dict[str, Any]:
    assert answer.status_code == 200, answer.text
    found: dict[str, Any] = answer.json()
    return found


def test_a_rule_is_turned_on_in_one_write_and_twice_is_one_write(client: TestClient, vault: Vault) -> None:
    rule = next(r for r in client.get(PAGE).json()["rules"] if r["button"])
    assert (rule["name"], rule["on"]) == ("phone", False)
    held = token(client)
    answer = said(post(client, RULE, rule="phone", state="on", token=held))
    assert answer["notice"].startswith("Phone numbers are redacted from now on. Run docket doc redact")
    assert config(vault).redaction.phone
    assert sh(vault.root, "git", "log", "-1", "--format=%B").strip() == "Config: redaction phone on\n\nvia page"
    # Sent again with the token the page read before it: so already, no write, the same words.
    again = said(post(client, RULE, rule="phone", state="on", token=held))
    assert again["notice"] == answer["notice"]
    assert subjects(vault).count("Config: redaction phone on") == 1
    off = said(post(client, RULE, rule="phone", state="off", token=token(client)))
    assert off["notice"].startswith("Phone numbers are no longer redacted, from the next sync")
    assert not config(vault).redaction.phone


def test_credentials_off_is_never_a_write_from_a_page(client: TestClient, vault: Vault) -> None:
    """The one change no page makes (DR-0601): the read names its command and gives it no button, and a crafted
    write is refused in the writer's words."""
    rules = {r["name"]: r for r in client.get(PAGE).json()["rules"]}
    assert (rules["credentials"]["button"], rules["credentials"]["on"]) == (False, True)
    assert rules["credentials"]["command"].startswith("docket redaction set credentials off")
    before = (vault.root / CONFIG_FILE).read_bytes()
    answer = post(client, RULE, rule="credentials", state="off", token=token(client))
    assert answer.status_code == 400
    assert "yours to confirm at your own console" in answer.json()["error"]
    bad = post(client, RULE, rule="phone", state="maybe", token=token(client))
    assert bad.status_code == 422
    assert (vault.root / CONFIG_FILE).read_bytes() == before


def test_a_pattern_is_turned_on_and_its_name_stays_out_of_the_notice(client: TestClient, vault: Vault) -> None:
    edit_config(vault, redaction=Redaction(patterns=[RedactionPattern(name="ticket-id", pattern="TCK", enabled=False)]))
    git(vault.root, "commit", "-qam", "the pattern off, by hand")
    answer = said(post(client, RULE, rule="ticket-id", state="on", token=token(client)))
    assert "ticket-id" not in answer["notice"]
    assert answer["notice"].startswith("Your pattern's matches are redacted from now on.")
    assert config(vault).redaction.patterns[0].enabled
    assert subjects(vault)[0] == "Config: redaction ticket-id on"


def test_a_source_made_mixed_or_turned_off_is_one_write_and_the_sweep_runs(client: TestClient, vault: Vault) -> None:
    assert states(vault) == ["ready", "ready"]
    mixed = said(post(client, SOURCE, source="memo", act="mixed", token=token(client)))
    assert mixed["notice"] == "memo is mixed: each record from it waits on the Staging page for work or private."
    assert config(vault).sources["memo"].sensitivity == "mixed"
    # Nobody classified them, and memo now asks: they wait again.
    assert states(vault) == ["pending", "pending"]
    off = said(post(client, SOURCE, source="memo", act="off", token=token(client)))
    assert off["notice"] == "memo is off: its syncs and scans stop, and what waits from it stays."
    assert not config(vault).sources["memo"].enabled
    assert states(vault) == ["pending", "pending"]
    assert subjects(vault)[:2] == ["Config: source memo off", "Config: source memo mixed"]


def test_a_source_made_private_asks_once_more_naming_what_waits(client: TestClient, vault: Vault) -> None:
    asked = said(post(client, SOURCE, source="memo", act="private", token="from-the-page"))  # noqa: S106 - made up
    question = asked["question"]
    assert question["source"] == "memo"
    assert "It drops 2 records of memo waiting in staging now." in question["lines"]
    assert any(line.startswith("From the next sync, nothing from memo is stored") for line in question["lines"])
    assert config(vault).sources["memo"].sensitivity == "work"
    answer = said(post(client, SOURCE, source="memo", act="private", token=question["token"], confirmed=True))
    assert answer["notice"].startswith("memo is private: nothing from it is stored")
    assert config(vault).sources["memo"].sensitivity == "private"
    assert states(vault) == []
    again = said(post(client, SOURCE, source="memo", act="private", token=question["token"], confirmed=True))
    assert again["notice"] == answer["notice"]
    assert subjects(vault).count("Config: source memo private") == 1


def test_a_page_older_than_the_file_writes_nothing(client: TestClient, vault: Vault) -> None:
    before = (vault.root / CONFIG_FILE).read_bytes()
    stale = "0" * 64
    rule = post(client, RULE, rule="phone", state="on", token=stale)
    mixed = post(client, SOURCE, source="memo", act="mixed", token=stale)
    for answer in (rule, mixed):
        assert answer.status_code == 409
        assert answer.json()["error"].startswith("vault.yaml changed since the page showed it, so nothing was written")
    private = post(client, SOURCE, source="memo", act="private", token=stale, confirmed=True)
    assert private.status_code == 409
    assert private.json()["error"].startswith("vault.yaml changed since you saw this, so nothing was written")
    assert (vault.root / CONFIG_FILE).read_bytes() == before


@pytest.mark.parametrize(
    ("body", "said_back"),
    [
        ({"source": "memo", "act": "looser"}, "a page makes a source work, mixed or private, or turns it on or off"),
        ({"source": "attic", "act": "off"}, "no source attic in vault.yaml"),
        ({"source": "../x", "act": "off"}, "source is a source's name from vault.yaml"),
        ({"source": "documents", "act": "mixed"}, "the change doesn't fit vault.yaml"),
    ],
)
def test_a_change_pages_don_t_make_is_refused_and_writes_nothing(
    client: TestClient, vault: Vault, body: dict[str, str], said_back: str
) -> None:
    before = (vault.root / CONFIG_FILE).read_bytes()
    answer = post(client, SOURCE, **body, token=token(client), confirmed=True)
    assert answer.status_code == 400
    assert said_back in answer.json()["error"]
    assert (vault.root / CONFIG_FILE).read_bytes() == before


def test_a_looser_sensitivity_and_a_source_turned_on_are_one_write_each(client: TestClient, vault: Vault) -> None:
    edit_config(vault, sources={**config(vault).sources, "memo": SourceConfig(sensitivity="private", enabled=False)})
    git(vault.root, "commit", "-qam", "memo private and off by hand")
    notices = [
        said(post(client, SOURCE, source="memo", act=act, token=token(client)))["notice"]
        for act in ("mixed", "work", "on")
    ]
    memo = config(vault).sources["memo"]
    assert (memo.sensitivity, memo.enabled) == ("work", True)
    assert subjects(vault)[:3] == ["Config: source memo on", "Config: source memo work", "Config: source memo mixed"]
    assert notices[1:] == [
        "memo is work: its records are stored without a look.",
        "memo is on: its syncs and scans start again.",
    ]
    rules = post(client, RULE, rule="nope", state="on", token=token(client))
    assert rules.status_code == 400
    assert "no redaction rule by that name" in rules.json()["error"]


def test_a_never_store_value_is_taken_off_in_one_write_naming_no_value(client: TestClient, vault: Vault) -> None:
    people = next(n for n in client.get(f"{PAGE}?show=lists").json()["lists"] if n["name"] == "people")
    assert [v["value"] for v in people["values"]] == ["Cleo Test", "Dora #2"]
    answer = post(client, ADD, act="off", name="people", value="Cleo Test", token=token(client))
    assert said(answer)["notice"].startswith("Took one value off people")
    assert "Cleo" not in answer.text
    assert config(vault).never_store.people == ["Dora #2"]
    assert subjects(vault)[0] == "Config: never-store people, 1 taken off"
    assert "Cleo" not in sh(vault.root, "git", "log", "-1", "--format=%B")
    again = said(post(client, ADD, act="off", name="people", value="Cleo Test", token=token(client)))
    assert again["notice"] == "The people list didn't hold that, so nothing changed."
    stale = post(client, ADD, act="off", name="people", value="Dora #2", token="0" * 64)
    assert stale.status_code == 409
    assert "Dora" not in stale.text
    assert config(vault).never_store.people == ["Dora #2"]


def test_an_uncommitted_credentials_off_is_refused_at_each_step(client: TestClient, vault: Vault) -> None:
    path = vault.root / CONFIG_FILE
    edited = path.read_text(encoding="utf-8").replace("credentials: true", "credentials: false", 1)
    path.write_text(edited, encoding="utf-8", newline="\n")
    at = sh(vault.root, "git", "rev-parse", "HEAD")
    held = token(client)
    answers = [
        post(client, RULE, rule="phone", state="on", token=held),
        post(client, SOURCE, source="memo", act="off", token=held),
        post(client, SOURCE, source="memo", act="private", token=held),
        post(client, SOURCE, source="memo", act="private", token=held, confirmed=True),
    ]
    for answer in answers:
        assert answer.status_code == 400
        assert settings.UNCOMMITTED_CREDENTIALS_OFF in answer.json()["error"]
    assert path.read_text(encoding="utf-8") == edited
    assert sh(vault.root, "git", "rev-parse", "HEAD") == at


def test_any_other_uncommitted_edit_goes_in_first(client: TestClient, vault: Vault) -> None:
    path = vault.root / CONFIG_FILE
    edited = path.read_text(encoding="utf-8").replace("sensitivity: mixed", "sensitivity: work", 1)
    path.write_text(edited, encoding="utf-8", newline="\n")
    assert said(post(client, RULE, rule="phone", state="on", token=token(client)))["notice"]
    assert subjects(vault)[:2] == ["Config: redaction phone on", "Config: vault.yaml as edited"]


def test_a_hand_edited_file_keeps_its_buttons_and_is_edited_in_place(client: TestClient, vault: Vault) -> None:
    path = vault.root / CONFIG_FILE
    mine = path.read_text(encoding="utf-8").replace("sources:\n", "sources:   # mine\n", 1)
    path.write_text(mine, encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qam", "A comment of mine")
    found = client.get(PAGE).json()
    assert [r for r in found["rules"] if r["button"]] and found["sources"][0]["changes"]
    assert said(post(client, SOURCE, source="memo", act="off", token=found["token"]))["notice"]
    assert "sources:   # mine\n" in path.read_text(encoding="utf-8")
    assert not config(vault).sources["memo"].enabled


def test_a_change_docket_can_t_place_gets_the_line(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    cant_place(monkeypatch)
    path = vault.root / CONFIG_FILE
    mine = path.read_text(encoding="utf-8").replace("sources:\n", "sources:   # mine\n", 1)
    path.write_text(mine, encoding="utf-8", newline="\n")
    held = token(client)
    rule = said(post(client, RULE, rule="phone", state="on", token=held))
    off = said(post(client, SOURCE, source="memo", act="off", token=held))
    private = said(post(client, SOURCE, source="memo", act="private", token=held, confirmed=True))
    assert rule["warning"].endswith("change it there: phone: true, under redaction:.")
    assert off["warning"].endswith("change it there: enabled: false, under sources.memo.")
    assert private["warning"].endswith("change it there: sensitivity: private, under sources.memo.")
    assert (rule["notice"], off["notice"], private["notice"]) == (None, None, None)
    assert path.read_text(encoding="utf-8") == mine


def test_a_value_docket_can_t_take_off_in_place_is_left_to_the_owner(client: TestClient, vault: Vault) -> None:
    """An alias anywhere in the file: no splice may change what else reads it (DR-0602), so the page says which line to
    delete and writes nothing."""
    path = vault.root / CONFIG_FILE
    text = path.read_text(encoding="utf-8")
    mine = text.replace("  chat:\n    enabled: true\n", "  chat:\n    enabled: &on true\n", 1)
    mine = mine.replace("  memo:\n    enabled: true\n", "  memo:\n    enabled: *on\n", 1)
    assert mine.count("&on") == mine.count("*on") == 1
    path.write_text(mine, encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qam", "An alias of mine")
    at = sh(vault.root, "git", "rev-parse", "HEAD")
    answer = post(client, ADD, act="off", name="people", value="Cleo Test", token=token(client))
    assert said(answer)["warning"].endswith("change it there: delete its line under never_store.people.")
    assert "Cleo" not in answer.text
    assert path.read_text(encoding="utf-8") == mine
    assert sh(vault.root, "git", "rev-parse", "HEAD") == at


def test_a_list_s_last_values_come_off_in_place_with_a_colon_in_its_key_s_comment(
    client: TestClient, vault: Vault
) -> None:
    """The comment on the list's key line stays after the emptied list, its colon never taken for the key's."""
    path = vault.root / CONFIG_FILE
    mine = path.read_text(encoding="utf-8").replace("  people:\n", "  people:   # names: never stored\n", 1)
    path.write_text(mine, encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qam", "A comment of mine")
    for value in ("Cleo Test", "Dora #2"):
        answer = said(post(client, ADD, act="off", name="people", value=value, token=token(client)))
        assert answer["notice"].startswith("Took one value off people")
    text = path.read_text(encoding="utf-8")
    assert "  people: []   # names: never stored\n  containers:\n" in text
    assert config(vault).never_store.people == []


@pytest.mark.parametrize(("headers", "status"), [({"Origin": "http://evil.example"}, 403), ({}, 403), (SAME, 200)])
def test_every_write_passes_the_guard(client: TestClient, headers: dict[str, str], status: int) -> None:
    held = token(client)
    rule = client.post(RULE, json={"rule": "phone", "state": "on", "token": held}, headers=headers)
    assert rule.status_code == status
    source = client.post(SOURCE, json={"source": "chat", "act": "off", "token": token(client)}, headers=headers)
    assert source.status_code == status


def test_a_get_is_405_and_a_body_that_isn_t_json_is_refused(client: TestClient) -> None:
    for route in (RULE, SOURCE, ADD):
        assert client.get(route).status_code == 405
        assert client.post(route, data={"rule": "phone"}, headers=SAME).status_code == 415


def test_a_source_nobody_has_gets_no_question(client: TestClient) -> None:
    answer = post(client, SOURCE, source="attic", act="private", token=token(client))
    assert answer.status_code == 400
    assert "no source attic in vault.yaml" in answer.json()["error"]
