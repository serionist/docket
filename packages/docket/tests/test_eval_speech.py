"""`docket eval speech <file>` (DR-0571): each engine's transcripts against invented stretches corrected by hand, and
the speech file's own errors. The vault and the file are invented; nothing is written."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import empty_vault, index_config, make_record

from docket.cli import main
from docket.speech import evaluate
from docket.speech.evaluate import SpeechFileError
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

AT = datetime(2026, 10, 12, 8, tzinfo=UTC)
LATER = datetime(2026, 10, 14, 9, tzinfo=UTC)
REC = "calls:rec-9"
WHISPER = "whisper-large-v3@edaa852 faster-whisper-1.2.1"
FILE = """\
schema_version: 1
stretches:
  - at: "r:calls:rec-9@00:00-00:20"
    text: "The crane budget is fine for now, says Ada Example."
    names: [Ada Example]
  - at: "r:calls:rec-9@05:00-05:10"
    text: Nobody said this.
    note: past the end of both transcripts
"""


def said(rid: str, parts: list[tuple[int, int, str]], **fields: Any) -> RawRecord:
    segs = [{"start_ms": s, "end_ms": e, "text": t} for s, e, t in parts]
    under = {"source": "calls", "container": REC, "thread": REC, "ts": AT}
    return make_record(rid=rid, kind="transcript", segments=segs, **under, **fields)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = empty_vault(tmp_path, index_config())
    v.write_run(
        "calls",
        [
            make_record(rid=REC, kind="recording", source="calls", container=REC, thread=REC, ts=AT, text=""),
            said(f"{REC}/t", [(1000, 9000, "the crane budget is fine for now"), (10_000, 15_000, "says Ada Example")]),
            said(
                f"{REC}/whisper",
                [
                    (1000, 9000, "The crane budget is fine for now."),
                    (10_000, 15_000, "says Ida Crane Works"),
                    (15_000, 18_000, "[cut]"),
                    (30_000, 35_000, "words after the stretch"),
                ],
                engine=WHISPER,
                via="docket transcribe 0f",
            ),
        ],
        LATER,
    )
    entity = v.root / "entities" / "project" / "crane-works.yaml"
    entity.parent.mkdir(parents=True)
    entity.write_text("id: crane-works\ntype: project\nname: {en: Crane Works}\n", encoding="utf-8")
    return v


def test_each_engine_s_error_rates_against_the_corrected_stretch(vault: Vault, tmp_path: Path) -> None:
    path = tmp_path / "speech.yaml"
    path.write_text(FILE, encoding="utf-8")
    report = evaluate.run(vault, path, evaluate.load(path))
    by = {e.engine: e for e in report.engines}
    recorder, whisper = by[evaluate.RECORDER], by[WHISPER]
    assert (recorder.stretches, recorder.words, recorder.wer, recorder.cer) == (1, 10, 0.0, 0.0)
    assert recorder.name_error == 0.0 and recorder.added == []
    # ada -> ida, example -> crane, and works added: three edits in ten words.
    assert whisper.wer == pytest.approx(0.3) and whisper.cer > 0
    assert (whisper.names, whisper.names_missed, whisper.name_error) == (1, 1, 1.0)
    assert whisper.added == ["Crane Works"]
    assert report.unmatched == [2]
    lines = evaluate.lines(report)
    assert lines[0] == f"eval      {path}: 2 stretch(es)"
    assert any(row.startswith(WHISPER) and "30.0%" in row and "100.0%" in row for row in lines)
    assert f"added     {WHISPER}: Crane Works" in lines
    assert lines[-1] == "no transcript reached stretch(es) 2"


def test_the_numbers_and_their_parts() -> None:
    assert evaluate.normal("Árvíztűrő, tükörfúrógép!") == ["árvíztűrő", "tükörfúrógép"]
    assert evaluate.edits(["a", "b", "c"], ["a", "x", "c", "d"]) == 2
    assert evaluate.says("Szia, Kovacs Dora!", "Kovács Dóra") and not evaluate.says("Kovácsné", "Kovács")
    assert not evaluate.says("anything", "  ")


def test_the_command_prints_the_table_or_the_json(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "speech.yaml"
    path.write_text(FILE, encoding="utf-8")
    assert main(["eval", "speech", str(path), "--vault", str(vault.root)]) == 0
    assert capsys.readouterr().out.startswith(f"eval      {path}: 2 stretch(es)\n")
    assert main(["eval", "speech", str(path), "--json", "--vault", str(vault.root)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["stretches"] == 2 and {e["engine"] for e in data["engines"]} == {evaluate.RECORDER, WHISPER}
    assert main(["eval", str(path), str(path), "--vault", str(vault.root)]) == 2
    assert "one file: your question file, or speech and then your speech file" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("[unclosed", "isn't YAML"),
        ("- a list\n", "a speech file is a map"),
        ("schema_version: 1\nstretches: []\nextra: 1\n", "unknown key 'extra'"),
        ("schema_version: 2\nstretches: []\n", "schema_version is 1"),
        ("schema_version: 1\nstretches: []\n", "stretches is a list of at least one"),
        ("schema_version: 1\nstretches: [text]\n", "stretch 1: a stretch is a map"),
        ("schema_version: 1\nstretches: [{at: 'r:calls:a@00:01-00:02', text: x, by: me}]\n", "unknown key 'by'"),
        ("schema_version: 1\nstretches: [{at: 3, text: x}]\n", "at is a recording's stretch"),
        ("schema_version: 1\nstretches: [{at: 'r:calls:a@00:01', text: x}]\n", "at is a stretch of a recording"),
        ("schema_version: 1\nstretches: [{at: 'r:nocolon@00:01-00:02', text: x}]\n", "isn't a rid"),
        ("schema_version: 1\nstretches: [{at: 'r:calls:a@00:01-00:02', text: ' '}]\n", "text is the stretch"),
        ("schema_version: 1\nstretches: [{at: 'r:calls:a@00:01-00:02', text: x, names: [1]}]\n", "names is a list"),
    ],
)
def test_a_speech_file_that_doesn_t_read_is_named_with_what_is_wrong(tmp_path: Path, text: str, said: str) -> None:
    path = tmp_path / "speech.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SpeechFileError, match=said.replace("[", r"\[")):
        evaluate.load(path)


def test_a_missing_or_unreadable_file_is_named(tmp_path: Path) -> None:
    with pytest.raises(SpeechFileError, match="no such file"):
        evaluate.load(tmp_path / "none.yaml")
    with pytest.raises(SpeechFileError, match="can't be read"):
        evaluate.load(tmp_path)
