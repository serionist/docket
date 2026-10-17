"""Stored audio that gains a cut later (DR-0562): it isn't played while its cut log line holds less than its family
needs, the run names it, a put goes in beside it, and promotion swaps the two. The old file's key goes only once the
replacement is committed, never before (#169's rule). Audio is a tone the test makes; records are invented."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import git, make_record
from media_kit import CALL, POLISHED, audio, call, dbfs, decoded, in_raw, media_vault, tone

from docket.errors import DocketError
from docket.media import cutlog, layout, reader
from docket.media import put as media
from docket.media import staged as media_staged
from docket.media.keystore import KeyStore
from docket.vault import holds, releaselog
from docket.vault.cuts import Cut
from docket.vault.gate import Gate
from docket.vault.releaselog import Kept, LoggedCut, Release
from docket.vault.staging import Staging, rid_hash
from docket.vault.vault import Vault

AT = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def put(vault: Vault, data: bytes | None = None) -> media.Stored:
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "take").write_bytes(tone(20.0) if data is None else data)
    return media.put(vault, CALL, path=str(drop / "take"))


def release(vault: Vault, *cuts: tuple[int, int]) -> None:
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


def stored(vault: Vault) -> Path:
    [found] = layout.stored_files(vault.root, CALL)
    return found


def in_head(vault: Vault) -> list[str]:
    return [
        p for p in git(vault.root, "ls-tree", "-r", "--name-only", "HEAD", "media").splitlines() if p.endswith(".age")
    ]


def released_and_stored(tmp_path: Path) -> Vault:
    """A call released with one cut, its audio cut by it and stored."""
    vault = media_vault(tmp_path)
    in_raw(vault)
    release(vault, (2000, 4000))
    put(vault)
    in_raw(vault)
    assert reader.state_of(vault, CALL) == "ok"
    return vault


def test_a_stored_file_cut_by_less_than_now_is_replaced_in_place_and_its_key_goes(tmp_path: Path) -> None:
    vault = released_and_stored(tmp_path)
    old = stored(vault)
    old_key = media_staged.key_of(old)
    assert old_key is not None
    # A second release line for the id: its cuts are the union (DR-0200), past what the stored file was cut by.
    release(vault, (12_000, 13_000))
    assert reader.state_of(vault, CALL) == "recut"
    assert reader.read(vault, CALL).data is None
    assert Gate(vault).media_due("plaud") == ["rec-a"]
    put(vault)
    assert reader.state_of(vault, CALL) == "recut", "the stored file isn't played while its replacement waits"
    result = Gate(vault).promote()
    assert [m.stored for m in result.media] == [1]
    assert stored(vault) == old, "the same month and type: the same path"
    assert in_head(vault) == [old.relative_to(vault.root).as_posix()]
    new_key = media_staged.key_of(old)
    assert new_key is not None and new_key != old_key
    keys = KeyStore(vault.home)
    assert not keys.has(old_key) and keys.has(new_key)
    assert reader.state_of(vault, CALL) == "ok"
    found = reader.read(vault, CALL)
    assert found.data is not None
    samples = decoded(found.data)
    assert dbfs(samples, 1780, 4220) < -60 and dbfs(samples, 11_780, 13_220) < -60
    assert dbfs(samples, 6000, 9000) > -15
    assert cutlog.logged(vault.root, "plaud")[rid_hash(CALL)] == ((2000, 4000), (12_000, 13_000))
    assert Gate(vault).media_due("plaud") == []
    with pytest.raises(DocketError, match="stored already"):
        put(vault)


def test_a_replacement_at_a_new_name_takes_the_old_file_out_in_its_commit(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    put(vault, audio(3000))
    in_raw(vault)
    old = stored(vault)
    old_key = media_staged.key_of(old)
    assert old.name.endswith(".mp3.age") and old_key is not None
    # A new version of the transcript, cut by hand: the stored mp3 holds the stretch.
    Gate(vault).stage([r for r in call(text="The crane passed its load test, twice.") if r.rid == POLISHED])
    Gate(vault).classify(POLISHED, "work", [Cut(start=2000, end=3000, unit="ms")])
    in_raw(vault)
    assert reader.state_of(vault, CALL) == "recut"
    assert Gate(vault).media_due("plaud") == ["rec-a"]
    put(vault)
    Gate(vault).promote()
    new = stored(vault)
    assert new.name.endswith(".webm.age") and not old.exists()
    assert in_head(vault) == [new.relative_to(vault.root).as_posix()]
    assert not KeyStore(vault.home).has(old_key)
    assert reader.state_of(vault, CALL) == "ok"


def test_a_held_stored_file_isn_t_played_and_its_release_makes_it_recut(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    put(vault, audio(3000))
    in_raw(vault)
    holds.add(vault, "plaud", ["rec-a"])
    assert reader.state_of(vault, CALL) == "held"
    assert reader.read(vault, CALL).data is None
    assert Gate(vault).media_due("plaud") == []
    release(vault, (2000, 4000))
    holds.remove(vault, "plaud", ["rec-a"])
    assert reader.state_of(vault, CALL) == "recut"
    assert Gate(vault).media_due("plaud") == ["rec-a"]


def test_a_swap_whose_commit_doesn_t_land_puts_both_files_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = released_and_stored(tmp_path)
    old = stored(vault)
    old_key = media_staged.key_of(old)
    before = cutlog.log_path(vault.root, "plaud").read_bytes()
    release(vault, (12_000, 13_000))
    put(vault)
    real = Vault.commit

    def fails(self: Vault, paths: list[str], message: str) -> str | None:
        if message.startswith("Media:"):
            raise DocketError("git refused")
        return real(self, paths, message)

    monkeypatch.setattr(Vault, "commit", fails)
    with pytest.raises(DocketError, match="git refused"):
        Gate(vault).promote()
    assert layout.staged_files(vault.root, CALL), "the replacement back in staging"
    assert media_staged.key_of(stored(vault)) == old_key, "the stored file back from HEAD"
    assert old_key is not None and KeyStore(vault.home).has(old_key)
    assert cutlog.log_path(vault.root, "plaud").read_bytes() == before
    assert reader.state_of(vault, CALL) == "recut"
    monkeypatch.setattr(Vault, "commit", real)
    assert [m.stored for m in Gate(vault).promote().media] == [1]
    assert reader.state_of(vault, CALL) == "ok"
    assert old_key is not None and not KeyStore(vault.home).has(old_key)


def test_a_swap_that_lands_and_then_raises_keeps_the_old_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = released_and_stored(tmp_path)
    old_key = media_staged.key_of(stored(vault))
    release(vault, (12_000, 13_000))
    put(vault)
    real = Vault.commit

    def lands_then_raises(self: Vault, paths: list[str], message: str) -> str | None:
        done = real(self, paths, message)
        if message.startswith("Media:"):
            raise KeyboardInterrupt
        return done

    monkeypatch.setattr(Vault, "commit", lands_then_raises)
    with pytest.raises(KeyboardInterrupt):
        Gate(vault).promote()
    monkeypatch.setattr(Vault, "commit", real)
    assert reader.state_of(vault, CALL) == "ok", "the replacement is what HEAD holds"
    assert old_key is not None and KeyStore(vault.home).has(old_key), "kept: Docket can't tell the commit landed"
    assert not Gate(vault).promote().media
    assert not layout.sidecar_path(vault.root, CALL).exists()


def _swap_by_hand(vault: Vault) -> Path:
    """What a crash between the swap's moves and its commit leaves: the replacement where it goes, the old file gone,
    the sidecar still in staging."""
    [file] = layout.staged_files(vault.root, CALL)
    versions = vault.rawlog.read(CALL)
    target = layout.stored_path(vault.root, CALL, versions[-1].ts, "webm")
    for path in layout.stored_files(vault.root, CALL):
        if path != target:
            path.unlink()
    media_staged.move(file, target)
    return target


def test_a_crash_mid_swap_is_committed_by_the_next_promotion(tmp_path: Path) -> None:
    # In place: the replacement over a tracked file.
    vault = released_and_stored(tmp_path)
    old_key = media_staged.key_of(stored(vault))
    release(vault, (12_000, 13_000))
    put(vault)
    target = _swap_by_hand(vault)
    assert [m.stored for m in Gate(vault).promote().media] == [1]
    assert in_head(vault) == [target.relative_to(vault.root).as_posix()]
    assert git(vault.root, "status", "--porcelain", "media") == ""
    assert old_key is not None and KeyStore(vault.home).has(old_key), "unknown after a crash: kept"
    assert reader.state_of(vault, CALL) == "ok"
    # At a new name: the old file's removal goes in the same commit.
    other = media_vault(tmp_path / "other")
    put(other, audio(3000))
    in_raw(other)
    release(other, (2000, 4000))
    put(other)
    target = _swap_by_hand(other)
    assert [m.stored for m in Gate(other).promote().media] == [1]
    assert in_head(other) == [target.relative_to(other.root).as_posix()]


def test_a_record_cut_by_characters_leaves_its_stored_file_unplayed(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    put(vault, audio(3000))
    in_raw(vault)
    note = make_record(rid=f"{CALL}/note", source="plaud", kind="note", container=CALL, text="the budget, and more")
    Gate(vault).stage([note])
    Gate(vault).classify(f"{CALL}/note", "work", [Cut(start=0, end=4)])
    in_raw(vault)
    assert reader.state_of(vault, CALL) == "recut"
    assert Gate(vault).media_due("plaud") == []
    assert not reader.recut(vault, "plaud:nothing")
