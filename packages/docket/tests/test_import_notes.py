from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import (
    ADA,
    NEVER_STORE_KEYWORDS,
    REC,
    UNTRACKED_NOTE,
    VENDOR,
    at,
    copy_vault,
    imported_template,
    make_record,
    notes_template,
    screening,
    sh,
    template_vault,
)

from docket import recall
from docket.errors import DocketError
from docket.imports import notes
from docket.imports.notes import (
    ImportReport,
    Listed,
    Names,
    Take,
    body_hash,
    canonical,
    clash_slug,
    clean_prefix,
    head_of,
    head_tokens,
    import_notes,
    list_folder,
    screened_path,
    short,
    slug_for,
    words,
)
from docket.vault import derived
from docket.vault.config import NeverStore
from docket.vault.docstore import DocStore
from docket.vault.neverstore import matches_text
from docket.vault.processed import DocProblem, DocRefusedError, ProcessedDoc
from docket.vault.records import RawRecord
from docket.vault.segments import Segment
from docket.vault.vault import Vault

CALL = "notes/calls/2026-09-10-supplier-call.md"
CALL_ID = "legacy-note:2026-09-10-supplier-call"
CHAT = "notes/chat/2026-09-10-supplier-call.md"
CHAT_ID = "legacy-note:2026-09-10-supplier-call-" + hashlib.sha256(CHAT.encode()).hexdigest()[:6]
LATER = "notes/calls/2026-09-14-later.md"
LATER_ID = "legacy-note:2026-09-14-later"
OFFER = "notes/mail/2026-09-11-offer.md"
OFFER_ID = "legacy-note:2026-09-11-offer"
LISTS = NeverStore(keywords=NEVER_STORE_KEYWORDS)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_vault(tmp_path_factory)


@pytest.fixture(scope="module")
def repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return notes_template(tmp_path_factory)


@pytest.fixture(scope="module")
def imported(tmp_path_factory: pytest.TempPathFactory, template: Path, repo_template: Path) -> Path:
    return imported_template(tmp_path_factory, template, repo_template)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return screening(copy_vault(template, tmp_path))


@pytest.fixture
def folder(repo_template: Path, tmp_path: Path) -> Path:
    shutil.copytree(repo_template, tmp_path / "work")
    return tmp_path / "work" / "notes"


@pytest.fixture
def after(imported: Path, tmp_path: Path) -> tuple[Vault, Path]:
    """A copy of the vault after its first import, and a copy of the folder."""
    shutil.copytree(imported / "vault", tmp_path / "vault")
    shutil.copytree(imported / "work", tmp_path / "work")
    return screening(Vault.open(tmp_path / "vault")), tmp_path / "work" / "notes"


def run(vault: Vault, folder: Path, **kwargs: Any) -> ImportReport:
    return import_notes(vault, folder, **kwargs)


def head(path: Path) -> str:
    return sh(path, "git", "rev-parse", "HEAD").strip()


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    out: dict[str, tuple[bytes, int]] = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            out[path.relative_to(root).as_posix()] = (path.read_bytes(), path.stat().st_mtime_ns)
    return out


def set_body(vault: Vault, doc_id: str, body: str, **meta: Any) -> None:
    """Claude's own edit of a legacy doc, through doc_put."""
    doc = DocStore(vault).get(doc_id)
    assert doc is not None
    updated = doc.meta.model_copy(update={"by": "claude-test", **meta})
    recall.doc_put(vault, ProcessedDoc(updated, body, 0).render())


# -- pieces -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stem", "slug"),
    [
        ("2026-09-10-supplier-call", "2026-09-10-supplier-call"),
        ("Árvíztűrő Jegyzet", "arvizturo-jegyzet"),
        ("Notes.From.Ben", "notes-from-ben"),
        ("_index", "index"),
        ("---", "note"),
        ("con", "con-note"),
        ("ß", "note"),
        ("a" * 81, "a" * 80),
        ("a" * 79 + "-b", "a" * 79),
    ],
)
def test_a_slug_from_a_file_name(stem: str, slug: str) -> None:
    assert slug_for(stem) == slug


def test_a_clash_suffix_stays_within_80() -> None:
    stem = "a" * 78
    clashed = clash_slug(slug_for(stem), "notes/x/" + stem + ".md")
    assert len(clashed) == 80
    assert clashed.startswith("a" * 73 + "-")
    assert clash_slug("a" * 72 + "-bbbbbbb", "x.md")[:73] == "a" * 72 + "-"
    assert clash_slug("", "x.md").startswith("note-")


@pytest.mark.parametrize(
    "body",
    ["text\n\n\n", "", "\n", "---\ntags: [old]\n# never closed\n", "a\r\nb", "  \n"],
)
def test_the_hash_is_of_the_body_as_a_doc_gives_it_back(body: str) -> None:
    once = canonical(body)
    assert canonical(once) == once
    assert body_hash(body) == body_hash(once)
    assert body_hash(body).startswith("sha256:")
    assert short(body_hash(body)) == body_hash(body)[7:15]


def test_the_head_ends_at_the_first_subheading_or_40_lines() -> None:
    body = "# Title\n\nSource: `rec-0042`.\n\n## Then\n\n`rec-0099`\n"
    assert head_of(body) == ["# Title", "", "Source: `rec-0042`.", ""]
    assert head_of("## First line is one\nmore\n") == ["## First line is one", "more", ""]
    assert len(head_of("x\n" * 100)) == 40
    tokens = head_tokens(["Source: `rec 0042 x`, calls:rec-0042.", "(see vendor:rec-0007!)", "tiny ab"])
    assert {"rec 0042 x", "calls:rec-0042", "vendor:rec-0007", "Source"} <= tokens
    assert "ab" not in tokens


def test_title_words() -> None:
    assert words("Supplier call, 2026 #3 ok") == frozenset({"supplier", "call"})


def record(rid: str, title: str | None = None, *, timed: bool = True, ts: Any = None) -> RawRecord:
    source = rid.partition(":")[0]
    segs = [Segment(start_ms=0, end_ms=1000, speaker=None, text="hi")] if timed else None
    return make_record(rid=rid, source=source, kind="recording", title=title, segments=segs, ts=ts or at(9, 30))


def test_covers_from_what_the_head_names() -> None:
    names = Names(
        [
            record("calls:rec-0042", "Supplier call"),
            record("rec:of_0123456789abcdef0123"),
            record("chat:C0TEST/1791000001.000100", timed=False),
        ],
        ZoneInfo("Europe/Budapest"),
    )
    listed = Listed("notes/calls/x.md", Path("x.md"))
    assert names.covers(listed, "# x\n\n`rec-0042` and calls:rec-0042\n") == ["calls:rec-0042"]
    assert names.covers(listed, "# x\n\nfile `0123456789abcdef0123`\n") == ["rec:of_0123456789abcdef0123"]
    assert names.covers(listed, "# x\n\nthread chat:C0TEST/1791000001.000100\n") == ["chat:C0TEST/1791000001.000100"]
    assert names.covers(listed, "# x\n\n## later\n\n`rec-0042`\n") == []


def test_covers_by_day_source_folder_and_title_when_the_head_names_none() -> None:
    zone = ZoneInfo("Europe/Budapest")
    late = at(22, 30)  # 00:30 the next day in Budapest
    names = Names(
        [
            record("calls:rec-1", "Supplier call"),
            record("calls:rec-2", "Board review", ts=late),
            record("calls:rec-3", "Board review"),
            record("calls:rec-4", "Ok"),
            record("chat:x-1", "Supplier call", timed=False),
            record("other:rec-5", "Supplier call"),
        ],
        zone,
    )

    def covers(old_path: str, heading: str = "x") -> list[str]:
        return names.covers(Listed(old_path, Path(old_path)), f"# {heading}\n\nno ids\n")

    assert covers("notes/calls/2026-10-14-supplier-call.md") == ["calls:rec-1"]
    assert covers("notes/calls/2026-10-14-notes.md", "Supplier call") == ["calls:rec-1"]
    assert covers("notes/calls/2026-10-15-board-review.md") == ["calls:rec-2"]
    assert covers("notes/calls/2026-10-14-board-review.md") == ["calls:rec-3"]
    assert covers("notes/calls/2026-10-14-ok.md") == []
    assert covers("notes/2026-10-14-supplier-call.md") == []
    assert covers("notes/calls/2026-10-13-supplier-call.md") == []
    assert covers("notes/calls/2026-13-45-supplier-call.md") == []
    assert covers("notes/calls/supplier-call.md") == []
    two = Names([record("calls:rec-1", "Supplier call"), record("calls:rec-9", "Supplier call")], zone)
    assert two.covers(Listed("notes/calls/2026-10-14-supplier-call.md", Path("x")), "# x\n") == []


@pytest.mark.parametrize(
    ("text", "take"),
    [
        ("note:3f1a9c2e:notes/calls/x.md", Take(side="note", hash="3f1a9c2e", old_path="notes/calls/x.md")),
        ("vault:3f1a9c2e:notes\\calls\\x.md", Take(side="vault", hash="3f1a9c2e", old_path="notes/calls/x.md")),
    ],
)
def test_a_take(text: str, take: Take) -> None:
    assert Take.parse(text) == take


@pytest.mark.parametrize("text", ["both:abc:x.md", "note:abc", "note::x.md", "note"])
def test_a_take_that_isnt_one(text: str) -> None:
    with pytest.raises(DocketError, match="note:<hash>:<old path>"):
        Take.parse(text)


@pytest.mark.parametrize(("given", "prefix"), [("old\\notes/", "old/notes"), (".", ""), ("", ""), ("./a", "a")])
def test_a_prefix(given: str, prefix: str) -> None:
    assert clean_prefix(given) == prefix


@pytest.mark.parametrize("given", ["../x", "a:b", "a\u0007b"])
def test_a_prefix_that_isnt_one(given: str) -> None:
    with pytest.raises(DocketError):
        clean_prefix(given)


@pytest.mark.parametrize(
    ("old_path", "screened"),
    [
        ("notes/people/dora-example/x.md", True),
        ("notes/people/dora_example/x.md", True),
        ("notes/people/DoraExample/x.md", True),
        ("notes/people/doraexample/x.md", True),
        ("notes/2026-09-01-call_with_dora_example.md", True),
        ("notes/DoraExample.md", True),
        ("notes/project/falcon/kickoff.md", True),
        ("notes/Dóra-Example.md", True),
        ("notes/people/Dora%20Example/x.md", True),
        ("notes/people/Dora%20Example.md", True),
        ("notes/dora/x.md", False),
        ("notes/adorable-example.md", False),
    ],
)
def test_the_path_screen(old_path: str, screened: bool) -> None:
    assert screened_path(old_path, LISTS) is screened


@pytest.mark.parametrize(
    ("text", "keyword", "screened"),
    [
        ("see ../people/dora-example/x.md", "dora example", True),
        ("See [the catch-up](../people/Dora%20Example/2026-09-01-catch-up.md).", "dora example", True),
        ("See [intro](../people/Kov%C3%A1cs%20B%C3%A9la/2026-09-02-intro.md).", "kovács", True),
        ("notes/people/Kov%C3%A1cs%20B%C3%A9la/intro.md", "kovács", True),
        ("call_with_dora.md", "dora", True),
        ("DoraExample said", "dora example", True),
        ("doraexample", "dora example", True),
        ("dora_example", "dora", True),
        ("adorable", "dora", False),
    ],
)
def test_the_text_screen_is_5s_matcher(text: str, keyword: str, screened: bool) -> None:
    assert matches_text(text, NeverStore(keywords=[keyword])) is screened


# -- the folder ---------------------------------------------------------------------------------------------------


def listing(folder: Path, vault_root: Path, prefix: str | None = None) -> tuple[list[str], Counter[str]]:
    skipped: Counter[str] = Counter()
    repo = notes.repo_root(folder.resolve())
    the_prefix = notes.default_prefix(folder.resolve(), repo) if prefix is None else prefix
    found = list_folder(folder.resolve(), the_prefix, repo, vault_root.resolve(), skipped)
    return [item.old_path for item in found], skipped


def test_a_folder_in_a_repo_is_listed_through_git(folder: Path, tmp_path: Path) -> None:
    repo = folder.parent
    sub = folder / "sub-repo"
    sub.mkdir()
    sh(sub, "git", "init", "-q")
    (sub / "inside.md").write_text("# nested\n", encoding="utf-8")
    try:
        (folder / "link.md").symlink_to(folder / "falcon.md")
    except OSError:
        (folder / "link.md").write_text("# not a link here\n", encoding="utf-8")
    before = (repo / ".git" / "index").read_bytes()
    old_paths, skipped = listing(folder, tmp_path / "vault")
    assert "notes/private/diary.md" not in old_paths
    assert UNTRACKED_NOTE in old_paths
    assert CALL in old_paths
    assert not any(p.startswith("notes/sub-repo") for p in old_paths)
    assert skipped["nested repos"] == 1
    assert skipped["links"] == (0 if "notes/link.md" in old_paths else 1)
    assert old_paths == sorted(old_paths)
    assert (repo / ".git" / "index").read_bytes() == before


def test_an_uncommitted_delete_is_gone_and_a_rename_is_new(folder: Path, tmp_path: Path) -> None:
    (folder / "falcon.md").unlink()
    (folder / "chat" / "2026-09-10-supplier-call.md").rename(folder / "chat" / "renamed.md")
    old_paths, _ = listing(folder, tmp_path / "vault")
    assert "notes/falcon.md" not in old_paths
    assert CHAT not in old_paths
    assert "notes/chat/renamed.md" in old_paths


def test_a_conflicted_note_is_listed_once(folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = notes._git  # pyright: ignore[reportPrivateUsage]

    def three_stages(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args[0] == "ls-files":
            return subprocess.CompletedProcess(args, 0, f"{CALL}\0{CALL}\0{CALL}\0", "")
        return real(cwd, *args)

    monkeypatch.setattr(notes, "_git", three_stages)
    assert listing(folder, tmp_path / "vault")[0] == [CALL]


def test_git_that_cant_read_or_list_stops_the_import(
    folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(what: str) -> Any:
        def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
            code = 128 if args[0] == what else 0
            out = str(folder.parent) if args[0] == "rev-parse" else ""
            return subprocess.CompletedProcess(args, code, out, "fatal: detected dubious ownership")

        return git

    monkeypatch.setattr(notes, "_git", failing("rev-parse"))
    with pytest.raises(DocketError, match="ignore rules can't be kept: fatal: detected dubious ownership"):
        listing(folder, tmp_path / "vault")
    monkeypatch.setattr(notes, "_git", failing("ls-files"))
    with pytest.raises(DocketError, match="git couldn't list"):
        listing(folder, tmp_path / "vault")


def test_a_folder_outside_git_is_walked(tmp_path: Path) -> None:
    plain = tmp_path / "plain" / "notes"
    (plain / ".obsidian").mkdir(parents=True)
    (plain / ".obsidian" / "workspace.md").write_text("x", encoding="utf-8")
    (plain / ".hidden.md").write_text("x", encoding="utf-8")
    (plain / "a.md").write_text("# a\n", encoding="utf-8")
    (plain / "deep" / "b.md").parent.mkdir()
    (plain / "deep" / "b.md").write_text("# b\n", encoding="utf-8")
    (plain / "nested" / ".git").mkdir(parents=True)
    (plain / "vault").mkdir()
    (plain / "vault" / "c.md").write_text("# the vault's\n", encoding="utf-8")
    try:
        (plain / "linked").symlink_to(plain / "deep", target_is_directory=True)
        (plain / "link.md").symlink_to(plain / "a.md")
        links = 2
    except OSError:
        links = 0
    old_paths, skipped = listing(plain, plain / "vault")
    assert old_paths == ["notes/a.md", "notes/deep/b.md"]
    assert (skipped["nested repos"], skipped["links"]) == (1, links)
    assert listing(plain, tmp_path / "elsewhere", prefix="")[0] == ["a.md", "deep/b.md", "vault/c.md"]


def test_the_prefix_by_default(folder: Path) -> None:
    repo = folder.parent.resolve()
    assert notes.default_prefix(folder.resolve(), repo) == "notes"
    assert notes.default_prefix(repo, repo) == ""
    assert notes.default_prefix(folder.resolve(), None) == "notes"


def test_a_folder_inside_the_vault_or_missing_is_refused(vault: Vault, tmp_path: Path) -> None:
    with pytest.raises(DocketError, match="inside the vault"):
        run(vault, vault.root / "processed")
    with pytest.raises(DocketError, match="no folder"):
        run(vault, tmp_path / "nowhere")


# -- the run ------------------------------------------------------------------------------------------------------


def test_the_first_import(vault: Vault, folder: Path) -> None:
    repo = folder.parent
    before = snapshot(repo)
    report = run(vault, folder)
    assert (report.new, report.updated, report.covers, report.same, report.gone) == (9, 0, 0, 0, 0)
    assert report.skipped == {".jpg": 1, ".json": 1, "never-store": 2}
    assert report.reported == ["notes/latin1.md: not UTF-8, skipped"]
    assert (report.refused, report.kept, report.deleted, report.ok) == ([], [], [], True)
    assert (report.prefix, report.sample, report.covered) == ("notes", CALL, 3)
    assert report.commit == head(vault.root)
    message = sh(vault.root, "git", "log", "-1", "--format=%B").splitlines()
    assert message[0] == "Import notes: 9 new, 0 updated from notes"
    assert sum(line.startswith("Doc: legacy-note:") for line in message) == 9
    store = DocStore(vault)
    call = store.get(CALL_ID)
    assert call is not None
    assert (call.meta.covers, call.meta.old_paths, call.meta.by, call.meta.rev) == ([REC], [CALL], "docket import", 1)
    assert call.meta.import_hash == body_hash(call.body)
    assert call.body.startswith("# Supplier call\n\nSource: call recorder `rec-0042`")
    chat = store.get(CHAT_ID)
    assert chat is not None
    assert (chat.meta.old_paths, chat.meta.covers) == ([CHAT], [])
    offer = store.get(OFFER_ID)
    assert offer is not None
    assert offer.body == "# The offer\n\nCleo sent the offer.\npassword: [redacted:password]\n"
    two = store.get("legacy-note:2026-09-12-two-parts")
    assert two is not None
    assert two.meta.covers == [REC, VENDOR]
    again = store.get("legacy-note:2026-10-14-supplier-call")
    assert again is not None
    assert again.meta.covers == [REC]
    own = store.get("legacy-note:own-frontmatter")
    assert own is not None
    assert own.body.startswith("---\ntags: [old]\n---\n")
    assert store.get("legacy-note:arvizturo-jegyzet") is not None
    assert store.get("legacy-note:2026-09-13-untracked") is not None
    assert snapshot(repo) == before


def test_a_screened_note_leaves_no_trace(vault: Vault, folder: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="docket")
    report = run(vault, folder)
    stored = [p.read_text(encoding="utf-8").casefold() for p in (vault.root / "processed").rglob("*.md")]
    history = sh(vault.root, "git", "log", "-p", "--all").casefold()
    for name in ("dora", "falcon"):
        assert not any(name in text for text in stored)
        assert name not in history
        assert name not in report.model_dump_json().casefold()
        assert name not in caplog.text.casefold()
    listed = [r.getMessage() for r in caplog.records if r.name == "docket.imports.notes"]
    assert "import: 14 file(s) listed" in listed


def test_a_dry_run_writes_nothing(vault: Vault, folder: Path) -> None:
    before = head(vault.root)
    report = run(vault, folder, dry_run=True)
    assert (report.new, report.dry_run, report.commit) == (9, True, None)
    assert head(vault.root) == before
    assert not (vault.root / "processed" / "legacy-note").exists()


def test_a_re_run_with_nothing_new_writes_nothing(after: tuple[Vault, Path]) -> None:
    vault, folder = after
    before = head(vault.root)
    report = run(vault, folder)
    assert (report.new, report.updated, report.covers, report.same, report.commit) == (0, 0, 0, 9, None)
    assert head(vault.root) == before


def test_a_changed_note_is_the_next_rev(after: tuple[Vault, Path]) -> None:
    vault, folder = after
    (folder / "calls" / "2026-09-10-supplier-call.md").write_text("# Supplier call\n\nCorrected.\n", encoding="utf-8")
    report = run(vault, folder)
    assert (report.updated, report.same) == (1, 8)
    store = DocStore(vault)
    doc = store.get(CALL_ID)
    assert doc is not None
    assert (doc.body, doc.meta.rev, doc.meta.import_hash) == ("# Supplier call\n\nCorrected.\n", 2, body_hash(doc.body))
    assert doc.meta.covers == [REC]  # stored covers are kept, never removed
    assert store.at_rev(CALL_ID, 1).body.startswith("# Supplier call\n\nSource:")


def test_a_recording_arriving_later_adds_a_cover_only(after: tuple[Vault, Path]) -> None:
    vault, folder = after
    vault.write_run("calls", [make_record(rid="calls:rec-0050", source="calls", kind="recording")], run_at=at(13))
    report = run(vault, folder)
    assert (report.covers, report.updated) == (1, 0)
    doc = DocStore(vault).get(LATER_ID)
    assert doc is not None
    assert (doc.meta.covers, doc.meta.rev) == (["calls:rec-0050"], 2)


def test_an_edit_in_the_vault_is_kept_while_the_note_is_the_same(after: tuple[Vault, Path]) -> None:
    vault, folder = after
    set_body(vault, CALL_ID, "# Supplier call\n\nClaude's correction.\n", covers=[REC, VENDOR], entities=["ben-sample"])
    report = run(vault, folder)
    assert (report.same, report.updated) == (9, 0)
    doc = DocStore(vault).get(CALL_ID)
    assert doc is not None
    assert (doc.body, doc.meta.covers, doc.meta.entities) == (
        "# Supplier call\n\nClaude's correction.\n",
        [REC, VENDOR],
        ["ben-sample"],
    )


def test_both_changed_is_kept_and_a_take_settles_it(after: tuple[Vault, Path]) -> None:
    vault, folder = after
    set_body(vault, CALL_ID, "# Supplier call\n\nClaude's correction.\n")
    note_file = folder / "calls" / "2026-09-10-supplier-call.md"
    note_file.write_text("# Supplier call\n\nThe note changed too.\n", encoding="utf-8")
    report = run(vault, folder)
    note_hash = short(body_hash("# Supplier call\n\nThe note changed too.\n"))
    assert report.kept == [notes.Kept(old_path=CALL, doc=CALL_ID, hash=note_hash)]
    assert not report.ok
    stale = run(vault, folder, takes=[Take(side="vault", hash="00000000", old_path=CALL)])
    assert stale.refused == [f"--take vault:00000000:{CALL}: the note changed since; it is {note_hash} now"]
    assert stale.kept == report.kept
    kept_vault = run(vault, folder, takes=[Take(side="vault", hash=note_hash, old_path=CALL)])
    assert (kept_vault.updated, kept_vault.ok) == (1, True)
    doc = DocStore(vault).get(CALL_ID)
    assert doc is not None
    assert doc.body == "# Supplier call\n\nClaude's correction.\n"
    assert run(vault, folder).updated == 0
    note_file.write_text("# Supplier call\n\nChanged once more.\n", encoding="utf-8")
    newer = short(body_hash("# Supplier call\n\nChanged once more.\n"))
    took_note = run(vault, folder, takes=[Take(side="note", hash=newer, old_path=CALL)])
    assert took_note.updated == 1
    store = DocStore(vault)
    doc = store.get(CALL_ID)
    assert doc is not None
    assert (doc.body, doc.meta.by) == ("# Supplier call\n\nChanged once more.\n", "docket import")
    assert any(store.at_rev(CALL_ID, n).body.endswith("Claude's correction.\n") for n, _ in store.history(CALL_ID))


def test_a_doc_merged_by_hand_only_moves_its_hash(after: tuple[Vault, Path]) -> None:
    vault, folder = after
    (folder / "calls" / "2026-09-10-supplier-call.md").write_text("# Merged\n", encoding="utf-8")
    set_body(vault, CALL_ID, "# Merged\n")
    report = run(vault, folder)
    assert (report.updated, report.ok) == (1, True)
    doc = DocStore(vault).get(CALL_ID)
    assert doc is not None
    assert (doc.body, doc.meta.import_hash, doc.meta.by) == ("# Merged\n", body_hash("# Merged\n"), "claude-test")


def test_a_doc_without_its_import_hash_counts_as_edited(after: tuple[Vault, Path]) -> None:
    vault, folder = after
    set_body(vault, CALL_ID, "# Rewritten without a hash\n", import_hash=None)
    assert run(vault, folder).kept[0].doc == CALL_ID


def test_gone_and_renamed_notes(after: tuple[Vault, Path]) -> None:
    vault, folder = after
    (folder / "chat" / "2026-09-10-supplier-call.md").rename(folder / "chat" / "renamed.md")
    report = run(vault, folder)
    assert (report.new, report.gone) == (1, 1)
    store = DocStore(vault)
    assert store.get(CHAT_ID) is not None
    assert store.get("legacy-note:renamed") is not None


def test_a_doc_deleted_in_the_vault_stays_deleted_until_taken(after: tuple[Vault, Path]) -> None:
    vault, folder = after
    rel = "processed/legacy-note/2026/09/2026-09-11-offer.md"
    (vault.root / rel).unlink()
    sh(vault.root, "git", *ADA, "commit", "-q", "-am", "drop the offer note")
    report = run(vault, folder)
    note_hash = report.deleted[0].hash
    assert report.deleted == [notes.Kept(old_path=OFFER, doc=OFFER_ID, hash=note_hash)]
    assert (report.new, report.ok) == (0, True)
    assert not (vault.root / rel).exists()
    wrong = run(vault, folder, takes=[Take(side="vault", hash=note_hash, old_path=OFFER)])
    assert wrong.refused == [f"--take vault:{note_hash}:{OFFER}: only note brings back a deleted doc"]
    stale = run(vault, folder, takes=[Take(side="note", hash="00000000", old_path=OFFER)])
    assert stale.refused == [f"--take note:00000000:{OFFER}: the note changed since"]
    back = run(vault, folder, takes=[Take(side="note", hash=note_hash, old_path=OFFER)])
    assert back.updated == 1
    doc = DocStore(vault).get(OFFER_ID)
    assert doc is not None
    assert doc.meta.rev == 2


def test_a_take_for_a_path_that_isnt_there_is_refused(after: tuple[Vault, Path]) -> None:
    vault, folder = after
    report = run(vault, folder, takes=[Take(side="note", hash="abc", old_path="notes/nowhere.md")])
    assert report.refused == ["--take note:abc:notes/nowhere.md: no note there"]
    assert not report.ok


def test_a_run_killed_before_its_commit_is_finished_by_the_next(
    vault: Vault, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def killed(self: DocStore, files: list[tuple[str, str]], message: str) -> str | None:
        for rel, text in files:
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text, encoding="utf-8", newline="\n")
        raise KeyboardInterrupt

    with monkeypatch.context() as mp:
        mp.setattr(DocStore, "_write_all_and_commit", killed)
        with pytest.raises(KeyboardInterrupt):
            run(vault, folder)
    report = run(vault, folder)
    assert (report.refused, report.ok, report.same) == ([], True, 9)
    assert sh(vault.root, "git", "status", "--porcelain", "--", "processed") == ""
    assert DocStore(vault).latest_revs("legacy-note").keys() >= {CALL_ID, CHAT_ID, OFFER_ID}


def test_a_hand_edit_made_during_the_run_is_kept(after: tuple[Vault, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    vault, folder = after
    (folder / "calls" / "2026-09-10-supplier-call.md").write_text("# Changed in the folder\n", encoding="utf-8")
    real = DocStore.put_many
    path = vault.root / "processed" / "legacy-note" / "2026" / "09" / "2026-09-10-supplier-call.md"

    def edited_meanwhile(self: DocStore, texts: list[str], subject: str) -> Any:
        if path.read_text(encoding="utf-8").endswith("(12:08).\n- The follow-up is `rec-0099`.\n"):
            path.write_text(path.read_text(encoding="utf-8") + "- Added by hand.\n", encoding="utf-8")
        return real(self, texts, subject)

    monkeypatch.setattr(DocStore, "put_many", edited_meanwhile)
    report = run(vault, folder)
    assert [k.doc for k in report.kept] == [CALL_ID]
    assert path.read_text(encoding="utf-8").endswith("- Added by hand.\n")
    rel = path.relative_to(vault.root).as_posix()
    assert "(edited by hand)" in sh(vault.root, "git", "log", "--format=%B", "--", rel)


def test_a_doc_the_store_refuses_is_reported(after: tuple[Vault, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    vault, folder = after
    (folder / "calls" / "2026-09-10-supplier-call.md").write_text("# Changed\n", encoding="utf-8")

    def refusing(self: DocStore, texts: list[str], subject: str) -> Any:
        return [DocRefusedError([DocProblem(None, "a hook said no")]) for _ in texts]

    monkeypatch.setattr(DocStore, "put_many", refusing)
    report = run(vault, folder)
    assert report.refused == [f"{CALL}: the doc was refused: doc: a hook said no"]


def test_a_stale_doc_gone_meanwhile_is_reported(after: tuple[Vault, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    vault, folder = after
    (folder / "calls" / "2026-09-10-supplier-call.md").write_text("# Changed\n", encoding="utf-8")
    path = vault.root / "processed" / "legacy-note" / "2026" / "09" / "2026-09-10-supplier-call.md"
    real = DocStore.put_many
    calls: list[int] = []

    def stale_then_gone(self: DocStore, texts: list[str], subject: str) -> Any:
        calls.append(1)
        results = real(self, [t.replace("rev: 1\n", "rev: 9\n") for t in texts], subject)
        path.unlink()
        return results

    monkeypatch.setattr(DocStore, "put_many", stale_then_gone)
    report = run(vault, folder)
    assert report.refused == [f"{CALL}: {CALL_ID} changed in the vault during the import"]
    assert len(calls) == 1


def test_a_second_refusal_is_reported(after: tuple[Vault, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    vault, folder = after
    (folder / "calls" / "2026-09-10-supplier-call.md").write_text("# Changed\n", encoding="utf-8")
    real = DocStore.put_many

    def always_stale(self: DocStore, texts: list[str], subject: str) -> Any:
        return real(self, [t.replace("rev: 1\n", "rev: 9\n") for t in texts], subject)

    monkeypatch.setattr(DocStore, "put_many", always_stale)
    report = run(vault, folder)
    assert len(report.refused) == 1
    assert "is at rev 1 now" in report.refused[0]


def test_a_clash_whose_fallback_is_taken_too(vault: Vault, folder: Path) -> None:
    taken = clash_slug("2026-09-10-supplier-call", CHAT)
    path = vault.root / "processed" / "legacy-note" / "2026" / "09" / f"{taken}.md"
    path.parent.mkdir(parents=True)
    path.write_text("not a doc\n", encoding="utf-8")
    report = run(vault, folder)
    assert report.refused == [f"{CHAT}: its doc id and the fallback id are both taken"]


def test_a_note_over_2_mb_or_with_a_path_old_paths_cant_hold(vault: Vault, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "big.md").write_bytes(b"x" * (notes.MAX_BYTES + 1))
    (plain / "fine.md").write_text("# fine\n", encoding="utf-8")
    bad = plain / "odd\u0007name.md"
    try:
        bad.write_text("# odd\n", encoding="utf-8")
        odd = True
    except OSError:
        odd = False
    report = run(vault, plain)
    assert report.reported == ["plain/big.md: over 2 MB, skipped"]
    assert report.new == 1
    assert len(report.refused) == (1 if odd else 0)


def test_a_deleted_doc_whose_last_rev_cant_be_read_is_ignored(
    after: tuple[Vault, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, folder = after
    (vault.root / "processed" / "legacy-note" / "2026" / "09" / "2026-09-11-offer.md").unlink()

    def unreadable(self: DocStore, doc_id: str, rev: int) -> Any:
        raise DocketError("gone")

    monkeypatch.setattr(DocStore, "at_rev", unreadable)
    report = run(vault, folder)
    assert report.deleted == []
    assert report.refused == []


def test_a_frontmatter_edit_made_during_the_run_is_kept_and_the_note_still_lands(
    after: tuple[Vault, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, folder = after
    (folder / "calls" / "2026-09-10-supplier-call.md").write_text("# Changed in the folder\n", encoding="utf-8")
    real = DocStore.put_many
    path = vault.root / "processed" / "legacy-note" / "2026" / "09" / "2026-09-10-supplier-call.md"

    def tagged_meanwhile(self: DocStore, texts: list[str], subject: str) -> Any:
        text = path.read_text(encoding="utf-8")
        if "entities: []" in text:
            path.write_text(text.replace("entities: []", "entities: [ben-sample]"), encoding="utf-8")
        return real(self, texts, subject)

    monkeypatch.setattr(DocStore, "put_many", tagged_meanwhile)
    report = run(vault, folder)
    assert (report.updated, report.kept, report.refused) == (1, [], [])
    doc = DocStore(vault).get(CALL_ID)
    assert doc is not None
    assert (doc.body, doc.meta.entities, doc.meta.rev) == ("# Changed in the folder\n", ["ben-sample"], 3)


def test_the_vaults_own_folder_is_skipped_by_path(folder: Path) -> None:
    inside = folder / "vault"
    (inside / "processed").mkdir(parents=True)
    (inside / "processed" / "x.md").write_text("# the vault's own\n", encoding="utf-8")
    old_paths, skipped = listing(folder, inside)
    assert not any(p.startswith("notes/vault") for p in old_paths)
    assert skipped["nested repos"] == 0


def test_a_path_old_paths_cant_hold_is_refused(vault: Vault, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = notes.list_folder

    def odd(*args: Any) -> list[Listed]:
        found = real(*args)
        return [Listed("notes/odd\u0007name.md", found[0].path), *found[1:]]

    monkeypatch.setattr(notes, "list_folder", odd)
    report = run(vault, folder, dry_run=True)
    assert report.refused == ["notes/odd\u0007name.md: 'notes/odd\\x07name.md' has a control character or a line break"]


def test_a_same_day_recording_titled_with_only_part_of_the_notes_title_covers_nothing() -> None:
    zone = ZoneInfo("Europe/Budapest")
    listed = Listed("notes/calls/2026-10-14-supplier-call.md", Path("x"))
    call_only = Names([record("calls:rec-0101", "Call", ts=at(6))], zone)
    assert call_only.covers(listed, "# Supplier call\n\nno ids\n") == []
    both = Names([record("calls:rec-0101", "Call", ts=at(6)), record("calls:rec-0102", "Supplier call")], zone)
    assert both.covers(listed, "# Supplier call\n\nno ids\n") == ["calls:rec-0102"]
    assert both.covers(Listed("notes/calls/2026-10-14-call.md", Path("x")), "# x\n") == ["calls:rec-0101"]


def test_a_take_given_while_the_vault_edit_is_uncommitted_still_lands(after: tuple[Vault, Path]) -> None:
    vault, folder = after
    path = vault.root / "processed" / "legacy-note" / "2026" / "09" / "2026-09-10-supplier-call.md"
    path.write_text(path.read_text(encoding="utf-8") + "- Added by hand in the vault.\n", encoding="utf-8")
    (folder / "calls" / "2026-09-10-supplier-call.md").write_text("# Supplier call\n\nSecond.\n", encoding="utf-8")
    kept = run(vault, folder).kept[0]
    took = run(vault, folder, takes=[Take(side="note", hash=kept.hash, old_path=CALL)])
    assert (took.updated, took.kept, took.refused, took.ok) == (1, [], [], True)
    doc = DocStore(vault).get(CALL_ID)
    assert doc is not None
    assert doc.body == "# Supplier call\n\nSecond.\n"
    rel = path.relative_to(vault.root).as_posix()
    assert "(edited by hand)" in sh(vault.root, "git", "log", "--format=%B", "--", rel)


def test_a_name_behind_a_safe_link_or_an_old_encoding_reaches_no_doc(vault: Vault, tmp_path: Path) -> None:
    vault.config = vault.config.model_copy(
        update={"never_store": NeverStore(keywords=["dora example", "kovács", "erdős"])}
    )
    plain = tmp_path / "plain"
    plain.mkdir()
    safe_link = (
        "https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fcontoso.example%2FPeople"
        "%2FDora%2520Example%2F1-1.docx&data=05%7C02"
    )
    notes_with = {
        "a.md": f"# A\n\nSee [the 1-1]({safe_link}).\n",
        "b.md": "# B\n\nSee Dora&#37;20Example.\n",
        "c.md": "# C\n\nhttps://legacy.example/staff?name=Kov%E1cs\n",
        "d.md": "# D\n\nhttps://legacy.example/staff?name=Erd%u0151s\n",
        "e.md": '# E\n\n{"name": "Kov\\u00e1cs B\\u00e9la"}\n',
        "f.md": "# F\n\nNothing to hide.\n",
    }
    for name, text in notes_with.items():
        (plain / name).write_text(text, encoding="utf-8")
    report = run(vault, plain)
    assert (report.new, report.skipped) == (1, {"never-store": 5})
    stored = [p.read_text(encoding="utf-8").casefold() for p in (vault.root / "processed").rglob("*.md")]
    history = sh(vault.root, "git", "log", "-p", "--all").casefold()
    for needle in ("dora", "kov", "erd", "safelinks"):
        assert not any(needle in text for text in stored)
        assert needle not in history


# -- redaction settings (DR-0190, DR-0191, DR-0197) -----------------------------------------------------------------

PHONE_NOTE = "notes/calls/2026-09-20-cleo-s-line.md"
PHONE_ID = "legacy-note:2026-09-20-cleo-s-line"
KEY_NOTE = "notes/keys.md"
KEY_ID = "legacy-note:keys"
KEY_BLOCK = "PRIVATE " + "KEY-----"


def with_redaction_notes(folder: Path) -> None:
    """A note holding an invented phone number and one holding a key block over lines, not committed yet."""
    (folder / "calls" / "2026-09-20-cleo-s-line.md").write_text(
        "# Cleo's line\n\nShe is on +1 555 010 0188 until Friday.\n", encoding="utf-8"
    )
    key = f"# Keys\n\n-----BEGIN OPENSSH {KEY_BLOCK}\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH {KEY_BLOCK}\nafter\n"
    (folder / "keys.md").write_text(key, encoding="utf-8")


def redacting(vault: Vault, **settings: bool) -> Vault:
    """The vault with redaction switched in memory, as the import reads it."""
    vault.config = vault.config.model_copy(update={"redaction": vault.config.redaction.model_copy(update=settings)})
    return vault


def test_the_import_counts_what_it_redacted_and_a_key_block_keeps_its_lines(vault: Vault, folder: Path) -> None:
    with_redaction_notes(folder)
    report = run(vault, folder)
    assert report.redactions == {"password": 1, "private-key": 3}
    store = DocStore(vault)
    phone = store.get(PHONE_ID)
    assert phone is not None
    assert "+1 555 010 0188" in phone.body
    keys = store.get(KEY_ID)
    assert keys is not None
    assert keys.body == "# Keys\n\n" + "[redacted:private-key]\n" * 3 + "after\n"
    assert run(vault, folder).redactions == {}


def test_a_dry_run_counts_what_it_would_redact(vault: Vault, folder: Path) -> None:
    with_redaction_notes(folder)
    assert run(vault, folder, dry_run=True).redactions == {"password": 1, "private-key": 3}


@pytest.mark.parametrize("edited", [False, True], ids=["as-imported", "edited-in-the-vault"])
def test_an_import_after_phone_goes_off_writes_one_new_rev_of_a_note_holding_a_number(
    vault: Vault, folder: Path, monkeypatch: pytest.MonkeyPatch, edited: bool
) -> None:
    with_redaction_notes(folder)

    def whole_text(_doc_type: str, body: str, redactor: Any) -> Any:
        # The import before this change: one redaction over the whole body, a key block folded into one marker.
        counts: Counter[str] = Counter()
        return derived.Redacted(redactor.text(body, counts), counts, [])

    with monkeypatch.context() as patched:
        patched.setattr(derived, "redact", whole_text)
        first = run(redacting(vault, phone=True), folder)
    assert first.new == 11
    if edited:
        set_body(vault, PHONE_ID, "# Cleo's line\n\nShe is on [redacted:phone], edited in the vault.\n")
    report = run(redacting(vault, phone=False), folder)
    # The key block's note moves to the redaction that keeps lines; the phone number's, to the number as written.
    assert (report.updated, report.new) == (1 if edited else 2, 0)
    assert [kept.doc for kept in report.kept] == ([PHONE_ID] if edited else [])
    store = DocStore(vault)
    phone = store.get(PHONE_ID)
    keys = store.get(KEY_ID)
    assert phone is not None and keys is not None
    assert ("edited in the vault" in phone.body) if edited else ("+1 555 010 0188" in phone.body)
    assert keys.body.count("[redacted:private-key]") == 3


def test_turning_phone_on_writes_each_note_holding_a_number_as_its_next_rev(vault: Vault, folder: Path) -> None:
    with_redaction_notes(folder)
    run(vault, folder)
    report = run(redacting(vault, phone=True), folder)
    assert (report.updated, report.redactions) == (1, {"phone": 1})
    phone = DocStore(vault).get(PHONE_ID)
    assert phone is not None
    assert "[redacted:phone]" in phone.body
