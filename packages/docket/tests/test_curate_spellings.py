from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from curate_kit import curate_vault

from docket.curate.spellings import DOCS, HEARD, LABEL, TYPED, VENDOR, Scan, cased, generic, scan, trust_of
from docket.index.build import db_path


@pytest.fixture(scope="module")
def found(tmp_path_factory: pytest.TempPathFactory) -> Scan:
    vault = curate_vault(tmp_path_factory.mktemp("spellings"))
    conn = sqlite3.connect(db_path(vault))
    try:
        return scan(conn)
    finally:
        conn.close()


def test_a_label_is_trusted_most_and_a_generic_one_is_no_spelling(found: Scan) -> None:
    cleo = found.seen["Cleo Test"]
    assert (cleo.trust, cleo.label, len(cleo.chunks)) == (LABEL, True, 6)
    assert "Speaker 2" not in found.seen
    assert "Speaker" not in found.seen


def test_where_a_spelling_was_written_sets_its_trust(found: Scan) -> None:
    assert found.seen["Nortwin"].trust == HEARD
    assert found.seen["Northwind"].trust == DOCS
    assert found.seen["Harbour Fair"].trust == TYPED
    assert found.seen["Weekly"].trust == VENDOR


def test_a_sentence_s_first_word_is_counted_apart(found: Scan) -> None:
    okay = found.seen["Okay"]
    assert okay.mid == 0 and okay.start > 0
    assert found.seen["Nortwin"].mid == 6 and found.seen["Nortwin"].start == 0


def test_a_run_is_counted_and_its_words_are_not_alone(found: Scan) -> None:
    harbour = found.seen["Harbour Fair"]
    assert (harbour.words, harbour.mid) == (("harbour", "fair"), 2)
    assert found.seen["The Harbour Fair"].start == 1
    assert found.seen["Harbour"].alone == 0
    assert found.seen["Northwind"].alone > 0


def test_a_turn_s_label_is_left_out_of_its_words_and_counted_capitalised(found: Scan) -> None:
    assert found.cased["cleo"] >= found.seen["Cleo"].mid + found.seen["Cleo"].start


@pytest.mark.parametrize(
    ("word", "is_cased"),
    [("Cleo", True), ("eBay", True), ("NASA", True), ("ÁRAJÁNLAT", False), ("2B", False), ("mark", False)],
)
def test_what_counts_as_capitalised(word: str, is_cased: bool) -> None:
    assert cased(word) is is_cased


@pytest.mark.parametrize(
    ("label", "is_generic"),
    [("Speaker 1", True), ("Beszélő 2", True), ("Unknown", True), ("SPK_3", True), ("A", True), ("Cleo", False)],
)
def test_generic_labels(label: str, is_generic: bool) -> None:
    assert generic(label) is is_generic


def test_trust_by_kind() -> None:
    assert trust_of("doc", "recording-note:x", None) == DOCS
    assert trust_of("record", None, "transcript") == HEARD
    assert trust_of("record", None, "recording") == VENDOR
    assert trust_of("thread", None, None) == TYPED


def test_the_kit_vault_is_built_once(tmp_path: Path) -> None:
    assert curate_vault(tmp_path).root.exists()
