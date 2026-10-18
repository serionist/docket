"""The history rewrite a forget writes (DR-0594): its files, which hold no text, and `--check-rewrite`, which applies
them to HEAD; then run with `git filter-repo` in a `--no-local` clone where it is installed, skipped where it isn't:
HEAD's tree stays byte for byte; every forgotten raw line and every dropped doc
line, a dependent included, leaves every commit; a kept reply naming the forgotten starter as its thread, a released
cut version, a note written after the release on the cut version at the same times, a doc rebuilt at a removed doc's
path and a line outside the vault naming the rid all stay. A folder vault in an invented host repo."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import AtConsole, git, make_config, make_record
from media_kit import CALL, POLISHED, audio, call

from docket import recall
from docket.forget import now as forget_now
from docket.forget import plan as planning
from docket.forget import rewrite
from docket.media import put as media
from docket.media.keystore import KeyStore
from docket.vault import cursors, holds, releaselog
from docket.vault.config import SourceConfig
from docket.vault.docstore import DocStore
from docket.vault.gate import Gate
from docket.vault.records import version
from docket.vault.releaselog import Kept, LoggedCut, Release
from docket.vault.vault import Vault

FILTER_REPO = pytest.mark.skipif(shutil.which("git-filter-repo") is None, reason="git filter-repo isn't installed")

STARTER = "chat:C0TEST/1791000001.000100"
REPLY = "chat:C0TEST/1791000002.000100"
HELD = "plaud:rec-b"
HELD_POLISHED = f"{HELD}/transaction_polish"
NOTE = "recording-note:2026-10-12-crane-check"
LATER = "recording-note:2026-10-13-brake-pad"
DIGEST = "digest:2026-10-12-day"
GONE = "recording-note:2026-10-12-gone"
AT = datetime(2026, 10, 12, 8, 30, tzinfo=UTC)


def doc(vault: Vault, doc_id: str, covers: list[str], lines: list[str]) -> str:
    """A doc's text for a put, at the rev its history has reached, so a removed doc can be put again."""
    rev = max((n for n, _ in DocStore(vault).history(doc_id)), default=0)
    covered = "covers:\n" + "".join(f"  - {rid}\n" for rid in covers) if covers else "covers: []\n"
    front = f"id: {doc_id}\ntype: {doc_id.partition(':')[0]}\n" + (f"rev: {rev}\n" if rev else "") + covered
    return f"---\n{front}by: claude-test, sync 2026-10-12\n---\n\n# A note\n\n" + "\n".join(lines) + "\n"


def cite(vault: Vault, doc_id: str, text: str) -> str:
    found = DocStore(vault).get(doc_id)
    assert found is not None
    return f"[d:{doc_id}@{found.meta.rev}#{next(n for n, said in found.lines() if said == text)}]"


@pytest.fixture
def host(tmp_path: Path) -> Path:
    top = tmp_path / "host"
    top.mkdir()
    git(top, "init", "-q", "-b", "main")
    (top / "outside.txt").write_text(f"The call {CALL} was about the crane.\n", encoding="utf-8", newline="\n")
    git(top, "add", "-A")
    git(top, "commit", "-qm", "Host start")
    git(top, "lfs", "install", "--local")
    return top


def put(vault: Vault, rid: str) -> None:
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "take").write_bytes(audio(4000))
    media.put(vault, rid, path=str(drop / "take"))


def filter_repo(fresh: Path, folder: Path) -> None:
    args = [
        "git", "filter-repo", "--replace-text", str(folder / rewrite.LINES), "--blob-callback",
        str(folder / rewrite.CALLBACK), "--strip-blobs-with-ids", str(folder / rewrite.BLOBS),
    ]  # fmt: skip
    subprocess.run(args, cwd=fresh, check=True, capture_output=True)


@dataclass(frozen=True)
class Forgot:
    vault: Vault
    first: rewrite.Written
    second: rewrite.Written


@pytest.fixture
def forgot(host: Path, owner: Callable[..., AtConsole]) -> Forgot:
    """Two forgets of a folder vault: the starter and the call whole, then a held recording by its versions, released
    with a cut afterwards; and a note and a doc written again after them."""
    config = make_config("folder")
    config.sources = {"plaud": SourceConfig(media_hosts=["media.example"]), "chat": SourceConfig()}
    vault = Vault.init(host / "vault", config, "test-box")
    cursors.save(vault, "plaud", cursors.Cursor())
    KeyStore(vault.home).init("file")
    vault.write_run(
        "chat",
        [
            make_record(1, rid=STARTER, source="chat", text="The starter."),
            make_record(2, rid=REPLY, source="chat", thread=STARTER, text="The reply."),
        ],
    )
    gate = Gate(vault)
    gate.stage(call())
    put(vault, CALL)
    gate.stage(call(HELD, text="Ada checks the pad."))
    gate.promote()
    lines = [f"- The crane passed. [r:{POLISHED}@0:01]", f"- Someone replied. [r:{REPLY}]"]
    recall.doc_put(vault, doc(vault, NOTE, [POLISHED, REPLY], lines))
    recall.doc_put(vault, doc(vault, DIGEST, [], [f"- The crane is fine. {cite(vault, NOTE, lines[0])}"]))
    on_held = [f"- The pad comes Monday. [r:{HELD_POLISHED}@0:01]"]
    recall.doc_put(vault, doc(vault, LATER, [HELD_POLISHED], on_held))
    recall.doc_put(vault, doc(vault, GONE, [CALL], [f"- A whole call. [r:{CALL}]"]))
    # The first forget: the starter and the call, whole.
    owner("code")
    first = forget_now.forget(vault, "rids", [STARTER, CALL])
    # The second: the held recording by its versions in raw; then its release, with a cut.
    holds.add(vault, "plaud", ["rec-b"])
    owner("code")
    second = forget_now.forget(vault, "held", [])
    assert first.written is not None and second.written is not None
    recording = next(r for r in call(HELD) if r.rid == HELD)
    release = Release(
        id="rec-b",
        cuts=[LoggedCut(start=10_000, end=19_000)],
        keep=[Kept(rid=HELD, v=version(recording))],
        at=AT,
        machine="test-box",
    )
    releaselog.add(vault, "plaud", release)
    holds.remove(vault, "plaud", ["rec-b"])
    staged = Gate(vault)
    staged.stage(call(HELD, text="Ada checks the pad."))
    for entry in staged.list(state="pending").entries:
        staged.classify(entry.key, "work")
    staged.promote()
    # After the release: a note on the cut version at the same times, and a doc rebuilt at the removed one's path.
    recall.doc_put(vault, doc(vault, LATER, [HELD_POLISHED], on_held))
    recall.doc_put(vault, doc(vault, GONE, [REPLY], [f"- Rebuilt on the reply. [r:{REPLY}]"]))
    return Forgot(vault, first.written, second.written)


def test_the_files_hold_no_text_and_leave_head_as_it_is(forgot: Forgot) -> None:
    first, second = forgot.first, forgot.second
    assert first.folder.parent == second.folder.parent and first.folder.name < second.folder.name
    for written in (first, second):
        assert rewrite.check(forgot.vault, written.folder) == []
        text = "".join((written.folder / name).read_text(encoding="utf-8") for name in rewrite.FILES)
        for said in ("The crane passed", "A whole call", "The brake pad", "Ada checks"):
            assert said not in text
    lines = (second.folder / rewrite.LINES).read_text(encoding="utf-8")
    # The newest folder holds the earlier one's expressions, and the version forget's lines only by blob.
    assert (first.folder / rewrite.LINES).read_text(encoding="utf-8") in lines and HELD not in lines
    assert len((second.folder / rewrite.SCOPED).read_text(encoding="utf-8").splitlines()) > first.scoped
    assert set((first.folder / rewrite.BLOBS).read_text(encoding="utf-8").split()) <= set(
        (second.folder / rewrite.BLOBS).read_text(encoding="utf-8").split()
    )


def test_check_refuses_a_head_that_holds_a_blob_the_rewrite_strips(forgot: Forgot) -> None:
    vault = forgot.vault
    stripped = (forgot.second.folder / rewrite.BLOBS).read_text(encoding="utf-8").split()
    # A doc rebuilt word for word as a rev of a removed one is that blob again.
    old = next(b for b in stripped if git(vault.root, "cat-file", "-t", b).strip() == "blob")
    (vault.root / "processed" / "again.md").write_bytes(
        subprocess.run(["git", "cat-file", "blob", old], cwd=vault.root, capture_output=True, check=True).stdout
    )
    git(vault.root, "add", "processed/again.md")
    git(vault.root, "commit", "-qm", "A file by hand")
    problems = rewrite.check(vault, forgot.second.folder)
    assert problems == ["vault/processed/again.md: the whole file, whose blob the rewrite strips"]


def test_check_reads_expressions_as_filter_repo_does_and_leaves_binary_files_alone(
    forgot: Forgot, tmp_path: Path
) -> None:
    vault = forgot.vault
    (vault.root / "processed" / "kept.md").write_text(
        "Kept by hand.\nA ZQX token, ZQX here.\n", encoding="utf-8", newline="\n"
    )
    (vault.root / "processed" / "blob.bin").write_bytes(b"\0\0A ZQX token, ZQX here.\n")
    git(vault.root, "add", "processed/kept.md", "processed/blob.bin")
    git(vault.root, "commit", "-qm", "Two files by hand")
    folder = tmp_path / "rewrite"
    folder.mkdir()
    (folder / rewrite.LINES).write_text("\nliteral:ZQX token\nZQX here==>there\n", encoding="utf-8")
    (folder / rewrite.SCOPED).write_text("", encoding="utf-8")
    (folder / rewrite.BLOBS).write_text("", encoding="utf-8")
    # A literal with no replacement takes filter-repo's own; the binary file's first bytes hold a NUL.
    assert rewrite.check(vault, folder) == ["vault/processed/kept.md: line 2"]
    # A line blanked by its blob: the binary file's too, which the callback reads as any other.
    binary = git(vault.root, "rev-parse", "HEAD:./processed/blob.bin").strip()
    (folder / rewrite.LINES).write_text("", encoding="utf-8")
    line = rewrite.line_hash(b"\0\0A ZQX token, ZQX here.")
    (folder / rewrite.SCOPED).write_text(f"{binary} {line}\n", encoding="utf-8")
    assert rewrite.check(vault, folder) == ["vault/processed/blob.bin: line 1"]


@FILTER_REPO
def test_the_rewrite_takes_what_the_forgets_took_and_leaves_head_and_the_rest(forgot: Forgot, tmp_path: Path) -> None:
    host = forgot.vault.home.top
    head_tree = git(host, "rev-parse", "HEAD^{tree}").strip()
    fresh = tmp_path / "fresh"
    git(tmp_path, "clone", "-q", "--no-local", str(host), str(fresh))
    # The newest folder holds every earlier forget's too, and is the one to run: once one runs, the blob ids the next
    # names are gone.
    filter_repo(fresh, forgot.second.folder)
    assert git(fresh, "rev-parse", "HEAD^{tree}").strip() == head_tree
    history = git(fresh, "log", "--all", "-p", "--format=%H")
    assert f'"rid":"{STARTER}"' not in history
    assert f'"rid":"{CALL}' not in history
    assert "The crane passed." not in history and "The crane is fine." not in history
    assert "A whole call." not in history
    assert f'"rid":"{REPLY}"' in history and f'"thread":"{STARTER}"' in history
    assert "Rebuilt on the reply." in history
    assert f"The call {CALL} was about the crane." in history
    assert "- The pad comes Monday." in history
    assert '"text":"[cut]"' in history
    assert '"text":"The brake pad comes on Monday."' not in history


def test_two_forgets_in_one_second_get_folders_that_sort_in_order(forgot: Forgot) -> None:
    plan = planning.make(forgot.vault, "rids", [REPLY], stays=False)
    once = rewrite.write(forgot.vault, plan, rewrite.Before(), AT)
    again = rewrite.write(forgot.vault, plan, rewrite.Before(), AT)
    assert once.folder.name == "20261012T083000Z" and again.folder.name == "20261012T083000Z-2"
    assert once.folder.name < again.folder.name
