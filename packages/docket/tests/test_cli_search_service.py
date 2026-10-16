"""`docket search` through the running service (#110, DR-0500): the real service on a free port in a thread, the
command's output the same whether the service answered or the command searched by itself, and the command on its own
whenever the service can't answer for it."""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from docket_testkit import copy_vault, index_template
from models_kit import GPU_RERANKER, choose_fake, fake_command, make_env, needs_vectors, rerank_worker, with_reranker

from docket import cli
from docket.cli import main
from docket.errors import DocketError
from docket.models import runtime
from docket.models.client import Worker
from docket.service import client, server
from docket.service.token import Tokens
from docket.vault.vault import Vault

# What a search can print: a rebuild line aside, groups and summaries, an alias, time words and a name nobody has, a
# range, a source with a person and an entity lifted, nothing, a word in no chunk, anchors; then two refusals, one the
# service gives and one the command gives before asking.
QUERIES: list[tuple[str, ...]] = [
    ("offer", "in", "writing", "-k", "5"),
    ("Nortwin",),
    ("múlt", "héten", "pallet", "--person", "Nobody"),
    ("pallet", "--since", "2026-10-08", "--until", "2026-10-08T12:00"),
    ("pallet", "--source", "calls", "--person", "Ben Sample", "--entity", "harbour-site"),
    ("zebracorn",),
    ("the", "pallet", "zebracorn"),
    ("deadline", "with", "Ben"),
    ("pallet", "-k", "101"),
    ("pallet", "--since", "last tuesday"),
]
ASKED = len(QUERIES) - 1


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return copy_vault(template, tmp_path)


Run = tuple[int, str, str]


def run(vault: Vault, capsys: pytest.CaptureFixture[str], *args: str) -> Run:
    code = main([*args, "--vault", str(vault.root)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@contextlib.contextmanager
def serving(vault: Vault) -> Iterator[tuple[int, str]]:
    """The real service for the vault on a free port, in a thread: its port and this machine's token."""
    token = Tokens().ensure().value
    servers: list[uvicorn.Server] = []
    runner = threading.Thread(
        target=server.serve, args=(vault.root, 0), kwargs={"said": lambda _: None, "running": servers.append}
    )
    runner.start()
    try:
        port = _wait(_healthy, "the service didn't start")
        yield port, token
    finally:
        while not servers and runner.is_alive():
            time.sleep(0.05)
        if servers:
            servers[0].should_exit = True
        runner.join(30)


def _healthy() -> int | None:
    record = client.recorded()
    if record is None:
        return None
    answer = client.call("GET", "/api/health", at=record["port"])
    return int(record["port"]) if answer is not None and answer.status == 200 else None


def _wait[T](check: Callable[[], T | None], why: str) -> T:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        found = check()
        if found is not None:
            return found
        time.sleep(0.05)
    raise AssertionError(why)


class Asked:
    """The paths the command asks the service for, each call going through to the real one."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.paths: list[str] = []
        real = client.call

        def call(method: str, path: str, **kwargs: Any) -> client.Answer | None:
            self.paths.append(path.split("?", maxsplit=1)[0])
            return real(method, path, **kwargs)

        monkeypatch.setattr(client, "call", call)


def test_the_service_answers_what_the_command_prints_by_itself(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    run(vault, capsys, "index", "update")
    alone = [run(vault, capsys, "search", *query) for query in QUERIES]
    with serving(vault):
        asked = Asked(monkeypatch)
        through = [run(vault, capsys, "search", *query) for query in QUERIES]
    assert asked.paths == ["/api/models", "/api/search"] * ASKED
    assert through == alone
    # The queries said what they were there to say: a search that printed nothing would compare equal too.
    outs = [out for _, out, _ in alone]
    assert (
        "recording  Supplier call with Northwind  (calls  2026-10-07)" in outs[0]
        and "'nortwin' is northwind" in outs[1]
    )
    assert (
        "not found as a person or entity: Nobody" in outs[2] and "from 2026-10-08 00:00 to 2026-10-08 12:00" in outs[3]
    )
    assert "nothing found" in outs[5] and "in no chunk: zebracorn" in outs[6] and "       -> r:calls:" in outs[7]
    assert alone[-2][0] == alone[-1][0] == 2
    assert "k is from 1 to 100" in alone[-2][2] and "--since is a date or a time" in alone[-1][2]


def test_the_command_searches_by_itself_when_the_service_cant_answer_for_it(
    template: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = copy_vault(template, tmp_path / "mine")
    other = copy_vault(template, tmp_path / "other")
    for v in (mine, other):
        run(v, capsys, "index", "update")
    expected = run(mine, capsys, "search", "Nortwin")
    assert expected[0] == 0 and "'nortwin' is northwind" in expected[1]
    with serving(mine):
        asked = Asked(monkeypatch)
        # Its search updates the index and uses its models: neither flag would hold through it.
        assert run(mine, capsys, "search", "Nortwin", "--no-update") == expected
        assert run(mine, capsys, "search", "Nortwin", "--no-models") == expected
        # A service for another vault isn't asked at all, nor one whose token can't be read.
        assert run(other, capsys, "search", "Nortwin") == expected
        with monkeypatch.context() as m:
            m.setattr(cli, "_tokens", lambda: Token("no token yet"))
            assert run(mine, capsys, "search", "Nortwin") == expected
        assert asked.paths == []
        # Models installed after the service started: it would answer without the vectors the command has.
        choose_fake(monkeypatch)
        make_env("cpu")
        monkeypatch.setattr(runtime, "missing_files", lambda model: [])
        monkeypatch.setattr(runtime, "worker_for", lambda choice, device, rerank=None: Worker(fake_command()))
        code, out, _ = run(mine, capsys, "search", "Nortwin")
        assert code == 0 and "'nortwin' is northwind" in out and asked.paths == ["/api/models"]


class Token:
    def __init__(self, why: str | None = None) -> None:
        self.why = why

    def read(self) -> str:
        if self.why is not None:
            raise DocketError(self.why)
        return "t0ken-for-tests-only"


OFF: dict[str, Any] = {"state": "off", "model": None}


class Fake:
    """A service that serves the vault and answers its models and its search as a test sets them."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, vault: Vault, models: client.Answer | None, search: client.Answer | None
    ) -> None:
        self.models, self.search = models, search
        self.asked: list[str] = []
        monkeypatch.setattr(client, "recorded", lambda: {"vault": str(vault.root), "port": 9})
        monkeypatch.setattr(cli, "_tokens", Token)
        monkeypatch.setattr(client, "call", self.call)

    def call(self, method: str, path: str, **_: Any) -> client.Answer | None:
        self.asked.append(path.split("?", maxsplit=1)[0])
        return self.models if path == "/api/models" else self.search


@pytest.mark.parametrize(
    ("models", "search", "asked"),
    [
        (client.Answer(200, {"state": "up", "model": "fake"}), None, ["/api/models"]),
        (client.Answer(200, {"state": "down", "model": "fake"}), None, ["/api/models"]),
        (client.Answer(401, {}), None, ["/api/models"]),
        (client.Answer(200, OFF), None, ["/api/models", "/api/search"]),
        (
            client.Answer(200, OFF),
            client.Answer(400, {"error": "the index is being built; try again in a minute"}),
            ["/api/models", "/api/search"],
        ),
        (client.Answer(200, OFF), client.Answer(200, {"groups": "not a list"}), ["/api/models", "/api/search"]),
    ],
    ids=["another embedder", "worker down", "refused", "no answer in time", "index building", "not a result"],
)
def test_any_answer_but_a_result_leaves_the_search_to_the_command(
    vault: Vault,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    models: client.Answer,
    search: client.Answer | None,
    asked: list[str],
) -> None:
    run(vault, capsys, "index", "update")
    expected = run(vault, capsys, "search", "Nortwin")
    fake = Fake(monkeypatch, vault, models, search)
    assert run(vault, capsys, "search", "Nortwin") == expected and fake.asked == asked


def test_the_search_goes_to_the_service_with_every_flag_as_given(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    run(vault, capsys, "index", "update")
    asked: list[tuple[str, float | None]] = []

    def call(method: str, path: str, **kwargs: Any) -> client.Answer | None:
        asked.append((path, kwargs.get("timeout")))
        return client.Answer(200, OFF) if path == "/api/models" else None

    Fake(monkeypatch, vault, None, None)
    monkeypatch.setattr(client, "call", call)
    run(vault, capsys, "search", "múlt", "héten", "--person", "Ben", "--person", "Cleo Test", "--entity", "northwind",
        "--source", "calls", "--since", "2026-10-01", "--until", "2026-10-08T12:00", "-k", "7")  # fmt: skip
    # The dates go as given: the service reads them as the command does, in the owner's time zone.
    assert asked == [
        ("/api/models", None),
        (
            "/api/search?query=m%C3%BAlt+h%C3%A9ten&people=Ben&people=Cleo+Test&entities=northwind&sources=calls"
            "&since=2026-10-01&until=2026-10-08T12%3A00&k=7",
            cli.SERVICE_SEARCH_SECONDS,
        ),
    ]


@needs_vectors
def test_on_a_gpu_machine_the_command_gets_the_services_reranker(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with_reranker(monkeypatch, GPU_RERANKER, 0.13)
    rerank_worker(monkeypatch)
    run(vault, capsys, "index", "update")
    code, alone, _ = run(vault, capsys, "search", "pallet")
    assert (
        code == 0
        and "not reranked: the reranker runs on the GPU in the service; docket search goes without it" in alone
    )
    with serving(vault) as (port, token):

        def up() -> dict[str, Any] | None:
            answer = client.call("GET", "/api/models", token=token, at=port)
            return answer.body if answer is not None and answer.body.get("state") == "up" else None

        assert _wait(up, "the service's worker didn't come up")["reranked"] == 0
        code, through, _ = run(vault, capsys, "search", "pallet")
        status = up()
    assert code == 0 and "not reranked" not in through and "no vectors" not in through
    assert status is not None and status["reranker"] == "fake-gpu-reranker" and status["reranked"] == 1
