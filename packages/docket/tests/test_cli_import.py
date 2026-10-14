from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from docket_testkit import ADA, REC, copy_vault, notes_template, screening, sh, template_vault

from docket import recall
from docket.cli import main
from docket.vault.docstore import DocStore
from docket.vault.processed import ProcessedDoc
from docket.vault.vault import Vault

CALL = "notes/calls/2026-09-10-supplier-call.md"
CALL_ID = "legacy-note:2026-09-10-supplier-call"
OFFER = "notes/mail/2026-09-11-offer.md"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_vault(tmp_path_factory)


@pytest.fixture(scope="module")
def repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return notes_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    v = screening(copy_vault(template, tmp_path))
    monkeypatch.setenv("DOCKET_VAULT", str(v.root))
    return v


@pytest.fixture
def folder(repo_template: Path, tmp_path: Path) -> Path:
    shutil.copytree(repo_template, tmp_path / "work")
    return tmp_path / "work" / "notes"


def test_the_report(vault: Vault, folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["import", "notes", str(folder), "--dry-run"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"prefix   notes  (old paths look like {CALL})",
        "new      9",
        "updated  0",
        "covers   0",
        "same     0",
        "skipped  .jpg 1, .json 1, never-store 2",
        "         notes/latin1.md: not UTF-8, skipped",
        "dry run: nothing written",
    ]
    assert main(["import", "notes", str(folder), "--as", "."]) == 0
    out = capsys.readouterr().out
    assert "prefix   .  (old paths look like calls/2026-09-10-supplier-call.md)" in out
    assert f"commit   {sh(vault.root, 'git', 'rev-parse', 'HEAD')[:12]}" in out


def test_a_note_changed_in_both_places_and_its_take(
    vault: Vault, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["import", "notes", str(folder)])
    doc = DocStore(vault).get(CALL_ID)
    assert doc is not None
    recall.doc_put(vault, ProcessedDoc(doc.meta.model_copy(update={"by": "claude"}), "# Claude's\n", 0).render())
    (folder / "calls" / "2026-09-10-supplier-call.md").write_text("# Changed\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["import", "notes", str(folder)]) == 1
    out = capsys.readouterr().out
    assert f"kept     {CALL}: edited in the vault and changed in the folder" in out
    found = re.search(r'keep the vault: run again with --take "(vault:([0-9a-f]{8}):[^"]+)"', out)
    assert found is not None
    assert found.group(1) == f"vault:{found.group(2)}:{CALL}"
    assert f'keep the note:  run again with --take "note:{found.group(2)}:{CALL}"' in out
    assert main(["import", "notes", str(folder), "--take", found.group(1)]) == 0
    assert "updated  1" in capsys.readouterr().out


def test_a_deleted_doc_says_how_to_bring_it_back(
    vault: Vault, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["import", "notes", str(folder)])
    (vault.root / "processed" / "legacy-note" / "2026" / "09" / "2026-09-11-offer.md").unlink()
    sh(vault.root, "git", *ADA, "commit", "-q", "-am", "drop it")
    capsys.readouterr()
    assert main(["import", "notes", str(folder)]) == 0
    out = capsys.readouterr().out
    assert f"deleted  {OFFER}: its doc was deleted in the vault" in out
    assert re.search(rf'bring it back: run again with --take "note:[0-9a-f]{{8}}:{re.escape(OFFER)}"', out)


def test_the_take_hints_are_quoted_for_a_path_with_a_space(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    note = plain / "a b.md"
    note.write_text("# First\n", encoding="utf-8")
    main(["import", "notes", str(plain)])
    doc = DocStore(vault).get("legacy-note:a-b")
    assert doc is not None
    recall.doc_put(vault, ProcessedDoc(doc.meta.model_copy(update={"by": "claude"}), "# Claude's\n", 0).render())
    note.write_text("# Second\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["import", "notes", str(plain)]) == 1
    out = capsys.readouterr().out
    found = re.search(r'keep the note:  run again with --take "(note:[0-9a-f]{8}:plain/a b\.md)"', out)
    assert found is not None
    assert main(["import", "notes", str(plain), "--take", found.group(1)]) == 0
    (vault.root / "processed" / "legacy-note" / "a-b.md").unlink()
    sh(vault.root, "git", *ADA, "commit", "-q", "-am", "drop it")
    capsys.readouterr()
    main(["import", "notes", str(plain)])
    hint = re.search(r'bring it back: run again with --take "note:[0-9a-f]{8}:plain/a b\.md"', capsys.readouterr().out)
    assert hint is not None


def test_gone_refused_and_what_isnt_listed(vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plain = tmp_path / "plain"
    (plain / "nested" / ".git").mkdir(parents=True)
    (plain / "a.md").write_text("# a\n", encoding="utf-8")
    main(["import", "notes", str(plain)])
    (plain / "a.md").unlink()
    capsys.readouterr()
    assert main(["import", "notes", str(plain), "--take", "note:abc:plain/nowhere.md"]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "prefix   plain",
        "new      0",
        "updated  0",
        "covers   0",
        "same     0",
        "gone     1 (their docs stay, so old references still resolve)",
        "refused  --take note:abc:plain/nowhere.md: no note there",
        "skipped  nested repos 1",
    ]
    assert main(["import", "notes", str(plain), "--take", "both:abc:x.md"]) == 2


def test_doc_read_says_what_an_old_path_led_to(vault: Vault, folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["import", "notes", str(folder)])
    capsys.readouterr()
    assert main(["doc", "read", f"{CALL}@12:08", "--view", "both"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "read as r:calls:rec-0042@12:08"
    assert "12:08  Ben Sample: So ten million, for the whole quarter." in "\n".join(out)
    assert main(["doc", "read", "notes/chat/2026-09-10-supplier-call.md@12:08"]) == 0
    assert capsys.readouterr().out.splitlines()[1].startswith("! legacy-note:2026-09-10-supplier-call-")
    assert main(["doc", "read", REC]) == 0
    assert f"{CALL_ID}, a legacy note, covers it: {CALL}" in capsys.readouterr().out
    assert main(["doc", "read", CALL_ID]) == 0
    assert not capsys.readouterr().out.startswith("read as")


def test_the_import_speaks_utf8_through_a_pipe(vault: Vault, folder: Path) -> None:
    env = {**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": ""}
    done = subprocess.run(
        [sys.executable, "-m", "docket", "import", "notes", str(folder), "--as", "régi"],
        capture_output=True,
        env=env,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert "prefix   régi  (old paths look like régi/calls/" in done.stdout.decode("utf-8")
    read = subprocess.run(
        [sys.executable, "-m", "docket", "doc", "read", "régi/árvíztűrő-jegyzet.md"],
        capture_output=True,
        env=env,
        check=False,
    )
    assert read.returncode == 0, read.stderr.decode("utf-8", "replace")
    assert "# Árvíztűrő tükörfúrógép" in read.stdout.decode("utf-8")
