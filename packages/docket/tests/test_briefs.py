"""Briefs (#161, DR-0555, DR-0556, DR-0558): the form doc_put holds a brief to, the never-store screen in every name
shape, and which briefs are stale and why, by the run files a brief's last rev couldn't have seen, its flagged lines,
its entity gone or listed. On a copy of the index fixture, with invented people and records."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import git, index_template, make_record

from docket import briefs, recall
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
        (brief("ben-sample", "Ben", changed=[CITED]).replace("# Ben\n", ""), "a brief starts with a # <title> heading"),
        (brief("ben-sample", "Ben", changed=["- No anchor at all.", CITED]), "no anchors at the end of the line"),
    ],
)
def test_a_brief_out_of_form_is_refused(vault: Vault, text: str, says: str) -> None:
    problems = refused(vault, text.replace("<cite>", cite(vault, TEN)))
    assert any(says in p for p in problems), problems


def test_a_section_after_the_three_is_kept_with_a_warning(vault: Vault) -> None:
    text = put_text = brief("ben-sample", "Ben Sample", changed=[f"- Above the budget. {cite(vault, TEN)}"])
    put_text = text + "\n## Open items\n\n- Send the offer. " + cite(vault, FRIDAY) + "\n"
    result = recall.doc_put(vault, put_text)
    assert result.changed
    assert result.notes == ["a section the form doesn't name yet, kept until the form settles: Open items"]


# -- never-store ------------------------------------------------------------------------------------------------------


def test_an_entity_a_list_holds_gets_no_brief(vault: Vault) -> None:
    held = set_lists(vault, people=["Ben Sample"])
    problems = refused(held, brief("ben-sample", "Ben Sample", changed=[CITED.replace("<cite>", cite(held, TEN))]))
    assert problems == ["doc: the brief is for an entity a never-store list holds; it gets no brief"]


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


def test_a_merge_in_the_briefs_history_reads_each_brief_s_own_log(vault: Vault) -> None:
    # The fixture leaves its entity files staged, not committed; a branch switch and a merge want them in a commit.
    git(vault.root, "commit", "-qm", "Entities", "--", "entities")
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


def test_a_brief_a_list_now_holds_is_hidden_and_counted(vault: Vault) -> None:
    put_ben(vault)
    held = set_lists(vault, people=["Ben Sample"])
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
