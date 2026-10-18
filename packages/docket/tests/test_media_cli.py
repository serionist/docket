"""`docket media` (DR-0547 to DR-0549): put, status, the drop folder, a source's hosts and folders through vault.yaml's
writer (adding asks at the console, removing doesn't), the key store's opt-in, and the staging commands' media lines."""

from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import git, sh
from media_kit import CALL, audio, in_raw, media_vault

from docket import console
from docket.cli import main
from docket.media import crypto, layout
from docket.media import put as media
from docket.media.keystore import KeyStore
from docket.vault import gate as gate_module
from docket.vault import yamledit
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.host import Lfs
from docket.vault.vault import Vault


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def dropped(name: str = "take.mp3") -> Path:
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / name).write_bytes(audio(4000))
    return drop / name


def sources(vault: Vault) -> dict[str, list[str]]:
    config = VaultConfig.load(vault.root / CONFIG_FILE)
    return {name: s.media_folders for name, s in config.sources.items()}


def test_put_status_and_the_staging_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = media_vault(tmp_path)
    root = str(vault.root)
    code, out = run(capsys, "media", "put", CALL, str(dropped()), "--vault", root)
    assert code == 0 and "staged mp3 media for plaud:rec-a" in out
    # Any https host, but never one that resolves to this machine or its network (DR-0604).
    code, out = run(capsys, "media", "put", CALL, "https://127.0.0.1/x", "--vault", root)
    assert code == 2 and "127.0.0.1 resolves to an address media is never fetched from" in out
    code, out = run(capsys, "staging", "list", "--vault", root)
    assert "1 media file(s) wait with their records" in out
    code, out = run(capsys, "staging", "promote", "--vault", root)
    assert "media     plaud: 1 file(s) stored in media/" in out
    code, out = run(capsys, "media", "status", "--vault", root)
    assert out.splitlines()[1:] == [
        "keys     1",
        "backup   1 key(s) no export holds; `docket media keys export <file>` in your own terminal",
        "waiting  0 file(s) in staging",
        "stored   1 file(s); 0 without a key here, 0 not downloaded",
    ]
    assert out.startswith("store    a file only you can read, store ")
    [stored] = layout.stored_files(vault.root, CALL)
    KeyStore(vault.home).delete(crypto.key_id(stored))
    assert "1 without a key here" in run(capsys, "media", "status", "--vault", root)[1]
    stored.write_bytes(crypto.LFS_POINTER + b"\n")
    assert "1 not downloaded" in run(capsys, "media", "status", "--vault", root)[1]


def test_promote_says_when_media_waits_or_went(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    media.put(vault, CALL, path=str(dropped()))
    monkeypatch.setattr(gate_module, "lfs_state", lambda top: Lfs(False, False, "missing"))
    _, out = run(capsys, "staging", "promote", "--vault", str(vault.root))
    assert "media     waits: git-lfs not installed" in out
    layout.sidecar_path(vault.root, CALL).unlink()
    _, out = run(capsys, "staging", "promote", "--vault", str(vault.root))
    assert "media     1 file(s) deleted, failing their checks" in out


def test_status_before_any_put(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = media_vault(tmp_path, store=False)
    _, out = run(capsys, "media", "status", "--vault", str(vault.root))
    assert out.splitlines() == [
        "store    none yet: the first put makes one in the system keychain",
        "keys     0",
        "waiting  0 file(s) in staging",
        "stored   0 file(s); 0 without a key here, 0 not downloaded",
    ]


def test_the_drop_folder_is_printed_and_made(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "media", "drop")
    assert code == 0 and Path(out.strip()) == media.drop_folder() and media.drop_folder().is_dir()


def test_adding_or_removing_a_folder_asks_nothing_and_says_who(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = media_vault(tmp_path)
    root = str(vault.root)
    # Claude, on the owner's yes in chat (DR-0599): written at once, the commit saying so.
    monkeypatch.setenv("CLAUDECODE", "1")
    _, out = run(capsys, "media", "folder", "add", "plaud", "~/Downloads", "--vault", root)
    assert "media    media folder ~/Downloads added to plaud" in out
    assert console.BY_CLAUDE in sh(vault.root, "git", "log", "-1", "--format=%B")
    monkeypatch.delenv("CLAUDECODE")
    assert sources(vault)["plaud"] == ["~/Downloads"]
    _, out = run(capsys, "media", "folder", "add", "plaud", "~/Downloads", "--vault", root)
    assert "media    plaud has media folder ~/Downloads already" in out
    _, out = run(capsys, "media", "folder", "remove", "plaud", "~/Downloads", "--vault", root)
    assert "removed from plaud" in out
    _, out = run(capsys, "media", "folder", "remove", "plaud", "~/Downloads", "--vault", root)
    assert "media    plaud has no media folder ~/Downloads already" in out
    assert sources(vault)["plaud"] == []
    subjects = sh(vault.root, "git", "log", "--format=%s").splitlines()
    assert subjects[:2] == ["Config: plaud, a media folder removed", "Config: plaud, a media folder added"]
    # The host list is gone with its commands (DR-0604).
    code, _ = run(capsys, "media", "host", "add", "plaud", "files.example", "--vault", root)
    assert code == 2


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (("folder", "add", "plaud", "C:/Users/x"), "is an absolute path"),
        (("folder", "remove", "nope", "~/x"), "source 'nope' isn't in vault.yaml"),
    ],
)
def test_a_value_or_source_that_can_t_be_one_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], args: tuple[str, ...], said: str
) -> None:
    vault = media_vault(tmp_path)
    code, out = run(capsys, "media", *args, "--vault", str(vault.root))
    assert code == 2 and said in out


def test_a_vault_yaml_of_your_own_is_edited_in_place_or_left_for_you(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = media_vault(tmp_path)
    path = vault.root / CONFIG_FILE
    path.write_text("# mine\n" + path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qam", "Mine")
    _, out = run(capsys, "media", "folder", "add", "plaud", "~/Rec", "--vault", str(vault.root))
    assert "media    media folder ~/Rec added to plaud" in out
    assert path.read_text(encoding="utf-8").startswith("# mine\n")
    monkeypatch.setattr(yamledit, "patch", lambda text, before, after: None)
    _, out = run(capsys, "media", "folder", "remove", "plaud", "~/Rec", "--vault", str(vault.root))
    assert "change sources.plaud.media_folders there" in out
    assert sources(vault)["plaud"] == ["~/Rec"]


def test_keys_init_opts_a_machine_in_to_a_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = media_vault(tmp_path, store=False)
    root = str(vault.root)
    code, out = run(capsys, "media", "keys", "init", "--vault", root)
    assert code == 2 and "docket media keys init --store file" in out
    code, out = run(capsys, "media", "keys", "init", "--store", "cloud", "--vault", root)
    assert code == 2 and "--store is keyring or file, not 'cloud'" in out
    code, out = run(capsys, "media", "keys", "init", "--store", "file", "--vault", root)
    assert code == 0 and out.startswith("keys     a file only you can read, store ")
