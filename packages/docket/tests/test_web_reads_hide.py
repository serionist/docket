"""What no /api/web read sends, whatever page or panel asks and with whatever selection a panel takes: a held record's
body and title (DR-0380, DR-0382), the words of an utterance its recording's release cuts (DR-0383), a value a
never-store list now holds where a page hides it (DR-0464, DR-0501), and the hash of a stoplist key that names one,
which trying short names would find again (DR-0786); and a hash of vault.yaml, which holds the lists' values, where
the Settings page needs a token for it (DR-0790).

A page's own test checks the fields it expects; this one reads the whole answer of every read the app has, so a field
added later that carries what its page hides fails here. A read the app gains fails it too until it is walked below.
One vault holds all four, built once and shared read-only (DR-0779). Invented people and words only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from curate_kit import fill_curate
from docket_testkit import configure, make_config, read_only, shared
from docket_webkit import OURS, serving
from staging_kit import TALK, mail, transcript
from starlette.testclient import TestClient

from docket.curate import batch
from docket.index import build as index_build
from docket.vault import curatelog, holds, releaselog
from docket.vault.config import CONFIG_FILE
from docket.vault.cuts import Cut
from docket.vault.gate import Gate
from docket.vault.releaselog import LoggedCut, Release
from docket.vault.settings import file_hash
from docket.vault.vault import Vault
from docket.web import curate as curate_routes
from docket.web import webapi

# A held recording's title and words, an utterance a release cuts, and a value a never-store list holds.
HELD_TITLE = "Quillmoss briefing"
HELD_WORDS = "the Quillmoss quarry deal closes"
RELEASED_WORDS = "Brambleton orchard pays twice"
LISTED = "Nortwin"
HIDDEN_KEY = "new:-:nortwin"
HELD_RID = "calls:rec-held/transcript"
HELD_MAIL = "calls:mail-held"
RELEASED_RID = "calls:rec-rel/transcript"
# Each panel's selections, as its URL takes them.
CHOSEN = (
    "",
    "u=1",
    "u=2",
    "u=3",
    "u=99",
    "line=1",
    "line=2",
    "cut=0-5",
    "cut=0:00-0:20",
    "picked=1",
    "confirm=private",
)


def build(folder: Path) -> Path:
    """curate_kit's recordings and notes with a round started and its Northwind card rejected; staged, a held call and
    a held mail Claude cut before their hold, a call whose release cuts its second utterance, and a mail; then Nortwin
    listed,
    so the round's card and its stoplist key hide it, and the index built over all of it."""
    vault = Vault.init(folder / "vault", make_config(), "test-box")
    fill_curate(vault)
    gate = Gate(vault)
    said = [*TALK[:1], {**TALK[1], "text": HELD_WORDS}, *TALK[2:3]]
    released = [*TALK[:1], {**TALK[1], "text": RELEASED_WORDS}, *TALK[2:3]]
    gate.stage([
        transcript("rec-held", title=HELD_TITLE, segments=said),
        transcript("rec-rel", title="Orchard call", segments=released),
        mail("mail-held", title=f"{HELD_TITLE} terms", text=f"Ada,\n{HELD_WORDS} on Friday.\nBen"),
        mail(),
    ])  # fmt: skip
    keys = {e.staged.record.rid: e.key for e in gate.list().entries}
    gate.classify(keys[HELD_RID], "work", [Cut(start=12_000, end=30_400, unit="ms")], by="claude")
    # The mail's stored cut makes a panel opened with nothing chosen start from it, the way a selection does.
    gate.classify(keys[HELD_MAIL], "work", [Cut(start=0, end=4)], by="claude")
    holds.add(vault, "calls", ["rec-held", "mail-held"])
    cut = LoggedCut(start=TALK[1]["start_ms"], end=TALK[1]["end_ms"])
    when = "2026-10-14T09:00:00Z"
    releaselog.add(
        vault,
        "calls",
        Release.model_validate({"id": "rec-rel", "cuts": [cut], "keep": [], "at": when, "machine": "test-box"}),
    )
    found = batch.start(vault)
    northwind = next(c for c in found.cards if c.name == "Northwind")
    batch.reject(vault, found, [northwind])
    configure(vault.root, keywords=[LISTED])
    index_build.rebuild(vault)
    return vault.root


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return read_only(shared(build, "web-reads-hide", tmp_path_factory))


@pytest.fixture(scope="module")
def client(root: Path) -> Iterator[TestClient]:
    with serving(root) as found:
        yield found


def queries(root: Path) -> dict[str, list[str]]:
    """Each read the app has, with the query strings it is walked with: every record and card open, with each
    selection a panel takes."""
    vault = Vault.open(root)
    staged = [e.key for e in Gate(vault).list().entries]
    round_ = batch.load(vault)
    assert round_ is not None
    cards = [c.id for c in round_.cards]
    rids = (HELD_RID, RELEASED_RID, "calls:rec-held", "calls:rec-rel", "plaud:rec-0000")
    return {
        "/api/web/shell": [""],
        "/api/web/staging": ["", "source=calls", "state=pending", "state=ready", "k=1000"],
        "/api/web/staging/panel": [f"open={key}&{chosen}".rstrip("&") for key in staged for chosen in CHOSEN],
        "/api/web/curate": ["", "done=lifted-1"],
        "/api/web/curate/panel": [f"{how}={card}" for card in cards for how in ("open", "edit")],
        "/api/web/briefs": ["", "type=person", "brief=brief:northwind"],
        # A search echoes its query: it asks for the held and released records by their other words.
        "/api/web/search": ["query=quarry", "query=orchard", "query=briefing", "query=twice", "query=pallets"],
        "/api/web/search/panel": [f"hit=r:{rid}@00:00" for rid in rids],
        "/api/web/recording": [f"rid={rid}" for rid in rids],
        "/api/web/ledger": [""],
        "/api/web/ledger/panel": ["open=A-0001"],
        "/api/web/ledger/changed": ["page=ledger", "page=today"],
        "/api/web/ledger/text": ["open=A-0001&text=notes/plan.md"],
        "/api/web/ledger/who": [""],
        "/api/web/queue": [""],
        "/api/web/queue/panel": ["open=A-0001"],
        "/api/web/meetings": [""],
        "/api/web/meetings/panel": ["open=M-0001"],
        "/api/web/today": [""],
        "/api/web/today/panel": ["open=A-0001"],
        "/api/web/sync": ["", "source=calls", "k=1", "run=run-20261015T000000-ffffffffffffffff"],
        "/api/web/sync/runs": ["", "source=calls"],
        "/api/web/settings": ["", "done=listed-keywords-1", "show=lists"],
    }


# Raw keeps what it stored before a list held it, until the owner forgets it: Search and the Recording page show raw
# as it stands, so a value listed since may be in their answers. Every other read hides it.
SHOWS_STORED_RAW = frozenset({"/api/web/search", "/api/web/search/panel", "/api/web/recording"})
# The Settings page shows the lists' values when its URL asks for them, so one can be taken off (DR-0789).
SHOWS_LISTS = frozenset({("/api/web/settings", "show=lists")})


def test_every_read_the_app_has_is_walked(root: Path) -> None:
    reads = {path for path, ops in webapi.openapi()["paths"].items() if "get" in ops}
    assert reads == set(queries(root))


def test_no_read_sends_what_its_page_hides(client: TestClient, root: Path) -> None:
    hidden_id = curate_routes.key_id(HIDDEN_KEY)
    plain = file_hash((root / CONFIG_FILE).read_bytes())
    secrets = ["quillmoss", "brambleton", hidden_id, plain]
    walked = 0
    for path, asked in queries(root).items():
        for query in asked:
            got = client.get(f"{path}?{query}" if query else path)
            assert got.status_code in (200, 400), (path, query, got.status_code)
            said = got.text.casefold()
            shown = got.json().get("panel") if path == "/api/web/staging/panel" and got.status_code == 200 else None
            if shown and shown["on_hold"]:
                # Nothing worked out from a held record's body: neither a preview nor a selection's problem with it.
                assert (shown["preview"], shown["problem"]) == (None, None), (path, query)
            for secret in secrets:
                assert secret not in said, (path, query, secret)
            if path not in SHOWS_STORED_RAW and (path, query) not in SHOWS_LISTS:
                assert LISTED.casefold() not in said, (path, query)
            walked += 1
    assert walked >= 90
    # The vault holds what the walk looks for: the held record, the released stretch and the hidden key.
    vault = Vault.open(root)
    assert HIDDEN_KEY in curatelog.fold(curatelog.read_all(vault.root).lines).stoplist
    assert {e.staged.record.rid for e in Gate(vault).list().entries} >= {HELD_RID, HELD_MAIL, RELEASED_RID}


# What would tell a held body's size or words: a selection's problem with it, or its preview.
SIZES = ("names utterance", "names line", "transcript has", "text has", "removes", "cuts ")
SAME_ORIGIN = {"Origin": OURS, "Sec-Fetch-Site": "same-origin"}


def test_no_write_on_a_held_record_answers_with_its_body(client: TestClient, root: Path) -> None:
    """Each Staging write a page can send on a held record, with each selection, gets the hold's one refusal, and
    writes nothing, so the shared vault stays as it was."""
    vault = Vault.open(root)
    held = [e for e in Gate(vault).list().entries if e.staged.record.rid in (HELD_RID, HELD_MAIL)]
    assert len(held) == 2
    for entry in held:
        shown = client.get(f"/api/web/staging/panel?open={entry.key}").json()["panel"]
        answers: set[str] = set()
        for chosen in ("", "u=2", "u=99", "line=1", "line=99", "cut=0-5", "cut=0:00-0:10"):
            name, _, value = chosen.partition("=")
            picks = {"u": [], "line": [], "cut": []} | ({name: [value]} if name else {})
            for label in ("work", "private") if not name else ("work",):
                sent = {"key": entry.key, "label": label, **picks, "seen": shown["seen"]}
                got = client.post("/api/web/staging/classify", json=sent, headers=SAME_ORIGIN)
                assert got.status_code == 400, (entry.key, chosen, label)
                said = got.text.casefold()
                assert not any(secret in said for secret in ("quillmoss", "brambleton", *SIZES)), (chosen, said)
                answers.add(got.json()["error"])
        assert len(answers) == 1 and "is on hold" in answers.pop()


def test_the_walk_finds_what_it_looks_for_where_a_page_shows_it(client: TestClient, root: Path) -> None:
    """The secrets are where the reads could reach them: the stored raw that a listed value stays in, the stoplist
    key's own line on the Curate page, its words in the staged files."""
    pallets = client.get("/api/web/search?query=pallets").text
    assert LISTED in pallets
    shown = client.get("/api/web/settings?show=lists").json()["lists"]
    assert [v["value"] for n in shown if n["name"] == "keywords" for v in n["values"]] == [LISTED]
    lifts = client.get("/api/web/curate").json()["lifts"]
    assert [(lift["shown"], lift["liftable"], lift["id"]) for lift in lifts if not lift["liftable"]] == [
        ("new:-:(on a never-store list)", False, "")
    ]
    staged = "".join(p.read_text(encoding="utf-8") for p in (root / "staging").glob("*/*.json"))
    assert "Quillmoss" in staged and "Brambleton" in staged
    assert json.loads(client.get("/api/web/shell").text)["staging"] == 4
