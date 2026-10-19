"""The history rewrite a forget writes (DR-0594, DR-0598): its files, which hold no text, and `--check-rewrite`,
which applies them to every branch tip; then run with `git filter-repo`, a dev dependency, in a `--no-local` clone:
HEAD's tree stays byte for byte; every forgotten raw line and every dropped doc line, a dependent included, in each
wording any rev gave it, leaves every commit of every branch; a kept reply naming the forgotten starter as its thread,
a released cut version, a note written after the release on the cut version at the same times, a doc rebuilt at a
removed doc's path and the owner's own files outside the vault, a token on the rid included, all stay. A folder vault
in an invented host repo."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import AtConsole, copied_with_seals, git, make_config, make_record
from media_kit import CALL, POLISHED, audio, call, in_raw, media_vault

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
from docket.vault.processed import parse_doc
from docket.vault.records import version
from docket.vault.releaselog import Kept, LoggedCut, Release
from docket.vault.vault import Vault

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


def _host(folder: Path) -> Path:
    top = folder / "host"
    top.mkdir()
    git(top, "init", "-q", "-b", "main")
    (top / "outside.txt").write_text(f"The call {CALL} was about the crane.\n", encoding="utf-8", newline="\n")
    # The owner's own note outside the vault, a token on the rid in an earlier commit only: theirs, never touched.
    mine = top / "notes" / "mine.md"
    mine.parent.mkdir()
    mine.write_text(f"Kept line.\nI said [r:{CALL}@0:01] once, in my own words.\n", encoding="utf-8", newline="\n")
    git(top, "add", "-A")
    git(top, "commit", "-qm", "Host start")
    mine.write_text("Kept line.\n", encoding="utf-8", newline="\n")
    git(top, "commit", "-qam", "The owner edits their note")
    git(top, "lfs", "install", "--local")
    return top


def put(vault: Vault, rid: str) -> None:
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "take").write_bytes(audio(4000))
    media.put(vault, rid, path=str(drop / "take"))


def filter_repo(fresh: Path, folder: Path) -> None:
    # A dev dependency, so the rewrite's promises run in CI too: uv sync installs it.
    assert shutil.which("git-filter-repo") is not None, "git filter-repo isn't installed: run uv sync"
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
    # The host's commit before the first forget, for a branch that predates them.
    before: str


# The forgets' rewrite folders, kept beside a template, so a test's copy finds them in its own vault (DR-0531).
WRITTEN = "written.json"


def _keep(folder: Path, first: rewrite.Written, second: rewrite.Written, before: str = "") -> None:
    data = {"first": first, "second": second}
    kept = {k: [w.folder.name, w.expressions, w.scoped, w.blobs] for k, w in data.items()}
    (folder / WRITTEN).write_text(json.dumps({"written": kept, "before": before}), encoding="utf-8")


def _kept(folder: Path, vault: Vault) -> tuple[rewrite.Written, rewrite.Written, str]:
    """The template's two forgets in the copy's vault. Each folder's callback names its scoped table by its absolute
    path, the template's, so it is written again as the rewrite writes it, naming the copy's own."""
    data = json.loads((folder / WRITTEN).read_text(encoding="utf-8"))
    base = vault.home.private / rewrite.FOLDER
    kept = [data["written"][k] for k in ("first", "second")]
    first, second = (rewrite.Written(base / name, e, s, b) for name, e, s, b in kept)
    for written in (first, second):
        body = rewrite.CALLBACK_BODY.replace("{path!r}", repr((written.folder / rewrite.SCOPED).as_posix()))
        (written.folder / rewrite.CALLBACK).write_text(body, encoding="utf-8", newline="\n")
    return first, second, data["before"]


@pytest.fixture
def forgot(tmp_path: Path, owner: Callable[..., AtConsole]) -> Forgot:
    """Two forgets of a folder vault: the starter and the call whole, then a held recording by its versions, released
    with a cut afterwards; and a note and a doc written again after them. Built once per run; each test a copy."""
    top = copied_with_seals(tmp_path, lambda d: _build_forgot(d, owner), f"{__name__}.forgot")
    vault = Vault.open(top / "vault")
    first, second, before = _kept(tmp_path, vault)
    return Forgot(vault, first, second, before)


def _build_forgot(folder: Path, owner: Callable[..., AtConsole]) -> Path:
    host = _host(folder)
    config = make_config("folder")
    config.sources = {"plaud": SourceConfig(), "chat": SourceConfig()}
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
    before = git(host, "rev-parse", "HEAD").strip()
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
    _keep(folder, first.written, second.written, before)
    return host


def names_its_own_table(written: rewrite.Written) -> bool:
    callback = (written.folder / rewrite.CALLBACK).read_text(encoding="utf-8")
    return repr((written.folder / rewrite.SCOPED).as_posix()) in callback


def test_a_copy_s_callbacks_name_its_own_scoped_tables(forgot: Forgot) -> None:
    assert names_its_own_table(forgot.first) and names_its_own_table(forgot.second)


def test_the_files_hold_no_text_and_leave_head_as_it_is(forgot: Forgot) -> None:
    first, second = forgot.first, forgot.second
    assert first.folder.parent == second.folder.parent and first.folder.name < second.folder.name
    for written in (first, second):
        assert rewrite.check(forgot.vault, written.folder) == rewrite.Checked([], {})
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
    checked = rewrite.check(vault, forgot.second.folder)
    assert checked.refused == ["vault/processed/again.md: the whole file, whose blob the rewrite strips"]


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
    assert rewrite.check(vault, folder).refused == ["vault/processed/kept.md: line 2"]
    # A line blanked by its blob: the binary file's too, which the callback reads as any other.
    binary = git(vault.root, "rev-parse", "HEAD:./processed/blob.bin").strip()
    (folder / rewrite.LINES).write_text("", encoding="utf-8")
    line = rewrite.line_hash(b"\0\0A ZQX token, ZQX here.")
    (folder / rewrite.SCOPED).write_text(f"{binary} {line}\n", encoding="utf-8")
    assert rewrite.check(vault, folder).refused == ["vault/processed/blob.bin: line 1"]


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
    assert "in my own words" in history and "Kept line." in history
    assert "- The pad comes Monday." in history
    assert '"text":"[cut]"' in history
    assert '"text":"The brake pad comes on Monday."' not in history


def test_two_forgets_in_one_second_get_folders_that_sort_in_order(forgot: Forgot) -> None:
    plan = planning.make(forgot.vault, "rids", [REPLY], stays=False)
    once = rewrite.write(forgot.vault, plan, rewrite.Before(), AT)
    again = rewrite.write(forgot.vault, plan, rewrite.Before(), AT)
    assert once.folder.name == "20261012T083000Z" and again.folder.name == "20261012T083000Z-2"
    assert once.folder.name < again.folder.name


def test_the_rewrite_rewrites_every_branch_and_the_check_names_one_that_still_holds_the_text(
    forgot: Forgot, tmp_path: Path
) -> None:
    vault, host = forgot.vault, forgot.vault.home.top
    lines = rewrite.history_lines(vault, forgot.second)
    assert any("removes its origin remote" in line for line in lines)
    assert any("a branch only the remote has isn't in the clone and keeps the text" in line for line in lines)
    assert not any("other branches keep the text" in line for line in lines)
    git(host, "branch", "before", forgot.before)
    # A branch from before the forget holds what it took in the vault: named, with what to do, but no refusal.
    checked = rewrite.check(vault, forgot.second.folder)
    assert checked.refused == [] and list(checked.branches) == ["before"]
    assert all(p.startswith("vault/") for p in checked.branches["before"])
    assert any(p.startswith("vault/raw/chat/") for p in checked.branches["before"])
    # A change outside the vault on any tip refuses: a copy of a forgotten raw line in the owner's own file.
    git(host, "checkout", "-q", "-b", "side")
    copy = host / "notes" / "copy.jsonl"
    copy.write_text(f'{{"rid":"{STARTER}","text":"The starter."}}\nKept.\n', encoding="utf-8", newline="\n")
    git(host, "add", "notes/copy.jsonl")
    git(host, "commit", "-qm", "A copy of my own")
    git(host, "checkout", "-q", "main")
    assert rewrite.check(vault, forgot.second.folder).refused == ["side: notes/copy.jsonl: line 1"]
    git(host, "branch", "-D", "side")
    fresh = tmp_path / "fresh"
    git(tmp_path, "clone", "-q", "--no-local", str(host), str(fresh))
    filter_repo(fresh, forgot.second.folder)
    # filter-repo turned origin's branches into the clone's own and removed origin.
    assert "before" in git(fresh, "branch", "--format=%(refname:short)").split()
    assert git(fresh, "remote").strip() == ""
    history = git(fresh, "log", "--all", "-p", "--format=%H")
    assert f'"rid":"{STARTER}"' not in history and "The crane passed." not in history
    assert f'"rid":"{REPLY}"' in history


@dataclass(frozen=True)
class Reworded:
    vault: Vault
    first: rewrite.Written
    second: rewrite.Written


REWORDED_NOTE = "recording-note:2026-10-12-reworded"
SHORT = "recording-note:2026-10-12-short"
DAY = "digest:2026-10-12-other"
KEPT = f"- Someone replied. [r:{REPLY}]"


@pytest.fixture
def reworded(tmp_path: Path, owner: Callable[..., AtConsole]) -> Reworded:
    """Docs reworded between revs, each keeping a line on a kept reply: a note on the transcript, with a line its first
    rev alone holds; a digest built on it; a note covering the transcript alone, by short anchors; and a note on a held
    recording's transcript. Then the transcript forgotten whole, and the held recording by its versions. Built once
    per run; each test a copy."""
    top = copied_with_seals(tmp_path, lambda d: _build_reworded(d, owner), f"{__name__}.reworded")
    vault = Vault.open(top)
    first, second, _ = _kept(tmp_path, vault)
    return Reworded(vault, first, second)


def _build_reworded(folder: Path, owner: Callable[..., AtConsole]) -> Path:
    vault = media_vault(folder)
    in_raw(vault)
    vault.write_run("chat", [make_record(2, rid=REPLY, source="chat", text="The reply.")])
    rev1 = [f"- The crane went through. [r:{POLISHED}@0:01]", f"- Ada was pleased with it. [r:{POLISHED}@0:02]", KEPT]
    recall.doc_put(vault, doc(vault, REWORDED_NOTE, [POLISHED, REPLY], rev1))
    rev2 = [f"- The crane went through its test. [r:{POLISHED}@0:01]", KEPT]
    recall.doc_put(vault, doc(vault, REWORDED_NOTE, [POLISHED, REPLY], rev2))
    reply = f"- A reply came. {cite(vault, REWORDED_NOTE, KEPT)}"
    built = [f"- The crane looks fine. {cite(vault, REWORDED_NOTE, rev2[0])}", reply]
    recall.doc_put(vault, doc(vault, DIGEST, [], built))
    built = [f"- The crane is fine. {cite(vault, REWORDED_NOTE, rev2[0])}", reply]
    recall.doc_put(vault, doc(vault, DIGEST, [], built))
    recall.doc_put(vault, doc(vault, DAY, [REPLY], [KEPT]))
    see = f"- See the day. {cite(vault, DAY, KEPT)}"
    recall.doc_put(vault, doc(vault, SHORT, [POLISHED], ["- The crane cleared. @0:01", see]))
    recall.doc_put(vault, doc(vault, SHORT, [POLISHED], ["- The crane cleared its check. @0:01", see]))
    gate = Gate(vault)
    gate.stage(call(HELD, text="Ada checks the pad."))
    gate.promote()
    for said in ("The pad might come Monday.", "The pad comes Monday."):
        recall.doc_put(vault, doc(vault, LATER, [HELD_POLISHED, REPLY], [f"- {said} [r:{HELD_POLISHED}@0:01]", KEPT]))
    owner("code")
    first = forget_now.forget(vault, "rids", [POLISHED])
    holds.add(vault, "plaud", ["rec-b"])
    owner("code")
    second = forget_now.forget(vault, "held", [])
    assert first.written is not None and second.written is not None
    _keep(folder, first.written, second.written)
    return vault.root


DROPPED = (
    "The crane went through.",
    "Ada was pleased with it.",
    "The crane went through its test.",
    "The crane looks fine.",
    "The crane is fine.",
    "The crane cleared.",
    "The crane cleared its check.",
    "The pad might come Monday.",
    "The pad comes Monday.",
)


def test_a_reworded_copy_s_callbacks_name_its_own_scoped_tables(reworded: Reworded) -> None:
    assert names_its_own_table(reworded.first) and names_its_own_table(reworded.second)


def test_each_rev_of_a_doc_is_judged_on_its_own_and_head_stays(reworded: Reworded) -> None:
    vault = reworded.vault
    assert rewrite.check(vault, reworded.second.folder) == rewrite.Checked([], {})
    rows = (reworded.second.folder / rewrite.SCOPED).read_text(encoding="utf-8")
    # A row for each dropped line in each blob that held it: the first forget's six doc lines over their revs, and
    # the held note's two, besides the held recording's raw lines.
    assert len(rows.splitlines()) >= len(DROPPED)
    text = "".join((reworded.second.folder / name).read_text(encoding="utf-8") for name in rewrite.FILES)
    assert not any(said in text for said in DROPPED)


def test_the_rewrite_takes_every_wording_any_rev_gave_a_dropped_line(reworded: Reworded, tmp_path: Path) -> None:
    top = reworded.vault.home.top
    head_tree = git(top, "rev-parse", "HEAD^{tree}").strip()
    fresh = tmp_path / "fresh"
    git(tmp_path, "clone", "-q", "--no-local", str(top), str(fresh))
    filter_repo(fresh, reworded.second.folder)
    assert git(fresh, "rev-parse", "HEAD^{tree}").strip() == head_tree
    history = git(fresh, "log", "--all", "-p", "--format=%H")
    assert [said for said in DROPPED if said in history] == []
    for said in ("Someone replied.", "A reply came.", "See the day."):
        assert said in history


def test_a_blob_that_doesn_t_read_as_a_doc_loses_the_words_the_forget_dropped(
    tmp_path: Path, owner: Callable[..., AtConsole]
) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    vault.write_run("chat", [make_record(2, rid=REPLY, source="chat", text="The reply.")])
    recall.doc_put(vault, doc(vault, DAY, [REPLY], [KEPT]))
    said = "- The crane cleared. @0:01"
    recall.doc_put(vault, doc(vault, SHORT, [POLISHED], [said, f"- See the day. {cite(vault, DAY, KEPT)}"]))
    path = DocStore(vault).file(SHORT)
    good = path.read_bytes()
    # The owner once committed the file broken, the line in it, then put it back as Docket wrote it.
    path.write_text(f"not a doc any more\n{said}\n", encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qam", "A broken file")
    broken = git(vault.root, "rev-parse", "HEAD:./" + path.relative_to(vault.root).as_posix()).strip()
    path.write_bytes(good)
    git(vault.root, "commit", "-qam", "Put back")
    owner("code")
    done = forget_now.forget(vault, "rids", [POLISHED])
    assert done.written is not None
    rows = (done.written.folder / rewrite.SCOPED).read_text(encoding="utf-8")
    assert f"{broken} {rewrite.line_hash(said)}" in rows
    assert rewrite.check(vault, done.written.folder) == rewrite.Checked([], {})


def test_a_doc_the_forget_leaves_at_head_loses_what_its_earlier_revs_built_on_the_family(
    tmp_path: Path, owner: Callable[..., AtConsole], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A digest whose first rev was built on the note's line about the transcript, rebuilt without it before the
    forget, and another built on it that the owner deleted: neither is touched at HEAD, and both lose the line."""
    vault = media_vault(tmp_path)
    in_raw(vault)
    vault.write_run("chat", [make_record(2, rid=REPLY, source="chat", text="The reply.")])
    line = f"- The crane passed its load test. [r:{POLISHED}@0:01]"
    recall.doc_put(vault, doc(vault, NOTE, [POLISHED, REPLY], [line, KEPT]))
    reply = f"- A reply came. {cite(vault, NOTE, KEPT)}"
    recall.doc_put(vault, doc(vault, DIGEST, [], [f"- The crane looks fine. {cite(vault, NOTE, line)}", reply]))
    recall.doc_put(vault, doc(vault, DIGEST, [], [reply]))
    brief = "digest:2026-10-11-week"
    recall.doc_put(vault, doc(vault, brief, [], [f"- The brief said it passed. {cite(vault, NOTE, line)}"]))
    git(vault.root, "rm", "-q", DocStore(vault).path(brief).as_posix())
    git(vault.root, "commit", "-qm", "The owner deletes a digest")
    other = "recording-note:2026-10-12-other"
    recall.doc_put(vault, doc(vault, other, [REPLY], ["- Unrelated words. [r:" + REPLY + "]"]))
    judged: list[bytes] = []
    real = rewrite._Judged.dropped

    def watched(self: rewrite._Judged, data: bytes, cut: object) -> set[int]:
        judged.append(data)
        return real(self, data, cut)  # type: ignore[arg-type]

    monkeypatch.setattr(rewrite._Judged, "dropped", watched)
    owner("code")
    done = forget_now.forget(vault, "rids", [POLISHED])
    assert done.written is not None
    assert [c.id for c in done.plan.docs] == [NOTE]
    # Only what names the family or cites a doc built on it is judged: the unrelated note never is.
    assert judged and not any(b"Unrelated words." in data for data in judged)
    assert rewrite.check(vault, done.written.folder) == rewrite.Checked([], {})
    top = vault.home.top
    head_tree = git(top, "rev-parse", "HEAD^{tree}").strip()
    fresh = tmp_path / "fresh"
    git(tmp_path, "clone", "-q", "--no-local", str(top), str(fresh))
    filter_repo(fresh, done.written.folder)
    assert git(fresh, "rev-parse", "HEAD^{tree}").strip() == head_tree
    history = git(fresh, "log", "--all", "-p", "--format=%H")
    assert "The crane looks fine." not in history and "The brief said it passed." not in history
    for said in ("A reply came.", "Someone replied.", "Unrelated words."):
        assert said in history


def test_refs_a_judged_blob_can_t_follow_drop_nothing(tmp_path: Path, owner: Callable[..., AtConsole]) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    vault.write_run("chat", [make_record(2, rid=REPLY, source="chat", text="The reply.")])
    line = f"- The crane passed its load test. [r:{POLISHED}@0:01]"
    recall.doc_put(vault, doc(vault, NOTE, [POLISHED, REPLY], [line, KEPT]))
    # A ref to a rev history doesn't hold, and one to a doc whose file isn't UTF-8, in a digest written by hand.
    broken = vault.root / "processed" / "recording-note" / "2026" / "10" / "2026-10-14-broken.md"
    broken.write_bytes(b"---\nid: recording-note:2026-10-14-broken\n\xff\n")
    refs = [
        f"- Gone rev. [d:{NOTE}@7#9=deadbeef]",
        "- Bad file. [d:recording-note:2026-10-14-broken@1#9=deadbeef]",
        f"- A reply came. {cite(vault, NOTE, KEPT)}",
    ]
    path = vault.root / "processed" / "digest" / "2026" / "10" / "2026-10-12-refs.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(parse_doc(doc(vault, "digest:2026-10-12-refs", [], refs), lenient=True).render(), encoding="utf-8",
                    newline="\n")  # fmt: skip
    git(vault.root, "add", "-A")
    git(vault.root, "commit", "-qm", "Docs by hand")
    owner("code")
    done = forget_now.forget(vault, "rids", [POLISHED])
    assert done.written is not None
    assert rewrite.check(vault, done.written.folder) == rewrite.Checked([], {})
    assert "Gone rev." in path.read_text(encoding="utf-8")
