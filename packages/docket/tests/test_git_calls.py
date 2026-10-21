"""The git calls each write makes, as an exact list per path (#132, DR-0529, DR-0764 to DR-0767), so a call
added later fails here with its name: a doc put and a promote on a repo vault and a folder vault, a curate add, an
import of notes, a `vault.yaml` write and a ledger write, each run in process on a vault of the test kit's. A release's
list is in test_release.py, beside its owner at the console. Then the fewer calls held to the answers of the ones they
replace: one status call for the run files, one log for a batch's doc histories, one status for the ledger's op files.
Invented data only."""

from __future__ import annotations

import os
import subprocess
import sys
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import (
    COMMIT_CALLS,
    FIRST_COMMIT_CALLS,
    entity_vault,
    fixture_vault,
    folder_vault,
    git,
    git_calls,
    host_repo,
    make_config,
    make_notes_repo,
    make_record,
    note,
    sh,
    write_raw,
)
from ledger_kit import ADD_FIELDS
from staging_kit import signed_in, staging_vault

from docket import recall
from docket.cli import main
from docket.errors import DocketError
from docket.ledger import store as ledger_store
from docket.vault import docstore, gitfiles, host
from docket.vault import git as git_module
from docket.vault.docstore import DocStore
from docket.vault.gate import Gate
from docket.vault.git import Git, config_stamp, git_home, toplevel
from docket.vault.home import repo_at
from docket.vault.vault import Vault, remove_tree

DOC_PATH = "processed/recording-note/2026/10/2026-10-14-supplier-call.md"


def run(vault: Vault, *args: str) -> None:
    assert main([*args, "--vault", str(vault.root)]) == 0


def put_doc(vault: Vault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    doc = tmp_path / "note.md"
    doc.write_text(note(lines=["- Ben put it at 10 million. @12:08"]), encoding="utf-8")
    with git_calls(monkeypatch) as calls:
        run(vault, "doc", "put", str(doc))
    return calls


def promote(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    with git_calls(monkeypatch) as calls:
        run(vault, "staging", "promote")
    return calls


def test_a_doc_put_makes_four_git_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = fixture_vault(tmp_path)
    assert put_doc(vault, tmp_path, monkeypatch) == [
        f"log --no-show-signature --format=%H%x1f%B%x1e -- {DOC_PATH}",
        *COMMIT_CALLS,
    ]


def test_a_doc_put_in_a_folder_vault_makes_the_same_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = folder_vault(tmp_path)
    write_raw(vault.root)
    vault.commit([], "Raw: the fixture")
    # The folder vault was opened above, so git's answer for the folder is kept (DR-0532): the command's open asks
    # nothing.
    assert put_doc(vault, tmp_path, monkeypatch) == [
        f"log --no-show-signature --format=%H%x1f%B%x1e -- {DOC_PATH}",
        *COMMIT_CALLS,
    ]


def test_a_promote_makes_four_git_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = staging_vault(tmp_path)
    # The first commit stages nothing, yet still asks about run files and reads the unmerged paths from the index, so a
    # write during a conflict refuses as it always did. The vault's first commit in this process asks git's identity.
    assert promote(vault, monkeypatch) == [COMMIT_CALLS[0], *FIRST_COMMIT_CALLS]


def test_a_promote_in_a_folder_vault_makes_the_same_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = folder_vault(tmp_path)
    Gate(vault).stage([make_record(1)])
    assert promote(vault, monkeypatch) == [COMMIT_CALLS[0], *FIRST_COMMIT_CALLS]


CHANGES = """\
changes:
  - create: {id: cleo-test, type: person, name: {en: Cleo Test}}
  - entity: northwind
    add: {aliases: [{text: Nortwin, kind: asr}]}
"""


def test_a_curate_add_makes_five_git_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = entity_vault(tmp_path)
    changes = tmp_path / "changes.yaml"
    changes.write_text(CHANGES, encoding="utf-8", newline="\n")
    with git_calls(monkeypatch) as calls:
        run(vault, "curate", "add", str(changes))
    # The entity files that differ from HEAD and the ones git doesn't track, asked once: a stopped write's log lines
    # are among them.
    assert calls == [
        "diff --name-only -z HEAD -- entities",
        "ls-files -z --others -- entities",
        *FIRST_COMMIT_CALLS,
    ]


def test_an_import_of_notes_makes_one_history_call_for_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = fixture_vault(tmp_path)
    folder = make_notes_repo(tmp_path)
    # The histories are read from the commit HEAD was at, never from HEAD again.
    head = vault.git.head_id()
    with git_calls(monkeypatch) as calls:
        run(vault, "import", "notes", str(folder))
    legacy = "-- processed/legacy-note"
    assert calls == [
        # The notes folder's repo and files.
        "rev-parse --show-toplevel",
        "ls-files -z -co --exclude-standard -- notes",
        # The notes imported before, and the ones changed by hand since.
        f"log --no-show-signature --format=%B {legacy}",
        f"diff --name-only -z HEAD {legacy}",
        f"ls-files -z --others --exclude-standard {legacy}",
        # Every note's history in one log, no merge being in it, instead of one log per note.
        f"rev-list --merges -1 {head} {legacy}",
        f"log -z --no-renames --no-show-signature --format=%x1e%H%x1f%B --name-only {head} {legacy}",
        *COMMIT_CALLS,
    ]


def test_a_vault_yaml_write_makes_three_git_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = fixture_vault(tmp_path)
    with git_calls(monkeypatch) as calls:
        run(vault, "redaction", "set", "phone", "on")
    # HEAD's vault.yaml, which the take-in weighs an edit against, comes from the settings in force's cache (DR-0521).
    assert calls == COMMIT_CALLS


# -- the run-file check: one status call answers as the two calls did --------------------------------------------------


def run_file(vault: Vault, name: str) -> str:
    rel = f"raw/chat/2026/10/{name}"
    (vault.root / rel).parent.mkdir(parents=True, exist_ok=True)
    # Each file's own text, so git can't take one file for another's rename.
    (vault.root / rel).write_text(f'{{"rid": "chat:{name}"}}\n', encoding="utf-8", newline="\n")
    return rel


def both_ways(vault: Vault) -> tuple[list[str], list[str]]:
    git = vault.git
    return sorted(git.not_in_head("raw")), sorted({*git.untracked("raw", ignored_too=True), *git.staged_new("raw")})


@pytest.mark.parametrize("storage", ["repo", "folder"])
def test_the_status_call_lists_what_the_two_calls_listed(storage: str, tmp_path: Path) -> None:
    vault = fixture_vault(tmp_path) if storage == "repo" else folder_vault(tmp_path)
    new, old = both_ways(vault)
    assert new == old == []
    top = vault.git.git_dir.parent
    # Not tracked; hidden by a .gitignore; staged by an add whose commit never came; a name git would quote.
    loose = run_file(vault, "2026-10-14T0900-test-box.jsonl")
    (vault.root / "raw" / ".gitignore").write_text("*-hidden.jsonl\n", encoding="utf-8")
    hidden = run_file(vault, "2026-10-14T0905-test-box-hidden.jsonl")
    staged = run_file(vault, "2026-10-14T0910-test-box.jsonl")
    odd = run_file(vault, "ünnep és szünet.jsonl")
    sh(vault.root, "git", "add", "--", staged, odd)
    # A committed run file renamed and staged: git counts it a rename both ways, not a new file.
    moved = run_file(vault, "2026-10-14T0915-test-box.jsonl")
    git(vault.root, "add", "--", moved)
    git(vault.root, "commit", "-qm", "A run file", "--", moved)
    git(vault.root, "mv", moved, moved.replace("0915", "0920"))
    new, old = both_ways(vault)
    assert new == old == sorted([loose, "raw/.gitignore", hidden, staged, odd])
    assert sh(top, "git", "status", "--porcelain", "--", ".").count("R  ") == 1


# -- put_many's histories: one log for the batch reads the revs each doc's own log reads -------------------------------

DOCS = [f"recording-note:2026-10-{day}-supplier-call" for day in (14, 15, 16)]
FOLDER = "-- processed/recording-note"


def put_all(vault: Vault) -> DocStore:
    for doc_id in DOCS:
        recall.doc_put(vault, note(doc_id))
    return DocStore(vault)


def per_doc(store: DocStore) -> dict[str, list[tuple[int, str]]]:
    return {doc_id: store.history(doc_id) for doc_id in DOCS}


def hand_edit(vault: Vault, rel: str, line: str) -> None:
    path = vault.root / rel
    path.write_text(path.read_text(encoding="utf-8") + f"{line}\n", encoding="utf-8", newline="\n")


def test_one_log_reads_the_revs_each_doc_s_log_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = fixture_vault(tmp_path)
    store = put_all(vault)
    first, second, third = (store.path(doc_id).as_posix() for doc_id in DOCS)
    # A hand commit that touched a doc, with no Doc: line.
    hand_edit(vault, first, "- by hand")
    git(vault.root, "commit", "-qm", "Fix a typo", "--", first)
    # A commit whose message names a doc it didn't touch.
    hand_edit(vault, second, "- by hand too")
    git(vault.root, "commit", "-qm", f"Doc: {DOCS[2]} rev 9", "--", second)
    # A doc deleted, then put again.
    git(vault.root, "rm", "-q", "--", third)
    git(vault.root, "commit", "-qm", "Drop a doc")
    recall.doc_put(vault, note(DOCS[2], rev="1", lines=["- Ben put it at 11 million. @12:08"]))
    head = vault.git.head_id()
    with git_calls(monkeypatch) as calls:
        batched = store.histories(DOCS)
    assert calls == [
        f"rev-list --merges -1 {head} {FOLDER}",
        f"log -z --no-renames --no-show-signature --format=%x1e%H%x1f%B --name-only {head} {FOLDER}",
    ]
    assert batched == per_doc(store)
    assert [n for n, _ in batched[DOCS[2]]] == [2, 1]
    assert [n for n, _ in batched[DOCS[1]]] == [1]


def test_a_folder_vault_under_a_non_ascii_path_reads_the_same_revs(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    vault = Vault.init(top / "jegyzetek és ő" / "vault", make_config("folder"), "test-box")
    write_raw(vault.root)
    vault.commit([], "Raw: the fixture")
    store = put_all(vault)
    batched = store.histories(DOCS)
    assert batched == per_doc(store)
    assert all(len(revs) == 1 for revs in batched.values())


def test_a_merge_in_the_folder_s_history_reads_each_doc_s_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = fixture_vault(tmp_path)
    store = put_all(vault)
    paths = [store.path(doc_id).as_posix() for doc_id in DOCS]
    git(vault.root, "checkout", "-q", "-b", "side")
    hand_edit(vault, paths[0], "- on the side")
    git(vault.root, "commit", "-qm", "On the side", "--", paths[0])
    git(vault.root, "checkout", "-q", "main")
    recall.doc_put(vault, note(DOCS[1], rev="1", lines=["- Ben put it at 12 million. @12:08"]))
    git(vault.root, "merge", "-q", "--no-ff", "-m", "Merge the side", "side")
    head = vault.git.head_id()
    with git_calls(monkeypatch) as calls:
        batched = store.histories(DOCS)
    assert calls == [
        f"rev-list --merges -1 {head} {FOLDER}",
        *(f"log --no-show-signature --format=%H%x1f%B%x1e {head} -- {rel}" for rel in paths),
    ]
    assert batched == per_doc(store)
    assert [n for n, _ in batched[DOCS[1]]] == [2, 1]


def test_the_histories_at_one_head_are_asked_once_and_a_new_commit_asks_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git's log at a HEAD never changes, so the same docs' histories at the same HEAD ask git nothing, as the Briefs
    page's read asks for them on every open; a commit since asks again and finds its rev, and the keep is bounded."""
    monkeypatch.setattr(docstore, "_HISTORIES", {})
    vault = fixture_vault(tmp_path)
    store = put_all(vault)
    first = store.histories(DOCS)
    with git_calls(monkeypatch) as calls:
        assert store.histories(DOCS) == first
    assert calls == []
    recall.doc_put(vault, note(DOCS[1], rev="1", lines=["- Ben put it at 12 million. @12:08"]))
    with git_calls(monkeypatch) as calls:
        again = store.histories(DOCS)
    assert calls and [n for n, _ in again[DOCS[1]]] == [2, 1]
    assert again == per_doc(store)
    monkeypatch.setattr(docstore, "HISTORIES_KEPT", 1)
    store.histories(DOCS[:1])
    with git_calls(monkeypatch) as calls:
        assert store.histories(DOCS) == again
    assert calls, "a keep of one holds the last docs asked for only"


def test_histories_are_read_from_the_head_they_are_kept_under(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Another process moves HEAD while the log runs and moves it back, as a rebase and its abort do: the histories
    kept for the HEAD read first are that HEAD's, since the log reads from it and never from HEAD again."""
    monkeypatch.setattr(docstore, "_HISTORIES", {})
    vault = fixture_vault(tmp_path)
    store = put_all(vault)
    branch = git(vault.root, "branch", "--show-current").strip()
    git(vault.root, "checkout", "-q", "-b", "upstream")
    recall.doc_put(vault, note(DOCS[1], rev="1", lines=["- Ben put it at 12 million. @12:08"]))
    git(vault.root, "checkout", "-q", branch)
    here = per_doc(DocStore(vault))
    real = DocStore._histories  # pyright: ignore[reportPrivateUsage]

    def racing(self: DocStore, *args: Any) -> dict[str, list[tuple[int, str]]]:
        git(vault.root, "checkout", "-q", "--detach", "upstream")
        try:
            return real(self, *args)
        finally:
            git(vault.root, "checkout", "-q", branch)

    monkeypatch.setattr(DocStore, "_histories", racing)
    store.histories(DOCS)
    monkeypatch.setattr(DocStore, "_histories", real)
    assert DocStore(vault).histories(DOCS) == here
    assert [n for n, _ in here[DOCS[1]]] == [1]


def test_two_folder_vaults_of_one_host_repo_keep_their_own_histories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two folder vaults share their host repo's git dir and HEAD; each keeps its own histories, by its folder."""
    monkeypatch.setattr(docstore, "_HISTORIES", {})
    top = host_repo(tmp_path)
    vaults = []
    for name in ("a", "b"):
        vault = Vault.init(top / "notes" / name, make_config("folder"), "test-box")
        write_raw(vault.root)
        vault.commit([], "Raw: the fixture")
        recall.doc_put(vault, note(DOCS[0]))
        vaults.append(vault)
    a, b = vaults
    recall.doc_put(b, note(DOCS[0], rev="1", lines=["- Ben put it at 12 million. @12:08"]))
    assert [n for n, _ in DocStore(a).histories(DOCS[:1])[DOCS[0]]] == [1]
    assert [n for n, _ in DocStore(b).histories(DOCS[:1])[DOCS[0]]] == [2, 1]


# -- a ledger write: one status answers for its op files as the diff against HEAD and the status did (DR-0764) --------

OPS = "ledger/ops"
STATUS_OPS = f"status --porcelain=v1 -z --untracked-files=all --ignored=traditional -- {OPS}"
ADD: dict[str, Any] = {"op": "add", "fields": dict(ADD_FIELDS), "force": True}
LINES = "".join(
    f'{{"op": {n}, "text": "a line long enough that a renamed file stays like itself"}}\n' for n in range(30)
)


def op_file(vault: Vault, name: str, text: str | None = None) -> str:
    """An op file whose every line names it, so git takes no two of them for a rename, unless `text` says otherwise."""
    rel = f"{OPS}/{name}"
    own = "".join(f'{{"op": {n}, "file": "{name}"}}\n' for n in range(30))
    (vault.root / rel).write_text(own if text is None else text, encoding="utf-8", newline="\n")
    return rel


def the_two_calls(vault: Vault) -> tuple[list[str], list[str]]:
    return sorted(vault.git.changed_against_head(OPS)), sorted(vault.git.not_in_head(OPS))


def the_one_status(vault: Vault) -> tuple[list[str], list[str]]:
    changed, new = vault.git.against_head(OPS)
    return sorted(changed), sorted(new)


def test_a_ledger_write_makes_four_git_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = fixture_vault(tmp_path)
    with git_calls(monkeypatch) as calls:
        ledger_store.write(vault, [ADD])
    assert calls == [STATUS_OPS, *COMMIT_CALLS]


def test_an_op_file_saved_with_crlf_alone_still_lets_the_next_write_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = fixture_vault(tmp_path)
    ledger_store.write(vault, [ADD])
    # An editor's save: the same lines, CRLF. Under eol=lf git reads the content as HEAD's, so the diff against HEAD
    # lists nothing, while the status marks the file changed by its size.
    for path in (vault.root / OPS).glob("*.jsonl"):
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert vault.git.against_head(OPS)[0]
    assert vault.git.changed_against_head(OPS) == []
    head = vault.git.head_id()
    with git_calls(monkeypatch) as calls:
        written = ledger_store.write(vault, [dict(ADD, fields=dict(ADD_FIELDS, title="Second item"))])
    assert written.commit is not None and written.commit != head
    # The diff answers before anything is refused, as the two calls did.
    assert calls[0] == STATUS_OPS
    assert f"diff --name-only -z HEAD -- {OPS}" in calls


@pytest.mark.parametrize("storage", ["repo", "folder"])
def test_one_status_lists_what_the_diff_and_the_status_listed(
    storage: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = fixture_vault(tmp_path) if storage == "repo" else folder_vault(tmp_path)
    (vault.root / OPS).mkdir(parents=True, exist_ok=True)
    names = ["kept", "changed", "staged", "staged-gone", "staged-gone-back", "gone-from-index"]
    committed = [op_file(vault, f"{name}.jsonl") for name in names]
    git(vault.root, "add", "--", *committed)
    git(vault.root, "commit", "-qm", "Ops", "--", *committed)
    assert the_one_status(vault) == the_two_calls(vault) == ([], [])
    # Each state the status settles: changed in the working tree, staged, deleted from the index with the file gone
    # or still there, untracked, hidden by an ignore rule, added, added and changed, added and deleted, added with -N.
    op_file(vault, "changed.jsonl", LINES + "more\n")
    op_file(vault, "staged.jsonl", LINES + "staged\n")
    git(vault.root, "add", "--", f"{OPS}/staged.jsonl")
    git(vault.root, "rm", "-q", "--", f"{OPS}/staged-gone.jsonl")
    git(vault.root, "rm", "-q", "--cached", "--", f"{OPS}/staged-gone-back.jsonl")
    op_file(vault, "ünnep és szünet.jsonl")
    (vault.root / OPS / ".gitignore").write_text("*.hidden\n", encoding="utf-8")
    op_file(vault, "x.hidden")
    for name in ("added", "added-changed", "added-gone"):
        git(vault.root, "add", "--", op_file(vault, f"{name}.jsonl"))
    op_file(vault, "added-changed.jsonl", "changed after its add\n")
    (vault.root / OPS / "added-gone.jsonl").unlink()
    git(vault.root, "add", "-N", "--", op_file(vault, "intent.jsonl"))
    with git_calls(monkeypatch) as calls:
        one = the_one_status(vault)
    assert calls == [STATUS_OPS]
    assert one == the_two_calls(vault)
    assert one[0] and one[1]


STATES: dict[str, Callable[[Vault], object]] = {
    "staged and changed again": lambda v: (
        op_file(v, "a.jsonl", LINES + "staged\n"), git(v.root, "add", "--", f"{OPS}/a.jsonl"),
        op_file(v, "a.jsonl", LINES + "again\n"),
    ),
    "staged and changed back": lambda v: (
        op_file(v, "a.jsonl", LINES + "staged\n"), git(v.root, "add", "--", f"{OPS}/a.jsonl"), op_file(v, "a.jsonl"),
    ),
    "deleted from the working tree": lambda v: (v.root / OPS / "a.jsonl").unlink(),
    "added with -N and deleted": lambda v: (
        git(v.root, "add", "-N", "--", op_file(v, "n.jsonl")), (v.root / OPS / "n.jsonl").unlink(),
    ),
    "renamed": lambda v: git(v.root, "mv", f"{OPS}/a.jsonl", f"{OPS}/b.jsonl"),
    "renamed and changed": lambda v: (
        git(v.root, "mv", f"{OPS}/a.jsonl", f"{OPS}/b.jsonl"), op_file(v, "b.jsonl", LINES + "one more\n"),
    ),
    "renamed and deleted": lambda v: (
        git(v.root, "mv", f"{OPS}/a.jsonl", f"{OPS}/b.jsonl"), (v.root / OPS / "b.jsonl").unlink(),
    ),
    "unmerged": lambda v: unmerge(v, f"{OPS}/a.jsonl"),
}  # fmt: skip


def unmerge(vault: Vault, rel: str) -> None:
    blob = sh(vault.root, "git", "hash-object", "-w", "--", rel).strip()
    info = "".join(f"100644 {blob} {n}\t{vault.home.prefix}{rel}\n" for n in (1, 2, 3))
    proc = subprocess.run(["git", "update-index", "--index-info"], cwd=vault.home.top, input=info.encode(), check=False)
    assert proc.returncode == 0


@pytest.mark.parametrize("state", list(STATES))
def test_a_state_the_status_can_t_settle_asks_the_two_calls(
    state: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = fixture_vault(tmp_path)
    (vault.root / OPS).mkdir(parents=True, exist_ok=True)
    rel = op_file(vault, "a.jsonl")
    git(vault.root, "add", "--", rel)
    git(vault.root, "commit", "-qm", "Ops", "--", rel)
    STATES[state](vault)
    with git_calls(monkeypatch) as calls:
        one = the_one_status(vault)
    assert calls == [STATUS_OPS, f"diff --name-only -z HEAD -- {OPS}", STATUS_OPS]
    assert one == the_two_calls(vault)


# -- the batch reader: one per Git, closed at each command's end and each service call's (DR-0766) -------------------


def open_batches() -> list[Git]:
    return list(git_module._OPEN)  # pyright: ignore[reportPrivateUsage] - the readers this process holds


def old_blob_text(git: Git, spec: str) -> str | None:
    proc = git.run("cat-file", "blob", spec, check=False)
    return proc.stdout if proc.returncode == 0 else None


def test_a_git_s_blob_reads_share_one_batch_reader_and_answer_as_git_s_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = fixture_vault(tmp_path)
    reader = vault.git
    # A blob as it is stored: CRLF, a lone CR and a byte that isn't UTF-8, which text mode reads its own way.
    stored = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=vault.root,
        input=b"one\r\ntwo\rthree \xff\n",
        capture_output=True,
        check=True,
    )
    blob = stored.stdout.decode().strip()
    specs = [blob, "HEAD:./vault.yaml", "HEAD:./no-such-file.md", f"{reader.head_id()}:./{DOC_PATH}"]
    with git_calls(monkeypatch) as calls:
        found = [reader.blob_text(spec) for spec in specs]
        shown = [reader.show_at_head("vault.yaml"), reader.show_at_head("no-such-file.md")]
        out = reader.blob_out(blob)
    assert calls == ["cat-file --batch"]
    assert found == [old_blob_text(reader, spec) for spec in specs]
    assert found[0] == out == "one\ntwo\nthree \N{REPLACEMENT CHARACTER}\n"
    assert found[2] is None
    assert shown == [
        reader.run("show", f"HEAD:./{rel}", check=False).stdout for rel in ("vault.yaml", "no-such-file.md")
    ]
    # What the batch can't vouch for asks git as before: a folder, which show lists; a name git has no blob by, whose
    # error is git's own.
    listing = reader.run("show", "HEAD:./raw", check=False).stdout
    with git_calls(monkeypatch) as calls:
        assert reader.show_at_head("raw") == listing
    assert calls == ["show HEAD:./raw"]
    with pytest.raises(DocketError, match="git cat-file: "):
        reader.blob_out("HEAD:./no-such-file.md")


def test_a_batch_reader_that_broke_answers_through_git_and_starts_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = fixture_vault(tmp_path)
    reader = vault.git
    text = reader.blob_text("HEAD:./vault.yaml")
    reader.close()
    # A reader that died. Not a killed git: on Windows the git on PATH can be a launcher whose child outlives a kill.
    fake_batch(reader, "pass")
    with git_calls(monkeypatch) as calls:
        assert reader.blob_text("HEAD:./vault.yaml") == text
        assert reader.blob_text("HEAD:./vault.yaml") == text
    assert calls == ["cat-file blob HEAD:./vault.yaml", "cat-file --batch"]


def test_no_batch_reader_outlives_its_command_or_its_service_call(tmp_path: Path) -> None:
    vault = staging_vault(tmp_path)
    vault.git.show_at_head("vault.yaml")
    process = vault.git._batch  # pyright: ignore[reportPrivateUsage]
    assert process is not None
    # Others a test before this one left open are closed with it.
    assert vault.git in open_batches()
    run(vault, "staging", "list")
    assert open_batches() == []
    assert process.poll() is not None
    # The vault reads on, through a reader of its own again; a service call's end closes it, as it would one of the
    # call's own.
    vault.git.show_at_head(".gitattributes")
    assert vault.git in open_batches()
    with signed_in(vault) as client:
        assert client.get("/staging").status_code == 200
    assert open_batches() == []


def test_a_file_at_head_is_read_by_head_s_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The batch reader resolves an id the same way however often the refs moved; `HEAD` it reads by a packed-refs
    file that a git seeing whole seconds can take for unchanged."""
    vault = fixture_vault(tmp_path)
    head = vault.git.head_id()
    assert vault.git.at_head("vault.yaml") == f"{head}:./vault.yaml"
    asked: list[str] = []
    real = Git.blob

    def recording(self: Git, spec: str) -> bytes | gitfiles.Unknown | None:
        asked.append(spec)
        return real(self, spec)

    monkeypatch.setattr(Git, "blob", recording)
    assert DocStore(vault).committed_text(DOCS[0]) is None
    assert asked == [f"{head}:./{DOC_PATH}"]
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    git(fresh, "init", "-q", "-b", "main")
    assert Git(fresh).at_head("a.md") == "HEAD:./a.md"


def test_a_command_s_reads_share_its_one_batch_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = fixture_vault(tmp_path)
    for doc_id in DOCS:
        recall.doc_put(vault, note(doc_id))
    store = DocStore(vault)
    with git_calls(monkeypatch) as calls:
        texts = store.committed_texts([store.path(doc_id).as_posix() for doc_id in DOCS])
    assert [c for c in calls if c.startswith("cat-file")] == ["cat-file --batch"]
    assert all(texts.values())


def test_a_sync_closes_the_batch_readers_first(tmp_path: Path) -> None:
    vault = fixture_vault(tmp_path)
    vault.git.show_at_head("vault.yaml")
    assert vault.git in open_batches()
    vault.sync()
    assert open_batches() == []


# -- answers kept for the process while their files stand (DR-0767) ---------------------------------------------------

TOP = "rev-parse --show-toplevel"


def test_the_repo_a_folder_is_in_is_asked_once_while_the_files_stand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_module, "_TOPS", {})
    top = host_repo(tmp_path).resolve()
    inner = top / "projects" / "pilot"
    inner.mkdir(parents=True)
    with git_calls(monkeypatch) as calls:
        assert toplevel(inner) == toplevel(inner) == top
    assert calls == [TOP]
    # A repo made between the folder and the top, gone again, the top's config changed: each asks git again.
    sh(top / "projects", "git", "init", "-q")
    with git_calls(monkeypatch) as calls:
        assert toplevel(inner) == top / "projects"
    remove_tree(top / "projects" / ".git")
    with git_calls(monkeypatch) as calls_after:
        assert toplevel(inner) == top
    git(top, "config", "docket.test", "changed")
    with git_calls(monkeypatch) as calls_config:
        assert toplevel(inner) == top
    assert calls == calls_after == calls_config == [TOP]
    # No repo above a folder: kept as none until a .git appears.
    outside = tmp_path / "outside" / "pilot"
    outside.mkdir(parents=True)
    if repo_at(outside) is not None:
        pytest.skip("the temp folder is inside a repo")
    with git_calls(monkeypatch) as calls:
        assert toplevel(outside) is None
        assert toplevel(outside) is None
    sh(tmp_path / "outside", "git", "init", "-q")
    with git_calls(monkeypatch) as calls_init:
        assert toplevel(outside) == (tmp_path / "outside").resolve()
    assert calls == calls_init == [TOP]
    # A linked worktree, whose .git is a file the files can't vouch for, and a variable telling git where to look:
    # git every time.
    git(top, "worktree", "add", "-q", "--detach", str(tmp_path / "linked"))
    linked = (tmp_path / "linked").resolve()
    with git_calls(monkeypatch) as calls:
        assert toplevel(linked) == toplevel(linked) == linked
    assert calls == [TOP, TOP]
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    with git_calls(monkeypatch) as calls:
        assert toplevel(inner) == toplevel(inner) == top
    assert calls == [TOP, TOP]


def test_git_s_home_is_found_as_git_finds_it(tmp_path: Path) -> None:
    """HOME first; on Windows HOMEDRIVE and HOMEPATH where they name a folder, a domain's redirected home, before
    USERPROFILE; else Python's own home."""
    (tmp_path / "redirected").mkdir()
    # Git for Windows joins the two as they are; a temp folder stands in for the drive on every system.
    drive, path = str(tmp_path), "/redirected"
    profile = str(tmp_path / "profile")
    assert git_home({"HOME": str(tmp_path / "home"), "USERPROFILE": profile}, windows=True) == tmp_path / "home"
    windows = {"HOMEDRIVE": drive, "HOMEPATH": path, "USERPROFILE": profile}
    assert git_home(windows, windows=True) == Path(drive + path)
    assert git_home({**windows, "HOMEPATH": path + "-gone"}, windows=True) == Path(profile)
    assert git_home({}, windows=True) == git_home({}, windows=False) == Path.home()


def test_a_repo_s_answers_from_its_config_stand_while_its_config_files_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_module, "_CONFIGURED", {})
    top = host_repo(tmp_path)
    with git_calls(monkeypatch) as calls:
        first = (host.lfs_state(top), host.remote_urls(top), host.hooks_dir(top))
    assert calls
    with git_calls(monkeypatch) as calls:
        assert (host.lfs_state(top), host.remote_urls(top), host.hooks_dir(top)) == first
    assert calls == []
    # The user's global config, where `git lfs install` writes its filter: asked again, and git's answer follows.
    globally = Path(os.environ["GIT_CONFIG_GLOBAL"])
    filtered = '[filter "lfs"]\n\tprocess = git-lfs filter-process\n'
    globally.write_text(globally.read_text(encoding="utf-8") + filtered, encoding="utf-8")
    with git_calls(monkeypatch) as calls:
        assert host.lfs_state(top).filter_set
    assert "config --get filter.lfs.process" in calls
    # The repo's own: a remote added.
    git(top, "remote", "add", "mirror", str(tmp_path / "mirror.git"))
    assert len(host.remote_urls(top)) == len(first[1]) + 1


def fake_batch(reader: Git, script: str) -> subprocess.Popen[bytes]:
    """A process standing in for the reader's batch, answering as `script` says."""
    proc = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    reader._batch = proc  # pyright: ignore[reportPrivateUsage]
    reader._closer = weakref.finalize(reader, git_module._shut, proc)  # pyright: ignore[reportPrivateUsage]
    return proc


@pytest.mark.parametrize(
    "answer",
    ["sys.stdout.write('HEAD:./vault.yaml ambiguous\\n')", "sys.stdout.write('0123 blob 10\\nabc')"],
    ids=["out of form", "ended part way"],
)
def test_a_batch_answer_out_of_form_closes_it_and_git_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    vault = fixture_vault(tmp_path)
    reader = vault.git
    proc = fake_batch(reader, f"import sys; sys.stdin.readline(); {answer}; sys.stdout.flush()")
    assert reader.blob("HEAD:./vault.yaml") is gitfiles.UNKNOWN
    assert reader._batch is None  # pyright: ignore[reportPrivateUsage]
    assert proc.poll() is not None
    with git_calls(monkeypatch) as calls:
        assert reader.blob("HEAD:./vault.yaml\nraw") is gitfiles.UNKNOWN
        assert reader.blob_text("HEAD:./vault.yaml") == (vault.root / "vault.yaml").read_text(encoding="utf-8")
    # A name with a line break never goes to the batch; the next read starts a real one.
    assert calls == ["cat-file --batch"]


def test_a_batch_that_won_t_exit_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git_module, "_BATCH_EXIT_SECONDS", 0.2)
    stuck = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    git_module._shut(stuck)  # pyright: ignore[reportPrivateUsage]
    assert stuck.returncode is not None


def test_one_status_before_the_first_commit_lists_nothing_changed(tmp_path: Path) -> None:
    top = tmp_path / "fresh"
    top.mkdir()
    git(top, "init", "-q", "-b", "main")
    (top / "staged.md").write_text("staged\n", encoding="utf-8")
    (top / "loose.md").write_text("loose\n", encoding="utf-8")
    git(top, "add", "staged.md")
    fresh = Git(top)
    changed, new = fresh.against_head(".")
    assert (changed, sorted(new)) == ([], ["loose.md", "staged.md"])
    assert (changed, sorted(new)) == (fresh.changed_against_head("."), sorted(fresh.not_in_head(".")))


def test_a_config_stamp_follows_each_file_git_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top = host_repo(tmp_path)
    home = tmp_path / "user"
    home.mkdir()
    system = tmp_path / "system.gitconfig"
    system.write_text("", encoding="utf-8")
    monkeypatch.delenv("GIT_CONFIG_GLOBAL")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system))
    seen = [config_stamp(top)]
    assert seen[0] is not None and config_stamp(top) == seen[0]
    # The user's own, the XDG one appearing, the system's that a variable names, the repo's: each changes it.
    (home / ".gitconfig").write_text("[docket]\n\ttest = 1\n", encoding="utf-8")
    seen.append(config_stamp(top))
    (home / "xdg" / "git").mkdir(parents=True)
    (home / "xdg" / "git" / "config").write_text("[docket]\n\ttest = 22\n", encoding="utf-8")
    seen.append(config_stamp(top))
    system.write_text("[docket]\n\ttest = 333\n", encoding="utf-8")
    seen.append(config_stamp(top))
    git(top, "config", "docket.test", "4444")
    seen.append(config_stamp(top))
    assert len(set(seen)) == len(seen)
    # A folder that isn't a plain checkout's top keeps nothing.
    assert config_stamp(home) is None
