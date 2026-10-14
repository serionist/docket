from __future__ import annotations

from pathlib import Path

import pytest

from docket.errors import DocketError
from docket.vault import legacy
from docket.vault.legacy import OldPath, find, normalize
from docket.vault.processed import parse_doc

ENTRIES = [
    OldPath("notes/calls/2026-09-10-supplier-call.md", "legacy-note:2026-09-10-supplier-call"),
    OldPath("notes/chat/2026-09-10-supplier-call.md", "legacy-note:2026-09-10-supplier-call-1a2b3c"),
    OldPath("notes/mail/2026-09-11-offer.md", "legacy-note:2026-09-11-offer"),
    OldPath("notes/árvíztűrő-jegyzet.md", "legacy-note:arvizturo-jegyzet"),
    OldPath("README.md", "legacy-note:readme"),
    OldPath("Notes/Plans.md", "legacy-note:plans-upper"),
    OldPath("notes/plans.md", "legacy-note:plans-lower"),
]


@pytest.mark.parametrize(
    ("text", "normal"),
    [
        ("notes\\calls\\x.md", "notes/calls/x.md"),
        ("./notes//calls///x.md", "notes/calls/x.md"),
        ("././x.md", "x.md"),
        (" notes/a\u0301rvi\u0301z.md ", "notes/árvíz.md"),
        ("D:\\work\\x.md", "D:/work/x.md"),
    ],
)
def test_normalizing_a_path(text: str, normal: str) -> None:
    assert normalize(text) == normal


def test_a_path_that_walks_up_is_refused() -> None:
    with pytest.raises(DocketError, match=r"has a .. part"):
        normalize("notes/../x.md")


@pytest.mark.parametrize(
    ("locator", "doc"),
    [
        ("notes/calls/2026-09-10-supplier-call.md", "legacy-note:2026-09-10-supplier-call"),
        ("notes\\mail\\2026-09-11-offer.md", "legacy-note:2026-09-11-offer"),
        ("NOTES/MAIL/2026-09-11-Offer.md", "legacy-note:2026-09-11-offer"),
        ("notes/a\u0301rvi\u0301ztu\u030bro\u030b-jegyzet.md", "legacy-note:arvizturo-jegyzet"),
        ("notes/plans.md", "legacy-note:plans-lower"),
        ("Notes/Plans.md", "legacy-note:plans-upper"),
        ("calls:notes/calls/2026-09-10-supplier-call.md", "legacy-note:2026-09-10-supplier-call"),
        ("D:\\work\\repo\\notes\\calls\\2026-09-10-supplier-call.md", "legacy-note:2026-09-10-supplier-call"),
        ("/home/ada/repo/notes/chat/2026-09-10-supplier-call.md", "legacy-note:2026-09-10-supplier-call-1a2b3c"),
        ("old/notes/mail/2026-09-11-offer.md", "legacy-note:2026-09-11-offer"),
        ("calls:README.md", "legacy-note:readme"),
        ("2026-09-11-offer.md", "legacy-note:2026-09-11-offer"),
        ("mail:2026-09-11-OFFER.md", "legacy-note:2026-09-11-offer"),
    ],
)
def test_each_lookup_step(locator: str, doc: str) -> None:
    assert find(ENTRIES, locator) == doc


@pytest.mark.parametrize(
    ("locator", "says"),
    [
        ("notes/PLANS.md", "could be any of legacy-note:plans-lower, legacy-note:plans-upper"),
        ("2026-09-10-supplier-call.md", "could be any of"),
        ("elsewhere/2026-09-10-supplier-call.md", "no legacy note has the old path"),
        ("notes/calls/2026-09-30-nothing.md", "no legacy note has the old path"),
    ],
)
def test_nothing_or_several_is_said_never_guessed(locator: str, says: str) -> None:
    with pytest.raises(DocketError, match=says):
        find(ENTRIES, locator)


def test_another_folders_readme_never_resolves_to_the_root_one() -> None:
    for locator in ("D:\\work\\other-repo\\README.md", "vendor/tool/README.md", "/srv/README.md"):
        with pytest.raises(DocketError) as e:
            find(ENTRIES, locator)
        assert "a note at the root of its folder ends it: README.md (legacy-note:readme)" in str(e.value)


def test_a_flat_notes_repo_names_its_note_as_the_candidate() -> None:
    flat = [OldPath("2026-09-10-supplier-call.md", "legacy-note:2026-09-10-supplier-call")]
    with pytest.raises(DocketError, match=r"ends it: 2026-09-10-supplier-call\.md \(legacy-note:2026-09-10"):
        find(flat, "D:\\work\\notes\\2026-09-10-supplier-call.md")
    assert find(flat, "2026-09-10-supplier-call.md") == "legacy-note:2026-09-10-supplier-call"


def test_two_suffixes_of_the_same_length_are_several() -> None:
    twins = [OldPath("calls/x.md", "legacy-note:a"), OldPath("CALLS/X.md", "legacy-note:b")]
    with pytest.raises(DocketError, match="could be any of legacy-note:a, legacy-note:b"):
        find(twins, "D:/repo/calls/x.md")


def test_an_absolute_path_naming_a_real_file_is_matched_as_a_string(tmp_path: Path) -> None:
    real = tmp_path / "notes" / "calls" / "2026-09-10-supplier-call.md"
    real.parent.mkdir(parents=True)
    real.write_text("never read", encoding="utf-8")
    real.chmod(0o000)
    try:
        assert find(ENTRIES, str(real)) == "legacy-note:2026-09-10-supplier-call"
    finally:
        real.chmod(0o644)


def test_old_paths_come_from_legacy_notes_only() -> None:
    note = parse_doc(
        "---\nid: legacy-note:x\ntype: legacy-note\nby: import\nold_paths:\n  - a/x.md\n  - b/x.md\n---\nOld.\n"
    )
    brief = parse_doc("---\nid: brief:ben\ntype: brief\ncovers: [calls:a]\nby: Ada\n---\nBen. @00:01\n")
    assert legacy.old_paths([note, brief]) == [OldPath("a/x.md", "legacy-note:x"), OldPath("b/x.md", "legacy-note:x")]
