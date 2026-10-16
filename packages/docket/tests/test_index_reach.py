"""What a never-store value would have kept out of what is already stored (DR-0509): counted from the index, each
candidate held to the gate's own rule, so the confirm view's "at least" is true. Invented records only."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import empty_vault, make_config, make_record

from docket import index
from docket.errors import DocketError
from docket.index.build import open_index
from docket.index.query import NameQuery, Reach, reach
from docket.vault.config import SourceConfig
from docket.vault.vault import Vault

RUN = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def vault(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    config = make_config()
    config.sources["mail"] = SourceConfig()
    found = empty_vault(tmp_path_factory.mktemp("reach"), config)
    mail = make_record(
        1,
        rid="mail:m-1",
        source="mail",
        kind="mail",
        author="Dora Example <dora@example.invalid>",
        container="mail:inbox",
        title="Kick-off",
        text="Project Phoenix starts on Monday.",
    )
    call = make_record(
        2,
        rid="calls:rec-2",
        source="calls",
        kind="transcript",
        container="calls:rec-2",
        author=None,
        text="",
        segments=[{"start_ms": 0, "end_ms": 5_000, "speaker": "Dora Example", "text": "The budget is set."}],
    )
    chats = [
        make_record(3, rid="chat:c-3", container="chat:garden", text="Phoenixes rise from their ashes, they say."),
        make_record(4, rid="chat:c-4", container="chat:family", text="Dinner at eight, and the ++ operator later."),
    ]
    found.write_run("mail", [mail], RUN)
    found.write_run("calls", [call], RUN)
    found.write_run("chat", chats, RUN)
    index.rebuild(found)
    return found


def total(vault: Vault) -> int:
    conn = open_index(vault)
    try:
        return int(conn.execute("SELECT count(*) FROM chunks").fetchone()[0])
    finally:
        conn.close()


def test_a_person_is_counted_by_authors_and_speakers_as_the_gate_reads_them(vault: Vault) -> None:
    # The speaker is named; `Name <addr>` matches by its address, as the gate holds an author (DR-0026).
    by_name = reach(vault, "people", "Dora Example")
    assert (by_name.records, by_name.total) == (1, total(vault))
    assert by_name.chunks >= 1
    assert reach(vault, "people", "dora@example.invalid").records == 1
    assert reach(vault, "people", "Dora Example <dora@example.invalid>").records == 1
    assert reach(vault, "people", "Ben Sample") == Reach(0, 0, total(vault))


def test_a_container_is_counted_with_or_without_its_prefix(vault: Vault) -> None:
    assert reach(vault, "containers", "garden").records == 1
    assert reach(vault, "containers", "chat:family").records == 1
    assert reach(vault, "containers", "chat:attic").records == 0


def test_a_keyword_counts_only_the_chunks_its_rule_matches(vault: Vault) -> None:
    assert reach(vault, "keywords", "Project: Phoenix") == Reach(1, 1, total(vault))
    # Search's word forms pick "Phoenixes" as a candidate too; the keyword rule takes whole words, so it isn't counted.
    conn = open_index(vault)
    try:
        expression = NameQuery(conn, vault).expression("phoenix")
        found = conn.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?", (expression,)).fetchone()
        candidates = int(found[0])
    finally:
        conn.close()
    assert candidates == 2
    assert reach(vault, "keywords", "phoenix") == Reach(1, 1, total(vault))
    # A keyword with no words is held to every chunk.
    assert reach(vault, "keywords", "++") == Reach(1, 1, total(vault))


def test_no_index_is_refused_as_the_index_is(tmp_path: Path) -> None:
    bare = empty_vault(tmp_path, make_config())
    with pytest.raises(DocketError, match="there is no index yet"):
        reach(bare, "people", "Dora Example")
