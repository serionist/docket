"""Releasing held recordings on temp projects, with an invented mapper for an invented `rec` source: show writes
nothing; a release asks nothing, commits its log line and then the hold's removal, and stages the recording cut;
whatever fails a check stays held while the batch goes on. Then `docket hold list`, `show` and `release` end to end."""

from __future__ import annotations

import ast
import inspect
import json
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import typer
from docket_testkit import COMMIT_CALLS, FakePrompt, copy_project, git_calls, sh, template_project

from docket import console
from docket.cli import app, main
from docket.errors import DocketError
from docket.sources import release as releasing
from docket.sources.release import Proposal, load_proposals, show, show_lines
from docket.vault import holds, releaselog
from docket.vault.config import VaultConfig
from docket.vault.cuts import CUT_MARKER
from docket.vault.gate import Gate
from docket.vault.holds import HOLDS
from docket.vault.records import RawRecord
from docket.vault.releaselog import RELEASES, LoggedCut, Release
from docket.vault.vault import Vault

# Each file is one recording: the recording, its transcript when it has segments, Plaud-like notes when it has a
# summary. A held id is skipped before anything of it is read, as the Plaud example does.
MAPPER = """\
def map_run(run):
    held = set(run["holds"])
    out = {"contract": 1, "records": [], "set_aside": [], "waiting": [], "held": []}
    for capture in run["captures"]:
        item = capture["result"]
        if not isinstance(item, dict) or "id" not in item:
            continue
        native = item["id"]
        if native in held:
            out["held"].append(native)
            continue
        base = {"source": "rec", "container": "rec:" + native, "ts": "2026-10-14T09:30:00Z",
                "fetched": capture["captured"], "via": "rec-file"}
        out["records"].append({**base, "rid": "rec:" + native, "kind": "recording", "title": item.get("title")})
        if item.get("segments"):
            out["records"].append({**base, "rid": "rec:" + native + "/transcript", "kind": "transcript",
                                   "segments": item["segments"]})
        if item.get("second"):
            out["records"].append({**base, "rid": "rec:" + native + "/second", "kind": "transcript",
                                   "segments": item["second"]})
        if item.get("summary"):
            out["records"].append({**base, "rid": "rec:" + native + "/summary", "kind": "vendor-summary",
                                   "text": item["summary"]})
    return out
"""


def said(start: int, end: int, speaker: str, text: str) -> dict[str, Any]:
    return {"start_ms": start * 1000, "end_ms": end * 1000, "speaker": speaker, "text": text}


# Ben talks over Ada at 0:18, so a cut at 0:12 widens past her line into his, and on to Ada's next.
TALK = [
    said(0, 10, "Ben Sample", "Morning, Ada."),
    said(10, 20, "Ada Example", "First, my weekend plans."),
    said(18, 30, "Ben Sample", "The cabin by the lake?"),
    said(30, 40, "Ada Example", "Yes, from Saturday."),
    said(40, 50, "Ben Sample", "Now the offer: ten million."),
    # Nobody speaks from 0:50 to 1:00.
    said(60, 70, "Ada Example", "Send it by Friday."),
]
WEEKEND = ("weekend", "cabin", "Saturday")
TITLE = "Supplier sync \x1b]52;c;aGk=\x07"
SUMMARY = "Weekend plans at a cabin, then the offer."


def recording(native: str, **fields: Any) -> dict[str, Any]:
    return {"id": native, "title": TITLE, "segments": TALK, "summary": SUMMARY, **fields}


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_project(tmp_path_factory, {"rec": MAPPER})


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return copy_project(template, tmp_path)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """An invented backfill: two held recordings, one without a transcript, and one that isn't held."""
    out = tmp_path / "backfill"
    out.mkdir()
    files = {
        "rec-cut-01": recording("rec-cut-01"),
        "rec-cut-02": recording("rec-cut-02"),
        "rec-cut-03": recording("rec-cut-03", segments=[]),
        "rec-open-04": recording("rec-open-04"),
    }
    for native, content in files.items():
        (out / f"{native}.json").write_text(json.dumps(content), encoding="utf-8")
    (out / "hold.txt").write_text("rec-cut-01\nrec-cut-02\nrec-cut-03\n", encoding="utf-8")
    return out


def ingest(vault: Vault, folder: Path) -> None:
    assert main(["ingest", "rec", str(folder), "--vault", str(vault.root)]) == 0


def subjects(vault: Vault) -> list[str]:
    return sh(vault.root, "git", "log", "--format=%s").splitlines()


def snapshot(vault: Vault) -> tuple[str, str, frozenset[str], dict[str, str]]:
    root = vault.root / "staging"
    files = {p.relative_to(root).as_posix(): p.read_text("utf-8") for p in root.rglob("*") if p.is_file()}
    head = sh(vault.root, "git", "rev-parse", "HEAD").strip()
    return head, sh(vault.root, "git", "status", "--porcelain"), holds.read(vault.root, "rec"), files


def staged(vault: Vault) -> dict[str, tuple[str, str | None, RawRecord]]:
    return {
        e.staged.record.rid: (e.staged.state, e.staged.classified_by, e.staged.record)
        for e in Gate(vault).list().entries
    }


def cut_01() -> Proposal:
    return Proposal("rec-cut-01", ("0:12-0:35",), ("rec:rec-cut-01",), note="the weekend plans")


def test_while_held_an_ingest_stages_only_the_unheld_recording(vault: Vault, folder: Path) -> None:
    ingest(vault, folder)
    assert {rid.split("/")[0] for rid in staged(vault)} == {"rec:rec-open-04"}
    assert holds.read(vault.root, "rec") == {"rec-cut-01", "rec-cut-02", "rec-cut-03"}


def test_show_prints_the_held_recording_with_its_times_and_writes_nothing(vault: Vault, folder: Path) -> None:
    ingest(vault, folder)
    before = snapshot(vault)
    lines = show_lines(show(vault, "rec", "rec-cut-01", folder))
    assert snapshot(vault) == before
    assert lines[0] == "rec:rec-cut-01  recording"
    assert lines[1] == f"  title     {TITLE}"
    assert "rec:rec-cut-01/transcript  transcript  0:00-1:10, 6 utterances" in lines
    assert "  [0:18-0:30] Ben Sample: The cabin by the lake?" in lines
    assert f"  text      {SUMMARY}" in lines
    with pytest.raises(DocketError, match="rec-open-04 isn't held"):
        show(vault, "rec", "rec-open-04", folder)
    holds.add(vault, "rec", ["rec-elsewhere"])
    with pytest.raises(DocketError, match="rec-elsewhere isn't in"):
        show(vault, "rec", "rec-elsewhere", folder)


def test_show_names_what_intake_drops_without_printing_it(vault: Vault, folder: Path) -> None:
    ingest(vault, folder)
    config = vault.root / "vault.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("  keywords: []", "  keywords:\n  - cabin"), encoding="utf-8"
    )
    lines = show_lines(show(vault, "rec", "rec-cut-01", folder))
    assert "rec:rec-cut-01/transcript  transcript  dropped at intake: never-store" in lines
    assert not any("cabin" in line for line in lines)


def test_a_release_asks_nothing_and_from_claude_s_shell_its_commits_say_so(
    vault: Vault, folder: Path, os_prompt: Callable[..., FakePrompt], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release runs on the owner's yes, given before the command (DR-0600): no prompt and no code, in Claude Code's
    shell too, and its two commits say Claude ran it (DR-0599)."""
    ingest(vault, folder)
    hello = os_prompt()
    monkeypatch.setenv("CLAUDECODE", "1")
    report = releasing.release(vault, "rec", folder, [cut_01()])
    assert report.released == ["rec-cut-01"]
    assert hello.acts == []
    assert sh(vault.root, "git", "log", "-2", "--format=%B").count(console.BY_CLAUDE) == 2
    assert "rec-cut-01" not in holds.read(vault.root, "rec")


def test_a_confirmed_release_commits_the_log_then_the_hold_and_stages_the_recording_cut(
    vault: Vault, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ingest(vault, folder)
    capsys.readouterr()
    report = releasing.release(vault, "rec", folder, [cut_01()])
    assert report.released == ["rec-cut-01"]
    out = capsys.readouterr().out
    # The screen: the note, the widened cut and its edges, what is left out, and the kept record read in full.
    assert "  note        the weekend plans" in out
    assert "    cut 0:12-0:35, widened to 0:10-0:40, removes 3 utterances" in out
    assert "        0:00-0:10  Ben Sample: Morning, Ada." in out
    assert "      - 0:18-0:30  Ben Sample: The cabin by the lake?" in out
    assert "        0:40-0:50  Ben Sample: Now the offer: ten million." in out
    assert "  vendor-summary rec:rec-cut-01/summary: left out" in out
    assert "  recording   rec:rec-cut-01: kept as it reads here" in out
    assert "    title     Supplier sync \\x1b]52;c;aGk=\\x07" in out
    assert "\x1b" not in out
    # Two commits, the log's first, naming no id; the hold off the list.
    assert subjects(vault)[:2] == ["Holds: rec, 1 released on test-box", "Releases: rec, 1 added on test-box"]
    assert not any("rec-cut" in s for s in subjects(vault)[:2])
    assert holds.read(vault.root, "rec") == {"rec-cut-02", "rec-cut-03"}
    [line] = releaselog.read(vault.root, "rec")["rec-cut-01"].lines
    assert [(c.start, c.end) for c in line.cuts] == [(10_000, 40_000)]
    view = Gate(vault).intake_view(RawRecord.model_validate(
        {"rid": "rec:rec-cut-01", "source": "rec", "kind": "recording", "container": "rec:rec-cut-01",
         "ts": "2026-10-14T09:30:00Z", "title": TITLE, "fetched": datetime.now(UTC), "via": "rec-file"}
    ))  # fmt: skip
    assert isinstance(view, RawRecord)
    assert [(k.rid, k.v) for k in line.keep] == [("rec:rec-cut-01", view.v)]
    # Staged ready by the user, already cut; the notes left out.
    got = staged(vault)
    assert "rec:rec-cut-01/summary" not in got
    state, by, transcript = got["rec:rec-cut-01/transcript"]
    assert (state, by, transcript.fidelity) == ("ready", "user", "cut")
    assert not any(word in transcript.model_dump_json() for word in WEEKEND)
    assert got["rec:rec-cut-01"][:2] == ("ready", "user")
    # After promotion the stored transcript has no segment inside a cut but the marker.
    Gate(vault).promote()
    stored = {r.rid: r for _, _, r in vault.rawlog.iter_records()}
    inside = [
        s for s in stored["rec:rec-cut-01/transcript"].segments or [] if s.start_ms < 40_000 and s.end_ms > 10_000
    ]
    assert [s.text for s in inside] == [CUT_MARKER]
    assert "rec:rec-cut-01/summary" not in stored


def test_a_release_makes_these_git_calls(vault: Vault, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every git call of one release, in order (#132): a call added later fails here with its name."""
    ingest(vault, folder)
    opened = Vault.open(vault.root)
    with git_calls(monkeypatch) as calls:
        assert releasing.release(opened, "rec", folder, [cut_01()]).released == ["rec-cut-01"]
    hold, log = "-- state/holds/rec.txt", "-- state/releases/rec.jsonl"
    assert calls == [
        # A conflict refuses before the screen, read from the index file (DR-0765); then the hold file kept as
        # committed, HEAD's .gitattributes from the batch reader (DR-0766), the mapper found in the project (asked of
        # git at the ingest, DR-0767), the log's first commit.
        f"ls-files -z --others {hold}",
        f"diff --name-only -z HEAD {hold}",
        "cat-file --batch",
        f"ls-files -z --others {log}",
        *COMMIT_CALLS,
        # The hold's removal, after a commit moved HEAD: asked again, the batch reader still open for .gitattributes.
        f"ls-files -z --others {log}",
        f"diff --name-only -z HEAD {log}",
        f"ls-files -z --others {hold}",
        f"diff --name-only -z HEAD {hold}",
        *COMMIT_CALLS,
    ]


def test_a_release_during_a_conflict_refuses_before_any_screen(vault: Vault, folder: Path) -> None:
    # An unmerged path, as a stash pop that conflicts leaves one: stages 1 to 3 and no *_HEAD file. The release's
    # commits would refuse it only after its screen and its log line on disk (#132, DR-0765).
    ingest(vault, folder)
    rel = "conflict.txt"
    blob = sh(vault.root, "git", "hash-object", "-w", "--", "vault.yaml").strip()
    # Paths from the top of the vault's repo, sent as bytes: text mode on Windows would end each line with a CR.
    info = "".join(f"100644 {blob} {n}\t{vault.home.prefix}{rel}\n" for n in (1, 2, 3))
    proc = subprocess.run(
        ["git", "update-index", "--index-info"], cwd=vault.home.top, input=info.encode("utf-8"), check=False
    )
    assert proc.returncode == 0
    assert sh(vault.root, "git", "ls-files", "-u")
    before = snapshot(vault), releaselog.read(vault.root, "rec")
    with pytest.raises(DocketError, match=f"{rel} in .* has a conflict; resolve it before Docket writes"):
        releasing.release(Vault.open(vault.root), "rec", folder, [cut_01()])
    assert (snapshot(vault), releaselog.read(vault.root, "rec")) == before


def test_what_fails_a_check_stays_held_and_writes_nothing_while_the_batch_goes_on(vault: Vault, folder: Path) -> None:
    ingest(vault, folder)
    # A recording imported before its hold arrived is in raw, uncut.
    (folder / "rec-raw-05.json").write_text(json.dumps(recording("rec-raw-05")), encoding="utf-8")
    ingest(vault, folder)
    gate = Gate(vault)
    for entry in gate.list(source="rec").entries:
        gate.classify(entry.key, "work")
    gate.promote()
    holds.add(vault, "rec", ["rec-raw-05", "rec-missing"])
    proposals = [
        Proposal("rec-cut-02"),
        Proposal("rec-cut-02", ("120-340",)),
        Proposal("rec-cut-02", ("2:00-2:30",)),
        Proposal("rec-cut-02", ("0:51-0:58",)),
        Proposal("rec-cut-02", ("1:2-3",)),
        Proposal("rec-cut-02", ("0:12-0:35",), ("rec:rec-cut-02/transcript",)),
        Proposal("rec-cut-02", ("0:12-0:35",), ("rec:rec-cut-01",)),
        Proposal("rec-open-04", ("0:12-0:35",)),
        Proposal("rec-missing", ("0:12-0:35",)),
        Proposal("rec-cut-03", ("0:12-0:35",)),
        Proposal("rec-raw-05", ("0:12-0:35",)),
        Proposal("rec-cut-02", held="the words match nothing in it"),
        cut_01(),
    ]
    report = releasing.release(vault, "rec", folder, proposals)
    assert report.released == ["rec-cut-01"]
    reasons = [why for _, why in report.held]
    assert reasons == [
        "no cut: a held recording goes in only with its left-out stretches cut",
        "cut 120-340 is characters; a transcript is cut by time, such as 12:30-14:05",
        "cut 2:00-2:30 starts after the transcript ends (1:10)",
        "cut 0:51-0:58 falls between utterances and removes none",
        "cut 1:2-3: a cut's times are mm:ss or hh:mm:ss, and both ends the same kind: '1:2-3'",
        "keep rec:rec-cut-02/transcript: a transcript is cut, not kept",
        "keep rec:rec-cut-01: not a record of rec-cut-02 in this folder",
        "not in this folder",
        "no transcript to cut by time",
        "a record under it is already in raw, uncut, so it stays held until docket forget",
        "the words match nothing in it",
    ]
    assert report.refused == [("rec-open-04", "not held in state/holds/rec.txt")]
    assert holds.read(vault.root, "rec") == {"rec-cut-02", "rec-cut-03", "rec-raw-05", "rec-missing"}
    assert set(releaselog.read(vault.root, "rec")) == {"rec-cut-01"}


def test_a_keep_intake_drops_stays_held(vault: Vault, folder: Path) -> None:
    ingest(vault, folder)
    config = vault.root / "vault.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("  keywords: []", "  keywords:\n  - supplier"), encoding="utf-8"
    )
    report = releasing.release(vault, "rec", folder, [cut_01()])
    assert report.held == [("rec-cut-01", "keep rec:rec-cut-01: intake drops it (never-store)")]


def test_a_hold_or_log_that_changes_after_the_screen_stops_that_id(
    vault: Vault, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ingest(vault, folder)
    shown = releasing._shown

    def meanwhile(gate: Gate, checked: Any) -> Any:
        # Another machine's change arrives after the screen: the hold is gone from the list.
        (vault.root / HOLDS / "rec.txt").write_text("rec-cut-02\nrec-cut-03\n", encoding="utf-8")
        return shown(gate, checked)

    monkeypatch.setattr(releasing, "_shown", meanwhile)
    report = releasing.release(vault, "rec", folder, [cut_01()])
    assert report.held == [("rec-cut-01", "its hold or release changed since the screen; run the release again")]
    assert releaselog.read(vault.root, "rec") == {}
    assert not any(s.startswith("Releases:") for s in subjects(vault))


def test_a_line_left_uncommitted_is_committed_and_a_rerun_only_takes_the_hold_off(vault: Vault, folder: Path) -> None:
    ingest(vault, folder)
    # A crash after the line was written and before its commit: held and released on disk.
    line = Release(id="rec-cut-01", cuts=[LoggedCut(start=10_000, end=40_000)], at=datetime.now(UTC), machine="m")
    path = vault.root / RELEASES / "rec.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(line.line() + "\n", encoding="utf-8")
    report = releasing.release(vault, "rec", folder, [cut_01()])
    assert report.taken_off == ["rec-cut-01"]
    assert subjects(vault)[:2] == ["Holds: rec, 1 released on test-box", "Releases: rec, the log committed on test-box"]
    assert len(releaselog.read(vault.root, "rec")["rec-cut-01"].lines) == 1
    assert staged(vault)["rec:rec-cut-01/transcript"][:2] == ("pending", None)


def test_a_hold_brought_back_comes_off_with_no_new_line(
    vault: Vault, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ingest(vault, folder)
    releasing.release(vault, "rec", folder, [cut_01()])
    (vault.root / HOLDS / "rec.txt").write_text("rec-cut-01\nrec-cut-02\n", encoding="utf-8")
    capsys.readouterr()
    report = releasing.release(vault, "rec", folder, [cut_01()])
    assert report.taken_off == ["rec-cut-01"]
    assert "rec-cut-01" not in holds.read(vault.root, "rec")
    assert "and held again: a merge brought the hold back" in capsys.readouterr().out
    assert len(releaselog.read(vault.root, "rec")["rec-cut-01"].lines) == 1


def test_released_and_off_the_hold_with_nothing_staged_is_staged_again(vault: Vault, folder: Path) -> None:
    ingest(vault, folder)
    # A crash after the hold's commit, before the records were staged.
    line = Release(id="rec-cut-01", cuts=[LoggedCut(start=10_000, end=40_000)], at=datetime.now(UTC), machine="m")
    releaselog.add(vault, "rec", line)
    holds.remove(vault, "rec", ["rec-cut-01"])
    report = releasing.release(vault, "rec", folder, [cut_01(), Proposal("rec-cut-09", ("0:12-0:35",))])
    assert report.restaged == ["rec-cut-01"]
    assert staged(vault)["rec:rec-cut-01/transcript"][:2] == ("pending", None)
    empty = folder.parent / "empty"
    empty.mkdir()
    report = releasing.release(vault, "rec", empty, [cut_01()])
    assert report.refused == [("rec-cut-01", "released already, and not in this folder")]


@pytest.mark.parametrize(
    "case",
    [
        (f"{RELEASES}/rec.jsonl", "not a release\n", "isn't a release"),
        (f"{HOLDS}/rec.txt", "two words\n", "isn't an id"),
    ],
)
def test_a_log_or_list_that_doesn_t_read_is_refused_before_it_is_committed(
    vault: Vault, folder: Path, case: tuple[str, str, str]
) -> None:
    rel, broken, message = case
    ingest(vault, folder)
    head = sh(vault.root, "git", "rev-parse", "HEAD")
    path = vault.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(DocketError, match=message):
        releasing.release(vault, "rec", folder, [cut_01()])
    assert sh(vault.root, "git", "rev-parse", "HEAD") == head


def test_a_disabled_source_stops_before_the_screen(vault: Vault, folder: Path) -> None:
    ingest(vault, folder)
    path = vault.root / "vault.yaml"
    config = VaultConfig.load(path)
    config.sources["rec"] = config.sources["rec"].model_copy(update={"enabled": False})
    path.write_text(config.dump(), encoding="utf-8")
    before = snapshot(vault)
    with pytest.raises(DocketError, match=r"'rec' is disabled in vault\.yaml"):
        releasing.release(vault, "rec", folder, [cut_01()])
    assert snapshot(vault) == before


def test_show_refuses_an_id_released_already_and_held_again(vault: Vault, folder: Path) -> None:
    ingest(vault, folder)
    releasing.release(vault, "rec", folder, [cut_01()])
    (vault.root / HOLDS / "rec.txt").write_text("rec-cut-01\nrec-cut-02\n", encoding="utf-8")
    with pytest.raises(DocketError, match="rec-cut-01 was released already and is held again") as e:
        show(vault, "rec", "rec-cut-01", folder)
    assert "docket hold release rec rec-cut-01 --from <folder>" in str(e.value)


def test_only_the_versions_the_owner_was_shown_are_classified_by_them(vault: Vault, folder: Path) -> None:
    # A version of the transcript staged before its hold arrived, as on another machine: never on this screen.
    variant = RawRecord.model_validate(
        {
            "rid": "rec:rec-cut-01/transcript",
            "source": "rec",
            "kind": "transcript",
            "container": "rec:rec-cut-01",
            "ts": "2026-10-14T09:30:00Z",
            "segments": [*TALK[:-1], said(60, 70, "Ada Example", "Send it by Monday.")],
            "fetched": datetime.now(UTC),
            "via": "rec-file",
        }
    )
    Gate(vault).stage([variant])
    holds.add(vault, "rec", ["rec-cut-01"])
    assert releasing.release(vault, "rec", folder, [cut_01()]).released == ["rec-cut-01"]
    versions = sorted(
        (e.staged.state, e.staged.classified_by)
        for e in Gate(vault).list().entries
        if e.staged.record.rid == "rec:rec-cut-01/transcript"
    )
    assert versions == [("pending", None), ("ready", "user")]


@pytest.mark.parametrize(
    ("second", "keyword"),
    [
        # A second transcript that ends before the cut starts: the gate leaves it out, never half cut.
        ([said(0, 5, "Ben Sample", "Morning.")], None),
        # One never-store drops at intake: shown as dropped, and never staged.
        ([said(0, 10, "Ben Sample", "Morning, zebrafinch."), said(10, 70, "Ada Example", "The rest.")], "zebrafinch"),
    ],
)
def test_a_transcript_the_gate_won_t_stage_is_not_classified_and_the_release_goes_on(
    vault: Vault, tmp_path: Path, second: list[dict[str, Any]], keyword: str | None
) -> None:
    folder = tmp_path / "two"
    folder.mkdir()
    (folder / "rec-two-06.json").write_text(json.dumps(recording("rec-two-06", second=second)), encoding="utf-8")
    (folder / "hold.txt").write_text("rec-two-06\n", encoding="utf-8")
    if keyword:
        path = vault.root / "vault.yaml"
        config = VaultConfig.load(path)
        config.never_store.keywords.append(keyword)
        path.write_text(config.dump(), encoding="utf-8")
    ingest(vault, folder)
    proposal = Proposal("rec-two-06", ("0:12-0:35",), ("rec:rec-two-06",))
    assert releasing.release(vault, "rec", folder, [proposal]).released == ["rec-two-06"]
    got = staged(vault)
    assert "rec:rec-two-06/second" not in got
    assert got["rec:rec-two-06/transcript"][:2] == ("ready", "user")


def test_show_needs_a_folder(vault: Vault, folder: Path) -> None:
    ingest(vault, folder)
    with pytest.raises(DocketError, match="isn't a folder"):
        show(vault, "rec", "rec-cut-01", folder / "missing")


def test_a_long_cut_shows_its_edges_and_a_count_and_a_dropped_record_is_named(
    vault: Vault, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ingest(vault, folder)
    config = vault.root / "vault.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("  keywords: []", "  keywords:\n  - supplier"), encoding="utf-8"
    )
    capsys.readouterr()
    releasing.release(vault, "rec", folder, [Proposal("rec-cut-01", ("0:05-1:05",))])
    out = capsys.readouterr().out
    assert "  recording   rec:rec-cut-01: dropped at intake (never-store)" in out
    assert "    cut 0:05-1:05, widened to 0:00-1:10, removes 6 utterances" in out
    assert "      - ... 2 more" in out
    assert "      - 1:00-1:10  Ada Example: Send it by Friday." in out
    assert "Supplier" not in out


def test_only_the_command_line_releases() -> None:
    assert list(inspect.signature(releasing.release).parameters) == ["vault", "source", "folder", "proposals"]
    src = Path(releasing.__file__).resolve().parents[1]
    callers = sorted(
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if p.name != "release.py" and re.search(r"sources(\.| import )release\b", p.read_text(encoding="utf-8"))
    )
    assert callers == ["cli.py"]


def test_only_the_release_writes_a_release_line_or_takes_a_hold_off() -> None:
    """No page, route or tool releases (DR-0382): only the release writes the log and takes a hold off."""
    src = Path(releasing.__file__).resolve().parents[1]
    writers: dict[str, set[str]] = {}
    for path in src.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            owner = node.func.value
            name = f"{owner.id}.{node.func.attr}" if isinstance(owner, ast.Name) else ""
            if name in ("releaselog.add", "holds.remove"):
                writers.setdefault(name, set()).add(path.relative_to(src).as_posix())
    assert writers == {"releaselog.add": {"sources/release.py"}, "holds.remove": {"sources/release.py"}}


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_proposals_file_is_read_from_outside_any_repo(tmp_path: Path) -> None:
    path = write(
        tmp_path / "loose" / "proposals.yaml",
        "# Proposed by Claude.\nsource: rec\nrecordings:\n"
        '  - id: rec-cut-01\n    cuts: ["0:12-0:35"]\n    keep: [rec:rec-cut-01]\n    note: the weekend\n'
        "  - id: rec-cut-03\n    held: no transcript\n",
    )
    assert load_proposals(path, "rec") == [
        Proposal("rec-cut-01", ("0:12-0:35",), ("rec:rec-cut-01",), "the weekend"),
        Proposal("rec-cut-03", held="no transcript"),
    ]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("source: rec\nrecordings:\n  - id: r1\n    cuts: [12:30]\n", 'write each time quoted, such as "12:30-14:05"'),
        (
            "source: rec\nrecordings:\n  - id: r1\n    cuts: ['0:01-0:02']\n    why: x\n",
            "a key a proposal doesn't know",
        ),
        ("source: other\nrecordings:\n  - id: r1\n    held: x\n", "proposes for another source than rec"),
        ("source: rec\nrecordings:\n  - id: r1\n", "give it cuts to release, or held"),
        ("source: rec\nrecordings:\n  - id: r1\n    cuts: []\n    held: x\n", "give it cuts to release, or held"),
        ("source: rec\nrecordings:\n  - cuts: []\n", "recording 1 has no id"),
        ("source: rec\nrecordings:\n  - id: r1\n    held: [x]\n", "held is text"),
        ("source: rec\nrecordings:\n  - id: r1\n    held: x\n    keep: rec:r1\n", "keep is a list of text"),
        ("source: rec\nrecordings: []\n", "recordings is a list"),
        ("source: rec\nplan: x\n", "source and recordings, and nothing else"),
        ("source: rec\nsource: rec\n", "can't read it as YAML"),
    ],
)
def test_a_proposals_file_that_isn_t_one_is_refused(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(DocketError, match=re.escape(message)):
        load_proposals(write(tmp_path / "loose" / "proposals.yaml", text), "rec")


def test_a_proposals_file_inside_a_git_work_tree_is_refused(vault: Vault) -> None:
    for where in (vault.root.parent / "notes" / "proposals.yaml", vault.root / "proposals.yaml"):
        path = write(where, "source: rec\nrecordings:\n  - id: r1\n    held: x\n")
        with pytest.raises(DocketError, match="inside a git work tree, where a commit could take it"):
            load_proposals(path, "rec")


# -- the command line --------------------------------------------------------------------------------------------------


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


def test_list_prints_ids_only_and_marks_a_hold_brought_back(
    vault: Vault, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = str(vault.root)
    assert run(capsys, "hold", "list", "--vault", root)[1] == "nothing held\n"
    assert main(["ingest", "rec", str(folder), "--vault", root]) == 0
    assert (
        main(["hold", "release", "rec", "rec-cut-01", "--from", str(folder), "--cut", "0:12-0:35", "--vault", root])
        == 0
    )
    (vault.root / HOLDS / "rec.txt").write_text("rec-cut-01\nrec-cut-02\n", encoding="utf-8")
    code, out, _ = run(capsys, "hold", "list", "rec", "--vault", root)
    assert code == 0
    assert out == "rec  rec-cut-01  held again after its release\nrec  rec-cut-02\n"
    assert TITLE not in out
    assert run(capsys, "hold", "list", "calls", "--vault", root)[1] == "nothing held\n"
    code, _, err = run(capsys, "hold", "list", "Rec", "--vault", root)
    assert code == 2
    assert "isn't a source name" in err


def test_show_prints_the_recording_escaped(vault: Vault, folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ingest", "rec", str(folder), "--vault", str(vault.root)]) == 0
    code, out, _ = run(capsys, "hold", "show", "rec", "rec-cut-01", "--from", str(folder), "--vault", str(vault.root))
    assert code == 0
    assert "  title     Supplier sync \\x1b]52;c;aGk=\\x07" in out
    assert "\x1b" not in out
    assert "  [0:10-0:20] Ada Example: First, my weekend plans." in out


def test_release_on_the_command_line_releases_and_says_what_comes_next(
    vault: Vault, folder: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = str(vault.root)
    assert main(["ingest", "rec", str(folder), "--vault", root]) == 0
    args = [
        "hold",
        "release",
        "rec",
        "rec-cut-01",
        "--from",
        str(folder),
        "--cut",
        "0:12-0:35",
        "--keep",
        "rec:rec-cut-01",
    ]
    code, out, _ = run(capsys, *args, "--vault", root)
    assert code == 0
    assert "released   rec-cut-01" in out
    assert "next       docket staging promote" in out
    assert "rec-cut-01" not in holds.read(vault.root, "rec")
    code, out, _ = run(capsys, *args, "--vault", root)
    assert "staged     rec-cut-01  released already" in out
    code, out, _ = run(capsys, "hold", "release", "rec", "rec-open-04", "--from", str(folder), "--vault", root)
    assert out == "refused    rec-open-04  not held in state/holds/rec.txt\n"


def test_release_takes_a_proposals_file_or_an_id_never_both(
    vault: Vault, folder: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = str(vault.root)
    assert main(["ingest", "rec", str(folder), "--vault", root]) == 0
    proposals = tmp_path / "loose" / "proposals.yaml"
    proposals.parent.mkdir()
    proposals.write_text(
        "source: rec\nrecordings:\n"
        "  - id: rec-cut-01\n    cuts: ['0:12-0:35']\n    keep: [rec:rec-cut-01]\n    note: the weekend plans\n"
        "  - id: rec-cut-03\n    held: no transcript to cut by time\n",
        encoding="utf-8",
    )
    base = ["hold", "release", "rec", "--from", str(folder), "--vault", root]
    code, _, err = run(capsys, *base, "rec-cut-01", "--proposals", str(proposals))
    assert (code, "not both" in err) == (2, True)
    code, _, err = run(capsys, *base)
    assert (code, "name the held id to release, or give --proposals" in err) == (2, True)
    code, out, _ = run(capsys, *base, "--proposals", str(proposals))
    assert "  note        the weekend plans" in out
    assert out.splitlines()[-3:] == [
        "released   rec-cut-01",
        "held       rec-cut-03  no transcript to cut by time",
        "next       docket staging promote",
    ]
    proposals.write_text("source: rec\nrecordings:\n  - id: rec-cut-02\n    held: later\n", encoding="utf-8")
    assert run(capsys, *base, "--proposals", str(proposals))[1] == "held       rec-cut-02  later\n"


def test_no_option_skips_the_question() -> None:
    command = typer.main.get_command(app)
    hold = command.commands["hold"]  # type: ignore[attr-defined]
    params = {p.name for p in hold.commands["release"].params}
    assert params == {"source", "native", "from_", "cut", "keep", "proposals", "vault"}
    assert not any(re.search(r"yes|force|confirm|by", name or "") for name in params)
