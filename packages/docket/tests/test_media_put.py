"""`media_put` end to end (DR-0547 to DR-0549): a path or a URL, encrypted while read, staged beside its record, stored
in media/ when the record is in raw, and every refusal before the fetch and after it."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import git
from media_kit import CALL, HOST, POLISHED, audio, https_server, in_raw, media_vault

from docket.errors import DocketError
from docket.media import crypto, layout, reader
from docket.media import put as media
from docket.media.keystore import KeyStore
from docket.vault import holds, releaselog
from docket.vault.gate import Gate
from docket.vault.releaselog import LoggedCut, Release
from docket.vault.vault import Vault


def downloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: bytes, name: str = "call.mp3") -> Path:
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    path = home / "Downloads" / name
    path.write_bytes(data)
    return path


def staged(vault: Vault, rid: str = CALL) -> list[Path]:
    return layout.staged_files(vault.root, rid)


def test_a_file_from_a_media_folder_is_staged_encrypted_and_stored_with_its_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = audio(30_000)
    source = downloads(tmp_path, monkeypatch, data)
    vault = media_vault(tmp_path, folders=("~/Downloads",))
    stored = media.put(vault, CALL, path=str(source))
    assert (stored.ext, stored.bytes) == ("mp3", len(data))
    assert "staged mp3 media for plaud:rec-a" in stored.line
    [file] = staged(vault)
    assert file.name.endswith(".mp3.age")
    assert data[:200] not in file.read_bytes()
    assert source.read_bytes() == data, "a media folder's file is left as it is"
    sidecar = layout.Sidecar.model_validate_json(layout.sidecar_path(vault.root, CALL).read_text(encoding="utf-8"))
    assert (sidecar.rid, sidecar.key) == (CALL, crypto.key_id(file))
    assert reader.state_of(vault, CALL) == "staged"
    # No plain copy anywhere Docket writes: staging, its private folder, the media folder.
    for folder in (vault.root / "staging", vault.home.private):
        assert not [p for p in folder.rglob("*") if p.is_file() and data[:200] in p.read_bytes()]
    in_raw(vault)
    [kept] = layout.stored_files(vault.root, CALL)
    assert kept.relative_to(vault.root).as_posix().startswith("media/plaud/2026/10/")
    assert not staged(vault) and not layout.sidecar_path(vault.root, CALL).exists()
    log = git(vault.root, "log", "--name-only", "--format=%s")
    assert "Media: plaud, 1 file(s) from test-box" in log
    assert "staging/" not in log
    found = reader.read(vault, CALL)
    assert (found.state, found.data, found.type) == ("ok", data, "audio/mpeg")


def test_a_file_from_the_drop_folder_goes_once_stored(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    drop = media.drop_folder()
    drop.mkdir(parents=True)
    dropped = drop / "call.ogg"
    dropped.write_bytes(audio(5000, head=b"OggS\x00\x02" + b"\x00" * 22 + b"OpusHead"))
    assert media.put(vault, CALL, path=str(dropped)).ext == "opus"
    assert not dropped.exists()
    outside = tmp_path / "elsewhere.mp3"
    outside.write_bytes(audio())
    with pytest.raises(DocketError, match="isn't under one of the source's media folders"):
        media.put(vault, POLISHED, path=str(outside))


def test_a_url_from_a_media_host_is_fetched_encrypted_and_staged(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    with https_server(tmp_path, audio(60_000)) as server:
        stored = media.put(vault, CALL, url=server.url("/audio?X-Signature=s3cr3t"), fetcher=server.fetcher())
        assert stored.bytes == 60_000
        in_raw(vault)
        assert reader.read(vault, CALL).data == server.body
        # A host not listed for the source is refused before anything is fetched.
        with pytest.raises(DocketError, match=r"other.example isn't one of the source's media hosts"):
            media.put(vault, POLISHED, url=server.url("/audio", "other.example"), fetcher=server.fetcher())
        # Over the cap: the reason, and no temp file left behind.
        with pytest.raises(DocketError, match="over the cap"):
            media.put(vault, POLISHED, url=server.url("/audio"), fetcher=server.fetcher(max_bytes=10_000))
        assert not list(vault.home.tmp_dir.glob("media-*.tmp"))
        assert not staged(vault, POLISHED)


def test_a_put_again_replaces_the_staged_file_and_its_key(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    keys = KeyStore(vault.home)
    drop = media.drop_folder()
    drop.mkdir(parents=True)
    for n, head in enumerate((b"ID3\x04", b"fLaC\x00")):
        (drop / f"take{n}").write_bytes(audio(3000, head=head))
        media.put(vault, CALL, path=str(drop / f"take{n}"), keys=keys)
    assert [layout.ext_of(p) for p in staged(vault)] == ["flac"]
    assert keys.count() == 1


@pytest.mark.parametrize(
    ("rid", "said"),
    [
        ("plaud", "isn't a rid"),
        ("plaud:a//b", "isn't a rid"),
        ("mail:<x@example.invalid>", "this machine hasn't ingested mail"),
        ("plaud:rec-zzz", "no record plaud:rec-zzz is staged or in raw"),
    ],
)
def test_a_put_that_can_t_go_in_is_refused_before_any_fetch(tmp_path: Path, rid: str, said: str) -> None:
    vault = media_vault(tmp_path)
    with pytest.raises(DocketError, match=said):
        media.put(vault, rid, url="https://media.example/a")
    with pytest.raises(DocketError, match="a path or a url: one of them"):
        media.fetch(vault, CALL, path=None, url=None)


def test_a_clone_without_a_keychain_is_told_how_to_opt_in(tmp_path: Path) -> None:
    vault = media_vault(tmp_path, store=False)
    with pytest.raises(DocketError, match="docket media keys init --store file"):
        media.put(vault, CALL, url=f"https://{HOST}/a")


def test_a_held_or_released_recording_s_media_is_refused(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    holds.add(vault, "plaud", ["rec-a"])
    with pytest.raises(DocketError, match="on hold"):
        media.put(vault, CALL, url=f"https://{HOST}/a")
    assert reader.state_of(vault, CALL) == "held"


def test_a_release_s_cuts_refuse_the_audio_until_it_can_be_cut(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    at = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
    releaselog.add(vault, "plaud", Release(id="rec-a", cuts=[LoggedCut(start=0, end=1000)], at=at, machine="test-box"))
    with pytest.raises(DocketError, match="released with cuts"):
        media.put(vault, CALL, url=f"https://{HOST}/a")
    assert reader.state_of(vault, CALL) == "held"


def test_the_checks_run_again_after_the_fetch(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    keys = KeyStore(vault.home)
    with https_server(tmp_path, audio(20_000)) as server:
        media.check(vault, CALL, keys)
        fetched = media.fetch(vault, CALL, path=None, url=server.url("/audio"), fetcher=server.fetcher())
        assert fetched.tmp.is_file()
        # A hold arrives while the bytes come: nothing is staged, the key never written, the temp file gone.
        holds.add(vault, "plaud", ["rec-a"])
        with pytest.raises(DocketError, match="on hold"):
            media.finish(vault, fetched, keys)
    assert not fetched.tmp.exists()
    assert not staged(vault)
    assert keys.count() == 0


def test_media_stored_already_isn_t_put_again(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    drop = media.drop_folder()
    drop.mkdir(parents=True)
    (drop / "a").write_bytes(audio())
    media.put(vault, CALL, path=str(drop / "a"))
    in_raw(vault)
    (drop / "b").write_bytes(audio())
    with pytest.raises(DocketError, match="media for plaud:rec-a is stored already"):
        media.put(vault, CALL, path=str(drop / "b"))
    assert os.environ["DOCKET_STATE_DIR"] in str(drop)


def test_a_body_over_the_cap_stops_the_encryption_and_leaves_nothing(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    with https_server(tmp_path, audio(1000)) as server:
        server.routes["/stream"] = (200, {"X-No-Length": "1"}, audio(300_000))
        with pytest.raises(DocketError, match="over the cap of 0 MiB"):
            media.put(vault, CALL, url=server.url("/stream"), fetcher=server.fetcher(max_bytes=100_000))
    assert not list(vault.home.tmp_dir.glob("media-*.tmp"))
    assert not staged(vault)


def test_a_source_the_vault_doesn_t_hold_fetches_nothing(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    with pytest.raises(DocketError, match=r"source mail isn't in vault.yaml in force"):
        media.fetch(vault, "mail:x", path="x", url=None)


def test_the_cap_is_a_setting_of_this_machine(tmp_path: Path) -> None:
    config = Path(os.environ["DOCKET_CONFIG_DIR"]) / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    assert media.limits().max_bytes == 512 * 1024 * 1024
    config.write_text("media:\n  max_bytes: 2048\n", encoding="utf-8")
    assert media.limits().max_bytes == 2048
    config.write_text("media:\n  max_bytes: lots\n", encoding="utf-8")
    with pytest.raises(DocketError, match=r"media.max_bytes must be a whole number of bytes"):
        media.limits()


def test_media_waits_while_its_record_waits_to_be_classified(tmp_path: Path) -> None:
    vault = media_vault(tmp_path, sensitivity="mixed")
    drop = media.drop_folder()
    drop.mkdir(parents=True)
    (drop / "a").write_bytes(audio())
    media.put(vault, CALL, path=str(drop / "a"))
    assert not Gate(vault).promote().media
    assert staged(vault)
