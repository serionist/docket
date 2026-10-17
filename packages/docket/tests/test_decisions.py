"""The decision log (#162, DR-0563 to DR-0565): the form doc_put holds a decision to, its status written only by the
Briefs page, every read taking it as committed, supersede and contradict links, a brief's Decisions section, and
never-store. On a copy of the index fixture, with invented people. A confirmed or rejected decision is written here as
the Briefs page will write one: its next rev, committed."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest
from docket_testkit import git, index_template, make_record

from docket import briefs, decisions, recall, render
from docket.cli import main
from docket.index import search
from docket.index import update as index_update
from docket.index.build import open_index
from docket.vault import docstore
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.decision_form import EDITED, NOT_CONFIRMED, as_committed, bare, committed_doc, standings
from docket.vault.docstore import DocStore
from docket.vault.processed import DocRefusedError, parse_doc
from docket.vault.vault import Vault

NOTE = "recording-note:2026-10-07-supplier-call"
TEN = "- The consultant asks ten million forints for the quarter, above the budget. @00:30-01:50"
FRIDAY = "- Cleo sends the offer in writing by Friday. @04:00-05:00"
FIRST = "decision:2026-10-13-charging-queue-first"
LATER = "decision:2026-10-14-pilot-first"
OTHER = "decision:2026-10-15-offer-in-writing"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    shutil.copytree(template, tmp_path / "vault")
    return Vault.open(tmp_path / "vault")


def cite(vault: Vault, doc_id: str, text: str) -> str:
    doc = DocStore(vault).get(doc_id)
    assert doc is not None
    return f"[d:{doc_id}@{doc.meta.rev}#{next(n for n, line in doc.lines() if line == text)}]"


def decision(
    doc_id: str,
    title: str,
    lines: Sequence[str],
    *,
    subject: str = "fleet-management",
    head: Sequence[str] = (),
    entities: str | None = None,
    rev: str | None = None,
) -> str:
    front = [
        f"id: {doc_id}",
        "type: decision",
        *([f"rev: {rev}"] if rev else []),
        "covers: []",
        "by: claude-test, decision 2026-10-14",
        f"entities: [{entities or subject}]",
        "status: proposed",
        f"subject: {subject}",
        *head,
    ]
    return "---\n" + "\n".join(front) + "\n---\n\n" + f"# {title}\n\n" + "\n".join(lines) + "\n"


def propose(
    vault: Vault, doc_id: str = FIRST, title: str = "Proposed: the charging queue comes first", head: Sequence[str] = ()
) -> None:
    recall.doc_put(vault, decision(doc_id, title, [f"- Cleo said so. {cite(vault, NOTE, FRIDAY)}"], head=head))


def decide(vault: Vault, doc_id: str, status: str, supersedes: Sequence[str] = ()) -> None:
    """The decision's next rev as the Briefs page writes it: its status, its title, `replaces` moved to `supersedes`."""
    store = DocStore(vault)
    doc = committed_doc(store.committed_text(doc_id))
    assert doc is not None
    meta = doc.meta.model_copy(
        update={"status": status, "supersedes": list(supersedes), "replaces": [], "rev": doc.meta.rev + 1}
    )
    title = next(line for _, line in doc.lines() if line.startswith("# "))
    word = {"confirmed": "", "rejected": "Rejected: "}[status]
    body = doc.body.replace(title, f"# {word}{bare(title[2:])}", 1)
    text = parse_doc(doc.render(), lenient=True)
    text = type(text)(meta, body, text.head_lines).render()
    path = store.file(doc_id)
    path.write_text(text, encoding="utf-8", newline="\n")
    git(vault.root, "add", "--", str(path))
    git(vault.root, "commit", "-qm", f"Decision: {doc_id} {status} on the Briefs page\n\nDoc: {doc_id} rev {meta.rev}")


def refused(vault: Vault, text: str) -> list[str]:
    with pytest.raises(DocRefusedError) as e:
        recall.doc_put(vault, text)
    return [str(p) for p in e.value.problems]


def set_lists(vault: Vault, **lists: list[str]) -> Vault:
    """vault.yaml's never-store lists grown, uncommitted: an edit that only keeps more out is in force at once."""
    path = vault.root / CONFIG_FILE
    config = VaultConfig.load(path)
    grown = config.never_store.model_copy(update=lists)
    path.write_text(config.model_copy(update={"never_store": grown}).dump(), encoding="utf-8", newline="\n")
    return Vault.open(vault.root)


def one(vault: Vault, doc_id: str) -> decisions.DecisionState:
    return next(d for d in decisions.report(vault).decisions if d.id == doc_id)


def brief(vault: Vault, entity: str, title: str, decided: Sequence[str]) -> str:
    changed = f"- The price comes up again. {cite(vault, NOTE, TEN)}"
    return (
        f"---\nid: brief:{entity}\ntype: brief\ncovers: []\nby: claude-test, brief 2026-10-17\nentities: [{entity}]\n"
        f"---\n\n# {title}\n\n## What changed\n\n{changed}\n\n## Last conversations\n\n## Decisions\n\n"
        + "".join(f"{line}\n" for line in decided)
    )


def decided_line(vault: Vault, doc_id: str) -> str:
    found = one(vault, doc_id)
    assert found.cite is not None
    return f"- {found.title} {found.cite}"


def hits(vault: Vault, query: str) -> list[str]:
    index_update(vault)
    return [g.title for g in search(vault, query, k=50).groups if g.key.startswith("decision:")]


# -- proposed is never decided ----------------------------------------------------------------------------------------


def test_a_proposed_decision_reads_as_proposed_everywhere(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    propose(vault)
    read = recall.read(vault, FIRST).doc
    assert read is not None
    assert (read.status, read.current) == ("proposed", False)
    assert "a proposed decision, not current" in render.read(recall.read(vault, FIRST))
    found = one(vault, FIRST)
    assert (found.status, found.current, found.cite, found.title) == (
        "proposed",
        False,
        None,
        "the charging queue comes first",
    )
    assert hits(vault, "charging queue comes first") == ["Proposed: the charging queue comes first"]
    assert DocStore(vault).file(FIRST).read_text(encoding="utf-8").count("# Proposed: the charging queue") == 1
    capsys.readouterr()
    assert main(["decision", "list", "--vault", str(vault.root)]) == 0
    assert capsys.readouterr().out.splitlines()[:2] == [
        f"proposed   {FIRST}  the charging queue comes first",
        "           on fleet-management, rev 1",
    ]


def test_a_put_writes_only_a_proposed_decision(vault: Vault) -> None:
    text = decision(FIRST, "Proposed: x", [f"- Cleo said so. {cite(vault, NOTE, FRIDAY)}"])
    assert any(
        "only the Briefs page confirms" in p
        for p in refused(vault, text.replace("status: proposed", "status: confirmed"))
    )
    supersedes = text.replace("subject: fleet-management", f"subject: fleet-management\nsupersedes: [{OTHER}]")
    assert any("the Briefs page's confirm moves it" in p for p in refused(vault, supersedes))


def test_a_decision_confirmed_or_rejected_takes_no_new_rev(vault: Vault) -> None:
    propose(vault)
    reworded = decision(
        FIRST, "Proposed: the charging queue goes first", [f"- Cleo said so. {cite(vault, NOTE, FRIDAY)}"], rev="1"
    )
    assert recall.doc_put(vault, reworded).rev == 2
    decide(vault, FIRST, "confirmed")
    again = reworded.replace("rev: 1", "rev: 3")
    assert refused(vault, again) == [
        f"doc: {FIRST} is confirmed: a decision confirmed or rejected takes no new rev; a change is a new decision "
        "that replaces it"
    ]


# -- every read takes it as committed ---------------------------------------------------------------------------------


def test_a_status_changed_on_disk_reads_as_its_last_commit_and_isn_t_taken_in(vault: Vault) -> None:
    propose(vault)
    path = DocStore(vault).file(FIRST)
    edited = path.read_text(encoding="utf-8").replace("status: proposed", "status: confirmed")
    edited = edited.replace("# Proposed: the charging", "# the charging")
    path.write_text(edited, encoding="utf-8", newline="\n")
    store = DocStore(vault)
    shown = store.get(FIRST)
    assert shown is not None
    assert (shown.meta.status, "# Proposed: the charging queue comes first" in shown.source) == ("proposed", True)
    assert "status: confirmed" not in shown.source
    assert one(vault, FIRST).status == "proposed"
    assert hits(vault, "charging queue comes first") == ["Proposed: the charging queue comes first"]
    head = git(vault.root, "rev-parse", "HEAD")
    problems = refused(vault, decision(FIRST, "Proposed: the queue", [f"- Cleo. {cite(vault, NOTE, FRIDAY)}"], rev="1"))
    assert any("changes the decision's status or links, which only the Briefs page writes" in p for p in problems)
    assert any("takes Proposed: from the title" in p for p in problems)
    assert (git(vault.root, "rev-parse", "HEAD"), path.read_text(encoding="utf-8")) == (head, edited)


def test_a_decision_no_commit_holds_reads_as_proposed(vault: Vault) -> None:
    text = decision(FIRST, "The queue comes first", [f"- Cleo said so. {cite(vault, NOTE, FRIDAY)}"])
    path = DocStore(vault).file(FIRST)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("status: proposed", f"status: confirmed\nsupersedes: [{OTHER}]"), encoding="utf-8")
    shown = DocStore(vault).get(FIRST)
    assert shown is not None
    assert (shown.meta.status, shown.meta.supersedes) == ("proposed", [])
    assert "\n# Proposed: The queue comes first\n" in shown.source
    # Read with the others, as every decision is: proposed, from no commit.
    [listed] = DocStore(vault).decisions()
    assert (listed.meta.id, listed.meta.status) == (FIRST, "proposed")
    # One with no title has none to put Proposed: before.
    path.write_text(text.replace("# The queue comes first\n", ""), encoding="utf-8")
    untitled = DocStore(vault).get(FIRST)
    assert untitled is not None
    assert "# " not in untitled.body
    assert committed_doc("no frontmatter here") is None


def test_a_final_decision_is_read_from_git_once_a_process(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    propose(vault)
    decide(vault, FIRST, "confirmed")
    monkeypatch.setattr(docstore, "BLOBS_KEPT", 0)
    for _ in range(2):
        [found] = DocStore(vault).decisions()
        assert found.meta.status == "confirmed"


def test_a_final_decision_reads_as_its_last_commit_whatever_its_file_says(vault: Vault) -> None:
    propose(vault)
    decide(vault, FIRST, "confirmed")
    path = DocStore(vault).file(FIRST)
    committed = path.read_text(encoding="utf-8")
    edited = committed.replace("the charging queue comes first", "the pilot comes first, zanzibar")
    path.write_text(edited, encoding="utf-8", newline="\n")
    shown = recall.read(vault, FIRST).doc
    assert shown is not None
    assert "zanzibar" not in shown.text
    assert not hits(vault, "zanzibar")
    assert hits(vault, "charging queue comes first") == ["the charging queue comes first"]
    assert recall.verify(vault, FIRST).lines[0].text.startswith("- Cleo said so.")
    assert one(vault, FIRST).note == EDITED
    conn = open_index(vault)
    try:
        assert ("doc", "processed/decision/2026/10/2026-10-13-charging-queue-first.md", EDITED) in conn.execute(
            "SELECT kind, place, message FROM problems"
        ).fetchall()
    finally:
        conn.close()
    head = git(vault.root, "rev-parse", "HEAD")
    # doc_put's path: refused, and the edit stays on disk.
    refused(vault, decision(FIRST, "Proposed: x", [f"- Cleo. {cite(vault, NOTE, FRIDAY)}"], rev="2"))
    # The redact pass's path: left, named, and nothing committed.
    report = DocStore(vault).redact_all()
    assert any("changes a decision that is confirmed, which is final" in left for left in report.left)
    assert (git(vault.root, "rev-parse", "HEAD"), path.read_text(encoding="utf-8")) == (head, edited)


def test_a_final_decision_whose_file_matches_takes_the_redact_pass_s_own_rev(vault: Vault) -> None:
    propose(vault)
    decide(vault, FIRST, "rejected")
    assert DocStore(vault).redact_all().left == []


# -- history and contradictions ---------------------------------------------------------------------------------------


def test_a_supersede_keeps_both_and_links_them_both_ways(vault: Vault) -> None:
    propose(vault)
    decide(vault, FIRST, "confirmed")
    recall.doc_put(vault, brief(vault, "fleet-management", "Fleet management", [decided_line(vault, FIRST)]))
    propose(vault, LATER, "Proposed: the pilot comes first", head=[f"replaces: [{FIRST}]"])
    # Proposed, it replaces nothing yet.
    assert (one(vault, FIRST).current, one(vault, LATER).replaces) == (True, [FIRST])
    decide(vault, LATER, "confirmed", supersedes=[FIRST])
    old, new = one(vault, FIRST), one(vault, LATER)
    assert (old.status, old.current, old.superseded_by, old.cite) == ("confirmed", False, LATER, None)
    assert (new.current, new.supersedes) == (True, [FIRST])
    assert recall.read(vault, FIRST).doc is not None
    assert hits(vault, "charging queue comes first")[0] == "Superseded: the charging queue comes first"
    stale = briefs.report(vault, ["fleet-management"], stale_only=False).briefs[0]
    assert (stale.state, stale.flagged) == ("stale", 1)


def test_a_contradiction_is_marked_and_nothing_else_changes(vault: Vault) -> None:
    propose(vault)
    decide(vault, FIRST, "confirmed")
    recall.doc_put(vault, brief(vault, "fleet-management", "Fleet management", [decided_line(vault, FIRST)]))
    store = DocStore(vault)
    before = (store.committed_text(FIRST), one(vault, FIRST))
    head = f"contradicts: [{FIRST}]"
    propose(vault, LATER, "Proposed: the pilot comes first", head=[head])
    after = one(vault, FIRST)
    assert store.committed_text(FIRST) == before[0]
    assert after.model_copy(update={"contradicted_by": []}) == before[1]
    assert after.contradicted_by == [LATER]
    assert briefs.report(vault, ["fleet-management"], stale_only=False).briefs[0].state == "current"


def test_a_statement_rejected_once_isn_t_proposed_again(vault: Vault) -> None:
    propose(vault)
    decide(vault, FIRST, "rejected")
    assert one(vault, FIRST).title == "the charging queue comes first"
    problems = refused(vault, decision(LATER, "Proposed: again", [f"- Cleo. {cite(vault, NOTE, FRIDAY)}"]))
    assert problems == [
        f"doc: it cites only what {FIRST}, rejected, cites: a statement the user said isn't a decision isn't "
        "proposed again"
    ]
    more = [f"- Cleo. {cite(vault, NOTE, FRIDAY)}", "- And the call. [r:calls:rec-0101@00:40]"]
    text = decision(LATER, "Proposed: again", more).replace("covers: []", "covers: [calls:rec-0101]")
    assert recall.doc_put(vault, text).rev == 1


# -- the form ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "says"),
    [
        (lambda t: t.replace(FIRST, "decision:charging-first"), "a decision's id is decision:<yyyy-mm-dd>-<words>"),
        (
            lambda t: t.replace("subject: fleet-management", "subject: no-such-thing"),
            "subject: no-such-thing isn't an entity",
        ),
        (lambda t: t.replace("subject: fleet-management", "subject: ben-sample"), "subject: ben-sample is a person"),
        (lambda t: t.replace("entities: [fleet-management]", "entities: []"), "entities: a decision names its subject"),
        (lambda t: t.replace("# Proposed: q", "# q"), "a decision starts with # Proposed:"),
        (lambda t: t.replace("# Proposed: q\n", ""), "a decision starts with # Proposed:"),
        (lambda t: t.replace("subject: fleet-management\n", ""), "a decision names its subject"),
        (
            lambda t: t.replace("status: proposed", "status: maybe"),
            "a decision's status is proposed, confirmed, rejected",
        ),
        (lambda t: t + "- Nothing cited.\n", "no anchors at the end of the line"),
        (
            lambda t: t.replace("subject: fleet-management", f"subject: fleet-management\nreplaces: [{OTHER}]"),
            f"replaces: {OTHER} doesn't exist",
        ),
        (
            lambda t: t.replace("subject: fleet-management", f"subject: fleet-management\nreplaces: [{FIRST}]"),
            "a decision can't replace or contradict itself",
        ),
        (
            lambda t: t.replace(
                "subject: fleet-management", "subject: fleet-management\nreplaces: [minutes:2026-10-13-fleet-review]"
            ),
            "isn't a decision",
        ),
        (
            lambda t: t.replace(
                "subject: fleet-management", f"subject: fleet-management\nreplaces: [{OTHER}]\ncontradicts: [{OTHER}]"
            ),
            "is in both replaces and contradicts",
        ),
        (lambda t: t.replace("subject: fleet-management", "subject: Fleet Management"), "isn't a slug"),
        (
            lambda t: t.replace("subject: fleet-management", "subject: fleet-management\nreplaces: [decision:Bad Id]"),
            "the slug 'Bad Id'",
        ),
    ],
)
def test_a_decision_out_of_form_is_refused(vault: Vault, change, says: str) -> None:
    text = change(decision(FIRST, "Proposed: q", [f"- Cleo said so. {cite(vault, NOTE, FRIDAY)}"]))
    assert any(says in p for p in refused(vault, text)), refused(vault, text)


def test_a_link_names_only_a_confirmed_current_decision_on_the_same_subject(vault: Vault) -> None:
    propose(vault)
    proposed = refused(
        vault, decision(LATER, "Proposed: x", [f"- Ben. {cite(vault, NOTE, TEN)}"], head=[f"replaces: [{FIRST}]"])
    )
    assert f"replaces: {FIRST} isn't confirmed" in proposed[0]
    decide(vault, FIRST, "confirmed")
    elsewhere = decision(LATER, "Proposed: x", [f"- Ben. {cite(vault, NOTE, TEN)}"], head=[f"contradicts: [{FIRST}]"])
    elsewhere = elsewhere.replace("project", "project").replace("subject: fleet-management", "subject: harbour-pilot")
    assert any(f"contradicts: {FIRST}" in p for p in refused(vault, elsewhere))
    propose(vault, OTHER, "Proposed: the offer comes in writing", head=[f"replaces: [{FIRST}]"])
    decide(vault, OTHER, "confirmed", supersedes=[FIRST])
    late = refused(
        vault, decision(LATER, "Proposed: x", [f"- Ben. {cite(vault, NOTE, TEN)}"], head=[f"replaces: [{FIRST}]"])
    )
    assert f"replaces: {FIRST} is superseded" in late[0]


def test_a_lenient_read_takes_an_unknown_status_as_proposed(caplog: pytest.LogCaptureFixture) -> None:
    text = decision(FIRST, "Proposed: q", ["- x. @00:01"]).replace("status: proposed", "status: maybe")
    with caplog.at_level(logging.WARNING):
        assert parse_doc(text, lenient=True).meta.status == "proposed"
    assert "a status this Docket doesn't know" in caplog.text
    note = "---\nid: minutes:2026-10-13-x\ntype: minutes\ncovers: [calls:rec-0101]\nby: x\nstatus: proposed\n---\n"
    with pytest.raises(DocRefusedError, match="belong to a decision only"):
        parse_doc(note)
    assert parse_doc(note, lenient=True).meta.status == "proposed"
    assert as_committed(parse_doc(note, lenient=True), None).meta.type == "minutes"


# -- a brief's Decisions ----------------------------------------------------------------------------------------------


def test_a_brief_s_decisions_line_is_a_current_decision_word_for_word(vault: Vault) -> None:
    raw = "- The offer goes in writing before Friday. [r:mail:<offer-1@northwind.example>#2]"
    noted = f"- The queue comes first. {cite(vault, NOTE, FRIDAY)}"
    for line in (raw, noted):
        text = brief(vault, "fleet-management", "Fleet management", [line])
        if line == raw:
            text = text.replace("covers: []", "covers: [mail:<offer-1@northwind.example>]")
        problems = refused(vault, text)
        assert any("a Decisions line cites one current decision and nothing else" in p for p in problems), problems
    propose(vault)
    title = standings(DocStore(vault).decisions())[FIRST].title_line
    assert title is not None
    proposed = f"- the charging queue comes first [d:{FIRST}@1#{title}]"
    assert any("which is proposed" in p for p in refused(vault, brief(vault, "fleet-management", "Fleet", [proposed])))
    decide(vault, FIRST, "confirmed")
    good = decided_line(vault, FIRST)
    worded = good.replace("comes first", "goes first")
    assert any(
        "reads exactly as its decision's title" in p
        for p in refused(vault, brief(vault, "fleet-management", "F", [worded]))
    )
    body = good.replace(f"#{title}]", f"#{title + 2}]")
    assert any("cites the title line of" in p for p in refused(vault, brief(vault, "fleet-management", "F", [body])))
    nothing = brief(vault, "fleet-management", "F", ["- Nothing. [d:decision:2026-01-01-nothing@1#3]"])
    assert any("which isn't a decision here" in p for p in refused(vault, nothing))
    assert recall.doc_put(vault, brief(vault, "fleet-management", "Fleet management", [good])).rev == 1


def test_a_brief_stored_with_an_unconfirmed_decisions_line_is_stale_until_its_next_rev(vault: Vault) -> None:
    text = brief(
        vault, "fleet-management", "Fleet management", ["- Decided by mail. [r:mail:<offer-1@northwind.example>#2]"]
    )
    path = vault.root / "processed" / "brief" / "fleet-management.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("covers: []", "covers: [mail:<offer-1@northwind.example>]"), encoding="utf-8")
    git(vault.root, "add", "--", str(path))
    git(vault.root, "commit", "-qm", "Doc: brief:fleet-management rev 1")
    found = briefs.report(vault, ["fleet-management"], stale_only=False).briefs[0]
    assert (found.state, found.why) == ("stale", NOT_CONFIRMED)


# -- never-store ------------------------------------------------------------------------------------------------------


def test_a_subject_or_a_person_a_list_holds_gets_no_decision(vault: Vault) -> None:
    held = set_lists(vault, people=["Fleet Management"])
    assert refused(held, decision(FIRST, "Proposed: q", [f"- Cleo said so. {cite(held, NOTE, FRIDAY)}"])) == [
        "line 8: subject: a never-store list holds this entity; it gets no decision"
    ]
    named = set_lists(vault, people=["Dóra Minta"])
    lines = [f"- Dóra Minta said so. {cite(named, NOTE, FRIDAY)}"]
    assert any("names someone" in p for p in refused(named, decision(FIRST, "Proposed: q", lines)))
    slug = decision("decision:2026-10-13-minta-dora-leads", "Proposed: q", [f"- Said. {cite(named, NOTE, FRIDAY)}"])
    assert any("an id here names someone" in p for p in refused(named, slug))
    # One proposed before its name was listed is left by the redact pass, its id named.
    vera = decision("decision:2026-10-13-vera-leads", "Proposed: q", [f"- Said. {cite(vault, NOTE, FRIDAY)}"])
    recall.doc_put(vault, vera)
    later = set_lists(vault, people=["Vera Leads"])
    left = DocStore(later).redact_all().left
    assert any(line.endswith("line 2 names someone a never-store list holds") for line in left), left


def test_the_tool_hides_a_decision_on_a_subject_listed_later_and_the_take_in_stops_a_name(vault: Vault) -> None:
    propose(vault)
    held = set_lists(vault, people=["Dóra Minta"])
    path = DocStore(held).file(FIRST)
    path.write_text(
        path.read_text(encoding="utf-8").replace("- Cleo said so.", "- Dóra Minta said so."), encoding="utf-8"
    )
    problems = refused(held, decision(FIRST, "Proposed: q", [f"- Cleo. {cite(held, NOTE, FRIDAY)}"], rev="1"))
    assert any("names someone a never-store list holds" in p for p in problems)
    listed = set_lists(vault, people=["Dóra Minta", "Fleet Management"])
    # The redact pass reads every decision: one on a subject listed later is left, named by its line.
    assert any("line 10 names someone a never-store list holds" in left for left in DocStore(listed).redact_all().left)
    found = decisions.report(listed)
    assert (found.decisions, found.hidden) == ([], 1)
    assert render.decisions(found) == [
        "no decisions yet",
        "hidden     1 decision(s) on an entity a never-store list holds",
    ]


# -- the tool and the CLI ---------------------------------------------------------------------------------------------


def test_the_report_filters_by_entity_and_status_and_says_its_links(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    propose(vault)
    decide(vault, FIRST, "confirmed")
    propose(vault, LATER, "Proposed: the pilot comes first", head=[f"contradicts: [{FIRST}]"])
    vault.write_run("chat", [make_record(902, text="the fleet dashboard again")])
    assert [d.id for d in decisions.report(vault, ["fleet-management"], "proposed").decisions] == [LATER]
    assert decisions.report(vault, ["harbour-site"]).decisions == []
    current = one(vault, FIRST)
    assert current.cite is not None and current.cite.startswith(f"[d:{FIRST}@2#")
    assert current.rev_time is not None
    capsys.readouterr()
    assert main(["decision", "list", "--vault", str(vault.root)]) == 0
    said = capsys.readouterr().out.splitlines()
    assert said[:4] == [
        f"proposed   {LATER}  the pilot comes first",
        "           on fleet-management, rev 1",
        f"           contradicts {FIRST}",
        f"current    {FIRST}  the charging queue comes first",
    ]
    assert f"           contradicted by {LATER}" in said
    assert main(["decision", "list", "--status", "maybe", "--vault", str(vault.root)]) == 2


def test_the_index_rebuilds_a_decision_from_its_last_commit(vault: Vault) -> None:
    propose(vault)
    decide(vault, FIRST, "confirmed")
    index_update(vault)
    assert hits(vault, "charging queue comes first") == ["the charging queue comes first"]


def test_the_report_says_flagged_lines_and_an_edit_on_disk() -> None:
    found = decisions.DecisionsReport(
        decisions=[
            decisions.DecisionState(
                id=FIRST, subject="fleet-management", title="t", status="rejected", current=False, rev=2, by="x",
                flagged=1, note=EDITED,
            )
        ]
    )  # fmt: skip
    assert render.decisions(found) == [
        f"rejected   {FIRST}  t",
        "           on fleet-management, rev 2",
        "           1 line(s) cite lines that changed",
        f"           {EDITED}",
    ]


# -- the review's side doors ------------------------------------------------------------------------------------------

STOP = "is a Decisions line that doesn't read as a current decision's title with its cite"


def test_a_decisions_line_added_by_hand_is_never_taken_in(vault: Vault) -> None:
    recall.doc_put(vault, brief(vault, "fleet-management", "Fleet management", []))
    path = DocStore(vault).file("brief:fleet-management")
    edited = path.read_text(encoding="utf-8") + f"- The dashboard ships before the pilot. {cite(vault, NOTE, TEN)}\n"
    path.write_text(edited, encoding="utf-8", newline="\n")
    head = git(vault.root, "rev-parse", "HEAD")
    again = brief(vault, "fleet-management", "Fleet management", []).replace("type: brief", "type: brief\nrev: 1")
    assert any(STOP in p for p in refused(vault, again))
    assert any(STOP in left for left in DocStore(vault).redact_all().left)
    assert (git(vault.root, "rev-parse", "HEAD"), path.read_text(encoding="utf-8")) == (head, edited)


def test_read_and_search_mark_a_decisions_line_no_confirmed_decision_backs(vault: Vault) -> None:
    text = brief(
        vault,
        "fleet-management",
        "Fleet management",
        ["- Decided by mail, zanzibar. [r:mail:<offer-1@northwind.example>#2]"],
    )
    path = vault.root / "processed" / "brief" / "fleet-management.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("covers: []", "covers: [mail:<offer-1@northwind.example>]"), encoding="utf-8")
    git(vault.root, "add", "--", str(path))
    git(vault.root, "commit", "-qm", "Doc: brief:fleet-management rev 1")
    shown = recall.read(vault, "brief:fleet-management").doc
    assert shown is not None
    [marked] = [line for line in shown.lines if "zanzibar" in line.text]
    assert marked.flags == ["not a confirmed decision"]
    assert "        ! not a confirmed decision" in render.read(recall.read(vault, "brief:fleet-management"))
    index_update(vault)
    [hit] = [h for g in search(vault, "zanzibar").groups for h in g.hits if h.doc == "brief:fleet-management"]
    assert hit.marks == ["not a confirmed decision"]
    # A redact pass doesn't hold up a brief stored before the log: its line was there already.
    assert DocStore(vault).redact_all().left == []


def test_the_report_hides_a_decision_whose_title_or_link_names_someone_listed_later(vault: Vault) -> None:
    recall.doc_put(
        vault, decision(FIRST, "Proposed: Dora Minta runs the charging queue", [f"- Said. {cite(vault, NOTE, TEN)}"])
    )
    propose(vault, "decision:2026-10-13-vera-plan", "Proposed: the plan holds")
    decide(vault, "decision:2026-10-13-vera-plan", "confirmed")
    propose(vault, LATER, "Proposed: the pilot comes first", head=["contradicts: [decision:2026-10-13-vera-plan]"])
    assert len(decisions.report(vault).decisions) == 3
    listed = set_lists(vault, people=["Dóra Minta", "Vera Plan"])
    found = decisions.report(listed)
    assert (found.decisions, found.hidden) == ([], 3)
