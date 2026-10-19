"""A search through the service against the same search in process, on a folder vault (#151).

The service opens the vault for each call, twice for a search (its index update, then the search), and a folder
vault's open asked git where its repo is, twice: about 0.7 s a search on Windows, where the search itself takes tens of
milliseconds. The untimed test holds that a warm search asks git nothing, under coverage too; the timed one, alone in
its lane, pins the service's search within 150 ms of the one in process.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import best_of, copied, git, make_config, make_record, traced
from starlette.testclient import TestClient

from docket import index
from docket.service.app import create_app
from docket.service.state import Settings
from docket.vault import home
from docket.vault.gate import Gate
from docket.vault.vault import Vault

TOKEN = "t0ken-for-tests-only"  # noqa: S105 - an invented token
PORT = 8799
OURS = f"http://127.0.0.1:{PORT}"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
RECORDS = 500
ROUNDS = 10
MARGIN = 0.150
QUERY = "crane offer"
WORDS = [
    "crane", "order", "offer", "supplier", "budget", "plan", "route", "lifting", "site", "visit", "review", "contract",
    "steel",
]  # fmt: skip
KEY = "service-search-folder-vault"


def build(folder: Path) -> Path:
    """A folder vault in a host repo of the user's, with a few months of messages, indexed."""
    top = folder / "host"
    top.mkdir()
    git(top, "init", "-q", "-b", "main")
    (top / "README.md").write_text("the user's own repo\n", encoding="utf-8")
    git(top, "add", "README.md")
    git(top, "commit", "-qm", "Host start")
    vault = Vault.init(top / "notes" / "vault", make_config("folder"), "test-box")
    start = datetime(2026, 7, 1, tzinfo=UTC)
    records = [
        make_record(
            n,
            text=" ".join(WORDS[(n * step) % len(WORDS)] for step in range(1, 30)) + f" message {n}",
            ts=start + timedelta(minutes=47 * n),
        )
        for n in range(RECORDS)
    ]
    gate = Gate(vault)
    gate.stage(records)
    gate.promote()
    index.update(vault)
    return vault.root


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return Vault.open(copied(tmp_path, build, KEY))


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    app = create_app(Settings(port=PORT, vault=vault.root, token=lambda: TOKEN, store="the system keychain"))
    with TestClient(app, base_url=OURS) as found:
        yield found


def searched(client: TestClient) -> dict[str, Any]:
    answer = client.get("/api/search", params={"query": QUERY}, headers=BEARER)
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_a_warm_search_through_the_service_asks_git_nothing(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    searched(client)
    asked: list[Path] = []
    real = home.rev_parse

    def spy(cwd: Path, *flags: str) -> list[str] | None:
        asked.append(cwd)
        return real(cwd, *flags)

    monkeypatch.setattr(home, "rev_parse", spy)
    body = searched(client)
    # The service still opens the vault for the update and for the search, so vault.yaml applies at once.
    assert asked == []
    alone = index.search(Vault.open(vault.root), QUERY).model_dump(mode="json")
    assert body["groups"] and body["groups"] == alone["groups"]


@pytest.mark.perf
def test_a_warm_search_through_the_service_answers_within_150_ms_of_one_in_process(
    client: TestClient, vault: Vault
) -> None:
    def per_search(search: Any) -> tuple[float, str]:
        """A search's seconds in the fastest of three runs of ten, after three to warm it, and each run's ten."""
        for _ in range(3):
            search()
        run = best_of(3, lambda: [search() for _ in range(ROUNDS)])
        return run.seconds / ROUNDS, run.runs("ms")

    opened = Vault.open(vault.root)
    through, through_runs = per_search(lambda: searched(client))
    alone, alone_runs = per_search(lambda: index.search(opened, QUERY))
    print(
        f"\na warm search through the service: {through * 1000:.0f} ms (runs of {ROUNDS}: {through_runs}), in process: "
        f"{alone * 1000:.0f} ms ({alone_runs})"
    )
    if not traced():
        assert through - alone < MARGIN
