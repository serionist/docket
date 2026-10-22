"""`docket sync check` and `docket sync learned` (#305): a block read as the reconcile will, each field it would leave
to a hand edit said, and the sync's LEARNED lines each with the file it goes in. Invented people and data only: the
pilot's ledger of test_sync_reconcile.py, and the invented memo source."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import copy_vault, template_with
from sync_kit import sync_template, sync_vault
from test_sync_reconcile import BEN_ASK, NOW, SENT, build, owed, waiting

from docket.cli import main
from docket.errors import DocketError
from docket.index import build as index_build
from docket.sync import check, start
from docket.vault.vault import Vault


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_with(tmp_path_factory, build)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    v = copy_vault(template, tmp_path)
    index_build.rebuild(v)
    return v


def written(vault: Vault, source: str, *lines: str) -> str:
    started = start.start(vault, [], NOW)
    file = started.folder / "blocks" / f"{source}.txt"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return started.info.sync


def test_a_clean_block_reads_clean(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    sync = written(vault, "slack", owed("Ben Sample", BEN_ASK, "2026-10-19 10:00", "Send Ben the badges"))
    assert main(["sync", "check", sync, "slack", "--vault", str(vault.root)]) == 0
    assert capsys.readouterr().out.splitlines() == ["blocks/slack.txt: every line reads as the reconcile takes it"]


def test_each_field_the_reconcile_would_leave_to_a_hand_edit_is_said_by_its_line(vault: Vault) -> None:
    sync = written(
        vault, "slack",
        owed("Ben", BEN_ASK, "2026-10-19 10:00", "Send Ben the badges", kind="ask", stream="-", due="Friday"),
        waiting("Cleo Test", "slack:C0PILOT/1", "the 19th", "the quote", stream="sensors"),
        f"CLOSES:   1 | finished | {SENT} | sent",
        f"STREAMS:  pilot | colour=blue | the news | {SENT}",
        "OWED:     Ben Sample | a | b",
    )  # fmt: skip
    rel, said = check.check(vault, sync, "slack")
    assert rel == "blocks/slack.txt"
    assert said == [
        "blocks/slack.txt:1 OWED: its kind, 'ask', is one of reply, decision, task",
        "blocks/slack.txt:1 OWED: it names no stream: one of pilot",
        "blocks/slack.txt:1 OWED: its due, 'Friday', is YYYY-MM-DD or -",
        "blocks/slack.txt:1 OWED: who: the ledger writes 'Ben Sample', not 'Ben'",
        "blocks/slack.txt:2 WAITING: its locator, slack:C0PILOT/1, doesn't open: give the one read opens, or drop the "
        "line",
        "blocks/slack.txt:2 WAITING: the stream 'sensors' isn't one the ledger holds: one of pilot",
        "blocks/slack.txt:2 WAITING: the day asked, 'the 19th', is YYYY-MM-DD, its HH:MM after it",
        "blocks/slack.txt:3 CLOSES: '1' isn't an item's A- ref",
        "blocks/slack.txt:3 CLOSES: 'finished' is done or moot",
        "blocks/slack.txt:4 STREAMS: 'colour=blue' isn't field=value with a field of name, status, where, next, "
        "deadline",
        "blocks/slack.txt:5 OWED: OWED takes 6 to 10 fields, not 3",
    ]


def test_a_block_not_of_the_sync_or_not_written_yet_is_refused(vault: Vault) -> None:
    sync = written(vault, "slack", "RUN: slack | none | the browser was closed")
    with pytest.raises(DocketError, match=re.escape("'chat' isn't a block of")):
        check.check(vault, sync, "chat")
    with pytest.raises(DocketError, match=re.escape("no block blocks/teams.txt yet: write it first")):
        check.check(vault, sync, "teams")
    with pytest.raises(DocketError, match=re.escape("isn't a block of")):
        check.check(vault, sync, "../slack")


def test_learned_lines_go_to_the_skill_or_its_sync_md_and_a_copied_example_s_route_to_route(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    memo = sync_vault(sync_template(tmp_path_factory), tmp_path)
    started = start.start(memo, [], datetime.now(UTC))
    (started.folder / "blocks" / "memo.txt").write_text(
        "RUN: memo | run-1 | captures 1\nLEARNED:  SKILL.md | list_memos pages by 20\nLEARNED:  sync.md | Ada's memos "
        "come late on Fridays\n",
        encoding="utf-8",
    )
    skills = memo.root.parent / ".claude" / "skills" / "sources" / "memo"
    assert main(["sync", "learned", started.info.sync, "--vault", str(memo.root)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "memo (2)",
        f"  {skills / 'SKILL.md'}: list_memos pages by 20  [blocks/memo.txt:2]",
        f"  {skills / 'sync.md'}: Ada's memos come late on Fridays  [blocks/memo.txt:3]",
    ]
    skill = skills / "SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8").replace("# memo", "# Memo, a Docket example source"),
                     encoding="utf-8")  # fmt: skip
    ((source, (route, other)),) = check.learned(memo, started.info.sync)
    assert (source, route.file, route.part, other.part) == ("memo", skills / "sync.md", "Route", None)
    assert check.learned_lines([]) == ["no block holds a LEARNED line"]
