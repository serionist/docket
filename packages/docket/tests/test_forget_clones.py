"""Two clones of one vault through a bare remote (DR-0581, DR-0595): clone A forgets and pushes; clone B, which imported
A's key, reads nothing of it and refuses its old index the moment it pulls, loses the key at its next gate call or
read, and rebuilds without it. A copy B stored itself before its pull goes at its next gate call, its key on a
keys-only line; and a union merge that brings back a line two forgets took reads as gone and goes again. Every vault is
a temp one; invented data only."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from docket_testkit import POLISHED as TRANSCRIPT
from docket_testkit import AtConsole, empty_vault, git, make_config, make_record, plaud_records, sh
from forget_kit import forget
from ledger_kit import ADD_FIELDS, NOW
from media_kit import CALL, POLISHED, audio, call, in_raw, media_vault

from docket import index, recall
from docket.errors import DocketError
from docket.forget import now as forget_now
from docket.ledger import ops as ledger_ops
from docket.ledger import store
from docket.ledger.ops import MARK
from docket.media import layout, reader
from docket.media import put as media
from docket.media import staged as media_staged
from docket.media.keystore import KeyStore
from docket.vault import cursors, forgotten
from docket.vault.gate import Gate
from docket.vault.vault import Vault


def put(vault: Vault, head: bytes = b"ID3\x04\x00") -> None:
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "take").write_bytes(audio(4000, head=head))
    media.put(vault, CALL, path=str(drop / "take"))


def stored_key(vault: Vault) -> str:
    (path,) = layout.stored_files(vault.root, CALL)
    key = media_staged.key_of(path)
    assert key is not None
    return key


def clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    """Clone B of the remote, a machine of its own, with a key store of its own."""
    sh(tmp_path, "git", "clone", "-q", "-b", "main", str(tmp_path / "remote.git"), str(tmp_path / "b"))
    monkeypatch.setenv("DOCKET_MACHINE", "other-box")
    second = Vault.open(tmp_path / "b")
    # Git LFS's filter is set per clone: a clone of its own sets it, as the setup skill does.
    second.git.lfs_install()
    KeyStore(second.home).init("file")
    return second


def with_remote(tmp_path: Path, first: Vault) -> None:
    sh(tmp_path, "git", "init", "-q", "--bare", "remote.git")
    first.git.run("remote", "add", "origin", str(tmp_path / "remote.git"))
    push(first)


def push(vault: Vault) -> None:
    vault.git.run("push", "-q", "origin", "main")


def forget_now_at(vault: Vault, owner: Callable[..., AtConsole], *rids: str) -> forget_now.Forgot:
    owner("code")
    done = forget_now.forget(vault, "rids", list(rids))
    assert done.applied is not None
    return done


def test_a_forget_reaches_a_clone_that_imported_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: Callable[..., AtConsole]
) -> None:
    first = media_vault(tmp_path)
    put(first)
    in_raw(first)
    key = stored_key(first)
    with_remote(tmp_path, first)
    second = clone(tmp_path, monkeypatch)
    # B imported A's key export.
    found = KeyStore(first.home).get(key)
    assert found is not None
    KeyStore(second.home).add(key, found.identity, found.path)
    index.rebuild(second)
    assert index.search(second, "crane").groups
    forget_now_at(first, owner, CALL)
    push(first)
    assert second.sync().fetched
    # At once: nothing of it reads, and the index built before the pull is refused.
    assert second.rawlog.read(CALL) == [] and second.rawlog.read(POLISHED) == []
    assert index.status(second).stale is not None
    with pytest.raises(DocketError, match="built before a forget"):
        index.search(second, "crane")
    assert KeyStore(second.home).has(key)
    # Its next gate call deletes the key; its next update rebuilds without the family.
    Gate(second).list()
    assert not KeyStore(second.home).has(key)
    assert index.update(second).rebuilt
    assert not index.search(second, "crane").groups


def test_a_clone_that_only_reads_loses_the_key_at_its_reader_s_first_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: Callable[..., AtConsole]
) -> None:
    first = media_vault(tmp_path)
    put(first)
    in_raw(first)
    key = stored_key(first)
    with_remote(tmp_path, first)
    second = clone(tmp_path, monkeypatch)
    found = KeyStore(first.home).get(key)
    assert found is not None
    KeyStore(second.home).add(key, found.identity, found.path)
    forget_now_at(first, owner, CALL)
    push(first)
    second.sync()
    # The pull took the file away; the reader meets the listed key all the same, for any rid.
    assert reader.state_of(second, CALL) == "forgotten"
    assert KeyStore(second.home).has(key)
    forget_apply_keys = forgotten.load(second.root, second.home).keys
    assert key in forget_apply_keys
    from docket.forget import apply as forget_apply

    assert forget_apply.drop_listed_keys(second) == 1
    assert not KeyStore(second.home).has(key)


def test_a_copy_the_clone_stored_before_its_pull_goes_at_its_next_gate_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: Callable[..., AtConsole]
) -> None:
    first = media_vault(tmp_path)
    with_remote(tmp_path, first)
    second = clone(tmp_path, monkeypatch)
    cursors.save(second, "plaud", cursors.Cursor())
    # A ingests, stores and forgets the call, and pushes.
    put(first)
    in_raw(first)
    forget_now_at(first, owner, CALL)
    push(first)
    # B ran the same source before it pulled: its own run file, and its own audio under a key no line names.
    Gate(second).stage(call())
    put(second, head=b"OggS" + b"\x00" * 24)
    Gate(second).promote()
    own = stored_key(second)
    assert second.sync().rebased
    assert second.rawlog.read(CALL) == []
    Gate(second).list()
    assert not layout.stored_files(second.root, CALL)
    assert not KeyStore(second.home).has(own)
    assert own in forgotten.load(second.root, second.home).keys
    assert git(second.root, "log", "-1", "--format=%s").strip() == "Forget: applied on other-box"
    assert git(second.root, "status", "--porcelain", "--", ".").strip() == ""
    assert not any(CALL in line for run in second.rawlog.run_files() for line in _lines(run.path))


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_a_union_merge_that_brings_back_forgotten_lines_is_finished_by_the_next_gate_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = media_vault(tmp_path, store=False)
    chat = [make_record(n, source="chat") for n in (1, 2, 3)]
    first.write_run("chat", chat)
    with_remote(tmp_path, first)
    second = clone(tmp_path, monkeypatch)
    (run,) = [r.path.relative_to(first.root).as_posix() for r in first.rawlog.run_files() if r.source == "chat"]
    # A forgets the first message and B the second: neighbouring lines of one run file.
    forget(first, rids=[chat[0].rid])
    Gate(first).list()
    push(first)
    forget(second, rids=[chat[1].rid], machine="other-box")
    Gate(second).list()
    ident = ("-c", "user.name=Ada Example", "-c", "user.email=ada@example.invalid")
    sh(second.root, "git", *ident, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")
    merged = _lines(second.root / run)
    # The union merge kept both sides' lines: the forgotten ones are back in the file.
    assert any(chat[0].rid in line for line in merged) or any(chat[1].rid in line for line in merged)
    reopened = Vault.open(second.root)
    assert reopened.rawlog.read(chat[0].rid) == [] and reopened.rawlog.read(chat[1].rid) == []
    assert [r.rid for r in reopened.rawlog.read(chat[2].rid)] == [chat[2].rid]
    Gate(reopened).list()
    left = _lines(second.root / run)
    assert len(left) == 1 and chat[2].rid in left[0]
    assert git(second.root, "log", "-1", "--format=%s").strip() == "Forget: applied on other-box"


# -- the ledger (DR-0694, DR-0696) -------------------------------------------------------------------------------------

SAID = "Ada said Tuesday at the venue"
ANCHORED = "As the channel had it"


def ledger_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Vault, Vault]:
    """A with an item from a chat message, pushed; B its clone, which logs on that item and anchors another one's log
    on the message before it pulls A's forget of it."""
    first = empty_vault(tmp_path, make_config())
    first.write_run("chat", [make_record(1), make_record(2)])
    store.write(first, [{"op": "add", "fields": {**ADD_FIELDS, "source": make_record(1).rid}}], now=NOW)
    with_remote(tmp_path, first)
    second = clone(tmp_path, monkeypatch)
    other = {**ADD_FIELDS, "title": "Book the venue", "source": make_record(2).rid}
    store.write(second, [{"op": "log", "item": "A-0001", "note": SAID}, {"op": "add", "fields": other}], now=NOW)
    store.write(second, [{"op": "log", "item": "A-0002", "note": f"{ANCHORED} [r:{make_record(1).rid}]"}], now=NOW)
    return first, second


def said(vault: Vault) -> list[str]:
    """The op lines on disk that still say what B wrote on the forgotten message."""
    return [line for _, path in ledger_ops.listing(vault.root)[0] for line in _lines(path)
            if SAID in line or ANCHORED in line]  # fmt: skip


def test_a_clone_s_own_ops_from_before_its_pull_are_masked_and_listed_at_its_next_gate_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: Callable[..., AtConsole]
) -> None:
    first, second = ledger_pair(tmp_path, monkeypatch)
    forget_now_at(first, owner, make_record(1).rid)
    push(first)
    assert second.sync().rebased
    # Read at once, the item a shell; B's log on it and its anchored log still on disk until the gate call.
    assert len(said(second)) == 2
    with pytest.raises(DocketError, match="A-0001 is a shell a forget left; it takes no log"):
        store.write(second, [{"op": "log", "item": "A-0001", "note": "Again"}], now=NOW)
    Gate(second).list()
    assert said(second) == []
    listed = forgotten.load(second.root, second.home)
    mine = [line.op for line in ledger_ops.read_all(second.root).lines if line.op.machine == "other-box"]
    assert {o.id for o in mine if o.op == "log"} <= listed.ops and not {
        o.id for o in mine if o.op == "add"
    } & listed.ops
    assert git(second.root, "log", "-1", "--format=%s").strip() == "Forget: applied on other-box"
    assert git(second.root, "status", "--porcelain", "--", ".").strip() == ""
    assert second.check() == []
    # The shell is still dropped and archived when due, its notes written as the marker.
    dropped = store.write(second, [{"op": "drop", "item": "A-0001", "note": "Forgotten"}], now=NOW)
    archived = store.write(second, [{"op": "archive", "note": "The sync's due archive"}], now=NOW + timedelta(days=15))
    assert [o.note for o in (*dropped.ops, *archived.ops)] == [MARK, MARK]


def test_ops_a_pull_brought_from_before_the_other_clone_s_gate_call_are_masked_here(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: Callable[..., AtConsole]
) -> None:
    first, second = ledger_pair(tmp_path, monkeypatch)
    forget_now_at(first, owner, make_record(1).rid)
    push(first)
    second.sync()
    # B pushes before its own gate call has masked anything.
    push(second)
    assert first.sync().fetched and len(said(first)) == 2
    Gate(first).list()
    assert said(first) == []
    assert git(first.root, "log", "-1", "--format=%s").strip() == "Forget: applied on test-box"
    assert first.check() == []


NOTE = "recording-note:2026-10-14-supplier-sync"
NOTE_PATH = "processed/recording-note/2026/10/2026-10-14-supplier-sync.md"
LINKED = "The supplier sync"


def test_a_clone_s_own_op_naming_a_doc_a_pulled_forget_removed_is_masked_at_its_next_gate_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: Callable[..., AtConsole]
) -> None:
    first = empty_vault(tmp_path, make_config())
    first.write_run("plaud", plaud_records("transaction_polish"))
    recall.doc_put(
        first,
        f"---\nid: {NOTE}\ntype: recording-note\ncovers:\n  - {TRANSCRIPT}\nby: claude-test, sync 2026-10-14\n---\n\n"
        f"# Supplier sync\n\n- Ten million, signed by Friday. [r:{TRANSCRIPT}@10:42]\n",
    )
    store.write(first, [{"op": "add", "fields": dict(ADD_FIELDS)}], now=NOW)
    with_remote(tmp_path, first)
    second = clone(tmp_path, monkeypatch)
    # B links the note by its id and by its path before it pulls A's forget, which removes it.
    store.write(second, [
        {"op": "material", "item": "A-0001", "ref": NOTE, "label": f"{LINKED}, by id"},
        {"op": "material", "item": "A-0001", "ref": NOTE_PATH, "label": f"{LINKED}, by path"},
    ], now=NOW)  # fmt: skip
    forget_now_at(first, owner, TRANSCRIPT)
    push(first)
    assert second.sync().rebased and not (second.root / NOTE_PATH).exists()
    Gate(second).list()
    materials = {line.op.id for line in ledger_ops.read_all(second.root).lines if line.op.op == "material"}
    assert len(materials) == 2 and materials <= forgotten.load(second.root, second.home).ops
    assert not [line for _, path in ledger_ops.listing(second.root)[0] for line in _lines(path) if LINKED in line]
    assert git(second.root, "log", "-1", "--format=%s").strip() == "Forget: applied on other-box"
    assert second.check() == []
