"""Media keys carried to a second machine, and backed up (DR-0550): the export and the import at the owner's console,
the bundle's format pinned by its version, the counters of keys no export holds, and the media step a run gains."""

from __future__ import annotations

import getpass
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import AtConsole
from media_kit import CALL, audio, in_raw, media_vault

from docket import console, hooks
from docket.cli import main
from docket.errors import DocketError
from docket.media import backup, crypto, layout, reader
from docket.media import put as media
from docket.media.keystore import KeyStore
from docket.service.install import ServiceStatus
from docket.service.state import Service, Settings
from docket.service.sync import FINAL_STEPS, STEPS, steps
from docket.vault.config import SourceConfig
from docket.vault.vault import Vault

PASSPHRASE = "ABCD-EFGH-JKLM-NPQR-STUV-WXYZ"  # noqa: S105 - an invented passphrase


@pytest.fixture
def fixed(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(backup, "draw", lambda: PASSPHRASE)
    return PASSPHRASE


def stored(vault: Vault) -> bytes:
    """Media put and promoted: one key on this clone, its file in media/."""
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    data = audio(6000)
    (drop / "take").write_bytes(data)
    media.put(vault, CALL, path=str(drop / "take"))
    in_raw(vault)
    return data


def typed(monkeypatch: pytest.MonkeyPatch, secret: str) -> None:
    monkeypatch.setattr(console, "ask_hidden", lambda prompt: secret)


def test_an_export_carries_every_key_to_a_second_clone(
    tmp_path: Path, owner: Callable[..., AtConsole], fixed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = media_vault(tmp_path / "a")
    data = stored(first)
    keys = KeyStore(first.home)
    assert keys.unbacked() == 1
    target = tmp_path / "usb" / "docket-keys.age"
    owner("WXYZ")
    done = backup.export(first, target)
    assert (done.keys, done.kept) == (1, True)
    assert keys.unbacked() == 0
    sealed = target.read_bytes()
    assert b"AGE-SECRET-KEY" not in sealed
    # A second clone: the stored file came with git, the key with the export.
    second = media_vault(tmp_path / "b")
    [file] = layout.stored_files(first.root, CALL)
    copy = second.root / file.relative_to(first.root)
    copy.parent.mkdir(parents=True)
    shutil.copy2(file, copy)
    assert reader.state_of(second, CALL) == "no-key"
    typed(monkeypatch, fixed.lower())
    owner()
    assert backup.import_keys(second, target) == backup.Imported(added=1, had=0)
    assert reader.read(second, CALL).data == data
    assert KeyStore(second.home).unbacked() == 0, "an imported key is held by the export it came from"
    assert backup.import_keys(second, target) == backup.Imported(added=0, had=1)


def test_the_bundle_s_format_is_pinned_by_its_version(
    tmp_path: Path, owner: Callable[..., AtConsole], fixed: str
) -> None:
    vault = media_vault(tmp_path)
    stored(vault)
    target = tmp_path / "keys.age"
    owner("WXYZ")
    backup.export(vault, target)
    found: Any = json.loads(crypto.open_with_passphrase(target.read_bytes(), fixed))
    assert backup.BUNDLE_VERSION == 1
    assert list(found) == ["schema_version", "keys"]
    assert found["schema_version"] == 1
    [key] = found["keys"]
    assert list(key) == ["id", "identity", "path", "at"]
    assert key["identity"].startswith("AGE-SECRET-KEY-1")
    assert key["path"].startswith("staging/_media/plaud/")


@pytest.mark.parametrize(
    ("payload", "said"),
    [
        ('{"schema_version": 2, "keys": []}', "made by a newer Docket"),
        ('{"schema_version": 1, "keys": [{"id": "x"}]}', "isn't a media key export"),
        ("not json", "isn't a media key export"),
    ],
)
def test_a_file_that_isn_t_a_bundle_this_docket_reads_is_named(
    tmp_path: Path, owner: Callable[..., AtConsole], monkeypatch: pytest.MonkeyPatch, payload: str, said: str
) -> None:
    vault = media_vault(tmp_path)
    path = tmp_path / "x.age"
    path.write_bytes(crypto.seal_with_passphrase(payload, PASSPHRASE))
    owner()
    typed(monkeypatch, PASSPHRASE)
    with pytest.raises(DocketError, match=said):
        backup.import_keys(vault, path)


def test_a_wrong_passphrase_or_a_missing_file_opens_nothing(
    tmp_path: Path, owner: Callable[..., AtConsole], fixed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = media_vault(tmp_path)
    stored(vault)
    target = tmp_path / "keys.age"
    owner("WXYZ")
    backup.export(vault, target)
    typed(monkeypatch, "AAAA-AAAA-AAAA-AAAA-AAAA-AAAA")
    with pytest.raises(DocketError, match="the passphrase doesn't open it"):
        backup.import_keys(vault, target)
    with pytest.raises(DocketError, match="can't read"):
        backup.import_keys(vault, tmp_path / "gone.age")


def test_an_unconfirmed_passphrase_leaves_no_file(tmp_path: Path, owner: Callable[..., AtConsole], fixed: str) -> None:
    vault = media_vault(tmp_path)
    stored(vault)
    target = tmp_path / "keys.age"
    owner("ABCD")
    assert not backup.export(vault, target).kept
    assert not target.exists()
    assert KeyStore(vault.home).unbacked() == 1


def test_an_export_refuses_what_it_can_t_do(tmp_path: Path, owner: Callable[..., AtConsole], fixed: str) -> None:
    vault = media_vault(tmp_path)
    owner("WXYZ", "WXYZ")
    with pytest.raises(DocketError, match="holds no media keys yet"):
        backup.export(vault, tmp_path / "keys.age")
    stored(vault)
    taken = tmp_path / "taken.age"
    taken.write_bytes(b"mine")
    with pytest.raises(DocketError, match="is there already"):
        backup.export(vault, taken)
    assert taken.read_bytes() == b"mine"
    info = KeyStore(vault.home).info()
    assert info is not None
    seal = KeyStore(vault.home)._seal_file(info.id)  # pyright: ignore[reportPrivateUsage]
    seal.unlink()
    with pytest.raises(DocketError, match="can't open its media key store's seal"):
        backup.export(vault, tmp_path / "keys.age")


def test_export_and_import_only_at_the_owner_s_console(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = media_vault(tmp_path)
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(DocketError, match="Claude Code's"):
        backup.export(vault, tmp_path / "keys.age")
    with pytest.raises(DocketError, match="Claude Code's"):
        backup.import_keys(vault, tmp_path / "keys.age")
    monkeypatch.delenv("CLAUDECODE")
    # The tests' own stdin and stdout aren't a console: a pipe is refused the same way.
    with pytest.raises(DocketError, match="asks you at a console"):
        backup.export(vault, tmp_path / "keys.age")


def test_the_passphrase_is_drawn_fresh_from_letters_with_no_look_alikes() -> None:
    drawn = {backup.draw() for _ in range(20)}
    assert len(drawn) == 20
    for one in drawn:
        groups = one.split("-")
        assert [len(g) for g in groups] == [4] * 6
        assert set("".join(groups)) <= set(console.CODE_LETTERS)


def test_the_commands_and_every_counter_of_keys_no_export_holds(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    owner: Callable[..., AtConsole],
    fixed: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = media_vault(tmp_path)
    stored(vault)
    root = str(vault.root)
    main(["media", "status", "--vault", root])
    main(["vault", "status", "--vault", root])
    out = capsys.readouterr().out
    assert "backup   1 key(s) no export holds; `docket media keys export <file>` in your own terminal" in out
    assert "media    1 key(s) no export holds; `docket media keys export <file>` in your own terminal" in out
    service = Service(Settings(port=8741, vault=vault.root, token=lambda: "t0ken-for-tests-only"))
    try:
        assert service.status().media_unbacked == 1
    finally:
        service.writer.close()
    owner("WXYZ")
    assert main(["media", "keys", "export", str(tmp_path / "keys.age"), "--vault", root]) == 0
    out = capsys.readouterr().out
    assert fixed in out
    assert "keys     1 exported to" in out
    main(["media", "status", "--vault", root])
    main(["vault", "status", "--vault", root])
    out = capsys.readouterr().out
    assert "backup   every key is in an export" in out
    assert "media    " not in out
    owner("n")
    assert main(["media", "keys", "export", str(tmp_path / "again.age"), "--vault", root]) == 0
    assert "keys     not exported: the passphrase wasn't confirmed" in capsys.readouterr().out
    owner()
    typed(monkeypatch, fixed)
    assert main(["media", "keys", "import", str(tmp_path / "keys.age"), "--vault", root]) == 0
    assert "keys     0 added, 1 held already" in capsys.readouterr().out


def test_the_session_start_asks_for_a_backup_while_a_key_has_none() -> None:
    def st(**body: Any) -> ServiceStatus:
        return ServiceStatus(None, True, 8741, {"vault": "D:/work/vault", **body}, "keyring", None, {}, None)

    out = hooks.session_start(lambda: st(media_unbacked=2), {})
    assert "media: 2 key(s) no export holds yet" in out["systemMessage"]
    assert (
        "media:" not in hooks.session_start(lambda: st(media_unbacked=0), {})["hookSpecificOutput"]["additionalContext"]
    )


def test_a_run_gains_the_media_step_only_where_media_can_come_from() -> None:
    plain = SourceConfig()
    assert steps(False, "run-1", plain) == STEPS.format(run="run-1")
    assert steps(True, "run-1", plain) == FINAL_STEPS.format(run="run-1")
    hosted = steps(False, "run-1", SourceConfig(media_hosts=["media.example"]))
    assert hosted.startswith(STEPS.format(run="run-1"))
    assert "8. Media: after closing" in hosted and "media_put(rid, url)" in hosted
    assert "6. Media: after closing" in steps(True, "run-1", SourceConfig(media_folders=["~/Downloads"]))


def test_a_hidden_answer_comes_from_getpass_and_the_end_of_input_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(getpass, "getpass", lambda prompt: "abcd-efgh")
    assert console.ask_hidden("Its passphrase: ") == "abcd-efgh"

    def ended(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr(getpass, "getpass", ended)
    assert console.ask_hidden("Its passphrase: ") == "q"


@pytest.mark.parametrize(
    ("body", "held"),
    [
        ("not json", set[str]()),
        ('{"ids": "abc"}', set[str]()),
        ('{"nothing": []}', set[str]()),
        ('{"ids": ["a", 1]}', {"a"}),
    ],
)
def test_a_list_of_exported_ids_that_doesn_t_read_holds_none_of_them(tmp_path: Path, body: str, held: set[str]) -> None:
    vault = media_vault(tmp_path)
    keys = KeyStore(vault.home)
    (keys.folder / "exported.json").write_text(body, encoding="utf-8")
    assert keys.exported() == held
