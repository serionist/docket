from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import commit_config, copy_vault, index_template, make_record

from docket.cli import main
from docket.index import build
from docket.vault.vault import Vault


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return copy_vault(template, tmp_path)


def run(vault: Vault, capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main([*args, "--vault", str(vault.root)])
    return code, capsys.readouterr().out


def test_rebuild_update_and_status(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(vault, capsys, "index", "status")
    assert code == 1 and "no index yet" in out
    code, out = run(vault, capsys, "index", "rebuild")
    assert code == 0 and out.startswith("rebuilt in ") and "records 22" in out
    assert "! processed/digest/2026/10/2026-10-09-week.md: entities: no entity has the id 'no-such-entity'" in out
    assert "1 problem(s); the rest is indexed" in out
    code, out = run(vault, capsys, "index", "status")
    assert code == 0 and "languages  en, hu (columns folded, stem_en, stem_hu)" in out and "new since" not in out
    vault.write_run("chat", [make_record(400, text="A new message.")], datetime(2026, 10, 14, 9, tzinfo=UTC))
    (vault.root / "entities" / "person" / "cleo-test.yaml").write_text(
        "id: cleo-test\ntype: person\nname: {en: Cleo Test}\n", encoding="utf-8"
    )
    code, out = run(vault, capsys, "index", "status")
    assert "new since  1 run file(s), 0 doc(s), the entity files: docket index update" in out
    code, out = run(vault, capsys, "index", "update")
    assert code == 0 and "updated in" in out and "1 run file(s), 0 doc(s), the entity files" in out
    first = sorted((vault.root / "raw" / "cal").rglob("*.jsonl"))[0]
    first.write_text(first.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    code, out = run(vault, capsys, "index", "status")
    assert code == 1 and "indexed run file(s) changed or went: run docket index rebuild" in out
    vault.config.owner.languages = ["en"]
    (vault.root / "vault.yaml").write_text(
        (vault.root / "vault.yaml").read_text(encoding="utf-8").replace("  - hu\n", ""), encoding="utf-8"
    )
    commit_config(vault.root)
    code, out = run(vault, capsys, "index", "status")
    assert code == 1 and "needs a rebuild: it was built with languages" in out


def test_search_prints_groups_hits_and_locators(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(vault, capsys, "search", "offer", "in", "writing", "-k", "5")
    assert code == 0
    assert "(the index was rebuilt: there was no index)" in out
    assert "recording  Supplier call with Northwind  (calls  2026-10-07)" in out
    assert "summary  minutes:2026-10-07-supplier-call rev 1: Minutes: supplier call" in out
    assert "r:calls:rec-0100@04:00-05:00  2026-10-07 10:04  Cleo Test" in out
    code, out = run(vault, capsys, "search", "Nortwin", "--no-update")
    assert "'nortwin' is northwind" in out and "lines 14-14" in out
    code, out = run(vault, capsys, "search", "múlt", "héten", "pallet", "--person", "Nobody", "--no-update")
    # Last week as the owner's clock reads it today: from last Monday's start to this Monday's.
    today = datetime.now(ZoneInfo(vault.config.owner.time_zone)).date()
    monday = today - timedelta(days=today.weekday())
    assert f"from {monday - timedelta(days=7)} 00:00 to {monday} 00:00 (múlt héten)" in out
    assert "not found as a person or entity: Nobody" in out
    code, out = run(
        vault, capsys, "search", "pallet", "--since", "2026-10-08", "--until", "2026-10-08T12:00", "--no-update"
    )
    assert "from 2026-10-08 00:00 to 2026-10-08 12:00\n" in out
    code, out = run(vault, capsys, "search", "zebracorn", "--no-update")
    assert "nothing found" in out
    code, out = run(vault, capsys, "search", "cold-rated", "--no-update")
    assert "also r:chat:C0OPS/1791400000.000100" in out and "-> " not in out.split("cold-rated")[0]
    code, out = run(vault, capsys, "search", "deadline", "with", "Ben", "--no-update")
    assert "       -> r:calls:rec-0100@02:30-03:20" in out


def test_search_names_a_language_problem_after_a_rebuild(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    vault_yaml = vault.root / "vault.yaml"
    vault_yaml.write_text(vault_yaml.read_text(encoding="utf-8").replace("  - hu\n", "  - hu\n  - klingon\n"), "utf-8")
    commit_config(vault.root)
    code, out = run(vault, capsys, "search", "pallet")
    assert code == 0 and "! vault.yaml: vault.yaml's language 'klingon' has no stemmer" in out


def test_search_refuses_a_bad_date_and_names_a_word_in_no_chunk(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["search", "pallet", "--since", "last tuesday", "--vault", str(vault.root)])
    assert code == 2 and "--since is a date or a time" in capsys.readouterr().err
    code, out = run(vault, capsys, "search", "the", "pallet", "zebracorn")
    assert "in no chunk: zebracorn" in out and "too common" not in out


def test_raw_text_is_printed_with_control_characters_escaped(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    hostile = make_record(500, text="clipboard \x1b]52;c;aGk=\x07 grab")
    vault.write_run("chat", [hostile], datetime(2026, 10, 14, 9, tzinfo=UTC))
    code, out = run(vault, capsys, "search", "clipboard")
    assert code == 0 and "\x1b" not in out and "\\x1b]52;c;aGk=\\x07 grab" in out


def test_the_index_commands_speak_utf8_through_a_pipe(vault: Vault) -> None:
    env = {**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": ""}
    proc = subprocess.run(
        [sys.executable, "-m", "docket", "search", "határidő", "--vault", str(vault.root)],
        capture_output=True, env=env, check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert "határidőről" in proc.stdout.decode("utf-8")
    assert build.db_path(vault).exists()
