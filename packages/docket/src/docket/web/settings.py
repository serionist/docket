"""The Settings page's routes under /api/web (DR-0789, DR-0790): the page, and its three writes, each way (DR-0601).

| Route | What |
|---|---|
| `GET /settings` | the sources, never-store lists and redaction, this machine's models; values only with show=lists |
| `POST /settings/never-store` | `ask`, the add's question; `add`, the value added; `off`, a value taken off |
| `POST /settings/redaction` | a rule turned on or off; credentials off is refused, its command named |
| `POST /settings/source` | a source's sensitivity, or the source on or off; private only after its question |

The read writes nothing: it reads vault.yaml, the user's config, the runner's mapper check, this machine's models and
the service's own models state, in this process, beside the writer. Every write goes through vault.yaml's one writer
on the service's writer thread (DR-0153, DR-0791), as JSON with the browser's session only (DR-0709). Turning
credentials redaction off is the one change no page makes: the writer refuses it, from the change or from an
uncommitted edit it would take in.

Each write carries the token the page read for vault.yaml, a hash keyed by a secret this process makes at start, so
a page older than the file writes nothing (409) and the page can't work the file's text back from it; a write sent
twice is no write. A never-store value travels only in the writes' bodies: never in a URL, an answer, an error, the
log or a commit message (DR-0790). A value the URL or the body can't hold, or a `DocketError`, answers 400 with its
words, which never quote the value; anything else is logged as its type and frames and answers 500 with no detail.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.responses import Response

from docket import userconfig
from docket.errors import DocketError
from docket.index.build import vector_counts
from docket.index.query import Reach, reach
from docket.models import report
from docket.service.state import Service
from docket.sources import runner
from docket.vault import inforce, neverstore, redaction
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.gate import Gate
from docket.vault.neverstore import ListName
from docket.vault.records import source_name_problem
from docket.vault.settings import (
    BUILTIN_RULES,
    LISTS,
    Added,
    StaleError,
    add_listed,
    checked_value,
    edit_to_take_in,
    file_hash,
    key_line,
    set_enabled,
    set_rule,
    set_sensitivity,
    take_off_listed,
)
from docket.vault.settings import Rewritten as Written
from docket.vault.vault import Vault
from docket.web import settings_view
from docket.web.day_api import Refused, safely
from docket.web.settings_view import (
    AddQuestion,
    Facts,
    PrivateQuestion,
    SettingsPage,
    SettingsState,
    SourceFacts,
    done_line,
)

BY_HAND = "vault.yaml has a layout Docket can't edit for this, so it is left as it is; change it there"
STALE = "vault.yaml changed since the page showed it, so nothing was written. Here it is as it stands now."
STALE_ASKED = (
    "vault.yaml changed since you saw this, so nothing was written. Here it is again, counted as the file stands now."
)
# What a source's button may ask for (DR-0601).
ACTS = ("work", "mixed", "private", "off", "on")
# The process's own key for the tokens: made at start, never written anywhere, so a token from before a restart names
# no file and its write answers 409.
_KEY = secrets.token_bytes(32)


def token_of(data: bytes) -> str:
    """The token a page holds for vault.yaml as it read it: keyed, so no value in the file can be found from it by
    trying short names against a page's other lines, as a plain hash would let."""
    return hmac.new(_KEY, data, hashlib.sha256).hexdigest()


def shown_by(vault: Vault, token: str) -> str:
    """The sha256 the writer checks under the vault's lock (DR-0791): the file's as it stands when the token names it,
    else one that names no file, so a change is refused as stale and a change already made is still no write."""
    data = (vault.root / CONFIG_FILE).read_bytes()
    return file_hash(data) if hmac.compare_digest(token_of(data), token) else ""


def gather(service: Service) -> Facts:
    """Everything the page shows, read now. Nothing here writes: the mapper check resolves paths, the models' report
    reads markers, and the vectors are counted in the index."""
    vault = service.vault()
    data = (vault.root / CONFIG_FILE).read_bytes()
    text = data.decode("utf-8")
    config = vault.written
    folders_problem: str | None = None
    try:
        here = userconfig.folders().get(vault.root.resolve(), {})
    except DocketError as e:
        folders_problem, here = str(e), {}
    sources: list[SourceFacts] = []
    for name, entry in sorted(config.sources.items()):
        problem: str | None = None
        if name not in vault.config.sources:
            # Added by hand and not committed: the runner takes it once it is (DR-0521).
            problem = "not in force until you commit vault.yaml"
        elif entry.folder is None:
            try:
                runner.find_mapper(vault, name)
            except DocketError as e:
                # The page says once, at the top, that an edit isn't in force.
                problem = str(e).removesuffix(inforce.note(True))
        folder = here.get(name)
        sources.append(SourceFacts(name, entry, problem, str(folder) if folder is not None else None))
    said, errors = report.service_lines(service.models.status().model_dump())
    return Facts(
        root=vault.root,
        machine=vault.machine,
        config=config,
        docket_form=config.written_by_docket(text),
        waiting=vault.waiting,
        sources=sources,
        models=report.read(),
        config_path=userconfig.config_path(),
        folders_problem=folders_problem,
        service=said,
        service_errors=errors,
        vectors=vector_counts(vault),
        shown=token_of(data),
    )


def _list_name(given: str) -> ListName:
    if given not in LISTS:
        # Never the name given, which a body could have put a value in.
        raise DocketError("a never-store list is people, containers or keywords")
    return given


def _with(config: VaultConfig, name: ListName, value: str) -> VaultConfig:
    """The settings as they would be with the value on its list: what the question counts against."""
    lists = config.never_store
    return config.model_copy(update={"never_store": lists.model_copy(update={name: [*getattr(lists, name), value]})})


def _staged(service: Service, name: ListName, value: str) -> tuple[int, str, bool]:
    """On the writer thread, so no write runs between the read and the count: what the value would drop from staging,
    vault.yaml's token as read, and whether an edit not committed yet goes in first. One that turns credentials
    redaction off is refused here already, as the add would refuse it (DR-0601)."""
    vault = service.vault()
    data = (vault.root / CONFIG_FILE).read_bytes()
    text = data.decode("utf-8")
    config = vault.written
    takes_in = edit_to_take_in(vault, config, text)
    dropped = Gate(vault).would_drop(_with(config, name, value))
    return dropped["never-store"], token_of(data), takes_in


def _stored(service: Service, name: ListName, value: str) -> tuple[Reach | None, str | None]:
    """What already stored the value would have kept out, from the index, beside the writer; or why it can't say."""
    try:
        return reach(service.vault(), name, value), None
    except DocketError as e:
        return None, str(e).split("; run ")[0]
    except sqlite3.Error:
        return None, "the index is busy being brought up to date; look again in a moment"


def _private_staged(service: Service, source: str) -> tuple[int, str, bool]:
    """On the writer thread: what making a source private would drop from staging, and vault.yaml's token and whether
    an edit not committed yet goes in first, refused here already when it turns credentials redaction off."""
    vault = service.vault()
    data = (vault.root / CONFIG_FILE).read_bytes()
    text = data.decode("utf-8")
    config = vault.written
    entry = config.sources.get(source)
    if entry is None:
        raise DocketError(f"no source {source} in vault.yaml")
    takes_in = edit_to_take_in(vault, config, text)
    private = config.model_copy(
        update={"sources": {**config.sources, source: entry.model_copy(update={"sensitivity": "private"})}}
    )
    before, after = Gate(vault).would_drop(config), Gate(vault).would_drop(private)
    return after["private"] - before["private"], token_of(data), takes_in


def _change(service: Service, source: str, act: str, token: str) -> Written:
    """A source's sensitivity set, or the source turned on or off, on the writer thread; then the sweep, so what the
    source's new setting forbids leaves staging at once and what it now asks about waits again."""
    vault = service.vault()
    shown = shown_by(vault, token)
    if act in ("off", "on"):
        written = set_enabled(vault, source, act == "on", shown=shown)
    else:
        written = set_sensitivity(vault, source, act, shown=shown)
    if written.changed:
        Gate(vault).list()
    return written


def _add(service: Service, name: ListName, value: str, token: str) -> Added:
    """The add, on the writer thread; then the gate's sweep, so what the list now names leaves staging at once."""
    vault = service.vault()
    added = add_listed(vault, name, value, shown=shown_by(vault, token))
    if added.written.changed:
        Gate(vault).list()
    return added


def _take_off(service: Service, name: ListName, value: str, token: str) -> Added:
    vault = service.vault()
    return take_off_listed(vault, name, value, shown=shown_by(vault, token))


def _rule(service: Service, rule: str, on: bool, token: str) -> Written:
    vault = service.vault()
    return set_rule(vault, rule, on, shown=shown_by(vault, token))


class NeverStoreWrite(BaseModel):
    """A never-store value: `ask` for the add's question, `add` once it is answered, `off` to take one off. The value
    is the body's only, and no answer to it holds it (DR-0790)."""

    model_config = ConfigDict(extra="forbid")

    act: Literal["ask", "add", "off"]
    name: str
    value: str
    token: str


class NeverStoreAnswer(BaseModel):
    """What a never-store write did: its notice, a warning when vault.yaml's layout left it to the owner, or the add's
    question. `by_hand` says the add's line goes in by hand: the page writes it from the value it holds."""

    notice: str | None = None
    warning: str | None = None
    question: AddQuestion | None = None
    by_hand: bool = False


class RuleWrite(BaseModel):
    """A redaction rule, built in or the owner's pattern by its name, turned on or off."""

    model_config = ConfigDict(extra="forbid")

    rule: str
    state: Literal["on", "off"]
    token: str


class RuleAnswer(BaseModel):
    notice: str | None = None
    warning: str | None = None


class SourceWrite(BaseModel):
    """A source made work, mixed or private, or turned off or on; private only `confirmed`, after its question."""

    model_config = ConfigDict(extra="forbid")

    source: str
    act: str
    token: str
    confirmed: bool = False


class SourceAnswer(BaseModel):
    notice: str | None = None
    warning: str | None = None
    question: PrivateQuestion | None = None


def _stale(words: str) -> Response:
    return JSONResponse(Refused(error=words).model_dump(mode="json"), status_code=409)


def router(service: Service) -> APIRouter:
    api = APIRouter()

    def config() -> VaultConfig:
        return service.vault().written

    async def read(request: Request) -> SettingsPage:
        state = SettingsState.read(request.query_params)
        facts = await asyncio.to_thread(gather, service)
        return settings_view.page(settings_view.build(facts, state), state, facts.shown)

    async def question(name: ListName, value: str) -> NeverStoreAnswer:
        staged, token, takes_in = await service.writer.run(lambda: _staged(service, name, value))
        stored, why = await asyncio.to_thread(_stored, service, name, value)
        view = settings_view.build_confirm(
            name, value, staged=staged, stored=stored, why=why, shown=token, docket_form=True, takes_in=takes_in
        )
        return NeverStoreAnswer(question=settings_view.add_question(view, token))

    async def never_store(body: NeverStoreWrite) -> Response | NeverStoreAnswer:
        name = _list_name(body.name)
        if body.act == "off":
            try:
                taken = await service.writer.run(lambda: _take_off(service, name, body.value, body.token))
            except StaleError:
                return _stale(STALE)
            if taken.written.by_hand:
                return NeverStoreAnswer(warning=f"{BY_HAND}: delete its line under never_store.{name}.")
            count = "0" if taken.already else "1"
            return NeverStoreAnswer(notice=done_line(f"unlisted-{name}-{count}", config()))
        value = checked_value(body.value)
        if body.act == "ask":
            if neverstore.holds(config().never_store, name, value):
                return NeverStoreAnswer(notice=done_line(f"listed-{name}-0", config()))
            return await question(name, value)
        try:
            added = await service.writer.run(lambda: _add(service, name, value, body.token))
        except StaleError:
            return _stale(STALE_ASKED)
        if added.written.by_hand:
            warning = (
                "vault.yaml has a layout Docket can't edit for this, so it is left as it is: add this line under "
                f"never_store: {name}: by hand."
            )
            return NeverStoreAnswer(warning=warning, by_hand=True)
        count = "0" if added.already else "1"
        return NeverStoreAnswer(notice=done_line(f"listed-{name}-{count}", config()))

    async def rule_change(body: RuleWrite) -> Response | RuleAnswer:
        on = body.state == "on"
        try:
            written = await service.writer.run(lambda: _rule(service, body.rule, on, body.token))
        except StaleError:
            return _stale(STALE)
        if written.by_hand:
            line = redaction.by_hand(body.rule, on, body.rule not in BUILTIN_RULES)
            return RuleAnswer(warning=f"{BY_HAND}: {line}.")
        kind = body.rule if body.rule in BUILTIN_RULES else "pattern"
        return RuleAnswer(notice=done_line(f"{kind}-{body.state}", config()))

    async def private(source: str) -> SourceAnswer:
        staged, token, takes_in = await service.writer.run(lambda: _private_staged(service, source))
        view = settings_view.build_source_confirm(
            source, staged=staged, shown=token, docket_form=True, takes_in=takes_in
        )
        return SourceAnswer(question=settings_view.private_question(view, token))

    async def source_change(body: SourceWrite) -> Response | SourceAnswer:
        source, act = body.source.strip(), body.act
        if source_name_problem(source) is not None:
            raise DocketError("source is a source's name from vault.yaml")
        if act not in ACTS:
            raise DocketError("a page makes a source work, mixed or private, or turns it on or off")
        if act == "private" and not body.confirmed:
            return await private(source)
        try:
            written = await service.writer.run(lambda: _change(service, source, act, body.token))
        except StaleError:
            return _stale(STALE_ASKED if act == "private" else STALE)
        if written.by_hand:
            line = key_line("enabled", act == "on") if act in ("off", "on") else key_line("sensitivity", act)
            return SourceAnswer(warning=f"{BY_HAND}: {line}, under sources.{source}.")
        return SourceAnswer(notice=done_line(f"{source}-{act}", config()))

    refusals: dict[int | str, dict[str, Any]] = {400: {"model": Refused}, 409: {"model": Refused}}

    @api.get("/settings", response_model=SettingsPage, responses={400: {"model": Refused}})
    async def settings_read(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: read(request))

    @api.post("/settings/never-store", response_model=NeverStoreAnswer, responses=refusals)
    async def settings_never_store(request: Request, body: NeverStoreWrite) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: never_store(body))

    @api.post("/settings/redaction", response_model=RuleAnswer, responses=refusals)
    async def settings_redaction(request: Request, body: RuleWrite) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: rule_change(body))

    @api.post("/settings/source", response_model=SourceAnswer, responses=refusals)
    async def settings_source(request: Request, body: SourceWrite) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: source_change(body))

    return api
