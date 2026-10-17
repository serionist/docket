"""Released and hand-cut recordings get their audio, cut (DR-0561, DR-0562): the ranges at the put, staged audio whose
cuts grew taken and put again, kind-less decisions confirmed at the owner's console, the timeline checks, the cut log
committed with the media, and the run's media line. Audio is a tone the test makes; records are invented."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import AtConsole, FakePrompt, git
from media_kit import CALL, POLISHED, audio, dbfs, decoded, https_server, in_raw, media_vault, tone

from docket.cli import main
from docket.errors import DocketError
from docket.media import cutlog, cutter, layout, reader
from docket.media import put as media
from docket.media import staged as media_staged
from docket.media.keystore import KeyStore
from docket.media.ranges import Needed
from docket.service.sync import RunOpened, opened_text
from docket.vault import holds, releaselog
from docket.vault.cuts import Cut
from docket.vault.gate import Gate
from docket.vault.records import RawRecord
from docket.vault.releaselog import Kept, LoggedCut, Release
from docket.vault.staging import Staging, rid_hash
from docket.vault.vault import Vault

AT = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def put(vault: Vault, data: bytes | None = None, name: str = "take") -> media.Stored:
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / name).write_bytes(tone(20.0) if data is None else data)
    return media.put(vault, CALL, path=str(drop / name))


def release(vault: Vault, *cuts: tuple[int, int]) -> None:
    """A release of the call with these cuts, keeping the recording's own record, which has no segments to cut."""
    staged = Staging(vault.root, vault.home).scan().entries
    kept = {(r.rid, r.v) for r in vault.rawlog.read(CALL)} | {
        (e.staged.record.rid, e.staged.record.v) for e in staged if e.staged.record.rid == CALL
    }
    releaselog.add(
        vault,
        "plaud",
        Release(
            id="rec-a",
            cuts=[LoggedCut(start=s, end=e) for s, e in cuts],
            keep=[Kept(rid=rid, v=v) for rid, v in sorted(kept)],
            at=AT,
            machine="test-box",
        ),
    )


def sidecar(vault: Vault) -> layout.Sidecar:
    found = media_staged.read_sidecar(layout.sidecar_path(vault.root, CALL))
    assert found is not None
    return found


def no_plain_files(vault: Vault) -> None:
    folder = vault.home.tmp_dir / f"{cutter.FOLDER}{__import__('os').getpid()}"
    assert not folder.exists() or not any(folder.iterdir())


def test_a_release_s_cuts_cut_the_audio_before_it_is_encrypted(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    release(vault, (2000, 4000))
    stored = put(vault)
    assert stored.ext == "webm"
    assert "cut at 1 stretch(es); from mp3float, 16000 Hz, 1 channel(s)" in stored.line
    assert "first sound at 0.0 s" in stored.line
    assert sidecar(vault).ranges == ((2000, 4000),)
    assert reader.state_of(vault, CALL) == "staged"
    no_plain_files(vault)
    result = Gate(vault).promote()
    assert [m.stored for m in result.media] == [1]
    found = reader.read(vault, CALL)
    assert found.ext == "webm"
    assert found.data is not None
    samples = decoded(found.data)
    assert dbfs(samples, 1780, 4220) < -60
    assert dbfs(samples, 5000, 8000) > -15
    # What it was cut by, committed with it.
    [line] = cutlog.read(vault.root, "plaud")
    assert line.rid == rid_hash(CALL) and [(c.start, c.end) for c in line.cuts] == [(2000, 4000)]
    assert line.machine == "test-box"
    changed = git(vault.root, "log", "-1", "--name-only", "--format=")
    assert "state/media-cuts/plaud.jsonl" in changed and "media/plaud/" in changed


def test_audio_with_no_cuts_is_stored_as_it_came_with_a_line_of_none(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    stored = put(vault, audio(3000))
    assert stored.ext == "mp3" and "cut at" not in stored.line
    in_raw(vault)
    [line] = cutlog.read(vault.root, "plaud")
    assert line.cuts == []


def test_a_cut_by_hand_takes_staged_audio_and_the_next_put_cuts_it(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    put(vault, audio(3000))
    Gate(vault).classify(POLISHED, "work", [Cut(start=2000, end=3000, unit="ms")])
    assert Gate(vault).list().entries
    assert not layout.staged_files(vault.root, CALL), "staged audio not cut by the new cut goes"
    assert KeyStore(vault.home).count() == 0
    in_raw(vault)
    assert Gate(vault).media_due("plaud") == ["rec-a"]
    put(vault)
    assert sidecar(vault).ranges == ((0, 9000),), "the cut, widened to the utterance it took"
    assert Gate(vault).media_due("plaud") == [], "it has audio now"
    in_raw(vault)
    found = reader.read(vault, CALL)
    assert found.data is not None
    samples = decoded(found.data)
    assert dbfs(samples, 0, 9220) < -60
    assert dbfs(samples, 10_000, 18_000) > -15


def test_staged_audio_whose_cuts_grow_goes_and_the_run_names_it(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    release(vault, (2000, 4000))
    put(vault)
    assert Gate(vault).list() is not None
    assert layout.staged_files(vault.root, CALL)
    # A second line for the same id: the union of their cuts (DR-0200), past what the audio was cut by.
    release(vault, (12_000, 13_000))
    result = Gate(vault).promote()
    assert not result.media and result.media_dropped == 1
    assert not layout.staged_files(vault.root, CALL)
    assert Gate(vault).media_due("plaud") == ["rec-a"]
    put(vault)
    assert sidecar(vault).ranges == ((2000, 4000), (12_000, 13_000))


def test_cuts_that_change_during_the_fetch_refuse_it(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    release(vault, (2000, 4000))
    keys = KeyStore(vault.home)
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "take").write_bytes(tone(20.0))
    needed = media.check(vault, CALL, keys)
    fetched = media.fetch(vault, CALL, path=str(drop / "take"), url=None, needed=needed)
    release(vault, (15_000, 16_000))
    with pytest.raises(DocketError, match="cuts changed while its media was fetched"):
        media.finish(vault, fetched, keys)
    assert not fetched.tmp.exists()
    assert keys.count() == 0
    no_plain_files(vault)


@pytest.mark.parametrize(
    ("data", "said"),
    [
        (lambda: tone(10.0), r"transcript ends at 19\.0 s, after its audio does \(10\.0 s\)"),
        (lambda: tone(22.0, start=3.0), r"first sounds at 3\.0 s and its transcript starts at 0\.0 s, 3\.0 s apart"),
        (lambda: tone(20.0, start=25.0), "silent throughout"),
        (lambda: b"%PDF-1.7 not audio" + bytes(100), "Docket cuts only audio, not a pdf file"),
    ],
    ids=["short", "late", "silent", "pdf"],
)
def test_audio_that_may_not_share_its_transcript_s_timeline_is_refused(
    tmp_path: Path, data: Callable[[], bytes], said: str
) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    release(vault, (2000, 4000))
    with pytest.raises(DocketError, match=said):
        put(vault, data())
    assert not layout.staged_files(vault.root, CALL)
    assert KeyStore(vault.home).count() == 0
    no_plain_files(vault)


def test_holds_and_character_cuts_still_refuse(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    note = Gate(vault).list().entries
    assert note
    holds.add(vault, "plaud", ["rec-a"])
    with pytest.raises(DocketError, match="on hold"):
        put(vault, audio(3000))
    held = media_vault(tmp_path / "second")
    Gate(held).stage([*_note(held)])
    Gate(held).classify(f"{CALL}/note", "work", [Cut(start=0, end=4)])
    with pytest.raises(DocketError, match="cut by characters"):
        put(held, audio(3000))
    in_raw(held)
    with pytest.raises(DocketError, match="cut by characters"):
        put(held, audio(3000))


def test_staged_cuts_that_don_t_fit_their_transcript_give_no_time_yet(tmp_path: Path) -> None:
    """Classify checks a cut fits before it stages it; a staged file holding one anyway refuses the audio."""
    vault = media_vault(tmp_path)
    staging = Staging(vault.root, vault.home)
    [entry] = [e for e in staging.scan().entries if e.staged.record.rid == POLISHED]
    staging.write(entry.staged.model_copy(update={"cuts": [Cut(start=60_000, end=70_000, unit="ms")]}))
    with pytest.raises(DocketError, match="staged cuts don't fit its transcript"):
        put(vault)


def _note(vault: Vault) -> list[RawRecord]:
    from docket_testkit import make_record

    return [make_record(rid=f"{CALL}/note", source="plaud", kind="note", container=CALL, text="the budget, and more")]


def _strip_kinds(vault: Vault) -> None:
    """The decided log as Docket wrote it before decisions kept their kind."""
    path = Staging(vault.root, vault.home).decided_file
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    path.write_text("".join(json.dumps({"rid": x["rid"], "key": x["key"]}) + "\n" for x in lines), encoding="utf-8")


def _hand_cut(tmp_path: Path) -> Vault:
    vault = media_vault(tmp_path)
    Gate(vault).classify(POLISHED, "work", [Cut(start=12_000, end=13_000, unit="ms")])
    in_raw(vault)
    return vault


def test_a_kind_less_decision_refuses_until_the_owner_confirms_it_was_a_cut(
    tmp_path: Path, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _hand_cut(tmp_path)
    _strip_kinds(vault)
    release(vault, (2000, 4000))
    with pytest.raises(DocketError, match="confirm-cuts plaud:rec-a"):
        put(vault)
    assert Gate(vault).media_due("plaud") == []
    owner("code")
    assert main(["media", "confirm-cuts", CALL, "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert f"{POLISHED}: 1 decision(s) without a kind; 1 version(s) cut by hand on this machine, 0 of them" in out
    assert "could be: 2026-" in out
    assert "cut at 0:10-0:19" in out, "the hand cut's span, not the release's"
    assert "cuts     1 decision(s) marked as cuts" in out
    assert Gate(vault).media_due("plaud") == ["rec-a"]
    assert put(vault).ext == "webm"
    assert sidecar(vault).ranges == ((2000, 4000), (10_000, 19_000))


def test_confirm_cuts_refuses_more_kind_less_decisions_than_versions_cut_by_hand(
    tmp_path: Path, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _hand_cut(tmp_path)
    _strip_kinds(vault)
    staging = Staging(vault.root, vault.home)
    # A second decision on the transcript: a private classification of another version, written before kinds.
    with staging.decided_file.open("a", encoding="utf-8") as out:
        out.write(json.dumps({"rid": rid_hash(POLISHED), "key": "a" * 64}) + "\n")
    before = staging.decided_file.read_bytes()
    owner("code")
    with pytest.raises(DocketError, match="more decisions without a kind than versions this machine cut by hand"):
        media_confirm(vault)
    assert (
        f"{POLISHED}: 2 decision(s) without a kind; 1 version(s) cut by hand on this machine" in capsys.readouterr().out
    )
    assert staging.decided_file.read_bytes() == before
    with pytest.raises(DocketError, match="nothing to confirm"):
        media_confirm(media_vault(tmp_path / "plain"))


def test_a_version_cut_after_kinds_or_on_another_machine_isn_t_evidence(
    tmp_path: Path, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    """An old private decision beside a version cut by hand since: that version's own decision says cut, so nothing
    is left for the kind-less one to be. A version another clone cut has no decision here at all."""
    vault = media_vault(tmp_path)
    staging = Staging(vault.root, vault.home)
    staging.decided_file.parent.mkdir(parents=True, exist_ok=True)
    with staging.decided_file.open("a", encoding="utf-8") as out:
        out.write(json.dumps({"rid": rid_hash(POLISHED), "key": "d" * 64}) + "\n")
    Gate(vault).classify(POLISHED, "work", [Cut(start=12_000, end=13_000, unit="ms")])
    in_raw(vault)
    owner("code")
    with pytest.raises(DocketError, match="no cut decision accounts for"):
        media_confirm(vault)
    out = capsys.readouterr().out
    assert "1 version(s) cut by hand on this machine, 1 of them already a cut decision" in out
    assert "decision dddddddd could be: none of its versions" in out
    # The same version, written by another machine: shown, never counted.
    other = media_vault(tmp_path / "other")
    other_staging = Staging(other.root, other.home)
    other_staging.decided_file.parent.mkdir(parents=True, exist_ok=True)
    with other_staging.decided_file.open("a", encoding="utf-8") as out_file:
        out_file.write(json.dumps({"rid": rid_hash(POLISHED), "key": "e" * 64}) + "\n")
    [version] = vault.rawlog.read(POLISHED)
    other.rawlog.machine = "desk-two"
    other.rawlog.write_run("plaud", [version])
    other.commit([], "Raw: plaud, from another machine")
    with pytest.raises(DocketError, match="no cut decision accounts for"):
        media_confirm(other)
    assert "on another machine, which this one's decisions don't cover" in capsys.readouterr().out


def test_confirm_cuts_asks_at_the_console_and_n_changes_nothing(
    tmp_path: Path, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _hand_cut(tmp_path)
    _strip_kinds(vault)
    owner("n")
    assert main(["media", "confirm-cuts", CALL, "--vault", str(vault.root)]) == 0
    assert "cuts     nothing confirmed" in capsys.readouterr().out
    monkeypatch.setenv("CLAUDECODE", "1")
    from docket import console

    monkeypatch.setattr(console, "problem", lambda act: f"{act} asks you at a console: Claude Code's shell can't")
    with pytest.raises(DocketError, match="Claude Code's shell"):
        media_confirm(vault)


def test_confirm_cuts_asks_the_os_prompt_where_there_is_one(
    tmp_path: Path, owner: Callable[..., AtConsole], os_prompt: Callable[..., FakePrompt]
) -> None:
    """A confirmation lets audio in for good, so it asks the OS prompt (DR-0575); its no confirms nothing."""
    from docket import osprompt

    vault = _hand_cut(tmp_path)
    _strip_kinds(vault)
    hello = os_prompt(osprompt.Refused("cancelled"), osprompt.Verified())
    owner("", "")
    assert media_confirm(vault) == 0
    assert media_confirm(vault) > 0
    assert hello.acts == ["confirm a recording's decisions were cuts"] * 2


def test_confirm_cuts_refuses_when_the_decisions_change_while_asked(
    tmp_path: Path, owner: Callable[..., AtConsole]
) -> None:
    vault = _hand_cut(tmp_path)
    _strip_kinds(vault)
    staging = Staging(vault.root, vault.home)

    def meanwhile() -> str:
        with staging.decided_file.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"rid": rid_hash(POLISHED), "key": "b" * 64}) + "\n")
        prompt = owner_said.prompts[-1]
        return prompt[prompt.index("Type ") + 5 : prompt.index("Type ") + 9].lower()

    owner_said = owner(meanwhile)
    with pytest.raises(DocketError, match="changed while you were asked"):
        media_confirm(vault)


def test_a_private_decision_beside_a_release_cut_version_refuses(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    release(vault, (2000, 4000))
    in_raw(vault)
    Staging(vault.root, vault.home).append_decided([(rid_hash(POLISHED), "c" * 64)], "private")
    with pytest.raises(DocketError, match="classified private by hand"):
        put(vault)


def media_confirm(vault: Vault) -> int:
    from docket.media import confirm

    return confirm.confirm(vault, CALL)


def test_the_run_names_cut_recordings_without_audio_only_where_media_comes_from(tmp_path: Path) -> None:
    vault = media_vault(tmp_path, hosts=())
    in_raw(vault)
    release(vault, (2000, 4000))
    assert Gate(vault).media_due("plaud") == [], "no host or folder: nowhere to put audio from"
    assert Gate(vault).media_due("nosuch") == []
    hosted = media_vault(tmp_path / "hosted")
    release(hosted, (2000, 4000))
    assert Gate(hosted).media_due("plaud") == ["rec-a"], "staged, released, with no audio"
    assert Gate(hosted).media_due("plaud", limit=0, look=0) == []
    assert Gate(hosted).media_due("plaud", limit=1) == ["rec-a"]
    opened = RunOpened(
        run="run-1", source="plaud", final=False, since=None, until=None, skill="s", tools=[], budget=1024,
        held=[], kept=[], kept_whole=True, waiting=[], retry=[], notes=[], steps="", media=["rec-a"],
    )  # fmt: skip
    assert "media    1: rec-a" in opened_text(opened)
    assert "media    none" in opened_text(opened.model_copy(update={"media": []}))


def test_a_cut_put_past_the_cap_or_failing_its_encryption_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    release(vault, (2000, 4000))
    with https_server(tmp_path, tone(20.0)) as server:
        # No length up front, so the cap stops the read as the bytes come.
        server.routes["/audio"] = (200, {"X-No-Length": "1"}, tone(20.0))
        with pytest.raises(DocketError, match="over the cap"):
            media.put(vault, CALL, url=server.url("/audio"), fetcher=server.fetcher(max_bytes=1000))
    no_plain_files(vault)
    from docket.media import crypto

    def broken(source: object, out: object, recipient: str) -> None:
        raise DocketError("age refused")

    monkeypatch.setattr(crypto, "encrypt", broken)
    with pytest.raises(DocketError, match="age refused"):
        put(vault)
    no_plain_files(vault)
    assert not list(vault.home.tmp_dir.glob(f"{media_staged.TEMP}*.tmp"))


def test_the_cut_log_reads_only_what_it_writes(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    assert cutlog.read(vault.root, "plaud") == []
    path = cutlog.log_path(vault.root, "plaud")
    path.parent.mkdir(parents=True)
    path.write_bytes(b'\n{"schema_version": 1, "rid": "x"}\n')
    with pytest.raises(DocketError, match=r"state/media-cuts/plaud.jsonl: line 2 isn't a media cut line"):
        cutlog.read(vault.root, "plaud")
    path.write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(DocketError, match="can't read it"):
        cutlog.read(vault.root, "plaud")
    path.write_bytes(b"")
    before = cutlog.append(vault.home, vault.root, "plaud", [(rid_hash(CALL), "f" * 64, [])], "test-box")
    assert before == b""
    text = path.read_text(encoding="utf-8").rstrip("\n")
    path.write_text(text, encoding="utf-8")
    cutlog.append(
        vault.home, vault.root, "plaud", [(rid_hash(CALL), "f" * 64, [layout.Span(start=0, end=1)])], "test-box"
    )
    assert [len(line.cuts) for line in cutlog.read(vault.root, "plaud")] == [0, 1]
    cutlog.put_back(vault.root, "plaud", b"")
    assert path.read_bytes() == b""
    cutlog.put_back(vault.root, "plaud", None)
    assert not path.exists()
    with pytest.raises(ValueError, match="32 hex"):
        cutlog.Line(rid="plaud:rec-a", at=AT, machine="m")
    with pytest.raises(ValueError, match="names its file's key"):
        cutlog.Line(rid="a" * 32, at=AT, machine="m")
    with pytest.raises(ValueError, match="names no key"):
        cutlog.Line(schema_version=1, rid="a" * 32, key="f" * 64, at=AT, machine="m")
    assert cutlog.Line(schema_version=1, rid="a" * 32, at=AT, machine="m").key is None
    with pytest.raises(ValueError, match="time zone"):
        cutlog.Line(rid="a" * 32, at=datetime(2026, 10, 1), machine="m")
    with pytest.raises(DocketError, match="isn't a source name"):
        cutlog.log_path(vault.root, "..")


def test_a_cut_log_or_folder_that_is_a_link_is_refused(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    folder = vault.root / cutlog.FOLDER
    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        folder.symlink_to(elsewhere, target_is_directory=True)
    except OSError:  # pragma: no cover - a system that can't make a symlink here
        pytest.skip("this system can't make a symlink here")
    with pytest.raises(DocketError, match="or its folder is a link"):
        cutlog.read(vault.root, "plaud")


def test_the_screen_s_plan_gives_the_family_s_ranges_and_timeline(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    release(vault, (2000, 4000))
    found = Gate(vault).media_plan(CALL)
    assert found == Needed(((2000, 4000),), first_ms=0, last_ms=19_000)
    assert isinstance(Gate(vault).media_plan("plaud:nothing"), str)
