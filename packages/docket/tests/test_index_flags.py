"""Doc lines that cite a line of another doc, and their flags when that line changes (#160, DR-0540 to DR-0543):
pinned by doc_put, flagged alike from the index and from the vault, cleared only by citing afresh or dropping the ref,
and the same after two rebuilds or an update. Invented docs on the fixture vault."""

from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import REC, copy_vault, git, note, template_vault

from docket import index, recall
from docket.index import flags
from docket.index.build import open_index
from docket.vault.docstore import DocStore, PutResult
from docket.vault.processed import cited_text, line_hash
from docket.vault.vault import Vault

NOTE = "recording-note:2026-10-14-supplier-call"
NOTE_PATH = "processed/recording-note/2026/10/2026-10-14-supplier-call.md"
BRIEF = "brief:northwind"
TEN = "- Ben put the consultant at 10 million forints. @12:08"
TWELVE = "- Ben put the consultant at 12 million forints. @12:08"
FRIDAY = "- Cleo wants the offer in writing by Friday. @14:30"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_vault(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return copy_vault(template, tmp_path)


def put(vault: Vault, text: str) -> PutResult:
    return recall.doc_put(vault, text)


def put_note(vault: Vault, *lines: str, rev: int | None = None) -> None:
    put(vault, note(NOTE, lines=list(lines), rev=str(rev)) if rev else note(NOTE, lines=list(lines)))


def line_no(vault: Vault, doc_id: str, text: str) -> int:
    """The line's number in the stored doc, as read and verify show it."""
    doc = DocStore(vault).get(doc_id)
    assert doc is not None
    return next(n for n, line in doc.lines() if line == text)


def cite(vault: Vault, doc_id: str, text: str) -> str:
    doc = DocStore(vault).get(doc_id)
    assert doc is not None
    return f"[d:{doc_id}@{doc.meta.rev}#{line_no(vault, doc_id, text)}]"


def put_brief(vault: Vault, *lines: str, rev: int | None = None, covers: list[str] | None = None) -> PutResult:
    fields = {"rev": str(rev)} if rev else {}
    return put(vault, note(BRIEF, covers=covers if covers is not None else [], lines=list(lines), **fields))


def from_index(vault: Vault) -> list[flags.Flag]:
    index.update(vault)
    conn = open_index(vault)
    try:
        return flags.from_index(conn)
    finally:
        conn.close()


def flagged(found: list[flags.Flag]) -> list[tuple[str, int]]:
    return [(f.doc, f.line) for f in found if f.flagged]


def brief_line(vault: Vault, start: str) -> int:
    doc = DocStore(vault).get(BRIEF)
    assert doc is not None
    return next(n for n, line in doc.lines() if line.startswith(start))


def read_flags(vault: Vault) -> dict[int, list[str]]:
    shown = recall.read(vault, BRIEF).doc
    assert shown is not None
    return {line.n: line.flags for line in shown.lines if line.flags}


def corrected(vault: Vault) -> None:
    """A note, a brief citing two of its lines, then the note's next rev changing one of them."""
    put_note(vault, TEN, FRIDAY)
    put_brief(
        vault, f"- The consultant: 10 million. {cite(vault, NOTE, TEN)}", f"- By Friday. {cite(vault, NOTE, FRIDAY)}"
    )
    put_note(vault, TWELVE, FRIDAY, rev=1)


def test_a_put_pins_a_cited_line_to_its_text(vault: Vault) -> None:
    put_note(vault, TEN, FRIDAY)
    token = cite(vault, NOTE, TEN)
    result = put_brief(vault, f"- The consultant: 10 million. {token}")
    pinned = line_hash(cited_text(TEN, content=True))
    assert pinned == line_hash("- Ben put the consultant at 10 million forints.")
    stored = DocStore(vault).get(BRIEF)
    assert stored is not None
    n = brief_line(vault, "- The consultant")
    assert dict(stored.lines())[n] == f"- The consultant: 10 million. {token[:-1]}={pinned}]"
    assert result.pinned_lines == [n]
    assert stored.meta.covers == []


def test_a_corrected_line_flags_the_line_built_on_it_and_no_other(vault: Vault) -> None:
    corrected(vault)
    ten, friday = brief_line(vault, "- The consultant"), brief_line(vault, "- By Friday")
    assert flagged(from_index(vault)) == [(BRIEF, ten)]
    marks = read_flags(vault)
    assert list(marks) == [ten]
    assert marks[ten][0].startswith(f"cites line 14 of {NOTE} rev 1, which reads otherwise in rev 2")
    report = recall.stale(vault)
    assert [(s.doc, s.n) for s in report.flagged] == [(BRIEF, ten)]
    assert report.flagged[0].text.startswith("- The consultant: 10 million.")
    assert friday not in marks


def test_an_unrelated_edit_flags_nothing(vault: Vault) -> None:
    put_note(vault, TEN, FRIDAY)
    put_brief(vault, f"- The consultant: 10 million. {cite(vault, NOTE, TEN)}")
    # A new line above the cited one moves it down: the numbers shift, the text doesn't.
    put_note(vault, "- Ada opened the call. @0:00", TEN, FRIDAY, rev=1)
    assert line_no(vault, NOTE, TEN) == 15
    assert flagged(from_index(vault)) == []
    assert read_flags(vault) == {}


def test_a_flag_clears_only_when_the_line_is_cited_afresh_or_its_ref_dropped(vault: Vault) -> None:
    corrected(vault)
    old = DocStore(vault).get(BRIEF)
    assert old is not None
    pinned = next(line for _, line in old.lines() if line.startswith("- The consultant")).rsplit(" ", 1)[1]
    friday = next(line for _, line in old.lines() if line.startswith("- By Friday"))
    # The text rewritten under the old ref: still flagged.
    put_brief(vault, f"- The consultant: 12 million now. {pinned}", friday, rev=1)
    assert flagged(from_index(vault)) == [(BRIEF, brief_line(vault, "- The consultant"))]
    # Cited afresh: the flag goes, and the ref holds the note's new rev.
    afresh = cite(vault, NOTE, TWELVE)
    put_brief(vault, f"- The consultant: 12 million now. {afresh}", friday, rev=2)
    assert flagged(from_index(vault)) == []
    assert f"[d:{NOTE}@2#14=" in (vault.root / "processed" / "brief" / "northwind.md").read_text(encoding="utf-8")
    # The ref dropped for a raw anchor: nothing to flag.
    put_note(vault, "- Ben put the consultant at 13 million forints. @12:08", FRIDAY, rev=2)
    put_brief(vault, "- The consultant: 12 million now. [r:calls:rec-0042@12:08]", friday, rev=3, covers=[REC])
    assert flagged(from_index(vault)) == []


def test_a_gone_or_superseded_doc_flags_its_citers(vault: Vault) -> None:
    put_note(vault, TEN, FRIDAY)
    put_brief(vault, f"- The consultant: 10 million. {cite(vault, NOTE, TEN)}")
    newer = "recording-note:2026-10-14-supplier-call-again"
    put(vault, note(newer, lines=[TEN], supersedes=NOTE))
    found = [f for f in from_index(vault) if f.flagged]
    assert [(f.why, f.superseded_by) for f in found] == [("superseded", newer)]
    assert "is superseded by" in next(iter(read_flags(vault).values()))[0]
    git(vault.root, "rm", "-q", NOTE_PATH)
    git(vault.root, "commit", "-qm", "Drop the first note")
    assert [f.why for f in from_index(vault) if f.flagged] == ["gone"]
    assert next(iter(read_flags(vault).values()))[0].endswith(f"and {NOTE} is gone")


def test_two_rebuilds_and_an_update_give_the_same_flags(vault: Vault) -> None:
    put_note(vault, TEN, FRIDAY)
    put_brief(
        vault, f"- The consultant: 10 million. {cite(vault, NOTE, TEN)}", f"- By Friday. {cite(vault, NOTE, FRIDAY)}"
    )
    index.rebuild(vault)
    put_note(vault, TWELVE, FRIDAY, rev=1)
    updated = from_index(vault)
    index.rebuild(vault)
    first = from_index(vault)
    index.rebuild(vault)
    assert updated == first == from_index(vault)
    assert len(flagged(first)) == 1


def test_a_correction_waits_until_the_corrected_line_changes(vault: Vault) -> None:
    put_note(vault, TWELVE, FRIDAY)
    correction = note(
        "correction:2026-10-15-supplier-call",
        lines=[f"- The note says 12 million; the call says 10. [r:{REC}@12:08] {cite(vault, NOTE, TWELVE)}"],
    )
    put(vault, correction)
    found = from_index(vault)
    assert [f.unapplied for f in found] == [True]
    assert flagged(found) == []
    put_note(vault, TEN, FRIDAY, rev=1)
    found = from_index(vault)
    assert [(f.unapplied, f.flagged) for f in found] == [(False, False)]
    assert found[0].message.endswith(": applied")


def test_a_form_this_docket_doesnt_read_loads_in_the_index_without_a_problem(vault: Vault) -> None:
    # As an older Docket's index reads a line with [d:...]: the token is left out of the text, and nothing breaks.
    path = vault.root / "processed" / "brief" / "older.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(note("brief:older", lines=["- The consultant. @12:08 [zz:later]"]), encoding="utf-8")
    index.rebuild(vault)
    conn = open_index(vault)
    try:
        problems = conn.execute("SELECT place, message FROM problems WHERE kind = 'doc'").fetchall()
        rows = conn.execute("SELECT doc FROM doc_lines WHERE doc = 'brief:older'").fetchall()
    finally:
        conn.close()
    assert problems == []
    assert rows
