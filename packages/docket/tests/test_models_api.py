"""The API provider (#89, DR-0536 to DR-0539): Voyage AI behind the models' runner, on a copy of the index fixture.

Every test that sends installs `FakeVoyage` in place of the one network path; conftest makes the real one fail any test
that reaches it, and the key is invented and kept in an in-memory keychain. What the fake was sent is what would have
left the machine.
"""

from __future__ import annotations

import http.server
import ssl
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from docket_testkit import copy_vault, git, index_template, make_record
from models_kit import FAKE_KEY, MemoryKeyring, choose, with_voyage

from docket import index
from docket.errors import DocketError
from docket.index import query as query_module
from docket.index import screen as screening
from docket.index import vectors
from docket.index.build import db_path, open_index, write_connection
from docket.index.embedpass import fill
from docket.models import voyage
from docket.models.catalogue import VOYAGE_4
from docket.models.client import PAST_BUDGET, Skip, WorkerError
from docket.models.provider import Models, Off
from docket.models.runtime import open_models
from docket.vault.config import RedactionPattern, SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

# The network path as shipped, kept before conftest swaps it, for the one test that runs it against a local server.
REAL_SEND = voyage.send
WHEN = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    copied = copy_vault(template, tmp_path)
    index.update(copied)
    return copied


def commit_config(vault: Vault, change: Callable[[VaultConfig], None], message: str = "Settings") -> Vault:
    """vault.yaml changed as the owner would, and committed, so it is in force (DR-0521)."""
    path = vault.root / "vault.yaml"
    config = VaultConfig.load(path)
    change(config)
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qm", message, "--", "vault.yaml")
    return Vault.open(vault.root)


def allow_api(vault: Vault) -> Vault:
    return commit_config(vault, lambda c: setattr(c, "api_provider", "voyage"), "Allow voyage")


def message(n: int, text: str, *, source: str = "chat", **fields: Any) -> RawRecord:
    """A record alone in its own container, so it is a chunk of its own."""
    return make_record(
        n, rid=f"{source}:Z{n}/{1791000000 + n}.000100", source=source, container=f"{source}:Z{n}", text=text, **fields
    )


def add(vault: Vault, source: str, records: list[RawRecord]) -> Vault:
    vault.write_run(source, records, WHEN)
    opened = Vault.open(vault.root)
    index.update(opened)
    return opened


def api(vault: Vault) -> Models:
    models = open_models(vault=Vault.open(vault.root))
    assert isinstance(models, Models), models
    return models


# -- what leaves the machine ---------------------------------------------------------------------------------------


def test_nothing_staged_or_never_store_reaches_the_provider(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = with_voyage(monkeypatch)
    vault = allow_api(vault)
    vault = add(
        vault,
        "chat",
        [
            message(1, "the zebrakept invoice for the pallet is late"),
            message(2, "zebranever wrote about the pallet", author="U0NEVER"),
            message(3, "the zebraredact pallet account is ACCT-1234"),
        ],
    )
    # Staged, never promoted: staging is never a chunk.
    Gate(vault).stage([message(4, "the zebrastaged pallet order went out")])

    def tighten(config: VaultConfig) -> None:
        config.never_store.keywords = ["zebrakept"]
        config.never_store.people = ["U0NEVER"]
        config.redaction.patterns = [RedactionPattern(name="account", pattern=r"ACCT-\d+")]

    vault = commit_config(vault, tighten)
    index.update(vault)
    models = api(vault)
    report = fill(vault, models)
    assert report.why is None and report.held >= 3 and report.embedded > 0
    for query in ("pallet", "invoice", "zebrakept"):
        index.search(vault, query, models=models)
    sent = fake.sent()
    assert sent and "pallet" in sent
    for word in ("zebrakept", "zebranever", "zebraredact", "ACCT-1234", "zebrastaged"):
        assert word not in sent, word


def test_a_source_made_private_or_removed_keeps_its_records_here(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = with_voyage(monkeypatch)
    vault = allow_api(vault)
    vault = add(vault, "mail", [message(11, "zebramail pallet delivery", source="mail")])
    vault = add(vault, "cal", [message(12, "zebracal pallet delivery", source="cal")])
    vault = add(vault, "chat", [message(13, "zebrachat pallet delivery")])
    models = api(vault)
    fill(vault, models)
    db = open_index(vault)
    try:
        before = vectors.screened(db, vectors.table_of(models.embedder))
    finally:
        db.close()
    assert before is not None

    def judge(config: VaultConfig) -> None:
        config.sources["mail"] = SourceConfig(sensitivity="private")
        del config.sources["cal"]
        config.sources["chat"] = SourceConfig(enabled=False)

    vault = commit_config(vault, judge)
    assert screening.fingerprint(vault.config) != before
    # The same models, as the service keeps them: the rerank reads the settings in force on this search.
    fake.requests.clear()
    result = index.search(vault, "zebramail zebracal zebrachat pallet delivery", models=models, k=50)
    assert result.rerank.state == "skipped" and result.rerank.why == screening.STALE
    assert all(path != "/rerank" for path in fake.paths())
    fill(vault, models)
    fake.requests.clear()
    index.search(vault, "zebramail zebracal zebrachat pallet delivery", models=models, k=50)
    reranked = "\n".join(str(d) for path, body in fake.requests if path == "/rerank" for d in body["documents"])
    assert "zebrachat" in reranked  # off is a pause, not a judgement
    assert "zebramail" not in reranked and "zebracal" not in reranked


def test_a_vector_an_update_copies_never_sends_a_private_source_s_chunk(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_voyage(monkeypatch)
    vault = allow_api(vault)
    vault = add(vault, "chat", [message(61, "zebratwin pallet delivery")])
    models = api(vault)
    fill(vault, models)
    vault = commit_config(vault, lambda c: c.sources.__setitem__("mail", SourceConfig(sensitivity="private")))
    fill(vault, models)
    # The same input from a private source: the update hands it the work chunk's vector, unscreened.
    vault = add(vault, "mail", [message(62, "zebratwin pallet delivery", source="mail")])
    db = open_index(vault)
    try:
        twin = int(db.execute("SELECT id FROM chunks WHERE rid = ?", ("mail:Z62/1791000062.000100",)).fetchone()[0])
        assert vectors.having(db, vectors.table_of(models.embedder), [twin]) == {twin}
    finally:
        db.close()
    placed: list[list[int]] = []
    real = query_module._in_places  # pyright: ignore[reportPrivateUsage]

    def spy(top: list[int], sent: list[int], order: list[int]) -> list[int]:
        placed.append(sent)
        return real(top, sent, order)

    monkeypatch.setattr(query_module, "_in_places", spy)
    result = index.search(vault, "zebratwin pallet delivery", models=models, k=50)
    assert result.rerank.state == "on" and placed and twin not in placed[-1]


def test_a_record_that_cant_be_read_holds_its_chunk_and_a_doc_covering_none_is_read_by_its_text(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_voyage(monkeypatch)
    vault = allow_api(vault)
    screen = screening.Screen(vault, "voyage", vectors.table_of(api(vault).embedder))
    db = open_index(vault)
    try:
        lookup = screen.lookup()
        rows = [
            screening.Row(1, None, "a call about pallets", "a call about pallets", ("calls:missing-0001",), None),
            screening.Row(2, None, "a legacy note on pallets", "a legacy note on pallets", (), None),
            screening.Row(3, None, "a legacy note on zebrakept", "a legacy note on zebrakept", (), None),
            screening.Row(4, None, "a message on pallets", "a message on pallets", ("mail:Z71/1.0",), None),
        ]
        config = vault.config.model_copy(update={"never_store": vault.config.never_store.model_copy(
            update={"keywords": ["zebrakept"]})})  # fmt: skip
        # A run file that doesn't parse here: its records can't be checked, so their chunks stay.
        broken = vault.root / "raw" / "mail" / "2026" / "10" / "2026-10-15T0900-test-box.jsonl"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("not a record\n", encoding="utf-8")
        assert screen.held(db, rows, config, lookup) == {1, 3, 4}
    finally:
        db.close()


def test_settings_that_dont_read_send_nothing(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    with_voyage(monkeypatch)
    vault = allow_api(vault)
    screen = screening.Screen(vault, "voyage", vectors.table_of(api(vault).embedder))
    (vault.root / "vault.yaml").write_text("owner: [not, a, map\n", encoding="utf-8")
    db = write_connection(db_path(vault))
    try:
        assert screen.allowed() == screening.UNREADABLE
        assert screen.query("pallet") == screening.UNREADABLE
        assert screen.sendable(db, [1, 2]) == screening.UNREADABLE
        table = vectors.table_of(VOYAGE_4)
        db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (vectors.META + table.key, "{not json"))
        assert vectors.screened(db, table) is None
    finally:
        db.close()


def test_a_query_holding_a_never_store_value_stays_here(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = with_voyage(monkeypatch)
    vault = allow_api(vault)

    def lists(config: VaultConfig) -> None:
        config.never_store.people = ["Dora Example"]
        config.never_store.containers = ["chat:C0SECRET"]
        config.never_store.keywords = ["zebrakept"]

    vault = commit_config(vault, lists)
    models = api(vault)
    for query in ("Dora Example", "Example Dora", "what did Dora Example say", "chat:C0SECRET", "about zebrakept"):
        result = index.search(vault, query, models=models)
        assert result.models.why == screening.HELD_QUERY, query
        assert "Dora" not in str(result.models.why)
    assert fake.requests == []


def test_an_anchored_pattern_holds_back_a_chunk_by_its_text_alone(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = with_voyage(monkeypatch)
    vault = allow_api(vault)
    vault = add(vault, "chat", [message(21, "ACCT-1234 is the zebrabudget account", kind="note", title="Budget call")])
    patterns = [RedactionPattern(name="account", pattern=r"^ACCT-\d+")]
    vault = commit_config(vault, lambda c: setattr(c.redaction, "patterns", patterns))
    report = fill(vault, api(vault))
    assert report.held >= 1
    assert "zebrabudget" not in fake.sent()


def test_candidates_not_sent_keep_their_places(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    assert query_module._in_places([1, 2, 3, 4, 5], [1, 3, 5], [5, 1, 3]) == [5, 2, 1, 4, 3]  # pyright: ignore[reportPrivateUsage]
    fake = with_voyage(monkeypatch)
    vault = allow_api(vault)
    models = api(vault)
    fill(vault, models)
    # Ingested after the pass: in the top N with no vector yet.
    vault = add(vault, "chat", [message(31, "pallet delivery zebrafresh pallet delivery")])
    db = open_index(vault)
    try:
        fresh = int(db.execute("SELECT id FROM chunks WHERE rid = ?", ("chat:Z31/1791000031.000100",)).fetchone()[0])
    finally:
        db.close()
    placed: list[tuple[list[int], list[int], list[int]]] = []
    real = query_module._in_places  # pyright: ignore[reportPrivateUsage]

    def spy(top: list[int], sent: list[int], order: list[int]) -> list[int]:
        out = real(top, sent, order)
        placed.append((top, sent, out))
        return out

    monkeypatch.setattr(query_module, "_in_places", spy)
    fake.requests.clear()
    result = index.search(vault, "pallet delivery zebrafresh", models=models, k=50)
    assert result.rerank.state == "on" and placed
    top, sent, out = placed[-1]
    assert fresh in top and fresh not in sent and out.index(fresh) == top.index(fresh)
    assert any("zebrafresh" in str(body.get("query", "")) for _, body in fake.requests)
    assert not any("zebrafresh pallet" in str(d) for _, body in fake.requests for d in body.get("documents", []))


def test_the_pass_screens_a_batch_again_when_the_settings_move(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = with_voyage(monkeypatch)
    vault = allow_api(vault)
    vault = add(vault, "chat", [message(41, "the zebralater pallet came last")])
    asked: list[int] = []

    def tighten_once(path: str, body: dict[str, Any]) -> None:
        if not asked:
            asked.append(1)
            commit_config(vault, lambda c: setattr(c.never_store, "keywords", ["zebralater"]))

    fake.before = tighten_once
    report = fill(vault, api(vault), rows=4)
    assert asked and report.held >= 1
    assert "zebralater" not in fake.sent()


def test_each_lock_missing_turns_the_provider_off_with_why(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = with_voyage(monkeypatch, key=False)
    models = api(vault)
    assert models.why == screening.not_allowed("voyage")
    vault = allow_api(vault)
    models = api(vault)
    assert models.why == voyage.NO_KEY
    assert models.query_vector("pallet") == voyage.NO_KEY
    unopened = open_models(vault=None)
    assert isinstance(unopened, Off)
    assert unopened.why == "the api provider opens for a vault, whose settings say whether its text may go"
    choose({"provider": "none"})
    assert open_models(vault=vault) == Off()
    assert fake.requests == []


def test_a_slow_or_failing_rerank_keeps_the_fused_order(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = with_voyage(monkeypatch)
    vault = allow_api(vault)
    models = api(vault)
    fill(vault, models)
    for _ in range(3):
        fake.fail["/rerank"] = ["late"]
        index.search(vault, "pallet delivery", models=models)
    assert models.counts.past_budget == 3 and models.counts.paused_until is not None
    models = api(vault)
    for status in (429, 500, 401):
        fake.requests.clear()
        fake.fail["/rerank"] = [status]
        result = index.search(vault, "pallet delivery", models=models)
        assert result.rerank.state == "skipped"
        assert result.rerank.why == (voyage.REFUSED if status == 401 else f"Voyage answered {status}")
        assert fake.paths().count("/rerank") == 1
    assert models.why == voyage.REFUSED


def test_the_pass_backs_off_and_stops_at_the_cap(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = with_voyage(monkeypatch)
    vault = allow_api(vault)
    models = api(vault)
    slept: list[float] = []
    monkeypatch.setattr(models.worker, "_sleep", slept.append)
    fake.fail["/embeddings"] = [429]
    assert fill(vault, models).why is None and slept == [1.0]
    vault = add(vault, "chat", [message(51, "zebramore pallet")])
    fake.fail["/embeddings"] = [500] * voyage.TRIES
    report = fill(vault, models)
    assert report.why == f"Voyage answered 500, {voyage.TRIES} times; the pass stops until the next one"
    assert slept[1:] == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0]
    choose({"provider": "api", "api_monthly_tokens": 10})
    capped = api(vault)
    assert capped.why is not None and "10 tokens for the month" in capped.why
    assert fill(vault, capped).why == capped.why


def test_a_redirect_or_an_error_says_only_its_status(monkeypatch: pytest.MonkeyPatch) -> None:
    hits: list[str] = []

    answers = {"/v1/ok": b'{"data": []}', "/v1/list": b"[1, 2]", "/v1/text": b"not json"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            hits.append(self.path)
            if self.path.endswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "/v1/elsewhere")
                self.end_headers()
                return
            if self.path.endswith("/slow"):
                time.sleep(1.0)
            body = answers.get(self.path, b"SECRETBODY of the answer")
            self.send_response(200 if self.path in answers or self.path.endswith("/slow") else 500)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(voyage, "BASE", f"http://127.0.0.1:{server.server_address[1]}/v1")
        for path, status in (("/redirect", 302), ("/fail", 500)):
            with pytest.raises(voyage.ApiError) as raised:
                REAL_SEND(path, {"input": ["a request's own text"]}, FAKE_KEY, 5.0)
            assert (raised.value.status, raised.value.why) == (status, f"Voyage answered {status}")
            assert "SECRETBODY" not in str(raised.value) and "request's own text" not in str(raised.value)
        assert hits == ["/v1/redirect", "/v1/fail"]
        assert REAL_SEND("/ok", {}, FAKE_KEY, 5.0) == {"data": []}
        for path, why in (("/list", "wasn't what Docket asked for"), ("/text", "didn't read as JSON")):
            with pytest.raises(voyage.ApiError, match=why):
                REAL_SEND(path, {}, FAKE_KEY, 5.0)
        with pytest.raises(voyage.ApiError) as late:
            REAL_SEND("/slow", {}, FAKE_KEY, 0.2)
        assert late.value.timeout and late.value.why == "Voyage didn't answer in time"
    finally:
        server.shutdown()
        server.server_close()
    # Nothing listens there now.
    with pytest.raises(voyage.ApiError, match="couldn't be reached"):
        REAL_SEND("/ok", {}, FAKE_KEY, 30.0)


def test_the_trust_store_holds_certificates() -> None:
    context = voyage.trust()
    paths = ssl.get_default_verify_paths()
    on_disk = any(p and Path(p).exists() for p in (paths.cafile, paths.capath))
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.cert_store_stats()["x509_ca"] > 0 or on_disk


def test_the_key_lives_only_in_the_keychain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    chain = MemoryKeyring()
    store = voyage.KeyStore(cast(voyage.Keychain, chain))
    store.store(FAKE_KEY)
    assert chain.kept == {(voyage.KEY_SERVICE, voyage.KEY_USER): FAKE_KEY}
    assert store.read() == FAKE_KEY and store.forget() and store.read() is None and not store.forget()
    # The failing backend conftest sets: no keychain, no key, and nowhere else to put it.
    with pytest.raises(DocketError, match="no keychain Docket can keep the Voyage key in"):
        voyage.KeyStore().store(FAKE_KEY)
    with pytest.raises(DocketError, match="no keychain Docket can keep the Voyage key in"):
        voyage.KeyStore().forget()
    assert voyage.KeyStore().read() is None

    class Forgetful(MemoryKeyring):
        def set_password(self, service: str, username: str, password: str) -> None:
            super().set_password(service, username, password[::-1])

    with pytest.raises(DocketError, match="no keychain Docket can keep the Voyage key in"):
        voyage.KeyStore(cast(voyage.Keychain, Forgetful())).store(FAKE_KEY)


def test_the_runner_says_why_in_fixed_words(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = with_voyage(monkeypatch, reranker=False)
    vault = allow_api(vault)
    models = api(vault)
    runner = models.worker
    assert runner.ensure() and runner.up
    runner.stop()
    fake.fail["/embeddings"] = [503]
    assert models.query_vector("pallet") == "Voyage answered 503"
    fake.fail["/embeddings"] = [400]
    with pytest.raises(WorkerError, match="Voyage answered 400"):
        runner.batch(["a text"])
    assert runner.rerank("pallet", ["a", "b"], None) == Skip("no reranker is chosen for the api provider")
    with_voyage(monkeypatch)
    models = api(vault)
    assert models.worker.rerank("pallet", ["a", "b"], 0.0) == Skip(PAST_BUDGET, budget=True)
    vault = commit_config(vault, lambda c: setattr(c, "api_provider", None))
    assert models.worker.rerank("pallet", ["a", "b"], None) == Skip(screening.not_allowed("voyage"))
    usage = voyage.Usage(Path(str(vault.root.parent / "usage.json")))
    usage.add(0)
    assert not usage.path.exists() and usage.spent() == 0


@pytest.mark.parametrize(
    ("answer", "count", "why"),
    [
        ({"data": "x"}, 1, "another number of vectors"),
        ({"data": [1]}, 1, "wasn't what Docket asked for"),
        ({"data": [{"index": 3, "embedding": [0.0]}]}, 1, "wasn't what Docket asked for"),
        ({"data": [{"index": 0, "embedding": [0.0]}]}, 1, "vectors of another size"),
        ({"data": [{"index": 0, "embedding": [0.0, 0.0]}, {"index": 0, "embedding": [0.0, 0.0]}]}, 2, "without its"),
    ],
)
def test_vectors_that_aren_t_what_was_asked_are_refused(answer: dict[str, Any], count: int, why: str) -> None:
    with pytest.raises(voyage.ApiError, match=why):
        voyage._vectors(answer, count, 2)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        ({"data": "x"}, "wasn't what Docket asked for"),
        ({"data": [{"index": 5, "relevance_score": 0.1}]}, "wasn't what Docket asked for"),
        ({"data": [{"index": 0, "relevance_score": 0.1}]}, "left a document without its score"),
    ],
)
def test_scores_that_aren_t_what_was_asked_are_refused(answer: dict[str, Any], why: str) -> None:
    with pytest.raises(voyage.ApiError, match=why):
        voyage._scores(answer, 2)  # pyright: ignore[reportPrivateUsage]
