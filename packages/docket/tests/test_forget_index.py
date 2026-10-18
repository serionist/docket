"""The index under the forgotten list (DR-0593): built under the list's digest, rebuilt by `update` and refused by a
search and `read` when built under another, even with no run file moved; a rebuild that fails leaves it refused; and a
rebuild leaves out what the list takes, the records and the doc lines the docs' rule drops (DR-0580), giving the rows of
a vault that never held them. Invented data only."""

from __future__ import annotations

import sqlite3
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import (
    PLAUD,
    POLISHED,
    copied,
    git_calls,
    logical_dump,
    make_config,
    make_record,
    plaud_records,
)
from forget_kit import KEY, forget, line, stored, write_raw_file

from docket import recall
from docket.cli import main
from docket.documents import records as documents
from docket.errors import DocketError
from docket.index import build, lookup
from docket.index.languages import resolve
from docket.index.schema import FORGOTTEN as META_KEY
from docket.index.schema import stale
from docket.vault import forgetdocs, forgotten
from docket.vault.anchors import Anchor
from docket.vault.docstore import DocStore
from docket.vault.processed import ProcessedDoc, parse_doc
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

NOTE = "recording-note:2026-10-14-supplier-sync"
THANKS = "recording-note:2026-10-15-thanks"
PRICE = "recording-note:2026-10-14-price"
HAND = "recording-note:2026-10-17-by-hand"
DIGEST = "digest:2026-10-14-day"
LATER = "digest:2026-10-16-week"
DECISION = "decision:2026-10-14-ten-million"
LEGACY = "legacy-note:2026-09-13-supplier-call"
KEPT_LEGACY = "legacy-note:2026-09-14-supplier-follow-up"
OLD_PATH = "notes/2026-09-13-supplier-call.md"
LEGACY_FILE = "legacy-note/2026/09/2026-09-13-supplier-call.md"
CHAT = make_record(1).rid
OTHER = make_record(2).rid
KEPT = f"- Cleo opened the channel. [r:{CHAT}]"
TEN = f"- Ten million, signed by Friday. [r:{POLISHED}@10:42]"
# A time on the recording, which reads on the transcript under it (DR-0370): only a hand edit writes one.
ON_RECORDING = f"- Ten million, signed by Friday. [r:{PLAUD}@10:42]"
BYE = f"- Ben thanked everyone. [r:{POLISHED}@15:00]"
TEN_AGAIN = f"- Ten million, signed by Friday. [r:{OTHER}]"


def at(hour: int) -> datetime:
    return datetime(2026, 10, 14, hour, tzinfo=UTC)


def doc(doc_id: str, covers: Sequence[str], lines: Sequence[str], *head: str, title: str = "Supplier sync") -> str:
    covered = ["covers:", *(f"  - {rid}" for rid in covers)] if covers else ["covers: []"]
    front = [f"id: {doc_id}", f"type: {doc_id.partition(':')[0]}", *covered, "by: claude-test, sync 2026-10-14", *head]
    return "---\n" + "\n".join(front) + f"\n---\n\n# {title}\n\n" + "\n".join(lines) + "\n"


def cite(vault: Vault, doc_id: str, text: str) -> str:
    found = DocStore(vault).get(doc_id)
    assert found is not None
    return f"[d:{doc_id}@{found.meta.rev}#{next(n for n, said in found.lines() if said == text)}]"


def with_records(root: Path, *, family: bool) -> Vault:
    """The recording, its notes and two chat messages; with the family, the transcript too, in a run file of its own."""
    vault = Vault.init(root / "vault", make_config(), "test-box")
    recording, polished, summary = plaud_records("transaction_polish")
    vault.write_run("plaud", [recording, summary], at(10))
    if family:
        vault.write_run("plaud", [polished], at(11))
    vault.write_run("chat", [make_record(1), make_record(2)], at(10))
    return vault


def write_doc(vault: Vault, rel: str, text: str) -> None:
    """A doc file as a hand edit or an import left it, not put."""
    path = vault.root / "processed" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def build_twin(root: Path, *, family: bool) -> Vault:
    """A note on a chat message and a digest built on it. With the family, a note on the transcript the digest cites
    too, last, and the imported transcript as a legacy note: a vault that never held the family has neither."""
    vault = with_records(root, family=family)
    recall.doc_put(vault, doc(NOTE, [CHAT], [KEPT]))
    lines = [f"- The channel opened. {cite(vault, NOTE, KEPT)}"]
    if family:
        recall.doc_put(vault, doc(PRICE, [POLISHED], [TEN], title="The price"))
        lines.append(f"- The deal is ten million. {cite(vault, PRICE, TEN)}")
        write_doc(vault, LEGACY_FILE, legacy(LEGACY, [PLAUD], OLD_PATH, "Ten million."))
    recall.doc_put(vault, doc(DIGEST, [], lines, title="The day"))
    return vault


def build_held(root: Path) -> Vault:
    return build_twin(root, family=True)


def build_never(root: Path) -> Vault:
    return build_twin(root, family=False)


def legacy(doc_id: str, covers: Sequence[str], old_path: str, body: str, rev: int = 1) -> str:
    covered = "".join(f"  - {rid}\n" for rid in covers)
    return (
        f"---\nid: {doc_id}\ntype: legacy-note\nrev: {rev}\ncovers:\n{covered}by: import\nold_paths:\n  - {old_path}\n"
        f"import_hash: sha256:{'ab' * 32}\n---\n\n# Supplier call\n\n{body}\n"
    )


def build_docs(root: Path) -> Vault:
    """The family's docs of every kind: a note whose current rev drops its first line and whose earlier rev two digests
    and a confirmed decision cite, a note on the transcript alone, a hand edit with a time on the recording, and two
    legacy notes."""
    vault = with_records(root, family=True)
    project = vault.root / "entities" / "project" / "supplier-deal.yaml"
    project.parent.mkdir(parents=True)
    project.write_text("id: supplier-deal\ntype: project\nname: {en: Supplier deal}\n", encoding="utf-8")
    vault.commit(["entities/project/supplier-deal.yaml"], "Entities")
    recall.doc_put(vault, doc(NOTE, [CHAT, POLISHED], [TEN, KEPT]))
    recall.doc_put(vault, doc(THANKS, [POLISHED], ["- Ben thanked everyone. @15:00"], title="Thanks"))
    day = [f"- The channel opened. {cite(vault, NOTE, KEPT)}", f"- The deal is ten million. {cite(vault, NOTE, TEN)}"]
    recall.doc_put(vault, doc(DIGEST, [], day, title="The day"))
    decided = ["entities: [supplier-deal]", "status: proposed", "subject: supplier-deal"]
    said = [f"- Said on the call. {cite(vault, NOTE, TEN)}"]
    recall.doc_put(vault, doc(DECISION, [], said, *decided, title="Proposed: the deal is ten million"))
    store = DocStore(vault)
    decision = store.get(DECISION)
    assert decision is not None
    store.decide(DECISION, decision.meta.rev, "confirm")
    # The note's second rev: the price now anchored on a chat message, and a thanks line on the transcript first.
    recall.doc_put(vault, doc(NOTE, [CHAT, OTHER, POLISHED], [BYE, TEN_AGAIN, KEPT], "rev: 1"))
    week = [f"- Signed by Friday. {cite(vault, NOTE, TEN_AGAIN)}", f"- Thanks said. {cite(vault, NOTE, BYE)}"]
    recall.doc_put(vault, doc(LATER, [], week, title="The week"))
    hand = parse_doc(doc(HAND, [PLAUD, CHAT], [ON_RECORDING, KEPT]), lenient=True).render()
    write_doc(vault, "recording-note/2026/10/2026-10-17-by-hand.md", hand)
    write_doc(vault, LEGACY_FILE, legacy(LEGACY, [PLAUD], OLD_PATH, "Ten million."))
    kept = legacy(KEPT_LEGACY, [PLAUD, POLISHED], "notes/2026-09-14-follow-up.md", "A follow-up.")
    write_doc(vault, "legacy-note/2026/09/2026-09-14-supplier-follow-up.md", kept)
    return vault


@pytest.fixture
def held(tmp_path: Path) -> Vault:
    return Vault.open(copied(tmp_path, lambda d: build_held(d).root, f"{__name__}.held"))


@pytest.fixture
def documented(tmp_path: Path) -> Vault:
    vault = Vault.open(copied(tmp_path, lambda d: build_docs(d).root, f"{__name__}.docs"))
    forget_the_transcript(vault)
    build.rebuild(vault)
    return vault


def rows(vault: Vault, sql: str, *args: Any) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(build.db_path(vault))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def forget_the_transcript(vault: Vault) -> None:
    """The transcript forgotten whole, as `docket forget` will list it: the recording it sits under on media, and the
    imported transcript's old path."""
    forget(vault, rids=[POLISHED], media=[POLISHED, PLAUD], old_paths=[OLD_PATH])


# -- the digest --------------------------------------------------------------------------------------------------------


def test_a_list_line_with_no_run_file_moved_makes_update_rebuild(held: Vault) -> None:
    assert build.update(held).rebuilt
    assert rows(held, "SELECT value FROM meta WHERE key = ?", META_KEY) == [("",)]
    assert not build.update(held).rebuilt
    forget_the_transcript(held)
    report = build.update(held)
    assert report.rebuilt and report.reason == "it was built before a forget"
    digest = forgotten.load(held.root, held.home).digest
    assert rows(held, "SELECT value FROM meta WHERE key = ?", META_KEY) == [(digest,)]
    assert rows(held, "SELECT count(*) FROM records WHERE rid = ?", POLISHED) == [(0,)]
    # A keys-only line changes no row, so it rebuilds nothing.
    forget(held, keys=[KEY])
    assert not build.update(held).rebuilt


def test_search_and_read_refuse_an_index_built_before_a_forget(held: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    build.rebuild(held)
    assert lookup.open_read(held) is not None
    forget_the_transcript(held)
    code = main(["search", "ten", "million", "--no-update", "--vault", str(held.root)])
    err = capsys.readouterr().err
    assert code == 2 and "the index can't be used: it was built before a forget; run docket index rebuild" in err
    assert main(["index", "status", "--vault", str(held.root)]) == 1
    assert "needs a rebuild: it was built before a forget" in capsys.readouterr().out
    assert lookup.open_read(held) is None
    assert build.status(held).stale == "it was built before a forget"
    build.rebuild(held)
    index = lookup.open_read(held)
    assert index is not None
    index.close()


def test_a_rebuild_that_fails_leaves_the_index_refused(held: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    build.rebuild(held)
    forget_the_transcript(held)

    def held_open(tmp: Path, db: Path) -> None:
        raise DocketError(f"{db} is open in another process")

    monkeypatch.setattr(build, "_replace", held_open)
    with pytest.raises(DocketError, match="open in another process"):
        build.update(held)
    with pytest.raises(DocketError, match="built before a forget"):
        build.open_index(held)
    assert rows(held, "SELECT count(*) FROM records WHERE rid = ?", POLISHED) == [(1,)]


def test_an_index_from_before_the_list_reads_as_built_under_none(held: Vault) -> None:
    build.rebuild(held)
    conn = sqlite3.connect(build.db_path(held))
    try:
        conn.execute("DELETE FROM meta WHERE key = ?", (META_KEY,))
        conn.commit()
        langs = resolve(held.config.owner.languages)
        assert stale(conn, langs) is None
        assert stale(conn, langs, "ab" * 32) == "it was built before a forget"
    finally:
        conn.close()
    assert not build.update(held).rebuilt


def test_update_under_the_same_list_skips_a_listed_line_a_new_run_file_brings(held: Vault) -> None:
    forget_the_transcript(held)
    build.rebuild(held)
    polished = plaud_records("transaction_polish")[1]
    write_raw_file(held.root, "raw/plaud/2026/10/2026-10-14T1500-other-box.jsonl", [polished, make_record(9)])
    report = build.update(held)
    assert not report.rebuilt and report.runs == 1
    assert rows(held, "SELECT count(*) FROM records WHERE rid = ?", POLISHED) == [(0,)]
    assert rows(held, "SELECT count(*) FROM records WHERE rid = ?", make_record(9).rid) == [(1,)]


COPY = "docs:aaaaaaaaaaaa"
COPY_NOTE = "recording-note:2026-10-14-copy"
SETTLED = "- The settlement is forty thousand."


def folder_record(rid: str, kind: str, text: str, **native: object) -> RawRecord:
    fields = {"rid": rid, "source": "docs", "kind": kind, "via": documents.VIA, "ts": at(9), "fetched": at(9)}
    return RawRecord.model_validate({**fields, "native": native, "text": text, "title": rid})


def test_an_update_judges_what_the_list_took_as_the_rebuild_before_it(tmp_path: Path) -> None:
    vault = with_records(tmp_path, family=False)
    sha = "c0" * 32
    write_raw_file(vault.root, "raw/docs/2026/10/2026-10-14T0900-test-box.jsonl", [
        folder_record(COPY, documents.FILE_KIND, "", sha256=sha),
        folder_record(f"{COPY}/page1", "page", SETTLED.removeprefix("- ")),
    ])  # fmt: skip
    vault.commit([], "Raw: docs")
    note = doc(COPY_NOTE, [COPY], [f"{SETTLED} [r:{COPY}]"], "rev: 1", title="The copy")
    write_doc(vault, "recording-note/2026/10/2026-10-14-copy.md", note)
    # A copy of a forgotten file, at another path: forgotten by its identity alone, its page with it.
    forget(vault, files=[f"sha256:{sha}"])
    build.rebuild(vault)
    assert rows(vault, "SELECT rid, parts FROM skipped") == [(COPY, 1), (f"{COPY}/page1", 0)]
    write_doc(vault, "recording-note/2026/10/2026-10-14-copy.md", note + "\nA line added by hand.\n")
    page = folder_record(f"{COPY}/page1", "page", "The settlement is forty thousand, signed.")
    write_raw_file(vault.root, "raw/docs/2026/10/2026-10-14T1000-test-box.jsonl", [page])
    report = build.update(vault)
    # The note is indexed again and the page's new version read alone, with what the rebuild knew.
    assert not report.rebuilt and report.docs == 1 and report.runs == 1
    assert rows(vault, "SELECT count(*) FROM records WHERE source = 'docs'") == [(0,)]
    assert rows(vault, "SELECT count(*) FROM doc_anchors WHERE doc = ?", COPY_NOTE) == [(0,)]
    assert rows(vault, "SELECT count(*) FROM chunks WHERE text LIKE '%forty thousand%'") == [(0,)]


def test_an_update_reads_only_the_docs_it_indexes_and_what_they_cite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = with_records(tmp_path, family=False)
    recall.doc_put(vault, doc(NOTE, [CHAT], [KEPT]))
    recall.doc_put(vault, doc(DIGEST, [], [f"- The channel opened. {cite(vault, NOTE, KEPT)}"], title="The day"))
    for n in range(4):
        later = f"recording-note:2026-10-15-other-{n}"
        recall.doc_put(vault, doc(later, [OTHER], [f"- Another message, {n}. [r:{OTHER}]"]))
    # The note's second rev, so the digest cites an earlier one.
    recall.doc_put(vault, doc(NOTE, [CHAT, OTHER], [KEPT, TEN_AGAIN], "rev: 1"))
    forget(vault, rids=["chat:C0ELSEWHERE"])
    build.rebuild(vault)
    rel = "processed/recording-note/2026/10/2026-10-14-supplier-sync.md"
    first = next(sha for n, sha in DocStore(vault).history(NOTE) if n == 1)
    digest = vault.root / "processed/digest/2026/10/2026-10-14-day.md"
    # Another process: the rebuild's earlier revs aren't kept in this one.
    build._EARLIER.clear()  # pyright: ignore[reportPrivateUsage]
    for said, wanted in (
        ("- By hand.", [f"log --no-show-signature --format=%H%x1f%B%x1e -- {rel}", f"cat-file blob {first}:./{rel}"]),
        ("- By hand again.", []),
    ):
        digest.write_text(digest.read_text(encoding="utf-8") + f"{said}\n", encoding="utf-8", newline="\n")
        with git_calls(monkeypatch) as calls:
            report = build.update(vault)
        # The digest's history isn't read, nor any other doc: the earlier rev it cites, once a process.
        assert not report.rebuilt and report.docs == 1 and calls == wanted
    assert rows(vault, "SELECT count(*) FROM derived_from WHERE doc = ?", DIGEST) == [(1,)]


# -- what a rebuild leaves out -----------------------------------------------------------------------------------------

# What a vault that never held the family can't match: the list's digest, the run files and the doc files' bytes.
UNSHARED = {"meta", "run_files", "doc_files"}


def test_a_rebuild_gives_the_rows_of_a_vault_never_holding_the_family(held: Vault, tmp_path: Path) -> None:
    never = Vault.open(copied(tmp_path / "never", lambda d: build_never(d).root, f"{__name__}.never"))
    forget_the_transcript(held)
    build.rebuild(held)
    build.rebuild(never)
    with_family, without = logical_dump(build.db_path(held)), logical_dump(build.db_path(never))
    assert with_family["records"] != [] and with_family["chunks"] != []
    for table in set(with_family) - UNSHARED:
        assert with_family[table] == without[table], table


def test_a_record_s_lines_go_with_its_chunks_segments_and_covers(documented: Vault) -> None:
    assert rows(documented, "SELECT count(*) FROM records WHERE rid = ?", POLISHED) == [(0,)]
    assert rows(documented, "SELECT count(*) FROM segments WHERE rid = ?", POLISHED) == [(0,)]
    assert rows(documented, "SELECT count(*) FROM chunks WHERE rids LIKE ?", f"%{POLISHED}%") == [(0,)]
    assert rows(documented, "SELECT count(*) FROM chunks WHERE text LIKE '%thanked%'") == [(0,)]
    assert rows(documented, "SELECT rid FROM doc_covers WHERE doc = ? ORDER BY rid", NOTE) == [(CHAT,), (OTHER,)]
    assert rows(documented, "SELECT rid FROM doc_covers WHERE doc = ?", KEPT_LEGACY) == [(PLAUD,)]


def test_a_time_on_the_recording_reads_on_the_transcript_it_landed_on(documented: Vault) -> None:
    hand = parse_doc((documented.root / "processed/recording-note/2026/10/2026-10-17-by-hand.md").read_text("utf-8"))
    kept = next(n for n, said in hand.lines() if said == KEPT)
    # The recording stays, its transcript went, and the list's media holds it: the time lands on nothing left.
    assert rows(documented, "SELECT line, rid FROM doc_anchors WHERE doc = ?", HAND) == [(kept - 1, CHAT)]
    assert rows(documented, "SELECT rid FROM doc_covers WHERE doc = ? ORDER BY rid", HAND) == [(CHAT,), (PLAUD,)]


def test_a_current_rev_s_line_goes_and_the_lines_after_it_take_its_place(documented: Vault) -> None:
    note = DocStore(documented).get(NOTE)
    assert note is not None
    numbers = {said: n for n, said in note.lines()}
    anchors = rows(documented, "SELECT line, rid FROM doc_anchors WHERE doc = ? ORDER BY line", NOTE)
    # As the rewritten file numbers them: one covered rid fewer, and the first line gone.
    assert anchors == [(numbers[TEN_AGAIN] - 2, OTHER), (numbers[KEPT] - 2, CHAT)]
    week = rows(documented, "SELECT src, rev, src_line FROM derived_from WHERE doc = ?", LATER)
    # The week's line on the thanks went with it; the one on the price stands, on rev 2 as the file numbers it.
    assert week == [(NOTE, 2, numbers[TEN_AGAIN])]


def test_a_ref_to_an_earlier_rev_is_judged_on_that_rev(documented: Vault) -> None:
    # The day's digest cites rev 1, where the price was anchored on the transcript; rev 2 anchors it on a message.
    day = rows(documented, "SELECT line FROM doc_anchors WHERE doc = ?", DIGEST)
    refs = rows(documented, "SELECT src, rev FROM derived_from WHERE doc = ?", DIGEST)
    assert day == [] and refs == [(NOTE, 1)]
    assert rows(documented, "SELECT count(*) FROM chunks WHERE doc = ? AND text LIKE '%ten million%'", DIGEST) == [(0,)]


def test_a_doc_left_with_no_line_goes_but_a_confirmed_decision_keeps_its_title(documented: Vault) -> None:
    docs = {doc_id for (doc_id,) in rows(documented, "SELECT id FROM docs")}
    assert THANKS not in docs and LEGACY not in docs
    assert {NOTE, DIGEST, LATER, DECISION, HAND, KEPT_LEGACY} <= docs
    assert rows(documented, "SELECT status, title FROM docs WHERE id = ?", DECISION) == [
        ("confirmed", "the deal is ten million"),
    ]
    assert rows(documented, "SELECT count(*) FROM derived_from WHERE doc = ?", DECISION) == [(0,)]
    assert rows(documented, "SELECT count(*) FROM doc_covers WHERE doc = ?", DECISION) == [(0,)]
    # A removed doc keeps its file's row, so the docs read as current and read asks the index.
    removed = rows(documented, "SELECT doc FROM doc_files WHERE path LIKE '%thanks.md' OR path LIKE '%09-13%'")
    assert removed == [(None,), (None,)]
    index = lookup.open_read(documented)
    assert index is not None
    try:
        assert index.docs_current() and index.covering(POLISHED) == []
    finally:
        index.close()


def test_a_version_forget_takes_the_lines_on_a_rid_it_leaves_no_version_of(tmp_path: Path) -> None:
    vault = Vault.open(copied(tmp_path, lambda d: build_docs(d).root, f"{__name__}.docs"))
    forget(vault, versions=[stored(make_record(2))])
    build.rebuild(vault)
    note = DocStore(vault).get(NOTE)
    assert note is not None
    numbers = {said: n for n, said in note.lines()}
    assert rows(vault, "SELECT rid FROM doc_covers WHERE doc = ? ORDER BY rid", NOTE) == [(CHAT,), (POLISHED,)]
    assert {rid for _, rid in rows(vault, "SELECT line, rid FROM doc_anchors WHERE doc = ?", NOTE)} == {
        CHAT, POLISHED,
    }  # fmt: skip
    assert rows(vault, "SELECT src_line FROM derived_from WHERE doc = ?", LATER) == [(numbers[BYE],)]


def test_the_rule_reads_past_a_doc_it_can_t_read_and_a_rev_git_has_none_of(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = Vault.open(copied(tmp_path, lambda d: build_docs(d).root, f"{__name__}.docs"))
    write_doc(vault, "digest/2026/10/2026-10-18-broken.md", "not a doc\n")
    write_doc(vault, "digest/misplaced.md", doc("digest:2026-10-18-misplaced", [], ["- Elsewhere."]))
    nine = "digest:2026-10-18-nine"
    lines = [f"- On rev 9. [d:{NOTE}@9#12=00000000]", "- On a doc that went. [d:digest:2026-10-01-gone@1#12=00000000]"]
    write_doc(vault, "digest/2026/10/2026-10-18-nine.md", doc(nine, [], lines))
    forget_the_transcript(vault)
    # Earlier revs kept for the process make room when full.
    monkeypatch.setattr(build, "EARLIER_KEPT", 0)
    problems = build.rebuild(vault).problems
    assert any("2026-10-18-broken.md" in p for p in problems) and any("misplaced.md" in p for p in problems)
    assert sorted(rows(vault, "SELECT rev FROM derived_from WHERE doc = ?", nine)) == [(1,), (9,)]


def test_no_list_means_every_doc_reads_as_it_is(tmp_path: Path) -> None:
    vault = Vault.open(copied(tmp_path, lambda d: build_docs(d).root, f"{__name__}.docs"))
    build.rebuild(vault)
    docs = {doc_id for (doc_id,) in rows(vault, "SELECT id FROM docs")}
    assert {THANKS, LEGACY} <= docs
    assert rows(vault, "SELECT count(*) FROM segments WHERE rid = ?", POLISHED) == [(3,)]
    assert rows(vault, "SELECT rid FROM doc_anchors WHERE doc = ? ORDER BY line", HAND) == [(PLAUD,), (CHAT,)]


# -- the rule, on its own ----------------------------------------------------------------------------------------------


class FakeRaw:
    """Raw as a test says it is: what is gone, what has segments of its own, whether a time lands, what media holds."""

    def __init__(
        self,
        gone: Collection[str] = (),
        *,
        timed: Collection[str] = (),
        lands: bool = False,
        media: Collection[str] = (),
    ) -> None:
        self._gone, self._timed, self._lands, self._media = set(gone), set(timed), lands, set(media)

    def gone(self, rid: str) -> bool:
        return rid in self._gone

    def timed(self, rid: str) -> bool:
        return rid in self._timed

    def lands(self, rid: str, start: int, last: int) -> bool:
        return self._lands

    def media(self, rid: str) -> bool:
        return rid in self._media


def parsed(doc_id: str, covers: Sequence[str], lines: Sequence[str], *head: str) -> ProcessedDoc:
    return parse_doc(doc(doc_id, covers, lines, *head), lenient=True)


def number(found: ProcessedDoc, text: str) -> int:
    return next(n for n, said in found.lines() if said == text)


def pin(doc_id: str, rev: int | None, n: int) -> str:
    return f"[d:{doc_id}#{n}]" if rev is None else f"[d:{doc_id}@{rev}#{n}=00000000]"


def revs(earlier: Mapping[tuple[str, int], ProcessedDoc]) -> forgetdocs.Older:
    """The earlier revs a test has, answered a round at a time as the index's reader answers."""
    return lambda wanted: {key: earlier[key] for key in wanted if key in earlier}


def test_an_anchor_reads_where_it_lands() -> None:
    time = Anchor(PLAUD, "time", 642)
    assert forgetdocs.on_family(time, PLAUD, FakeRaw({PLAUD}))
    assert forgetdocs.on_family(time, PLAUD, FakeRaw(media={PLAUD}))
    assert not forgetdocs.on_family(time, PLAUD, FakeRaw(lands=True, media={PLAUD}))
    assert not forgetdocs.on_family(time, PLAUD, FakeRaw(timed={PLAUD}, media={PLAUD}))
    assert not forgetdocs.on_family(time, PLAUD, FakeRaw())
    assert not forgetdocs.on_family(Anchor(PLAUD, "part", 2), PLAUD, FakeRaw(media={PLAUD}))


def test_a_short_anchor_reads_on_the_one_covered_record_only() -> None:
    thanks, both = "- Thanks. @15:00", f"- Both. [r:{CHAT}] [r:{POLISHED}]"
    one = parsed(NOTE, [POLISHED], [thanks])
    two = parsed(NOTE, [POLISHED, CHAT], [thanks, both])
    assert forgetdocs.anchored(one, FakeRaw({POLISHED})) == {number(one, thanks)}
    assert forgetdocs.anchored(two, FakeRaw({POLISHED})) == {number(two, both)}


def test_refs_are_followed_to_a_fixed_point_through_earlier_revs() -> None:
    first = parsed(NOTE, [POLISHED], [BYE], "rev: 1")
    built = "- Built on the first. " + pin(NOTE, 1, number(first, BYE))
    second = parsed(NOTE, [CHAT], [built], "rev: 2")
    note = parsed(NOTE, [CHAT], [KEPT], "rev: 3")
    on_two = "- On rev 2. " + pin(NOTE, 2, number(second, built))
    lost = "- On a rev nobody can read. " + pin(NOTE, 9, 12)
    current = "- On the current rev. " + pin(NOTE, None, number(note, KEPT))
    elsewhere = "- On a doc that isn't here. " + pin(THANKS, None, 12)
    brief = parsed(DIGEST, [], [on_two, lost, current, elsewhere])
    earlier = {(NOTE, 1): first, (NOTE, 2): second}
    found = forgetdocs.fates([note, brief], FakeRaw({POLISHED}), forgotten.EMPTY, older=revs(earlier))
    assert set(found) == {DIGEST} and found[DIGEST].dropped == {number(brief, on_two)}
    left = found[DIGEST].doc
    assert left is not None and [said for _, said in left.lines() if said.startswith("-")] == [lost, current, elsewhere]


def test_a_ref_loop_between_earlier_revs_ends() -> None:
    y = number(parsed(NOTE, [CHAT], ["- Loop."], "rev: 1"), "- Loop.")
    x = number(parsed(DIGEST, [], ["- Loop."], "rev: 1"), "- Loop.")
    earlier = {
        (NOTE, 1): parsed(NOTE, [CHAT], ["- Loop. " + pin(DIGEST, 1, x)], "rev: 1"),
        (DIGEST, 1): parsed(DIGEST, [], ["- Loop. " + pin(NOTE, 1, y)], "rev: 1"),
    }
    note = parsed(NOTE, [CHAT], [KEPT], "rev: 2")
    digest = parsed(DIGEST, [], ["- Loop. " + pin(NOTE, 1, y)], "rev: 2")
    assert forgetdocs.fates([note, digest], FakeRaw(), forgotten.EMPTY, older=revs(earlier)) == {}


def test_a_line_built_on_a_removed_legacy_note_goes() -> None:
    listed = forgotten.fold([line(old_paths=[OLD_PATH])])
    note = parse_doc(legacy(LEGACY, [PLAUD], OLD_PATH, "Ten million."), lenient=True)
    digest = parsed(DIGEST, [], ["- From the note. " + pin(LEGACY, 1, 12)], "rev: 1")
    found = forgetdocs.fates([note, digest], FakeRaw(), listed)
    assert found[LEGACY].doc is None and found[DIGEST].doc is None
    # The note renamed since: the old path its earlier rev held is the one listed.
    renamed = parse_doc(legacy(LEGACY, [PLAUD], "notes/renamed.md", "Ten million.", rev=2), lenient=True)
    older = parsed(LATER, [], ["- From the old note. " + pin(LEGACY, 1, 12)], "rev: 1")
    first = revs({(LEGACY, 1): note})
    assert set(forgetdocs.fates([renamed, older], FakeRaw(), listed, older=first)) == {LATER}
    assert forgetdocs.fates([renamed, older], FakeRaw(), forgotten.EMPTY, older=first) == {}


def test_without_drops_lines_by_their_number_and_reads_back_as_docket_writes_it() -> None:
    note = parsed(NOTE, [PLAUD, CHAT], [TEN, KEPT])
    left = forgetdocs.without(note, {number(note, TEN)}, [CHAT])
    assert [said for _, said in left.lines() if said] == ["# Supplier sync", KEPT]
    assert left.meta.covers == [CHAT] and number(left, KEPT) == left.head_lines + 4
    assert left.source == left.render()


def test_the_rule_reads_what_refs_lead_to_and_judges_only_its_docs() -> None:
    note = parsed(NOTE, [POLISHED], [BYE], "rev: 1")
    built = "- Built on the thanks. " + pin(NOTE, 1, number(note, BYE))
    elsewhere = "- On a doc no one can read. " + pin(THANKS, 1, 12)
    digest = parsed(DIGEST, [], [built, elsewhere], "rev: 1")
    asked: list[set[str]] = []

    def current(wanted: Collection[str]) -> dict[str, ProcessedDoc]:
        asked.append(set(wanted))
        return {NOTE: note} if NOTE in wanted else {}

    found = forgetdocs.fates([digest], FakeRaw({POLISHED}), forgotten.EMPTY, current=current)
    # The note is read to judge the digest's line, and judged only for that: it gets no fate of its own here.
    assert set(found) == {DIGEST} and found[DIGEST].dropped == {number(digest, built)}
    assert asked == [{NOTE, THANKS}]
