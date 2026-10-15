from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from docket_testkit import REC, at, copy_vault, make_record, note, template_vault

from docket.cli import main
from docket.render import printable
from docket.vault.vault import Vault

NOTE_ID = "recording-note:2026-10-14-supplier-call"
NOTE_PATH = "processed/recording-note/2026/10/2026-10-14-supplier-call.md"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_vault(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    v = copy_vault(template, tmp_path)
    monkeypatch.setenv("DOCKET_VAULT", str(v.root))
    return v


def put_file(tmp_path: Path, text: str, name: str = "draft.md") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="\n")
    return str(path)


def stdin(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data), encoding="utf-8"))


def test_put_from_a_file_then_unchanged_then_the_next_rev(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["doc", "put", put_file(tmp_path, note())]) == 0
    assert capsys.readouterr().out == f"put {NOTE_ID} rev 1  {NOTE_PATH}\n"
    assert main(["doc", "put", put_file(tmp_path, note(rev="1"))]) == 0
    assert capsys.readouterr().out == f"unchanged {NOTE_ID} rev 1  {NOTE_PATH}\n"
    hand = vault.root / NOTE_PATH
    hand.write_text(hand.read_text(encoding="utf-8").replace("forints", "forints, net"), encoding="utf-8")
    assert main(["doc", "put", put_file(tmp_path, note(rev="2", lines=["- Claude's edit @12:08"]))]) == 0
    assert capsys.readouterr().out == (f"a hand edit went in first, as rev 2\nput {NOTE_ID} rev 3  {NOTE_PATH}\n")


def test_put_from_stdin_drops_a_bom(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin(
        monkeypatch, b"\xef\xbb\xbf" + note(lines=["- \xc3\x81rv\xc3\xadzt\xc5\xb1r\xc5\x91 @12:08"]).encode("latin-1")
    )
    assert main(["doc", "put", "-"]) == 0
    assert capsys.readouterr().out.startswith(f"put {NOTE_ID} rev 1")
    assert "- Árvíztűrő @12:08" in (vault.root / NOTE_PATH).read_text(encoding="utf-8")


def test_a_refusal_prints_every_problem_and_exits_1(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["doc", "put", put_file(tmp_path, note(lines=["- no anchor", "- past the end @2:00:00"]))]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith('line 11 ("- no anchor"): no anchors')
    assert out[1].startswith('line 12 ("- past the end @2:00:00"): @2:00:00: 2:00:00 is outside')
    assert out[2] == "refused: 2 problem(s)"


@pytest.mark.parametrize(
    ("data", "says"),
    [(None, "no file"), (b"---\nby: \xe9\n---\n", "isn't UTF-8")],
)
def test_put_a_file_that_can_t_be_read(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str], data: bytes | None, says: str
) -> None:
    path = tmp_path / "draft.md"
    if data is not None:
        path.write_bytes(data)
    assert main(["doc", "put", str(path)]) == 2
    assert says in capsys.readouterr().err


def test_read_a_doc_its_record_and_the_raw(vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["doc", "put", put_file(tmp_path, note(lines=["- Ben: 10 million. @12:08"]))])
    main(["doc", "put", put_file(tmp_path, note("minutes:2026-10-14-call", supersedes=NOTE_ID), "minutes.md")])
    capsys.readouterr()
    assert main(["doc", "read", NOTE_ID]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:3] == [f"{NOTE_ID} rev 1  {NOTE_PATH}", "superseded by minutes:2026-10-14-call", "   1  ---"]
    assert out[-1] == "  14  - Ben: 10 million. @12:08"
    assert main(["doc", "read", "r:calls:rec-0042@12:08", "--view", "both"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "minutes:2026-10-14-call rev 1 line 15: - Ben put the consultant at 10 million forints. @12:08"
    assert out[1] == "[r:calls:rec-0042@12:08]  recording 2026-10-14 09:30Z U0ADA"
    assert out[2] == "  12:08  Ben Sample: So ten million, for the whole quarter."
    assert main(["doc", "read", NOTE_ID, "--rev", "1", "--view", "raw"]) == 0
    assert "  00:00  Ben Sample: Morning, Cleo." in capsys.readouterr().out


def test_read_says_when_no_doc_points_at_a_record_and_prints_text(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["doc", "read", "chat:C0TEST/1791000003.000100", "--view", "both"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "no current doc points at it yet"
    assert out[1].endswith("(2 versions; this is the latest)")
    assert out[2] == "  thread message 3, edited"
    assert main(["doc", "read", "calls:nothing"]) == 2


def test_verify_prints_each_line_its_problems_and_its_raw(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["doc", "put", put_file(tmp_path, note(lines=["- Ben: 10 million. @12:08", "- Cleo. @14:30-14:52"]))])
    hand = vault.root / NOTE_PATH
    hand.write_text(hand.read_text(encoding="utf-8") + "- a line added by hand\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["doc", "verify", NOTE_ID, "--line", "14", "--line", "16", "--window", "5"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"{NOTE_ID} rev 1",
        "  14  - Ben: 10 million. @12:08",
        "        @12:08  recording 2026-10-14 09:30Z U0ADA",
        "          12:08  Ben Sample: So ten million, for the whole quarter.",
        "  16  - a line added by hand",
        "        ! no anchors at the end of the line",
    ]
    assert main(["doc", "verify", NOTE_ID]) == 0
    assert "  15  - Cleo. @14:30-14:52" in capsys.readouterr().out


def test_verify_shows_a_span_that_doesnt_resolve(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    path = vault.root / "processed" / "brief" / "gone.md"
    path.parent.mkdir(parents=True)
    path.write_text(note("brief:gone", covers=["calls:gone"], lines=["- gone [r:calls:gone@1:00]"]), encoding="utf-8")
    assert main(["doc", "verify", "brief:gone"]) == 0
    assert capsys.readouterr().out.splitlines()[-2:] == [
        "        [r:calls:gone@1:00]",
        "          ! calls:gone isn't in the raw log",
    ]


def test_control_characters_are_printed_escaped(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    sneaky = make_record(9, text="paste this\x1b]52;c;ZXZpbA==\x07 and\x9b2J this\ttab\nnext")
    vault.write_run("chat", [sneaky], run_at=at(12))
    assert main(["doc", "read", sneaky.rid, "--view", "raw"]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "\x9b" not in out
    assert "paste this\\x1b]52;c;ZXZpbA==\\x07 and\\x9b2J this\ttab" in out
    assert "  next" in out
    assert printable("ok\x7f\x00\n") == "ok\\x7f\\x00\n"


def test_the_doc_commands_speak_utf8_through_a_pipe(vault: Vault) -> None:
    env = {**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": ""}
    text = note(lines=["- Árvíztűrő tükörfúrógép, said Ben. @12:08"])
    put = subprocess.run(
        [sys.executable, "-m", "docket", "doc", "put", "-"],
        input=text.encode("utf-8"),
        capture_output=True,
        env=env,
        check=False,
    )
    assert put.returncode == 0, put.stderr.decode("utf-8", "replace")
    read = subprocess.run(
        [sys.executable, "-m", "docket", "doc", "read", NOTE_ID], capture_output=True, env=env, check=False
    )
    assert read.returncode == 0
    assert "Árvíztűrő tükörfúrógép" in read.stdout.decode("utf-8")
    assert REC in read.stdout.decode("utf-8")
