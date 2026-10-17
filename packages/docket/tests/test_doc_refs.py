"""What doc_put checks of a line's doc-line refs, and how a hand edit and the redact pass treat refs and forms this
Docket doesn't read (#160, DR-0540, DR-0541, DR-0543). Invented docs and secrets on the fixture vault; a password is
assembled from parts, as in test_redact.py."""

from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import REC, copy_vault, git, note, sh, template_vault

from docket import recall
from docket.vault.anchors import AnchorError, parse_ref
from docket.vault.docstore import DocStore
from docket.vault.processed import DocRefusedError, cited_text, line_hash
from docket.vault.vault import Vault

NOTE = "recording-note:2026-10-14-supplier-call"
NOTE_PATH = "processed/recording-note/2026/10/2026-10-14-supplier-call.md"
BRIEF = "brief:northwind"
BRIEF_PATH = "processed/brief/northwind.md"
TEN = "- Ben put the consultant at 10 million forints. @12:08"
TWELVE = "- Ben put the consultant at 12 million forints. @12:08"
FRIDAY = "- Cleo wants the offer in writing by Friday. @14:30"
HIDDEN = "hunter2-" + "Example"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_vault(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    copied = copy_vault(template, tmp_path)
    recall.doc_put(copied, note(NOTE, lines=[TEN, FRIDAY]))
    return copied


def brief(*lines: str, covers: list[str] | None = None, **fields: str) -> str:
    return note(BRIEF, covers=covers if covers is not None else [], lines=list(lines), **fields)


def refused(vault: Vault, text: str) -> list[str]:
    with pytest.raises(DocRefusedError) as e:
        recall.doc_put(vault, text)
    return [str(p) for p in e.value.problems]


def head(vault: Vault) -> str:
    return sh(vault.root, "git", "rev-parse", "HEAD").strip()


@pytest.mark.parametrize(
    ("ref", "says"),
    [
        ("[d:minutes:2026-10-14-nothing@1#14]", "there is no doc minutes:2026-10-14-nothing"),
        (f"[d:{NOTE}@4#14]", f"{NOTE} has no rev 4; its revs: 1"),
        (f"[d:{NOTE}@1#13]", f"line 13 of {NOTE} rev 1 has no text to cite"),
        (f"[d:{NOTE}@1#99]", f"line 99 of {NOTE} rev 1 has no text to cite"),
        (f"[d:{BRIEF}@1#14]", "a line can't cite its own doc"),
        (f"[d:{NOTE}#14]", f"names no rev; cite the rev you read, as verify's cite gives it: [d:{NOTE}@1#14]"),
        (f"[d:{NOTE}@1]", "isn't a doc-line ref"),
        (f"[d:{NOTE}#14=00000000]", "isn't a doc-line ref"),
    ],
)
def test_a_ref_that_cites_nothing_it_can_is_refused(vault: Vault, ref: str, says: str) -> None:
    # With a raw anchor and covers too, so each refusal is the ref's own.
    problems = refused(vault, brief(f"- The consultant. @12:08 {ref}", covers=[REC]))
    assert any(says in p for p in problems), problems


def test_a_line_whose_text_stands_twice_can_t_be_cited(vault: Vault) -> None:
    recall.doc_put(vault, note(NOTE, lines=[TEN, FRIDAY, TEN], rev="1"))
    problems = refused(vault, brief(f"- The consultant. [d:{NOTE}@2#14]"))
    assert [p.split("): ", 1) for p in problems] == [
        [
            'line 10 ("- The consultant. [d:recording-note:2026..."',
            f"[d:{NOTE}@2#14]: line 14's text also stands at line 16; cite a line whose text is its own",
        ]
    ]


def test_an_earlier_rev_is_cited_from_git_and_its_line_is_born_flagged(vault: Vault) -> None:
    recall.doc_put(vault, note(NOTE, lines=[TWELVE, FRIDAY], rev="1"))
    recall.doc_put(vault, brief(f"- The consultant was 10 million. [d:{NOTE}@1#14]"))
    stored = (vault.root / BRIEF_PATH).read_text(encoding="utf-8")
    assert f"[d:{NOTE}@1#14={line_hash(cited_text(TEN, content=True))}]" in stored
    shown = recall.read(vault, BRIEF).doc
    assert shown is not None
    assert [flag for line in shown.lines for flag in line.flags] == [
        f"cites line 14 of {NOTE} rev 1, which reads otherwise in rev 2; check it, then cite it again or drop it"
    ]


def test_a_hand_edit_s_bare_ref_is_pinned_when_it_is_taken_in(vault: Vault) -> None:
    recall.doc_put(vault, brief(f"- The consultant. [d:{NOTE}@1#14]"))
    path = vault.root / BRIEF_PATH
    path.write_text(path.read_text(encoding="utf-8") + f"- By Friday. [d:{NOTE}#15]\n", encoding="utf-8")
    problems = refused(vault, brief(f"- The consultant again. [d:{NOTE}@1#14]", rev="1"))
    assert any("after a hand edit taken in as rev 2" in p for p in problems), problems
    committed = sh(vault.root, "git", "show", f"HEAD:{BRIEF_PATH}")
    assert f"- By Friday. [d:{NOTE}@1#15={line_hash(cited_text(FRIDAY, content=True))}]" in committed


def test_a_hand_edit_holding_a_form_this_docket_doesnt_read_stays_on_disk(vault: Vault) -> None:
    recall.doc_put(vault, brief(f"- The consultant. [d:{NOTE}@1#14]"))
    before = head(vault)
    path = vault.root / BRIEF_PATH
    edited = path.read_text(encoding="utf-8") + f"- Later. [d:{NOTE}@1#15] [zz:soon]\n"
    path.write_text(edited, encoding="utf-8")
    n = len(edited.splitlines())
    problems = refused(vault, brief(f"- The consultant again. [d:{NOTE}@1#14]", rev="1"))
    assert problems == [
        f"doc: {BRIEF_PATH} was edited by hand, and its line {n} holds [zz:soon], an anchor this Docket doesn't "
        "read; update Docket, or move it before the line's anchors; edit it or check it out again"
    ]
    assert head(vault) == before
    assert path.read_text(encoding="utf-8") == edited


def test_a_secret_in_a_token_that_only_looks_like_an_anchor_never_reaches_git(vault: Vault) -> None:
    path = vault.root / NOTE_PATH
    path.write_text(path.read_text(encoding="utf-8") + f"- The code. [r:password={HIDDEN}\n", encoding="utf-8")
    problems = refused(vault, note(NOTE, lines=[TEN], rev="1"))
    assert any("has an anchor that doesn't parse" in p for p in problems), problems
    assert HIDDEN not in sh(vault.root, "git", "log", "-p", "--all")


def test_the_redact_pass_leaves_a_doc_holding_a_form_it_doesnt_read_whole(vault: Vault) -> None:
    path = vault.root / "processed" / "brief" / "older.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = note("brief:older", covers=[REC], lines=["- Cleo on +1 555 010 0188. @12:08 [zz:soon]"])
    path.write_text(text, encoding="utf-8")
    git(vault.root, "add", "processed/brief/older.md")
    git(vault.root, "commit", "-qm", "A doc from a newer Docket")
    report = DocStore(vault).redact_all()
    assert report.left == [
        "processed/brief/older.md: line 11 holds [zz:soon], an anchor this Docket doesn't read; update Docket, or "
        "move it before the line's anchors"
    ]
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.parametrize(
    ("line", "says"),
    [
        (f"- It was 10 million. [d:{NOTE}@1#14]", "a correction's line ends with the raw that shows it"),
        (f"- It was 10 million. [r:{REC}@12:08]", "a correction's line cites the line it corrects"),
    ],
)
def test_a_correction_line_needs_its_raw_and_the_line_it_corrects(vault: Vault, line: str, says: str) -> None:
    problems = refused(vault, note("correction:2026-10-15-supplier-call", lines=[line]))
    assert any(says in p for p in problems), problems


def test_verify_gives_each_line_its_cite_and_follows_a_ref_one_level(vault: Vault) -> None:
    recall.doc_put(vault, brief(f"- The consultant: 10 million. [d:{NOTE}@1#14]"))
    checked = recall.verify(vault, BRIEF)
    [line] = checked.lines
    assert line.cite == f"[d:{BRIEF}@1#{line.n}]"
    [ref] = line.refs
    assert (ref.doc, ref.n, ref.text, ref.problem) == (NOTE, 14, TEN, None)
    assert [s.anchor for s in ref.spans] == ["@12:08"]
    assert verify_cites(vault, NOTE)[14] == f"[d:{NOTE}@1#14]"


def verify_cites(vault: Vault, doc_id: str) -> dict[int, str | None]:
    return {line.n: line.cite for line in recall.verify(vault, doc_id).lines}


def test_anchors_an_older_docket_redacted_are_named_with_the_rev_to_restore(vault: Vault) -> None:
    recall.doc_put(vault, brief(f"- The consultant. @12:08 [r:{REC}@12:08]", covers=[REC]))
    path = vault.root / BRIEF_PATH
    good = path.read_text(encoding="utf-8")
    # As `docket doc redact` on an older Docket wrote it: a pattern matched inside the rid, committed as rev 2.
    hurt = good.replace("rev: 1", "rev: 2").replace(f"[r:{REC}@12:08]", "[r:calls:rec-[redacted:pin]@12:08]")
    path.write_text(hurt, encoding="utf-8")
    git(vault.root, "commit", "-qam", f"Doc: {BRIEF} rev 2")
    n = next(i for i, line in enumerate(hurt.splitlines(), start=1) if "[redacted:pin]" in line)
    said = "[r:calls:rec-[redacted:pin]@12:08]: an older Docket's redaction wrote inside this anchor; restore the "
    said += "line's anchors from rev 1"
    [line] = [one for one in recall.verify(vault, BRIEF).lines if one.n == n]
    assert said in line.problems
    shown = recall.read(vault, BRIEF).doc
    assert shown is not None
    assert said in next(one.flags for one in shown.lines if one.n == n)
    damaged = recall.stale(vault).damaged
    assert [(d.doc, d.n, d.restore_from) for d in damaged] == [(BRIEF, n, 1)]


def test_a_pinned_ref_to_a_doc_that_never_was_is_refused(vault: Vault) -> None:
    problems = refused(vault, brief("- The consultant. [d:minutes:2026-10-14-nothing@1#3=0a1b2c3d]"))
    assert any("there is no doc minutes:2026-10-14-nothing" in p for p in problems), problems


def test_a_hand_edit_s_ref_to_nothing_stops_its_take_in(vault: Vault) -> None:
    recall.doc_put(vault, brief(f"- The consultant. [d:{NOTE}@1#14]"))
    path = vault.root / BRIEF_PATH
    path.write_text(path.read_text(encoding="utf-8") + "- Later. [d:minutes:2026-10-14-nothing#3]\n", encoding="utf-8")
    problems = refused(vault, brief(f"- The consultant again. [d:{NOTE}@1#14]", rev="1"))
    assert problems == [
        f"doc: {BRIEF_PATH} was edited by hand, and its line 14: [d:minutes:2026-10-14-nothing#3]: there is no doc "
        "minutes:2026-10-14-nothing"
    ]


def test_verify_says_what_became_of_each_cited_line(vault: Vault) -> None:
    recall.doc_put(vault, brief(f"- The consultant. [d:{NOTE}@1#14]", f"- By Friday. [d:{NOTE}@1#15]"))
    recall.doc_put(vault, note(NOTE, lines=[TWELVE, FRIDAY], rev="1"))
    path = vault.root / BRIEF_PATH
    # Unpinned refs a hand edit left, not taken in yet: by the current rev's number, by an old rev, to a blank line.
    path.write_text(
        path.read_text(encoding="utf-8") + f"- Now. [d:{NOTE}#15]\n- Then. [d:{NOTE}@1#15]\n- Blank. [d:{NOTE}#13]\n",
        encoding="utf-8",
    )
    refs = [(line.text.split(".")[0], line.refs[0]) for line in recall.verify(vault, BRIEF).lines if line.refs]
    assert [(said, ref.n, ref.problem) for said, ref in refs] == [
        ("- The consultant", None, f"line 14 of {NOTE} rev 1 reads otherwise in rev 2"),
        ("- By Friday", 15, None),
        ("- Now", 15, None),
        ("- Then", None, f"not pinned yet, and {NOTE} is at rev 2 now"),
        ("- Blank", None, f"line 13 of {NOTE} has no text to cite"),
    ]
    git(vault.root, "rm", "-q", NOTE_PATH)
    git(vault.root, "commit", "-qm", "Drop the note")
    assert {ref.problem for line in recall.verify(vault, BRIEF).lines for ref in line.refs} == {f"{NOTE} is gone"}


def test_a_hash_with_no_rev_is_refused_everywhere_a_ref_is_read(vault: Vault) -> None:
    # A hash only goes with a rev (DR-0540): [d:<id>#<n>=<hash>] is no ref, so it is never stored as pinned and left
    # where no flag can see it. doc_put refuses it, verify names it, and a hand edit's take-in stops on it.
    token = f"[d:{NOTE}#14=00000000]"
    with pytest.raises(AnchorError, match="isn't a doc-line ref"):
        parse_ref(token)
    problems = refused(vault, brief(f"- The consultant was quoted. @12:08 {token}", covers=[REC]))
    assert any(f"{token}: isn't a doc-line ref" in p for p in problems), problems
    recall.doc_put(vault, brief(f"- The consultant. [d:{NOTE}@1#14]"))
    path = vault.root / BRIEF_PATH
    path.write_text(path.read_text(encoding="utf-8") + f"- Later. {token}\n", encoding="utf-8")
    [line] = [one for one in recall.verify(vault, BRIEF).lines if one.text.startswith("- Later.")]
    assert any(token in problem and "isn't a doc-line ref" in problem for problem in line.problems)
    problems = refused(vault, brief(f"- The consultant again. [d:{NOTE}@1#14]", rev="1"))
    assert any("has an anchor that doesn't parse" in p and token in p for p in problems), problems
