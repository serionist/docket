"""Media's parts on their own (DR-0547 to DR-0549): the layout and the type from the first bytes, age through pyrage
and the header's key id, this clone's key store, and the address rule and capped reader of the fetch."""

from __future__ import annotations

import io
import os
import socket
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from media_kit import Keychain, audio

from docket import folders
from docket.errors import DocketError
from docket.media import crypto, fetch, keystore, layout
from docket.media.fetch import Capped, Limits
from docket.media.keystore import KeyStore
from docket.vault.home import Home
from docket.vault.staging import rid_hash

# -- the layout ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("head", "ext"),
    [
        (b"ID3\x04", "mp3"),
        (b"\xff\xfb\x90\x00", "mp3"),
        (b"\xff\xf1\x50\x80", "aac"),
        (b"\x00\x00\x00\x20ftypM4A ", "m4a"),
        (b"OggS\x00\x02" + b"\x00" * 22 + b"OpusHead", "opus"),
        (b"OggS\x00\x02" + b"\x00" * 22 + b"\x01vorbis", "ogg"),
        (b"RIFF\x24\x00\x00\x00WAVEfmt ", "wav"),
        (b"fLaC\x00", "flac"),
        (b"\x1aE\xdf\xa3\x01", "webm"),
        (b"%PDF-1.7", "pdf"),
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"\xff\xd8\xff\xe0", "jpg"),
        (b"PK\x03\x04", "bin"),
        (b"", "bin"),
    ],
)
def test_the_type_comes_from_the_first_bytes(head: bytes, ext: str) -> None:
    assert layout.sniff(head) == ext
    assert ext in layout.TYPES


def test_where_media_waits_and_is_stored(tmp_path: Path) -> None:
    rid = "plaud:of_1/transaction"
    h = rid_hash(rid)
    assert layout.staged_file(tmp_path, rid, "mp3") == tmp_path / "staging" / "_media" / "plaud" / f"{h}.mp3.age"
    assert layout.sidecar_path(tmp_path, rid) == tmp_path / "staging" / "_media" / "plaud" / f"{h}.json"
    # The month is the version's own, in UTC, however the time was written.
    late = datetime(2026, 11, 1, 0, 30, tzinfo=UTC).astimezone(UTC) - timedelta(hours=1)
    assert (
        layout.stored_path(tmp_path, rid, late, "m4a") == tmp_path / "media" / "plaud" / "2026" / "10" / f"{h}.m4a.age"
    )
    assert layout.stored_files(tmp_path, rid) == [] == layout.staged_files(tmp_path, rid)
    for month in ("2026/09", "2027/01"):
        found = tmp_path / "media" / "plaud" / month / f"{h}.opus.age"
        found.parent.mkdir(parents=True)
        found.write_bytes(b"x")
    assert [p.parent.name for p in layout.stored_files(tmp_path, rid)] == ["09", "01"]
    assert layout.ext_of(found) == "opus"
    with pytest.raises(DocketError, match="isn't a source name"):
        layout.source_of("Plaud:x")


def test_a_media_file_s_family_is_rid_nesting_only() -> None:
    assert layout.owners("plaud:x/transaction/part") == ["plaud:x/transaction/part", "plaud:x/transaction", "plaud:x"]
    assert layout.owners("chat:C0OPS/1791400000.000100") == ["chat:C0OPS/1791400000.000100", "chat:C0OPS"]
    assert layout.in_family("plaud:x", "plaud:x/summary")
    assert layout.in_family("plaud:x", "plaud:x")
    assert not layout.in_family("plaud:x", "plaud:xy")


def test_a_sidecar_holds_no_content_and_a_time_with_its_zone() -> None:
    sidecar = layout.Sidecar(rid="plaud:x", key="a" * 64, ext="mp3", bytes=3, put_at=datetime(2026, 10, 1, tzinfo=UTC))
    assert set(sidecar.model_dump()) == {
        "schema_version", "rid", "key", "ext", "bytes", "put_at", "problem", "cuts", "replaces_keys"
    }  # fmt: skip
    assert sidecar.schema_version == 3 and sidecar.ranges == () and sidecar.replaces_keys == []
    with pytest.raises(ValueError, match="time zone"):
        layout.Sidecar(rid="plaud:x", key="a" * 64, ext="mp3", bytes=3, put_at=datetime(2026, 10, 1))
    # Schema 2 keeps what the file was cut by; a schema 1 sidecar was cut by none (DR-0561).
    at = datetime(2026, 10, 1, tzinfo=UTC)
    cut = layout.Sidecar(
        rid="plaud:x", key="a" * 64, ext="webm", bytes=3, put_at=at, cuts=[layout.Span(start=0, end=9)]
    )
    assert cut.ranges == ((0, 9),)
    old = layout.Sidecar.model_validate_json(
        cut.model_dump_json(exclude={"cuts", "replaces_keys"}).replace('"schema_version":3', '"schema_version":1')
    )
    assert old.schema_version == 1 and old.ranges == ()
    with pytest.raises(ValueError, match="a schema 1 sidecar holds no cuts"):
        layout.Sidecar(rid="plaud:x", key="a" * 64, ext="webm", bytes=3, put_at=at, schema_version=1, cuts=cut.cuts)
    with pytest.raises(ValueError, match="a cut ends after it starts"):
        layout.Span(start=5, end=5)
    # Schema 3 names the keys a replacement takes over (DR-0562); only schema 3 does, and each is a key id.
    with pytest.raises(ValueError, match="only a schema 3 sidecar"):
        layout.Sidecar(
            rid="plaud:x", key="a" * 64, ext="webm", bytes=3, put_at=at, schema_version=2, replaces_keys=["b" * 64]
        )
    with pytest.raises(ValueError, match="a key id is 64 hex"):
        layout.Sidecar(rid="plaud:x", key="a" * 64, ext="webm", bytes=3, put_at=at, replaces_keys=["b"])


# -- age -----------------------------------------------------------------------------------------------------------


def test_a_file_is_encrypted_while_read_and_opens_with_its_own_key_only(tmp_path: Path) -> None:
    secret, recipient = crypto.new_identity()
    assert crypto.recipient_of(secret) == recipient
    data = audio(200_000)
    path = tmp_path / "x.age"
    with path.open("wb") as out:
        crypto.encrypt(io.BytesIO(data), out, recipient)
    sealed = path.read_bytes()
    assert data[:64] not in sealed
    assert crypto.decrypt(sealed, secret) == data
    other, _ = crypto.new_identity()
    with pytest.raises(DocketError, match="doesn't open it"):
        crypto.decrypt(sealed, other)
    with pytest.raises(DocketError, match="isn't an age identity"):
        crypto.decrypt(sealed, "not a key")
    # The key id is the header's: the same wherever the file moves, another for another file.
    moved = tmp_path / "moved.age"
    path.rename(moved)
    second = tmp_path / "y.age"
    with second.open("wb") as out:
        crypto.encrypt(io.BytesIO(data), out, recipient)
    assert crypto.key_id(moved) != crypto.key_id(second)
    assert len(crypto.key_id(moved)) == 64
    assert crypto.unseal(crypto.seal("a key", recipient), secret) == "a key"


def test_a_reader_that_stops_stops_the_encryption(tmp_path: Path) -> None:
    _, recipient = crypto.new_identity()

    class Broken:
        def read(self, size: int = -1, /) -> bytes:
            raise DocketError("over the cap")

    with (tmp_path / "x.age").open("wb") as out, pytest.raises(DocketError, match="couldn't encrypt the media"):
        crypto.encrypt(Broken(), out, recipient)


def test_a_header_that_isn_t_age_or_doesn_t_end_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DocketError, match="not an age file"):
        crypto.header(b"hello")
    with pytest.raises(DocketError, match="doesn't end"):
        crypto.header(crypto.MAGIC + b"-> X25519 abc\n")
    pointer = tmp_path / "p.age"
    pointer.write_bytes(crypto.LFS_POINTER + b"\noid sha256:00\nsize 3\n")
    assert crypto.is_pointer(pointer)
    with pytest.raises(DocketError, match="LFS pointer"):
        crypto.key_id(pointer)
    assert crypto.available()


# -- the key store -------------------------------------------------------------------------------------------------


def _home(tmp_path: Path) -> Home:
    git_dir = tmp_path / "repo" / ".git"
    subprocess.run(["git", "init", "-q", str(tmp_path / "repo")], check=True)
    return Home(
        root=tmp_path / "repo",
        storage="repo",
        top=tmp_path / "repo",
        prefix="",
        git_dir=git_dir,
        private=git_dir / "docket",
    )


def test_the_keychain_holds_the_seal_and_keys_are_added_without_it(tmp_path: Path) -> None:
    chain = Keychain()
    store = KeyStore(_home(tmp_path), chain)
    assert store.info() is None
    info = store.ensure()
    assert info.store == "keyring"
    assert list(chain.items) == [("docket", f"media-store:{info.id}")]
    secret, _ = crypto.new_identity()
    store.add("a" * 64, secret, "staging/_media/plaud/x.mp3.age")
    assert store.has("a" * 64)
    assert store.count() == 1
    # Another process opens the seal from the keychain; a key it doesn't hold is None.
    again = KeyStore(store.home, chain)
    key = again.get("a" * 64)
    assert key is not None and key.identity == secret and key.path.endswith("x.mp3.age")
    assert again.get("b" * 64) is None
    assert again.get("a" * 64) == key, "the seal, read once, is kept for the next key"
    # The keychain gone: nothing opens, and nothing is said but None.
    assert KeyStore(store.home, Keychain(fails=True)).get("a" * 64) is None
    assert KeyStore(store.home, Keychain()).get("a" * 64) is None
    again.delete("a" * 64)
    assert not again.has("a" * 64)
    assert again.ensure() == info
    with pytest.raises(DocketError, match="64 hex"):
        again.has("../x")


def test_no_keychain_refuses_and_names_the_opt_in(tmp_path: Path) -> None:
    home = _home(tmp_path)
    with pytest.raises(DocketError, match="docket media keys init --store file"):
        KeyStore(home, Keychain(fails=True)).ensure()
    with pytest.raises(DocketError, match="docket media keys init --store file"):
        KeyStore(home).ensure()
    assert KeyStore(home).info() is None
    # Linux counts the keychain only when the user manager has a session bus, as for the service's token.
    nobus = KeyStore(home, Keychain(), system="Linux", runner=lambda args: subprocess.CompletedProcess(args, 0, ""))
    with pytest.raises(DocketError, match="no keychain answers"):
        nobus.ensure()
    bus = "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1/bus\n"
    withbus = KeyStore(home, Keychain(), system="Linux", runner=lambda args: subprocess.CompletedProcess(args, 0, bus))
    assert withbus.ensure().store == "keyring"


def test_a_file_store_is_opted_in_and_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path)
    store = KeyStore(home)
    info = store.init("file")
    seal = Path(os.environ["DOCKET_STATE_DIR"]) / "media-stores" / info.id
    assert seal.is_file()
    assert not seal.is_relative_to(home.root)
    secret, _ = crypto.new_identity()
    store.add("c" * 64, secret, "x")
    assert KeyStore(home).get("c" * 64) is not None
    assert store.init("file") == info
    with pytest.raises(DocketError, match="already sealed with a file store"):
        store.init("keyring")
    monkeypatch.setattr(keystore, "others_can_read", lambda path, system: True)
    with pytest.raises(DocketError, match="others can read"):
        KeyStore(home).get("c" * 64)
    monkeypatch.undo()
    seal.unlink()
    assert KeyStore(home).get("c" * 64) is None


def test_a_store_file_that_doesn_t_read_says_so(tmp_path: Path) -> None:
    home = _home(tmp_path)
    (home.private / "media").mkdir(parents=True)
    (home.private / "media" / "store.json").write_text("{", encoding="utf-8")
    with pytest.raises(DocketError, match="isn't a media key store"):
        KeyStore(home).info()


# -- the fetch's rules ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "ok"),
    [
        ("93.184.215.14", True),
        ("2606:2800:21f:cb07:6820:80da:af6b:8b2c", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("192.168.0.4", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("224.0.0.1", False),
        ("0.0.0.0", False),  # noqa: S104 - an address the rule refuses, never bound
        ("::1", False),
        ("fe80::1%eth0", False),
        ("fd00::1", False),
        ("::ffff:93.184.215.14", False),
        ("::ffff:10.0.0.1", False),
        ("::5db8:d70e", False),
        ("64:ff9b::5db8:d70e", False),
        ("64:ff9b:1::a00:1", False),
        ("2002:5db8:d70e::1", False),
        ("2001:0:5db8:d70e::1", False),
        ("not an address", False),
    ],
)
def test_only_a_global_address_that_isn_t_ipv4_in_disguise_is_allowed(address: str, ok: bool) -> None:
    assert fetch.allowed(address) is ok


def test_the_resolver_gives_each_address_once_and_names_a_host_that_doesn_t_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    infos = [
        (2, 1, 6, "", ("93.184.215.14", 443)),
        (2, 1, 6, "", ("93.184.215.14", 443)),
        (10, 1, 6, "", ("::1", 443, 0, 0)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: infos)
    assert fetch.resolve("media.example", 443) == ["93.184.215.14", "::1"]

    def fails(*args: object, **kwargs: object) -> object:
        raise OSError("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", fails)
    with pytest.raises(DocketError, match=r"media.example doesn't resolve"):
        fetch.resolve("media.example", 443)


def test_the_capped_reader_keeps_the_head_counts_the_cap_and_the_time() -> None:
    data = audio(100_000)
    reader = Capped(io.BytesIO(data), Limits(max_bytes=200_000))
    out = b"".join(iter(reader.read, b""))
    assert out == data and reader.count == len(data) and reader.head == data[:64] and reader.readable()
    over = Capped(io.BytesIO(data), Limits(max_bytes=50_000))
    with pytest.raises(DocketError, match="over the cap of 0 MiB"):
        while over.read(70_000):
            pass
    assert over.failure is not None
    clock = iter([0.0, 1000.0])
    late = Capped(io.BytesIO(data), Limits(total=10), clock=lambda: next(clock))
    with pytest.raises(DocketError, match="took longer than 0 minutes"):
        late.read()

    class Quiet:
        def read(self, size: int = -1, /) -> bytes:
            raise TimeoutError

    stalled = Capped(Quiet(), Limits(idle=60))  # pyright: ignore[reportArgumentType]
    with pytest.raises(TimeoutError):
        stalled.read()
    assert stalled.failure == "nothing came for 60 seconds"


def test_a_path_must_be_a_file_under_a_media_folder_or_the_drop_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "Downloads"
    folder.mkdir()
    inside = folder / "call.mp3"
    inside.write_bytes(audio())
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(audio())
    roots = [folder.resolve()]
    limits = Limits(max_bytes=10_000)
    stream, real = fetch.open_path(str(inside), roots, limits)
    stream.close()
    assert real == inside.resolve()
    with pytest.raises(DocketError, match="isn't under one of the source's media folders"):
        fetch.open_path(str(outside), roots, limits)
    with pytest.raises(DocketError, match="no file at"):
        fetch.open_path(str(folder / "gone.mp3"), roots, limits)
    with pytest.raises(DocketError, match="isn't a file"):
        fetch.open_path(str(folder), roots, limits)
    with pytest.raises(DocketError, match="over the cap"):
        fetch.open_path(str(inside), roots, Limits(max_bytes=10))
    link = folder / "link.mp3"
    try:
        link.symlink_to(outside)
    except OSError:
        pass
    else:
        # A link in the folder that leads out of it is judged where it leads.
        with pytest.raises(DocketError, match="isn't under one of"):
            fetch.open_path(str(link), roots, limits)
    monkeypatch.setattr(folders, "placeholder", lambda info: True)
    with pytest.raises(DocketError, match="placeholder"):
        fetch.open_path(str(inside), roots, limits)


def test_a_media_folder_is_under_home_or_the_repo_and_must_be_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    (tmp_path / "home" / "Downloads").mkdir(parents=True)
    vault_root = tmp_path / "loose" / "vault"
    vault_root.mkdir(parents=True)
    assert fetch.media_folder(vault_root, "~/Downloads") == (tmp_path / "home" / "Downloads").resolve()
    assert fetch.media_folder(vault_root, "~/Missing") is None
    # Relative to the repo that holds the vault: none here.
    assert fetch.media_folder(vault_root, "inbox") is None
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "inbox").mkdir()
    (repo / "vault").mkdir()
    assert fetch.media_folder(repo / "vault", "inbox") == (repo / "inbox").resolve()
