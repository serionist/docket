from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import INDEX_NOW, copy_vault, git, index_template, make_record

from docket.cli import main
from docket.index import build, rebuild
from docket.vault.vault import Vault

QUESTIONS = f"""schema_version: 1
asked: "{INDEX_NOW.isoformat()}"
questions:
  - q: deadline with Ben
    want: ["recording-note:2026-10-07-supplier-call#L21"]
    kind: paraphrase
    lang: en
  - q: millió forint
    want: ["r:calls:rec-0100-second@00:30-01:51"]
    kind: inflection
    lang: hu
  - q: zebracorn
    want: ["r:cal:evt-0001", "r:calls:rec-9999"]
    kind: typo
    lang: en
"""


@pytest.fixture
def vault(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Vault:
    v = copy_vault(index_template(tmp_path_factory), tmp_path)
    rebuild(v)
    return v


@pytest.fixture
def file(tmp_path: Path) -> Path:
    path = tmp_path / "outside" / "questions.yaml"
    path.parent.mkdir()
    path.write_text(QUESTIONS, encoding="utf-8")
    return path


def run(vault: Vault, capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main([*args, "--vault", str(vault.root)])
    out, err = capsys.readouterr()
    return code, out, err


def state(vault: Vault) -> tuple[str, str, str]:
    db = build.db_path(vault).read_bytes()
    return (
        git(vault.root, "rev-parse", "HEAD"),
        git(vault.root, "status", "--porcelain"),
        hashlib.sha256(db).hexdigest(),
    )


def test_eval_prints_a_row_per_kind_and_language_and_the_misses(
    vault: Vault, file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = state(vault)
    code, out, _ = run(vault, capsys, "eval", str(file))
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == f"eval      {file}: 3 questions, k 10"
    assert lines[1].split() == ["kind", "n", "recall@10", "MRR"]
    assert [ln.split()[0] for ln in lines[2:5]] == ["paraphrase", "inflection", "typo"]
    assert lines[2].split()[:2] == ["paraphrase", "1"] and lines[3].split()[:2] == ["inflection", "1"]
    assert [ln.split()[:2] for ln in lines[6:9]] == [["en", "2"], ["hu", "1"], ["all", "3"]]
    assert "missed at 10" in lines
    assert "    3. typo  en  zebracorn  ->  r:cal:evt-0001, r:calls:rec-9999" in lines
    assert lines[-2:] == ["wanted, but naming no record, doc or thread in the index:", "  r:calls:rec-9999"]
    # Nothing is written: not the vault, not its index.
    assert state(vault) == before


def test_eval_takes_k_and_prints_json(vault: Vault, file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(vault, capsys, "eval", str(file), "-k", "5")
    assert code == 0 and out.splitlines()[0].endswith("3 questions, k 5") and "recall@5" in out
    code, out, _ = run(vault, capsys, "eval", str(file), "--k", "3", "--json")
    report = json.loads(out)
    assert out.isascii() and report["k"] == 3 and report["file"] == str(file)
    assert set(report["kinds"]) == {"paraphrase", "inflection", "typo"} and set(report["langs"]) == {"en", "hu"}
    assert [q["ranks"] for q in report["questions"]][2] == [None, None]
    assert report["questions"][1]["q"] == "millió forint" and report["unknown"] == ["r:calls:rec-9999"]


def test_eval_says_when_the_index_is_behind_and_never_updates_it(
    vault: Vault, file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault.write_run("chat", [make_record(400, text="A new message.")], datetime(2026, 10, 14, 9, tzinfo=UTC))
    before = state(vault)
    code, out, _ = run(vault, capsys, "eval", str(file))
    assert code == 0
    assert out.splitlines()[1] == (
        "the index is behind the vault: 1 new run file(s); docket index update for current numbers"
    )
    assert state(vault) == before


def test_a_bad_question_file_exits_2_with_the_reason(
    vault: Vault, file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file.write_text(QUESTIONS.replace("    kind: typo", "    knid: typo"), encoding="utf-8")
    code, out, err = run(vault, capsys, "eval", str(file))
    assert code == 2 and out == ""
    assert err.startswith(f"docket: {file}: question 3: unknown key 'knid'")
    code, _, err = run(vault, capsys, "eval", str(file.parent / "missing.yaml"))
    assert code == 2 and "no such file" in err
    file.write_text(QUESTIONS, encoding="utf-8")
    code, _, err = run(vault, capsys, "eval", str(file), "-k", "0")
    assert code == 2 and "k is from 1 to 100" in err


def test_eval_without_an_index_says_how_to_build_it(
    vault: Vault, file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build.db_path(vault).unlink()
    code, _, err = run(vault, capsys, "eval", str(file))
    assert code == 2 and "run docket index rebuild" in err
