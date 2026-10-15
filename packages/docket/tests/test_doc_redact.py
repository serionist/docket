"""`docket doc redact`: the docs already stored, redacted with the settings as they are now (DR-0210). Secret-shaped
strings are assembled from parts, as in test_redact.py; people and numbers are invented."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from docket_testkit import configure, copy_vault, git, note, sh, template_vault

from docket import index, recall, render
from docket.cli import main
from docket.vault import derived
from docket.vault.docstore import DocStore, Redacting, RedactReport
from docket.vault.vault import Vault

SECRET = "hunter2-" + "Example"
PHONE = "+1 555 010 0188"
NOTE_ID = "recording-note:2026-10-14-supplier-call"
NOTE_PATH = "processed/recording-note/2026/10/2026-10-14-supplier-call.md"
MINUTES_ID = "minutes:2026-10-14-supplier-call"
MINUTES_PATH = "processed/minutes/2026/10/2026-10-14-supplier-call.md"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_vault(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    """A vault holding two docs put while credentials redaction was off, then turned back on."""
    root = copy_vault(template, tmp_path).root
    configure(root, credentials=False)
    recall.doc_put(Vault.open(root), note(lines=[f"- the wifi password: {SECRET} @12:08"]))
    recall.doc_put(Vault.open(root), note(MINUTES_ID, lines=[f"- the door password: {SECRET}x @14:30"]))
    configure(root, credentials=True)
    return Vault.open(root)


def redact(vault: Vault, *flags: str) -> int:
    return main(["doc", "redact", *flags, "--vault", str(vault.root)])


def subjects(vault: Vault) -> list[str]:
    return sh(vault.root, "git", "log", "--format=%s").splitlines()


def at_head(vault: Vault, rel: str) -> str:
    return sh(vault.root, "git", "show", f"HEAD:{rel}")


def test_the_pass_redacts_the_docs_stored_before_in_one_commit(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    assert redact(vault) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:3] == [
        "redacted  2 docs: 2 password",
        f"          {MINUTES_ID} rev 2",
        f"          {NOTE_ID} rev 2",
    ]
    assert "history   earlier revs still hold what was redacted, in this vault's git history" in out
    message = sh(vault.root, "git", "log", "-1", "--format=%B").splitlines()
    assert message[0] == "Redact docs: 2"
    assert set(message[2:]) - {""} == {f"Doc: {NOTE_ID} rev 2", f"Doc: {MINUTES_ID} rev 2"}
    assert "- the wifi password: [redacted:password] @12:08" in at_head(vault, NOTE_PATH)
    assert "\nrev: 2\n" in at_head(vault, NOTE_PATH)
    assert SECRET not in at_head(vault, MINUTES_PATH)
    assert sh(vault.root, "git", "status", "--porcelain") == ""
    before = subjects(vault)
    assert redact(vault) == 0
    assert capsys.readouterr().out.startswith("redacted  nothing")
    assert subjects(vault) == before


def test_a_dry_run_writes_nothing(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    before = subjects(vault)
    assert redact(vault, "--dry-run") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "would redact 2 docs: 2 password"
    assert out[-1] == "dry run: nothing written"
    assert subjects(vault) == before
    assert SECRET in (vault.root / NOTE_PATH).read_text(encoding="utf-8")


def test_what_it_can_t_redact_is_left_and_named(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    configure(vault.root, keywords=["dora example"])
    recall.doc_put(Vault.open(vault.root), note("brief:ben-sample", lines=["- Ben works with Cleo. @12:08"]))
    brief = vault.root / "processed" / "brief" / "ben-sample.md"
    brief.write_text(brief.read_text(encoding="utf-8").replace("Cleo.", "Dora Example."), encoding="utf-8")
    git(vault.root, "commit", "-qam", "my edit, committed by hand")
    broken = vault.root / "processed" / "brief" / "broken.md"
    broken.write_text("no frontmatter here\n", encoding="utf-8")
    misplaced = vault.root / "processed" / "brief" / "elsewhere.md"
    misplaced.write_text(brief.read_text(encoding="utf-8"), encoding="utf-8")
    before = subjects(vault)
    assert redact(vault) == 1
    out = capsys.readouterr().out
    assert "left      3 problem(s); each file is left as it is:" in out
    assert "          processed/brief/ben-sample.md: line 14 says something on the never-store keywords list" in out
    assert "          processed/brief/broken.md: it doesn't parse as a doc; fix it" in out
    assert "processed/brief/elsewhere.md: its id brief:ben-sample belongs at processed/brief/ben-sample.md" in out
    assert "dora" not in out.lower().replace("dora example.", "")
    assert subjects(vault)[1:] == before
    assert "Dora Example." in at_head(vault, "processed/brief/ben-sample.md")


@pytest.mark.parametrize(
    ("line", "what"),
    [
        ("# a call with Dora Example", "says something on the never-store keywords list"),
        (f"title: call {PHONE}", "holds what the phone rule would redact"),
        # A key that is a label, and a key whose own name says a keyword.
        (f"db_password: {SECRET}", "holds what the password rule would redact"),
        ("dora_example: yes", "says something on the never-store keywords list"),
    ],
)
def test_a_committed_frontmatter_holding_a_keyword_or_a_secret_is_left(
    vault: Vault, capsys: pytest.CaptureFixture[str], line: str, what: str
) -> None:
    configure(vault.root, phone=True, keywords=["dora example"])
    path = vault.root / NOTE_PATH
    path.write_text(path.read_text(encoding="utf-8").replace("by: ", f"{line}\nby: ", 1), encoding="utf-8")
    git(vault.root, "commit", "-qam", "a line of my own")
    committed = at_head(vault, NOTE_PATH)
    assert redact(vault) == 1
    out = capsys.readouterr().out
    assert f"{NOTE_PATH}: line 7 {what}" in out
    # The minutes are redacted; the note is left as it was committed, no marker in its YAML.
    assert NOTE_ID not in sh(vault.root, "git", "log", "-1", "--format=%B")
    assert at_head(vault, NOTE_PATH) == committed
    assert line in committed


def test_a_doc_edited_while_the_pass_runs_is_left_not_written_over(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = derived.hand_edit
    path = vault.root / NOTE_PATH

    def then_the_user_saves(doc_type: str, text: str, *args: Any) -> derived.HandEdit:
        found = real(doc_type, text, *args)
        if "wifi" in text:
            path.write_text(text.replace("@12:08", "and the user's edit @12:08"), encoding="utf-8", newline="\n")
        return found

    monkeypatch.setattr(derived, "hand_edit", then_the_user_saves)
    assert redact(vault) == 1
    out = capsys.readouterr().out
    assert f"          {NOTE_PATH}: it changed while the pass ran; run docket doc redact again" in out
    assert out.startswith("redacted  1 doc: 1 password")
    assert "and the user's edit" in path.read_text(encoding="utf-8")
    message = sh(vault.root, "git", "log", "-1", "--format=%B")
    assert (MINUTES_ID in message, NOTE_ID in message) == (True, False)


def test_a_key_this_docket_doesn_t_know_is_kept(vault: Vault) -> None:
    path = vault.root / NOTE_PATH
    path.write_text(path.read_text(encoding="utf-8").replace("by: ", "title: Supplier call\nby: ", 1), encoding="utf-8")
    git(vault.root, "commit", "-qam", "a title of my own")
    assert redact(vault) == 0
    stored = at_head(vault, NOTE_PATH)
    assert "\ntitle: Supplier call\n" in stored
    assert "[redacted:password]" in stored


def test_an_uncommitted_hand_edit_goes_in_through_the_take_in(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    path = vault.root / NOTE_PATH
    path.write_text(path.read_text(encoding="utf-8").replace("@12:08", "and more @12:08"), encoding="utf-8")
    assert redact(vault) == 0
    assert f"          {NOTE_ID} rev 2, a hand edit taken in" in capsys.readouterr().out
    assert f"Doc: {NOTE_ID} rev 2 (edited by hand)" in subjects(vault)
    assert "[redacted:password] and more @12:08" in at_head(vault, NOTE_PATH)


def test_the_index_is_rebuilt_and_the_secret_is_found_no_more(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    index.rebuild(vault)
    assert index.search(vault, "hunter2").groups
    assert redact(vault) == 0
    assert (
        "index    rebuilt here; on another machine, run docket index rebuild after it pulls" in capsys.readouterr().out
    )
    assert not index.search(vault, "hunter2").groups


def test_turning_phone_on_then_a_pass_redacts_the_numbers_stored_before(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    recall.doc_put(vault, note("brief:cleo-test", lines=[f"- Cleo is on {PHONE}. @12:08"]))
    redact(vault)
    capsys.readouterr()
    assert main(["redaction", "set", "phone", "on", "--vault", str(vault.root)]) == 0
    capsys.readouterr()
    assert redact(vault) == 0
    assert capsys.readouterr().out.startswith("redacted  1 doc: 1 phone")
    assert PHONE not in at_head(vault, "processed/brief/cleo-test.md")


def test_a_vault_in_a_folder_says_whose_history_holds_the_old_text() -> None:
    report = RedactReport(dry_run=False, docs=[Redacting(id=NOTE_ID, rev=2, redactions={"phone": 1})])
    assert render.redacted(report, "folder")[-1] == (
        "history   earlier revs still hold what was redacted, in the history of the repo the vault is a folder of, "
        "which Docket never rewrites"
    )


def test_a_vault_with_no_processed_docs_has_nothing_to_redact(tmp_path: Path, template: Path) -> None:
    empty = copy_vault(template, tmp_path)
    assert DocStore(empty).redact_all().docs == []
