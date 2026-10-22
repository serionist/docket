"""Briefs (#161, DR-0555, DR-0556, DR-0558): the form doc_put holds a brief to, the never-store screen in every name
shape, and which briefs are stale and why, by the run files a brief's last rev couldn't have seen, its flagged lines,
its entity gone or listed. On a copy of the index fixture, with invented people and records."""

from __future__ import annotations

import shutil
from collections import OrderedDict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import git, git_calls, index_template, make_record

from docket import briefs, recall, render
from docket.cli import main
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.docstore import DocStore, PutResult
from docket.vault.processed import DocRefusedError
from docket.vault.vault import Vault

NOTE = "recording-note:2026-10-07-supplier-call"
TEN = "- The consultant asks ten million forints for the quarter, above the budget. @00:30-01:50"
FRIDAY = "- Cleo sends the offer in writing by Friday. @04:00-05:00"
SECTIONS = ["## What changed", "", "## Last conversations", "", "## Decisions"]


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


# A line citing a note line, so a brief's empty covers holds and each refusal below is the form's own.
CITED = "- Above the budget. <cite>"


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    shutil.copytree(template, tmp_path / "vault")
    return Vault.open(tmp_path / "vault")


def cite(vault: Vault, text: str) -> str:
    doc = DocStore(vault).get(NOTE)
    assert doc is not None
    n = next(n for n, line in doc.lines() if line == text)
    return f"[d:{NOTE}@{doc.meta.rev}#{n}]"


def brief(entity: str, title: str, *, changed: Sequence[str], said: Sequence[str] = (), rev: str | None = None) -> str:
    head = [
        f"id: brief:{entity}",
        "type: brief",
        *([f"rev: {rev}"] if rev else []),
        "covers: []",
        "by: claude-test, brief 2026-10-17",
        f"entities: [{entity}]",
    ]
    body = [
        f"# {title}",
        "",
        "## What changed",
        *changed,
        "",
        "## Last conversations",
        *said,
        "",
        "## Decisions",
    ]
    return "---\n" + "\n".join(head) + "\n---\n\n" + "\n".join(body) + "\n"


def put_ben(vault: Vault, rev: str | None = None, said: Sequence[str] = ()) -> PutResult:
    changed = [f"- The price is above the budget. {cite(vault, TEN)}"]
    return recall.doc_put(vault, brief("ben-sample", "Ben Sample", changed=changed, said=said, rev=rev))


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


def state(vault: Vault, entity: str = "ben-sample") -> briefs.BriefState:
    [found] = briefs.report(vault, [entity], stale_only=False).briefs
    return found


# -- the form ---------------------------------------------------------------------------------------------------------


def test_a_brief_citing_note_lines_only_is_stored_with_empty_covers(vault: Vault) -> None:
    result = put_ben(vault)
    assert result.changed
    stored = DocStore(vault).get("brief:ben-sample")
    assert stored is not None
    assert stored.meta.covers == []


@pytest.mark.parametrize(
    ("text", "says"),
    [
        (brief("nobody-here", "Nobody", changed=[CITED]), "brief:nobody-here is for an entity that doesn't exist"),
        (
            brief("ben-sample", "Ben", changed=[CITED]).replace("entities: [ben-sample]", "entities: []"),
            "entities: a brief names its entity",
        ),
        (
            brief("ben-sample", "Ben", changed=[CITED]).replace("## Last conversations", "## Talks"),
            "a brief's sections are",
        ),
        (
            brief("ben-sample", "Ben", changed=[CITED]).replace("## Decisions", "## What changed"),
            "a brief's sections are",
        ),
        (
            brief("ben-sample", "Ben", changed=[CITED]) + "\n## Decisions\n",
            "each of a brief's three sections stands once",
        ),
        (brief("ben-sample", "Ben", changed=[CITED]).replace("# Ben\n", ""), "a brief starts with a # <title> heading"),
        (brief("ben-sample", "Ben", changed=["- No anchor at all.", CITED]), "no anchors at the end of the line"),
    ],
)
def test_a_brief_out_of_form_is_refused(vault: Vault, text: str, says: str) -> None:
    problems = refused(vault, text.replace("<cite>", cite(vault, TEN)))
    assert any(says in p for p in problems), problems


def test_a_section_after_the_three_is_kept_with_a_warning(vault: Vault) -> None:
    text = brief("ben-sample", "Ben Sample", changed=[f"- Above the budget. {cite(vault, TEN)}"])
    put_text = text + "\n## Open items\n\n- Send the offer. " + cite(vault, FRIDAY) + "\n"
    result = recall.doc_put(vault, put_text)
    assert result.changed
    assert result.notes == ["a section the form doesn't name yet, kept until the form settles: Open items"]


# -- never-store ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["Ben Sample", "ben@northwind.example", "Ben", "U0BEN"])
def test_an_entity_a_list_holds_by_any_name_alias_or_id_gets_no_brief(vault: Vault, value: str) -> None:
    held = set_lists(vault, people=[value])
    problems = refused(held, brief("ben-sample", "Ben Sample", changed=[CITED.replace("<cite>", cite(held, TEN))]))
    assert problems == ["line 2: the brief is for an entity a never-store list holds; it gets no brief"]


@pytest.mark.parametrize("value", ["ben@northwind.example", "U0BEN"])
def test_a_person_listed_by_an_address_or_a_chat_id_alone_is_named_by_no_brief(vault: Vault, value: str) -> None:
    held = set_lists(vault, people=[value])
    for line in ("- Ben Sample asked for it.", "- Sample Ben asked for it.", "- Ben asked for it."):
        problems = refused(held, brief("northwind", "Northwind", changed=[f"{line} {cite(held, TEN)}"]))
        assert [p.split(": ", 1)[1] for p in problems] == ["names someone a never-store list holds; leave them out"]


@pytest.mark.parametrize(
    ("value", "other"),
    [("U0DORA", "dora-minta"), ("Cleo Nobody", "cleo-nobody")],
    ids=["an entity the list holds by an id", "an id that spells a listed name"],
)
def test_a_listed_person_in_the_entities_list_is_refused(vault: Vault, value: str, other: str) -> None:
    held = set_lists(vault, people=[value])
    text = brief("ben-sample", "Ben Sample", changed=[f"- Above the budget. {cite(held, TEN)}"])
    text = text.replace("entities: [ben-sample]", f"entities: [ben-sample, {other}]")
    assert refused(held, text) == [
        "line 6: entities: an entity here names someone a never-store list holds; leave them out"
    ]


def test_a_hand_edit_naming_a_listed_person_stays_on_disk(vault: Vault) -> None:
    put_ben(vault)
    held = set_lists(vault, people=["Dora Minta"])
    path = held.root / "processed" / "brief" / "ben-sample.md"
    edited = path.read_text(encoding="utf-8").replace(
        "## Decisions", f"## Decisions\n- Dora Minta agreed. {cite(held, TEN)}"
    )
    path.write_text(edited, encoding="utf-8", newline="\n")
    head = git(held.root, "rev-parse", "HEAD")
    n = edited.split("\n").index(next(line for line in edited.split("\n") if line.startswith("- Dora Minta"))) + 1
    assert refused(
        held, brief("ben-sample", "Ben Sample", changed=[CITED.replace("<cite>", cite(held, TEN))], rev="1")
    ) == [
        f"doc: processed/brief/ben-sample.md was edited by hand, and its line {n} names someone a never-store list "
        "holds; edit it or check it out again"
    ]
    assert (git(held.root, "rev-parse", "HEAD"), path.read_text(encoding="utf-8")) == (head, edited)


@pytest.mark.parametrize(
    "line",
    [
        "- I met Dora Minta today.",
        "- I met Minta Dóra today.",
        "- Minta, Dóra signed.",
        "- Ben Sample asked for it.",
        "- A mail from ben@example.invalid came.",
        "- Dóri said so.",
    ],
)
def test_a_brief_naming_a_listed_person_in_any_shape_is_refused(vault: Vault, line: str) -> None:
    alias = vault.root / "entities" / "person" / "dora-minta.yaml"
    alias.write_text(
        alias.read_text(encoding="utf-8").replace("  - {text: Dóra}", "  - {text: Dóra}\n  - {text: Dóri}"),
        encoding="utf-8",
    )
    git(vault.root, "commit", "-qam", "A nickname")
    held = set_lists(vault, people=["Dóra Minta", "Ben Sample <ben@example.invalid>"])
    text = brief("northwind", "Northwind", changed=[f"{line} {cite(held, TEN)}"])
    problems = refused(held, text)
    assert [p.split(": ", 1)[1] for p in problems] == ["names someone a never-store list holds; leave them out"]


def test_a_ref_to_a_doc_whose_slug_names_a_listed_person_is_refused(vault: Vault) -> None:
    held = set_lists(vault, people=["Dóra Minta"])
    text = brief("northwind", "Northwind", changed=["- A call. [d:recording-note:2026-10-08-minta-dora@1#3]"])
    problems = refused(held, text)
    assert any("a ref here cites a doc whose id names someone a never-store list holds" in p for p in problems), (
        problems
    )


# -- staleness --------------------------------------------------------------------------------------------------------


def test_a_brief_just_put_is_current(vault: Vault) -> None:
    put_ben(vault)
    found = state(vault)
    assert (found.state, found.new.count, found.flagged, found.why) == ("current", 0, 0, None)
    assert (found.entity, found.name, found.type, found.rev) == ("ben-sample", "Ben Sample", "person", 1)
    assert found.rev_time is not None
    assert briefs.report(vault).briefs == []


def test_a_new_record_makes_the_brief_stale_until_its_next_rev(vault: Vault) -> None:
    put_ben(vault)
    record = make_record(901, text="Ben Sample confirmed the offer for the quarter.")
    vault.write_run("chat", [record])
    found = state(vault)
    assert (found.state, found.new.count, found.new.newest) == ("stale", 1, [record.rid])
    put_ben(vault, rev="1", said=[f"- 14 Oct: Ben confirmed the offer. {cite(vault, FRIDAY)}"])
    assert state(vault).state == "current"


def test_a_record_from_before_the_brief_pulled_after_it_makes_it_stale(vault: Vault) -> None:
    # Its run file is named for a day before the brief, as another machine writes and a later pull brings it: no
    # clock decides, the brief's tree does.
    put_ben(vault)
    old = make_record(902, text="Ben Sample asked about the pallets.", ts=datetime(2026, 9, 1, 8, 0, tzinfo=UTC))
    vault.write_run("chat", [old], datetime(2026, 9, 1, 9, 0, tzinfo=UTC))
    found = state(vault)
    assert (found.state, found.new.newest) == ("stale", [old.rid])


def test_a_flagged_line_makes_the_brief_stale(vault: Vault) -> None:
    put_ben(vault)
    note = DocStore(vault).get(NOTE)
    assert note is not None
    recall.doc_put(vault, note.render(rev=note.meta.rev).replace("ten million", "twelve million"))
    found = state(vault)
    assert (found.state, found.flagged) == ("stale", 1)


def test_a_brief_whose_history_isnt_here_is_stale_never_current(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    put_ben(vault)

    def missing(_store: DocStore, ids: list[str]) -> dict[str, list[tuple[int, str]]]:
        return {doc_id: [(1, "0" * 40)] for doc_id in ids}

    monkeypatch.setattr(DocStore, "histories", missing)
    found = state(vault)
    assert (found.state, found.why, found.rev_time) == ("stale", briefs.HISTORY_NOT_HERE, None)


def test_one_brief_s_missing_commit_leaves_the_others_as_they_are(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_ben(vault)
    recall.doc_put(vault, brief("northwind", "Northwind", changed=[f"- Above the budget. {cite(vault, TEN)}"]))
    real = DocStore.histories

    def one_missing(store: DocStore, ids: list[str]) -> dict[str, list[tuple[int, str]]]:
        return {**real(store, ids), "brief:northwind": [(1, "0" * 40)]}

    monkeypatch.setattr(DocStore, "histories", one_missing)
    ben, northwind = sorted(briefs.report(vault, stale_only=False).briefs, key=lambda b: b.id)
    assert (ben.state, ben.why, ben.rev_time is not None) == ("current", None, True)
    assert (northwind.state, northwind.why, northwind.rev_time) == ("stale", briefs.HISTORY_NOT_HERE, None)

    monkeypatch.setattr(DocStore, "histories", lambda _store, _ids: {})
    assert {b.why for b in briefs.report(vault, stale_only=False).briefs} == {briefs.HISTORY_NOT_HERE}


def keeps_emptied(monkeypatch: pytest.MonkeyPatch) -> None:
    """The report's keeps, empty for this test alone: other tests of the worker filled them."""
    monkeypatch.setattr(briefs, "_TREES", OrderedDict())
    monkeypatch.setattr(briefs, "_RAW_TREES", {})
    monkeypatch.setattr(briefs, "_TIMES", {})


def asked(calls: list[str]) -> list[str]:
    """What a report asked git of a commit: its raw/ tree, its time, a raw/ tree's listing."""
    return [c.split()[0] for c in calls if c.startswith(("cat-file --batch-check", "log --no-walk", "ls-tree"))]


def test_commits_that_share_raw_share_one_listing_and_the_keep_holds_the_last_few(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A commit's raw/ tree and time never change, so the next report asks git for neither, which keeps the Briefs
    page's read from a git call a brief; the commits that share a raw/ tree share one listing, and the keep holds the
    last TREES_KEPT listed, so a service running for weeks keeps a few, never one for each brief rev it read."""
    keeps_emptied(monkeypatch)
    put_ben(vault)
    recall.doc_put(vault, brief("northwind", "Northwind", changed=[f"- Above the budget. {cite(vault, TEN)}"]))
    before = briefs.report(vault, stale_only=False)
    assert len(briefs._TREES) == 1, "both briefs' commits share raw/"  # pyright: ignore[reportPrivateUsage]
    with git_calls(monkeypatch) as calls:
        assert briefs.report(vault, stale_only=False) == before
    assert asked(calls) == []
    # A new raw/, then Ben's next rev on it: its time, its raw/ tree and that tree's listing. A keep of one holds one
    # listing, so Northwind's is listed again too when Ben's commit comes first, as the commits' ids order them.
    monkeypatch.setattr(briefs, "TREES_KEPT", 1)
    vault.write_run("chat", [make_record(902, text="Ben Sample asked for the drawings.")])
    vault.commit([], "Raw: a chat about Ben")
    put_ben(vault, rev="1", said=[f"- Cleo sends the offer by Friday. {cite(vault, FRIDAY)}"])
    with git_calls(monkeypatch) as calls:
        briefs.report(vault, stale_only=False)
    assert asked(calls) in (["log", "cat-file", "ls-tree"], ["log", "cat-file", "ls-tree", "ls-tree"])
    assert len(briefs._TREES) == 1  # pyright: ignore[reportPrivateUsage]
    # The commits' keep holds small strings, and empties when it would pass its own cap.
    monkeypatch.setattr(briefs, "COMMITS_KEPT", 1)
    monkeypatch.setattr(briefs, "_RAW_TREES", {"0" * 40: ""})
    monkeypatch.setattr(briefs, "_TIMES", {"0" * 40: "2026-10-01T09:00:00+00:00"})
    assert briefs.report(vault, stale_only=False) == briefs.report(vault, stale_only=False)
    assert "0" * 40 not in briefs._RAW_TREES and "0" * 40 not in briefs._TIMES  # pyright: ignore[reportPrivateUsage]


def test_a_raw_tree_that_doesn_t_list_here_leaves_its_brief_stale(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A commit whose raw/ can't be listed on this clone, as one a partial clone lacks the objects of, is a brief whose
    history isn't here, never current."""
    keeps_emptied(monkeypatch)
    put_ben(vault)
    head = vault.git.head_id()
    assert head is not None
    briefs._RAW_TREES[head] = "0" * 40  # pyright: ignore[reportPrivateUsage]
    found = state(vault)
    assert (found.state, found.why) == ("stale", briefs.HISTORY_NOT_HERE)
    # A commit with no raw/ at all held no run file, so every record about Ben came after it.
    briefs._RAW_TREES[head] = ""  # pyright: ignore[reportPrivateUsage]
    empty = state(vault)
    assert (empty.state, empty.why) == ("stale", None) and empty.new.count > 0


def test_a_merge_in_the_briefs_history_reads_each_brief_s_own_log(vault: Vault) -> None:
    put_ben(vault)
    git(vault.root, "checkout", "-q", "-b", "side")
    recall.doc_put(vault, brief("northwind", "Northwind", changed=[f"- Above the budget. {cite(vault, TEN)}"]))
    git(vault.root, "checkout", "-q", "main")
    recall.doc_put(vault, brief("dora-minta", "Dóra Minta", changed=[f"- Friday. {cite(vault, FRIDAY)}"]))
    git(vault.root, "merge", "-q", "--no-ff", "-m", "Merge the side", "side")
    found = briefs.report(vault, stale_only=False).briefs
    assert sorted((b.id, b.state) for b in found) == [
        ("brief:ben-sample", "current"), ("brief:dora-minta", "current"), ("brief:northwind", "current"),
    ]  # fmt: skip


def test_a_brief_whose_entity_is_gone_names_the_one_that_holds_its_name(vault: Vault) -> None:
    recall.doc_put(vault, brief("dora-minta", "Dóra Minta", changed=[f"- Friday. {cite(vault, FRIDAY)}"]))
    cleo = vault.root / "entities" / "person" / "cleo-test.yaml"
    text = cleo.read_text(encoding="utf-8")
    cleo.write_text(
        text.replace("aliases:\n", "aliases:\n  - {text: Dóra Minta}\n", 1)
        if "aliases:" in text
        else text + "aliases:\n  - {text: Dóra Minta}\n",
        encoding="utf-8",
    )
    git(vault.root, "rm", "-q", "-f", "entities/person/dora-minta.yaml")
    git(vault.root, "commit", "-qam", "Dóra is Cleo")
    found = state(vault, "dora-minta")
    assert (found.state, found.orphaned) == ("orphaned", "cleo-test")


def test_an_orphan_whose_name_a_listed_person_holds_now_is_hidden(vault: Vault) -> None:
    benny = vault.root / "entities" / "person" / "benny.yaml"
    benny.write_text("id: benny\ntype: person\nname: {en: Benny}\n", encoding="utf-8")
    git(vault.root, "add", "entities/person/benny.yaml")
    git(vault.root, "commit", "-qm", "Benny")
    recall.doc_put(vault, brief("benny", "Benny", changed=[f"- Asked about the price. {cite(vault, TEN)}"]))
    # Benny turns out to be Ben, merged by hand: his entity goes, and Ben gains the alias.
    ben = vault.root / "entities" / "person" / "ben-sample.yaml"
    ben.write_text(
        ben.read_text(encoding="utf-8").replace("  - {text: Ben}", "  - {text: Ben}\n  - {text: Benny}"),
        encoding="utf-8",
    )
    git(vault.root, "rm", "-q", "entities/person/benny.yaml")
    git(vault.root, "commit", "-qam", "Benny is Ben")
    orphan = state(vault, "benny")
    assert (orphan.state, orphan.orphaned) == ("orphaned", "ben-sample")
    held = set_lists(vault, people=["ben@northwind.example"])
    found = briefs.report(held, stale_only=False)
    assert ([b.id for b in found.briefs], found.hidden) == ([], 1)


@pytest.mark.parametrize("value", ["Ben Sample", "Ben", "ben@northwind.example"])
def test_a_brief_a_list_now_holds_is_hidden_and_counted(vault: Vault, value: str) -> None:
    put_ben(vault)
    held = set_lists(vault, people=[value])
    found = briefs.report(held, stale_only=False)
    assert (found.briefs, found.hidden) == ([], 1)
    asked = briefs.report(held, ["ben-sample", "northwind"])
    assert (asked.hidden, asked.missing) == (1, ["northwind"])


def test_docket_brief_stale_prints_each_brief(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    put_ben(vault)
    record = make_record(903, text="Ben Sample confirmed it.")
    vault.write_run("chat", [record])
    capsys.readouterr()
    assert main(["brief", "stale", "--vault", str(vault.root)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "stale     brief:ben-sample  Ben Sample (person), rev 1",
        f"          1 new record(s): {record.rid}",
    ]
    assert main(["brief", "stale", "--entity", "northwind", "--vault", str(vault.root)]) == 0
    assert capsys.readouterr().out.splitlines() == ["nothing stale: every brief is current", "no brief  northwind"]


def test_the_report_says_why_what_is_flagged_who_holds_an_orphan_s_name_and_what_is_hidden() -> None:
    found = briefs.BriefsReport(
        briefs=[
            briefs.BriefState(
                id="brief:ben-sample", entity="ben-sample", name="Ben Sample", type="person", rev=2,
                flagged=1, state="stale", why=briefs.HISTORY_NOT_HERE,
            ),
            briefs.BriefState(
                id="brief:ben", entity="ben", name="Ben", type="", rev=1, orphaned="ben-sample", state="orphaned"
            ),
        ],
        hidden=2,
    )  # fmt: skip
    assert render.briefs(found) == [
        "stale     brief:ben-sample  Ben Sample (person), rev 2",
        "          its history isn't here",
        "          1 line(s) cite lines that changed",
        "orphaned  brief:ben  Ben, rev 1",
        "          its entity is gone; ben-sample holds its name now",
        "hidden    2 brief(s) whose entity a never-store list holds",
    ]


def test_a_heading_in_a_code_block_isn_t_a_section(vault: Vault) -> None:
    fenced = ["```", "## Talks", "```", f"- Above the budget. {cite(vault, TEN)}"]
    assert recall.doc_put(vault, brief("ben-sample", "Ben Sample", changed=fenced)).changed


def test_a_batch_holds_a_brief_to_its_form(vault: Vault) -> None:
    good = brief("ben-sample", "Ben Sample", changed=[f"- Above the budget. {cite(vault, TEN)}"])
    bad = good.replace("id: brief:ben-sample", "id: brief:northwind").replace("[ben-sample]", "[northwind]")
    bad = bad.replace("## Decisions", "## Talks")
    ok, refused_one = DocStore(vault).put_many([good, bad], "Two briefs")
    assert isinstance(ok, PutResult)
    assert isinstance(refused_one, DocRefusedError)
    assert any("a brief's sections are" in str(p) for p in refused_one.problems)
