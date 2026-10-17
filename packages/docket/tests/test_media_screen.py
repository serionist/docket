"""Media's four checks one by one, on what a vault holds (DR-0547), and the withheld marks' file kept safe against a
torn line or a line that isn't one."""

from __future__ import annotations

from pathlib import Path

from media_kit import CALL, POLISHED, audio, media_vault

from docket.media import crypto, layout
from docket.media import put as media
from docket.media import staged as media_staged
from docket.media.keystore import KeyStore
from docket.media.screen import Screen
from docket.vault.config import VaultConfig
from docket.vault.gate import Gate
from docket.vault.records import RawRecord
from docket.vault.spans import RawLookup
from docket.vault.staging import rid_hash
from docket.vault.vault import Vault


def screen(vault: Vault, config: VaultConfig, *, left_out: bool = False) -> Screen:
    gate = Gate(vault)
    found = gate._screen(config)  # pyright: ignore[reportPrivateUsage]
    if left_out:
        found.left_out = lambda record: True
    return found


def test_each_reason_the_source_and_its_text_give(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    config = vault.config
    plaud = config.sources["plaud"]
    gone = config.model_copy(update={"sources": {k: v for k, v in config.sources.items() if k != "plaud"}})
    assert screen(vault, gone).why(CALL) == "source plaud isn't in vault.yaml in force"
    off = config.model_copy(
        update={"sources": {**config.sources, "plaud": plaud.model_copy(update={"enabled": False})}}
    )
    assert screen(vault, off).why(CALL) == "source plaud is off in vault.yaml"
    private = config.model_copy(
        update={"sources": {**config.sources, "plaud": plaud.model_copy(update={"sensitivity": "private"})}}
    )
    assert screen(vault, private).why(CALL) == "source plaud is private: nothing from it is stored"
    said = config.model_copy(update={"never_store": config.never_store.model_copy(update={"keywords": ["load test"]})})
    assert screen(vault, said).why(CALL) == f"a record of {CALL} matches a never-store value"
    assert screen(vault, config, left_out=True).why(CALL) == f"a release leaves a record of {CALL} out"
    assert screen(vault, config).why(CALL) is None


def test_a_rid_that_isn_t_one_nests_nothing(tmp_path: Path) -> None:
    assert RawLookup(tmp_path).nested("not a rid") == []


def test_the_withheld_file_skips_what_doesn_t_read_and_ends_a_torn_line(tmp_path: Path) -> None:
    root = tmp_path
    path = media_staged.withheld_path(root)
    path.parent.mkdir(parents=True)
    path.write_text('not json\n{"other": 1}\n{"rid": 5}\n{"rid": "ab', encoding="utf-8")
    assert media_staged.read_withheld(root) == frozenset()
    assert media_staged.withhold(root, POLISHED) == 2
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[-2:] == [f'{{"rid": "{rid_hash(POLISHED)}"}}', f'{{"rid": "{rid_hash(CALL)}"}}']
    assert media_staged.read_withheld(root) == {rid_hash(POLISHED), rid_hash(CALL)}


def test_staged_media_s_drop_keeps_every_key_when_a_stored_file_can_t_be_read(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    keys = KeyStore(vault.home)
    drop = media.drop_folder()
    drop.mkdir(parents=True)
    (drop / "a").write_bytes(audio())
    media.put(vault, CALL, path=str(drop / "a"))
    [file] = layout.staged_files(vault.root, CALL)
    key = crypto.key_id(file)
    stored = layout.stored_path(vault.root, CALL, RawRecord.model_validate(_any_record(vault)).ts, "mp3")
    stored.parent.mkdir(parents=True)
    stored.write_bytes(crypto.LFS_POINTER + b"\n")
    file.unlink()
    media_staged.delete(vault.root, keys, CALL)
    assert keys.has(key), "a stored file this machine can't read might be the one the key opens"


def _any_record(vault: Vault) -> dict[str, object]:
    return Gate(vault).list().entries[0].staged.record.model_dump()
