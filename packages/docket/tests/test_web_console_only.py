"""The two steps only the owner's console takes (DR-0599): turning credentials redaction off (DR-0601) and forgetting
for real (DR-0577). No page and no route takes either, whatever the body: every write route the service has is sent
bodies that ask for one, with the browser's session and with the token, and afterwards credentials are still redacted,
in the file and at HEAD, and nothing was forgotten. Invented settings only: settings_kit's."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import sh
from docket_webkit import OURS
from settings_kit import settings_template, settings_vault
from staging_kit import signed_in
from starlette.testclient import TestClient

from docket.service.app import ServiceApp
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.vault import Vault

SAME = {"Origin": OURS, "Sec-Fetch-Site": "same-origin"}
TOKEN = {"Authorization": "Bearer t0ken-for-tests-only"}
RUN = "run-20261015T000000-ffffffffffffffff"
# What a body could say to ask for either step, alone and beside each write's own keys.
ASKS: list[dict[str, Any]] = [
    {},
    {"rule": "credentials", "state": "off"},
    {"rule": "credentials", "state": "off", "confirmed": True, "console": True, "yes": True},
    {"rule": " credentials ", "state": "off"},
    {"rule": "Credentials", "state": "off"},
    {"redaction": {"credentials": False}},
    {"credentials": False},
    {"act": "off", "name": "credentials", "value": "credentials"},
    {"source": "credentials", "act": "off", "confirmed": True},
    {"act": "forget", "now": True, "rid": "calls:rec-0100"},
    {"forget": True, "now": True},
    {"op": "forget", "now": True, "by": "user"},
]


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


def writes(client: TestClient) -> list[str]:
    """Every route of the service that takes a write, from its own OpenAPI document, its run filled in."""
    app = client.app
    assert isinstance(app, ServiceApp)
    paths = app.api.openapi()["paths"]
    return sorted(p.replace("{run}", RUN) for p, ops in paths.items() if set(ops) - {"get", "head"})


def credentials(vault: Vault) -> tuple[bool, bool]:
    """Whether credentials are redacted in vault.yaml on disk and at HEAD."""
    disk = VaultConfig.load(vault.root / CONFIG_FILE).redaction.credentials
    head = VaultConfig.parse(sh(vault.root, "git", "show", f"HEAD:{CONFIG_FILE}"), vault.root / CONFIG_FILE)
    return disk, head.redaction.credentials


def test_no_route_turns_credentials_off_or_forgets_whatever_the_body(client: TestClient, vault: Vault) -> None:
    routes = writes(client)
    assert {"/api/web/settings/redaction", "/api/web/settings/source", "/api/web/settings/never-store"} <= set(routes)
    assert not [r for r in routes if "forget" in r]
    before = sh(vault.root, "git", "rev-parse", "HEAD").strip()
    answered: dict[int, int] = {}
    for route in routes:
        for ask in ASKS:
            token = client.get("/api/web/settings").json()["token"]
            for headers in (SAME, TOKEN):
                answer = client.post(route, json={**ask, "token": token}, headers=headers)
                assert answer.status_code != 500, (route, ask, answer.text)
                answered[answer.status_code] = answered.get(answer.status_code, 0) + 1
    assert sum(answered.values()) == len(routes) * len(ASKS) * 2
    # The bodies reached the routes' own checks, and some wrote: a 401 is a route that takes the other way in only.
    assert {200, 400, 422} <= set(answered)
    assert credentials(vault) == (True, True)
    assert not (vault.root / "state" / "forgotten.jsonl").exists()
    made = sh(vault.root, "git", "log", "--format=%B", f"{before}..HEAD")
    assert "credentials off" not in made
    assert "confirmed at the console" not in made
    assert "forget" not in made.lower()


def test_the_settings_page_names_the_command_and_refuses_the_write(client: TestClient, vault: Vault) -> None:
    rules = {r["name"]: r for r in client.get("/api/web/settings").json()["rules"]}
    assert (rules["credentials"]["on"], rules["credentials"]["button"]) == (True, False)
    assert f'docket redaction set credentials off --vault "{vault.root}"' == rules["credentials"]["command"]
    token = client.get("/api/web/settings").json()["token"]
    answer = client.post(
        "/api/web/settings/redaction", json={"rule": "credentials", "state": "off", "token": token}, headers=SAME
    )
    assert answer.status_code == 400
    assert "yours to confirm at your own console" in answer.json()["error"]
    assert credentials(vault) == (True, True)
