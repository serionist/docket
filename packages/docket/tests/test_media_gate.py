"""Media at the gate (DR-0547): deleted with any drop of a version in its family, screened again at every sweep and
promotion, the withheld marks, the orphans, and promotion into media/ with its failures and leftovers."""

from __future__ import annotations

import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import git, make_record
from media_kit import CALL, POLISHED, audio, call, in_raw, media_vault

from docket.errors import DocketError
from docket.media import crypto, layout, reader
from docket.media import put as media
from docket.media import staged as media_staged
from docket.media.keystore import KeyStore
from docket.vault import gate as gate_module
from docket.vault import holds, releaselog
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.cuts import Cut
from docket.vault.gate import Gate
from docket.vault.host import Lfs
from docket.vault.records import RawRecord
from docket.vault.releaselog import Kept, LoggedCut, Release
from docket.vault.staging import Staging, rid_hash
from docket.vault.vault import Vault


def put(vault: Vault, rid: str = CALL, head: bytes = b"ID3\x04") -> None:
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "take").write_bytes(audio(3000, head=head))
    media.put(vault, rid, path=str(drop / "take"))


def never_store(vault: Vault, keyword: str) -> None:
    """A keyword added to never-store by hand: stricter, so in force at once."""
    path = vault.root / CONFIG_FILE
    config = VaultConfig.load(path)
    config.never_store.keywords.append(keyword)
    path.write_text(config.dump(), encoding="utf-8", newline="\n")


def withheld_lines(vault: Vault) -> list[str]:
    path = media_staged.withheld_path(vault.root)
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def nothing_left(vault: Vault, rid: str = CALL) -> None:
    assert not layout.staged_files(vault.root, rid)
    assert not layout.sidecar_path(vault.root, rid).exists()
    assert not layout.stored_files(vault.root, rid)
    assert KeyStore(vault.home).count() == 0


def v2(text: str) -> list[RawRecord]:
    """The call again, its transcript's first line changed: a second version."""
    return call(text=text)


def test_a_version_dropped_for_never_store_takes_the_media_and_marks_the_rid(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    Gate(vault).stage(v2("Ask Zephyrine about the brake pad."))
    put(vault)
    never_store(vault, "Zephyrine")
    Gate(vault).list()
    nothing_left(vault)
    assert {rid_hash(CALL), rid_hash(POLISHED)} <= media_staged.read_withheld(vault.root)
    Gate(vault).promote()
    nothing_left(vault)
    with pytest.raises(DocketError, match=r"matches a never-store value|dropped for a never-store value"):
        put(vault)


def test_a_version_dropped_at_intake_leaves_a_mark_a_later_put_meets(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    never_store(vault, "Zephyrine")
    result = Gate(vault).stage(v2("Ask Zephyrine about the brake pad."))
    assert result.dropped["never-store"] == 1
    lines = withheld_lines(vault)
    assert len(lines) == 2
    # Sent again on the next sync: dropped again, no second line.
    Gate(vault).stage(v2("Ask Zephyrine about the brake pad."))
    assert withheld_lines(vault) == lines
    with pytest.raises(DocketError, match="was dropped for a never-store value or as private"):
        put(vault)
    # The value taken off the list: the mark stays.
    path = vault.root / CONFIG_FILE
    config = VaultConfig.load(path)
    config.never_store.keywords.remove("Zephyrine")
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    with pytest.raises(DocketError, match="was dropped for a never-store value or as private"):
        put(vault)
    assert not layout.staged_files(vault.root, CALL)


def test_a_version_classified_private_takes_the_media_for_good(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    Gate(vault).stage(v2("The plan changes."))
    put(vault)
    Gate(vault).classify(POLISHED, "private")
    nothing_left(vault)
    Gate(vault).promote()
    nothing_left(vault)
    with pytest.raises(DocketError, match="dropped for a never-store value or as private"):
        put(vault)


def test_a_source_made_private_takes_staged_media_at_the_sweep(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    put(vault)
    path = vault.root / CONFIG_FILE
    config = VaultConfig.load(path)
    config.sources["plaud"].sensitivity = "private"
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    result = Gate(vault).stage([])
    assert result.media_dropped == 1
    nothing_left(vault)


def test_a_hold_or_a_hand_cut_arriving_after_the_put_deletes_the_media(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    put(vault)
    holds.add(vault, "plaud", ["rec-a"])
    Gate(vault).list()
    nothing_left(vault)


def test_a_cut_by_hand_keeps_the_audio_out(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    put(vault)
    Gate(vault).classify(POLISHED, "work", [Cut(start=0, end=5000, unit="ms")])
    Gate(vault).list()
    nothing_left(vault)
    in_raw(vault)
    with pytest.raises(DocketError, match="cut or decided private by hand"):
        put(vault)


def test_a_release_that_leaves_the_record_out_takes_its_media(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    at = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
    kept = Kept(rid="plaud:other", v="sha256:0")
    releaselog.add(
        vault, "plaud", Release(id="rec-a", cuts=[LoggedCut(start=0, end=1000)], keep=[kept], at=at, machine="m")
    )
    gate = Gate(vault)
    with gate.vault.lock:
        gate._sweep(gate._config())  # pyright: ignore[reportPrivateUsage]
        screen = gate._screen(gate._config())  # pyright: ignore[reportPrivateUsage]
    assert screen.why(CALL) is not None
    # At intake: a version the release leaves out goes, and media put on its rid before goes with it.
    vault2 = media_vault(tmp_path / "second")
    put(vault2)
    releaselog.add(vault2, "plaud", Release(id="rec-a", cuts=[LoggedCut(start=0, end=1000)], at=at, machine="m"))
    result = Gate(vault2).stage(
        [make_record(rid=CALL, source="plaud", kind="recording", title="New", text="", container=CALL)]
    )
    assert result.dropped["left-out"] >= 1
    nothing_left(vault2)


def test_a_reply_s_drop_never_withholds_its_thread_starter_s_file(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    starter = make_record(1, rid="chat:C0OPS/1791400000.000100", container="chat:C0OPS", text="the plan")
    reply = make_record(
        2, rid="chat:C0OPS/1791400100.000100", container="chat:C0OPS", thread=starter.rid, text="Zephyrine said so"
    )
    Gate(vault).stage([starter])
    put(vault, starter.rid, head=b"%PDF-1.7")
    never_store(vault, "Zephyrine")
    Gate(vault).stage([reply])
    assert layout.staged_files(vault.root, starter.rid)
    assert rid_hash(starter.rid) not in media_staged.read_withheld(vault.root)


def test_a_sweep_stopped_after_the_media_went_finishes_on_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    Gate(vault).stage(v2("Ask Zephyrine."))
    put(vault)
    never_store(vault, "Zephyrine")
    real = Staging.delete

    def killed(self: object, path: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(Staging, "delete", killed)
    with pytest.raises(KeyboardInterrupt):
        Gate(vault).list()
    assert not layout.staged_files(vault.root, CALL)
    monkeypatch.setattr(Staging, "delete", real)
    Gate(vault).list()
    assert not Gate(vault).list().entries
    nothing_left(vault)


def test_orphans_go_with_their_keys(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    keys = KeyStore(vault.home)
    put(vault)
    put(vault, POLISHED)
    assert keys.count() == 2
    # A file without its sidecar, and a sidecar without its file.
    layout.sidecar_path(vault.root, CALL).unlink()
    layout.staged_files(vault.root, POLISHED)[0].unlink()
    folder = layout.staged_dir(vault.root, "plaud")
    (folder / f"{'0' * 32}.json").write_text("{", encoding="utf-8")
    (folder / f"{'1' * 32}.mp3.age").write_bytes(b"not age")
    result = Gate(vault).stage([])
    assert result.media_dropped == 4
    assert not list(folder.iterdir())
    assert keys.count() == 0


def test_a_file_whose_name_doesn_t_match_its_sidecar_is_an_orphan(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    put(vault)
    [file] = layout.staged_files(vault.root, CALL)
    file.rename(file.with_name(file.name.replace(".mp3.", ".wav.")))
    Gate(vault).list()
    nothing_left(vault)


def test_a_killed_put_s_temp_file_goes_after_an_hour(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    tmp = media_staged.temp_file(vault.home)
    tmp.write_bytes(b"x")
    fresh = media_staged.temp_file(vault.home)
    fresh.write_bytes(b"x")
    old = time.time() - 2 * media_staged.STALE_SECONDS
    os.utime(tmp, (old, old))
    Gate(vault).list()
    assert not tmp.exists() and fresh.exists()


def test_promotion_fixes_the_month_and_a_later_version_never_moves_it(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    put(vault)
    in_raw(vault)
    [first] = layout.stored_files(vault.root, CALL)
    assert first.parent.as_posix().endswith("media/plaud/2026/10")
    if shutil.which("git-lfs"):
        assert git(vault.root, "lfs", "ls-files", "--name-only").strip().endswith(first.name)
    Gate(vault).stage(call(ts=datetime(2026, 12, 2, 9, 0, tzinfo=UTC), text="Later, renamed."))
    in_raw(vault)
    assert layout.stored_files(vault.root, CALL) == [first]
    assert reader.state_of(vault, CALL) == "ok"


def test_media_waits_for_git_lfs_and_records_still_promote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = media_vault(tmp_path)
    put(vault)
    monkeypatch.setattr(gate_module, "lfs_state", lambda top: Lfs(False, False, "missing"))
    result = Gate(vault).promote()
    assert result.runs and not result.media
    assert result.media_held and "git-lfs not installed" in result.media_held[0]
    sidecar = media_staged.read_sidecar(layout.sidecar_path(vault.root, CALL))
    assert sidecar is not None and sidecar.problem is not None
    monkeypatch.undo()
    assert Gate(vault).promote().media


def test_a_failed_media_commit_moves_the_files_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    put(vault)
    real = Vault.commit

    def fails(self: Vault, paths: list[str], message: str) -> str | None:
        if message.startswith("Media:"):
            raise DocketError("git refused")
        return real(self, paths, message)

    monkeypatch.setattr(Vault, "commit", fails)
    with pytest.raises(DocketError, match="git refused"):
        Gate(vault).promote()
    assert layout.staged_files(vault.root, CALL) and not layout.stored_files(vault.root, CALL)
    monkeypatch.undo()
    assert Gate(vault).promote().media


def test_a_crash_between_the_move_and_the_commit_is_finished_next_time(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    put(vault)
    [file] = layout.staged_files(vault.root, CALL)
    target = layout.stored_path(vault.root, CALL, datetime(2026, 10, 12, tzinfo=UTC), "mp3")
    media_staged.move(file, target)
    result = Gate(vault).promote()
    assert [m.stored for m in result.media] == [1]
    assert "media/plaud/2026/10/" in git(vault.root, "log", "-1", "--name-only", "--format=")
    assert not layout.sidecar_path(vault.root, CALL).exists()
    assert reader.state_of(vault, CALL) == "ok"


def test_a_leftover_without_its_sidecar_is_never_committed(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    stray = layout.stored_path(vault.root, "plaud:rec-x", datetime(2026, 10, 1, tzinfo=UTC), "mp3")
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"x")
    result = Gate(vault).promote()
    assert result.media_held == [
        f"{stray.relative_to(vault.root).as_posix()} has no sidecar in staging, so it isn't committed"
    ]
    assert stray.exists()


def test_a_leftover_that_fails_its_checks_is_deleted(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    put(vault)
    [file] = layout.staged_files(vault.root, CALL)
    target = layout.stored_path(vault.root, CALL, datetime(2026, 10, 12, tzinfo=UTC), "mp3")
    media_staged.move(file, target)
    holds.add(vault, "plaud", ["rec-a"])
    Gate(vault).promote()
    nothing_left(vault)


def test_a_sidecar_left_after_its_commit_goes_and_the_key_stays(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    put(vault)
    sidecar = layout.sidecar_path(vault.root, CALL)
    kept = sidecar.read_bytes()
    in_raw(vault)
    sidecar.write_bytes(kept)
    Gate(vault).list()
    assert sidecar.exists(), "kept while its file is in media/"
    # A drop of the rid's staged media never takes a stored file's key.
    media_staged.delete(vault.root, KeyStore(vault.home), CALL)
    assert KeyStore(vault.home).count() == 1
    sidecar.write_bytes(kept)
    Gate(vault).promote()
    assert not sidecar.exists()
    assert reader.state_of(vault, CALL) == "ok"
    assert KeyStore(vault.home).count() == 1
    assert crypto.key_id(layout.stored_files(vault.root, CALL)[0])
