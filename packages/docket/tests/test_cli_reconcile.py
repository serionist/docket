"""`docket reconcile spots <rid> [--json]` and `docket reconcile list` (#183): a recording's spots and the recordings
waiting, read from raw and written nowhere. The call is invented (`reconcile_kit.py`)."""

from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import copy_vault, git
from reconcile_kit import REC, TRANSCRIPT, WHISPER, reconcile_template

from docket import render
from docket.cli import main
from docket.media import staged as media_staged
from docket.reconcile import spots
from docket.reconcile.spots import Fix
from docket.vault.vault import Vault


@pytest.fixture
def vault(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Vault:
    return copy_vault(reconcile_template(tmp_path_factory), tmp_path)


def run(vault: Vault, capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main([*args, "--vault", str(vault.root)])
    out, err = capsys.readouterr()
    return code, out, err


def test_spots_prints_the_pair_the_people_and_each_spot_with_both_lines(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    head = git(vault.root, "rev-parse", "HEAD")
    code, out, err = run(vault, capsys, "reconcile", "spots", REC)
    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[0] == f"{REC}: {TRANSCRIPT} beside {WHISPER}"
    assert lines[1].startswith("doc        transcript-fixes:2026-10-15-") and lines[1].endswith(", none yet")
    assert "people     ben-sample (person) Ben Sample" in lines
    assert "           northwind (customer) Northwind; Nortwin (asr), Northwind Traders" in lines
    name = next(i for i, line in enumerate(lines) if line.startswith("name "))
    assert lines[name].endswith('  "Nortwin" / "Northwood"  near northwind')
    assert lines[name + 1] == (
        f"           [r:{TRANSCRIPT}@00:00-00:05] Ben Sample: Morning, Rita. The offer from Nortwin came on Monday."
    )
    assert lines[name + 2] == (
        f"           [r:{WHISPER}@00:00-00:05] Whisper: Morning, Rita. The offer from Northwood came on Monday"
    )
    assert [line.split()[0] for line in lines if line.split()[0] in spots.KINDS] == ["name", "number", "word"]
    # It writes nothing.
    assert git(vault.root, "rev-parse", "HEAD") == head
    assert git(vault.root, "status", "--porcelain") == ""


def test_spots_json_holds_every_field(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(vault, capsys, "reconcile", "spots", WHISPER, "--json")
    assert code == 0
    found = json.loads(out)
    assert out.isascii()
    assert set(found) == {"rid", "transcript", "whisper", "doc", "why", "people", "spots", "fixes", "left_out"}
    assert (found["rid"], found["doc"]["rev"], found["why"]) == (REC, None, None)
    assert set(found["spots"][0]) == {
        "id", "kind", "left", "right", "recorder_anchor", "whisper_anchor", "speaker", "line", "heard", "near",
        "rejected",
    }  # fmt: skip
    assert found["left_out"] == {"cap": {"name": 0, "number": 0, "word": 0}, "heard_differently": 0, "never_store": 0}


def test_spots_says_why_a_recording_has_none(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    media_staged.withhold(vault.root, REC)
    code, out, _ = run(vault, capsys, "reconcile", "spots", REC)
    assert code == 0
    assert out.splitlines()[-1].startswith("no spots   its family is kept out: ")


def test_spots_refuses_what_isn_t_a_recording_with_both_transcripts(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(vault, capsys, "reconcile", "spots", "calls:rec-9999")
    assert code == 2
    assert err == "docket: no record calls:rec-9999 is in raw\n"


def test_list_prints_the_recordings_waiting_and_how_many_are_hidden(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(vault, capsys, "reconcile", "list")
    assert code == 0
    assert out.splitlines() == [
        f"{REC}  2026-10-15 00:30  Supplier call",
        "           spots: 1 name, 1 number, 1 word",
    ]
    media_staged.withhold(vault.root, REC)
    code, out, _ = run(vault, capsys, "reconcile", "list")
    assert out.splitlines() == [
        "nothing to reconcile: no recording here has spots no fix decides",
        "hidden     1 recording(s) a never-store list or your decisions keep out",
    ]


def test_the_lines_say_fixes_settled_places_and_what_was_left_out(vault: Vault) -> None:
    found = spots.report(vault, REC)
    first, second = found.spots[0], found.spots[1]
    fixes = [
        Fix(1, first.left, first.right, "Northwind", "rejected", (0, 5), (0, 5)),
        Fix(2, second.left, second.right, "million", "unsure", (6, 11), (6, 11)),
        Fix(3, "gone", "away", "here", "sure", (100, 105), (100, 105)),
        Fix(4, "a", "b", "c", "sure", (0, 5), (300, 305)),
    ]
    found = spots.report(vault, REC, fixes)
    lines = render.spots(found)
    assert any(line.endswith("settled; turned down: Northwind") for line in lines)
    assert 'fix        line 2 unsure: "thousand" / "million" -> "million"; waiting' in lines
    assert 'fix        line 3 sure: "gone" / "away" -> "here"; no longer matching' in lines
    hidden = found.model_copy(update={"fixes": [found.fixes[0].model_copy(update={"left": None, "why": "cut"})]})
    assert "fix        line 1 rejected: its texts not shown; cut" in render.spots(hidden)
    capped = found.model_copy(
        update={
            "spots": [],
            "left_out": spots.LeftOut(cap={"name": 2, "number": 0, "word": 1}, heard_differently=3, never_store=1),
        }
    )
    assert render.spots(capped)[-2:] == [
        "no spots   the two transcripts agree, or every spot is decided",
        "left out   3 past the cap (2 name, 1 word); 3 heard differently; 1 near a never-store value",
    ]
    doc = found.model_copy(update={"doc": spots.DocRef(id="transcript-fixes:2026-10-15-0123456789ab", rev=3)})
    assert render.spots(doc)[1] == "doc        transcript-fixes:2026-10-15-0123456789ab, rev 3"
    waiting = spots.Listing(recordings=[spots.listing(vault).recordings[0].model_copy(update={"waiting": 2})])
    assert render.reconcile(waiting, ZoneInfo("UTC"))[1].endswith("; 2 fix(es) wait for you")
