"""The Voyage AI provider (#89, DR-0536): its key in the keychain, the one network path, the month's usage, and the
runner the models use in place of the worker.

Nothing here decides what may leave the machine: the index's screen does (DR-0538). The runner asks it before each
request whether the vault allows the provider and whether a query may go, and the embedding pass and the search send
only what it passed. Every reason the runner gives is a status and fixed words, never an answer's body or a request's
text (DR-0539).
"""

from __future__ import annotations

import json
import os
import ssl
import struct
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast

import keyring
import keyring.errors

from docket import userconfig
from docket.errors import DocketError
from docket.models.catalogue import API_BATCH, API_BATCH_CHARS, ApiEmbedder, ApiReranker
from docket.models.client import PAST_BUDGET, QUERY_SECONDS, RERANK_UNBOUNDED, Skip, WorkerError

BASE = "https://api.voyageai.com/v1"
KEY_SERVICE = "docket"
KEY_USER = "voyage-api-key"
BATCH_SECONDS = 60.0
# A batch that meets a 429 or a 5xx waits 1, 2, 4 s and on, at most a minute, and the pass stops after this many tries
# (Voyage's docs name no Retry-After).
TRIES = 6
BACKOFF_CAP = 60.0
USAGE_FILE = "api-usage.json"
NO_KEY = "no Voyage key in this machine's keychain: docket models key voyage, at your console"
REFUSED = "Voyage refused the API key; set it again: docket models key voyage, at your console"
NO_KEYCHAIN = "this machine has no keychain Docket can keep the Voyage key in, so the API provider can't run here"


class ApiError(Exception):
    """A request that got no usable answer: its HTTP status, when there was one, and fixed words."""

    def __init__(self, why: str, status: int | None = None, *, timeout: bool = False) -> None:
        super().__init__(why)
        self.why = why
        self.status = status
        self.timeout = timeout


# -- the one network path ----------------------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is never followed: the request, its key and its text go only to Voyage's own address."""

    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


def trust() -> ssl.SSLContext:
    """The TLS context every request uses: the system's certificates, verified."""
    return ssl.create_default_context()


def send(path: str, body: dict[str, Any], key: str, timeout: float) -> dict[str, Any]:
    """POST a JSON body to Voyage and return its JSON answer; the only place Docket reaches the network for the
    models. Proxies come from the environment, as for any HTTPS request; a 3xx is an error, never followed."""
    request = urllib.request.Request(  # noqa: S310 - BASE is a fixed https address
        f"{BASE}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=trust()))
    try:
        with opener.open(request, timeout=timeout) as answer:
            data: Any = json.loads(answer.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # The answer's body is never read: it can quote the request.
        e.close()
        raise ApiError(f"Voyage answered {e.code}", e.code) from None
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        late = isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError)
        raise ApiError("Voyage didn't answer in time" if late else "Voyage couldn't be reached", timeout=late) from None
    except ValueError:
        raise ApiError("Voyage's answer didn't read as JSON") from None
    if not isinstance(data, dict):
        raise ApiError("Voyage's answer wasn't what Docket asked for")
    return cast(dict[str, Any], data)


Transport = Callable[[str, dict[str, Any], str, float], dict[str, Any]]


# -- the key -----------------------------------------------------------------------------------------------------


class Keychain(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class KeyStore:
    """The Voyage key in the system keychain, entry docket / voyage-api-key, and nowhere else: no file, no config, no
    vault (DR-0539). A process running as the user can still ask the keychain for it, as Docket does."""

    def __init__(self, backend: Keychain | None = None) -> None:
        self._backend = backend

    def _keychain(self) -> Keychain:
        return self._backend if self._backend is not None else cast(Keychain, keyring.get_keyring())

    def read(self) -> str | None:
        try:
            found = self._keychain().get_password(KEY_SERVICE, KEY_USER)
        except keyring.errors.KeyringError:
            return None
        return found or None

    def store(self, value: str) -> None:
        """Keep the key, then read it back: a keychain that takes it and gives back something else keeps nothing."""
        try:
            self._keychain().set_password(KEY_SERVICE, KEY_USER, value)
            kept = self._keychain().get_password(KEY_SERVICE, KEY_USER)
        except keyring.errors.KeyringError:
            raise DocketError(NO_KEYCHAIN) from None
        if kept != value:
            raise DocketError(NO_KEYCHAIN)

    def forget(self) -> bool:
        """Remove the key; whether there was one."""
        try:
            self._keychain().delete_password(KEY_SERVICE, KEY_USER)
        except keyring.errors.PasswordDeleteError:
            return False
        except keyring.errors.KeyringError:
            raise DocketError(NO_KEYCHAIN) from None
        return True


# -- the month's usage -------------------------------------------------------------------------------------------


class Usage:
    """The tokens this machine sent Voyage this month, from each answer's own count, in the state folder: never in the
    vault. Two processes adding at once may lose a count; it is a guard against a runaway loop, not a bill."""

    def __init__(self, path: Path | None = None, clock: Callable[[], datetime] = datetime.now) -> None:
        self.path = path if path is not None else userconfig.state_dir() / USAGE_FILE
        self._clock = clock
        self._lock = threading.Lock()

    def month(self) -> str:
        return f"{self._clock():%Y-%m}"

    def spent(self) -> int:
        try:
            data: Any = json.loads(self.path.read_text(encoding="utf-8"))
            held = cast(dict[str, Any], data) if isinstance(data, dict) else {}
            if held.get("month") == self.month():
                return int(held.get("tokens", 0))
        except (OSError, ValueError, TypeError):
            pass
        return 0

    def add(self, tokens: int) -> None:
        if tokens <= 0:
            return
        with self._lock:
            body = json.dumps({"month": self.month(), "tokens": self.spent() + tokens})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(body, encoding="utf-8", newline="\n")
            tmp.replace(self.path)


# -- the runner --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Gate:
    """What the runner asks before each request: whether the vault allows the provider now (DR-0537), and whether a
    query may go (DR-0538). Each gives why not, or None."""

    allowed: Callable[[], str | None]
    query: Callable[[str], str | None]


class VoyageRunner:
    """The models' runner for Voyage, in this process: a query's vector, a batch's vectors, and a rerank within its
    deadline, each only while the vault allows it, the key is here and was not refused, and the month's tokens last."""

    def __init__(
        self,
        embedder: ApiEmbedder,
        reranker: ApiReranker | None,
        gate: Gate,
        *,
        monthly: int,
        keys: KeyStore | None = None,
        usage: Usage | None = None,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.embedder = embedder
        self.reranker = reranker
        self.gate = gate
        self.monthly = monthly
        self.keys = keys if keys is not None else KeyStore()
        self.usage = usage if usage is not None else Usage()
        self._transport = transport
        self._sleep = sleep
        self._wall = wall
        self._key: str | None = None
        self._refused: str | None = None

    @property
    def up(self) -> bool:
        return self.why is None

    @property
    def why(self) -> str | None:
        """Why nothing may be sent now, or None."""
        why = self.gate.allowed()
        if why is not None:
            return why
        if self._key is None or self._key == self._refused:
            self._key = self.keys.read()
        if self._key is None:
            return NO_KEY
        if self._key == self._refused:
            return REFUSED
        if self.usage.spent() >= self.monthly:
            return (
                f"this machine sent Voyage its {self.monthly:,} tokens for the month; it sends again next month, or "
                "raise api_monthly_tokens in your config's models block"
            )
        return None

    def ensure(self) -> bool:
        return self.up

    def query(self, text: str) -> bytes | str:
        """A query's vector, or why there is none; the query itself is screened first."""
        why = self.gate.query(text) or self.why
        if why is not None:
            return why
        try:
            answer = self._send("/embeddings", self._embed_body([text], "query"), QUERY_SECONDS)
            return _vectors(answer, 1, self.embedder.dims)[0]
        except ApiError as e:
            return e.why

    def batch(self, texts: Sequence[str]) -> list[bytes]:
        """Each text's vector, in requests of at most API_BATCH texts and API_BATCH_CHARS characters; raises
        WorkerError when the provider can't, after backing off on a 429 or a 5xx."""
        out: list[bytes] = []
        for part in _parts(texts):
            out += self._batch(part)
        return out

    def _batch(self, texts: list[str]) -> list[bytes]:
        last = "Voyage didn't answer"
        for attempt in range(TRIES):
            why = self.why
            if why is not None:
                raise WorkerError(why)
            try:
                answer = self._send("/embeddings", self._embed_body(texts, "document"), BATCH_SECONDS)
                return _vectors(answer, len(texts), self.embedder.dims)
            except ApiError as e:
                last = e.why
                # A 429 or a 5xx is waited out; any other status is the request's own, and asking again won't help.
                if e.status is not None and e.status != 429 and e.status < 500:
                    raise WorkerError(e.why) from None
            self._sleep(min(BACKOFF_CAP, 2.0**attempt))
        raise WorkerError(f"{last}, {TRIES} times; the pass stops until the next one")

    def rerank(self, query: str, texts: Sequence[str], deadline: float | None) -> list[float] | Skip:
        """Each text's score for the query, within the deadline: past it, a budget skip, as the worker gives; any
        error, a skip with its status. Never retried inside a search."""
        if self.reranker is None:
            return Skip("no reranker is chosen for the api provider")
        why = self.gate.query(query) or self.why
        if why is not None:
            return Skip(why)
        seconds = RERANK_UNBOUNDED if deadline is None else deadline - self._wall()
        if seconds <= 0:
            return Skip(PAST_BUDGET, budget=True)
        body = {"query": query, "documents": list(texts), "model": self.reranker.model, "truncation": True}
        try:
            answer = self._send("/rerank", body, seconds)
            return _scores(answer, len(texts))
        except ApiError as e:
            return Skip(PAST_BUDGET, budget=True) if e.timeout else Skip(e.why)

    def stop(self) -> None:
        return None

    def _embed_body(self, texts: list[str], kind: str) -> dict[str, Any]:
        return {
            "input": texts,
            "model": self.embedder.model,
            "input_type": kind,
            "output_dimension": self.embedder.dims,
            "truncation": True,
        }

    def _send(self, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        key = self._key
        if key is None:  # pragma: no cover - every caller asks `why` first, which reads the key or says there is none
            raise ApiError(NO_KEY)
        transport = self._transport if self._transport is not None else send
        try:
            answer = transport(path, body, key, timeout)
        except ApiError as e:
            if e.status in (401, 403):
                self._refused = key
                raise ApiError(REFUSED, e.status) from None
            raise
        usage: Any = answer.get("usage")
        tokens: object = cast(dict[str, Any], usage).get("total_tokens") if isinstance(usage, dict) else None
        if isinstance(tokens, int):
            self.usage.add(tokens)
        return answer


def _parts(texts: Sequence[str]) -> list[list[str]]:
    out: list[list[str]] = []
    size = 0
    for text in texts:
        if out and len(out[-1]) < API_BATCH and size + len(text) <= API_BATCH_CHARS:
            out[-1].append(text)
            size += len(text)
        else:
            out.append([text])
            size = len(text)
    return out


def _vectors(answer: dict[str, Any], count: int, dims: int) -> list[bytes]:
    """The answer's embeddings in the order asked, as the index keeps them: float32, little-endian."""
    rows: Any = answer.get("data")
    if not isinstance(rows, list) or len(cast(list[Any], rows)) != count:
        raise ApiError("Voyage's answer held another number of vectors")
    out: list[bytes | None] = [None] * count
    for row in cast(list[Any], rows):
        if not isinstance(row, dict):
            raise ApiError("Voyage's answer wasn't what Docket asked for")
        item = cast(dict[str, Any], row)
        index, vector = item.get("index"), item.get("embedding")
        if not isinstance(index, int) or not 0 <= index < count or not isinstance(vector, list):
            raise ApiError("Voyage's answer wasn't what Docket asked for")
        values = cast(list[Any], vector)
        if len(values) != dims or not all(isinstance(v, int | float) for v in values):
            raise ApiError("Voyage's answer held vectors of another size")
        out[index] = struct.pack(f"<{dims}f", *values)
    if any(v is None for v in out):
        raise ApiError("Voyage's answer left a text without its vector")
    return [v for v in out if v is not None]


def _scores(answer: dict[str, Any], count: int) -> list[float]:
    """The rerank's score for each document, in the order sent."""
    rows: Any = answer.get("data")
    if not isinstance(rows, list):
        raise ApiError("Voyage's answer wasn't what Docket asked for")
    out: list[float | None] = [None] * count
    for row in cast(list[Any], rows):
        item = cast(dict[str, Any], row) if isinstance(row, dict) else {}
        index, score = item.get("index"), item.get("relevance_score")
        if not isinstance(index, int) or not 0 <= index < count or not isinstance(score, int | float):
            raise ApiError("Voyage's answer wasn't what Docket asked for")
        out[index] = float(score)
    if any(s is None for s in out):
        raise ApiError("Voyage's answer left a document without its score")
    return [s for s in out if s is not None]
