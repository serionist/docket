"""The git calls each write makes, as an exact list per path (#132, DR-0528 to DR-0530), so a call added later fails
here with its name: a doc put and a promote on a repo vault and a folder vault, a curate add, an import of notes and a
`vault.yaml` write, each run in process on a vault of the test kit's. A release's list is in test_release.py, beside its
owner at the console. Then the fewer calls held to the answers of the ones they replace: one status call for the run
files, one log for a batch's doc histories. Invented data only."""

from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import (
    COMMIT_CALLS,
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
from staging_kit import staging_vault

from docket import recall
from docket.cli import main
from docket.vault.docstore import DocStore
from docket.vault.gate import Gate
from docket.vault.vault import Vault

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


def test_a_doc_put_makes_seven_git_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_a_promote_makes_eight_git_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = staging_vault(tmp_path)
    # The first commit stages nothing, yet still asks about run files and unmerged paths, so a write during a conflict
    # refuses as it always did.
    assert promote(vault, monkeypatch) == [*COMMIT_CALLS[:2], *COMMIT_CALLS]


def test_a_promote_in_a_folder_vault_makes_the_same_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = folder_vault(tmp_path)
    Gate(vault).stage([make_record(1)])
    assert promote(vault, monkeypatch) == [*COMMIT_CALLS[:2], *COMMIT_CALLS]


CHANGES = """\
changes:
  - create: {id: cleo-test, type: person, name: {en: Cleo Test}}
  - entity: northwind
    add: {aliases: [{text: Nortwin, kind: asr}]}
"""


def test_a_curate_add_makes_eight_git_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        *COMMIT_CALLS,
    ]


def test_an_import_of_notes_makes_one_history_call_for_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = fixture_vault(tmp_path)
    folder = make_notes_repo(tmp_path)
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
        f"rev-list --merges -1 HEAD {legacy}",
        f"log -z --no-renames --no-show-signature --format=%x1e%H%x1f%B --name-only {legacy}",
        *COMMIT_CALLS,
    ]


def test_a_vault_yaml_write_makes_seven_git_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = fixture_vault(tmp_path)
    with git_calls(monkeypatch) as calls:
        run(vault, "redaction", "set", "phone", "on")
    assert calls == ["show HEAD:./vault.yaml", *COMMIT_CALLS]


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
    with git_calls(monkeypatch) as calls:
        batched = store.histories(DOCS)
    assert calls == [
        f"rev-list --merges -1 HEAD {FOLDER}",
        f"log -z --no-renames --no-show-signature --format=%x1e%H%x1f%B --name-only {FOLDER}",
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
    with git_calls(monkeypatch) as calls:
        batched = store.histories(DOCS)
    assert calls == [
        f"rev-list --merges -1 HEAD {FOLDER}",
        *(f"log --no-show-signature --format=%H%x1f%B%x1e -- {rel}" for rel in paths),
    ]
    assert batched == per_doc(store)
    assert [n for n, _ in batched[DOCS[1]]] == [2, 1]
