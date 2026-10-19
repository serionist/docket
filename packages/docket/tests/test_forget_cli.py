"""`docket forget` (DR-0577, DR-0581, DR-0594, DR-0598): the dry run in any shell, its next line quoting each rid;
`--now` refused under Claude Code, from a pipe and to a redirect before anything is read; n and q keep everything; the
code forgets in the order of writes, rebuilds the index after the lock and writes the rewrite's files, whatever stops
the list's commit or the apply once the line is in force; a change since the screen stops it with nothing written;
only the command calls `forget()`, by an AST scan; and `--check-rewrite` passes on a fresh forget and refuses once a
branch holds a line the files would blank. Every vault is a temp one with a test key store; invented data only."""

from __future__ import annotations

import ast
import inspect
import re
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from docket_testkit import AtConsole, FakePrompt, copied_with_seals, git, make_record
from media_kit import CALL, POLISHED, audio, call, in_raw, media_vault

from docket import cli, console, index, osprompt, recall
from docket.cli import main
from docket.errors import DocketError
from docket.forget import apply as forget_apply
from docket.forget import now as forget_now
from docket.forget import rewrite
from docket.index import build
from docket.media import layout
from docket.media import put as media
from docket.media.keystore import KeyStore
from docket.vault import forgotten
from docket.vault.config import VaultConfig
from docket.vault.docstore import DocStore
from docket.vault.gate import Gate
from docket.vault.vault import Vault

NOTE = "recording-note:2026-10-12-crane-check"


def put(vault: Vault) -> None:
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "take").write_bytes(audio(4000))
    media.put(vault, CALL, path=str(drop / "take"))


def _build_vault(folder: Path) -> Path:
    found = media_vault(folder)
    put(found)
    in_raw(found)
    text = (
        f"---\nid: {NOTE}\ntype: recording-note\ncovers:\n  - {POLISHED}\nby: claude-test, sync 2026-10-12\n---\n\n"
        f"# Crane check\n\n- The crane passed. [r:{POLISHED}@0:01]\n"
    )
    recall.doc_put(found, text)
    build.rebuild(found)
    return found.root


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    """The call in raw with its audio stored, and a note on its transcript: built once per run, each test a copy
    (DR-0531)."""
    return Vault.open(copied_with_seals(tmp_path, _build_vault, f"{__name__}.vault"))


def run(vault: Vault, *args: str) -> int:
    return main(["forget", *args, "--vault", str(vault.root)])


def subjects(vault: Vault) -> list[str]:
    return git(vault.root, "log", "--format=%s").splitlines()


# -- the console -------------------------------------------------------------------------------------------------------


def nothing_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(*_: object) -> Vault:
        raise AssertionError("the vault was read before the refusal")

    monkeypatch.setattr(cli, "find_vault", refused)
    monkeypatch.setattr(Vault, "open", refused)


def test_now_is_refused_in_claude_code_s_shell_before_anything_is_read(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    nothing_read(monkeypatch)
    assert run(vault, CALL, "--now") == 2
    assert "this shell is Claude Code's (CLAUDECODE is set): a forget is yours to confirm" in capsys.readouterr().err


def test_now_is_refused_from_a_pipe_and_to_a_redirect_before_anything_is_read(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    nothing_read(monkeypatch)
    # The test's own stdin and stdout are pipes, never a console.
    assert run(vault, CALL, "--now") == 2
    assert "a forget asks you at a console" in capsys.readouterr().err
    # A console on stdin, but stdout redirected.
    monkeypatch.setattr(console, "is_console", lambda stream: stream is sys.stdin)
    assert run(vault, CALL, "--now") == 2
    assert "a forget asks you at a console" in capsys.readouterr().err


def test_forget_refuses_a_python_caller_without_the_owner(vault: Vault) -> None:
    assert list(inspect.signature(forget_now.forget).parameters) == ["vault", "kind", "rids", "source"]
    with pytest.raises(DocketError, match="asks you at a console"):
        forget_now.forget(vault, "rids", [CALL])
    assert forgotten.load(vault.root, vault.home) == forgotten.EMPTY


NOW = "docket.forget.now"


def _imports_now(source: str, rel: str) -> bool:
    """Whether a module's source reaches `docket.forget.now`, however it is written: an import of it, of a name from
    it or of it by name from its package, a relative one, the package's attribute, or its name to importlib."""
    module = ast.parse(source)
    package = ["docket", *Path(rel).parent.parts]
    bound: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == NOW or alias.name.startswith(f"{NOW}."):
                    return True
                if alias.name == "docket.forget" and alias.asname:
                    bound.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join([*package[: len(package) - node.level + 1], *([base] if base else [])])
            if base == NOW or base.startswith(f"{NOW}."):
                return True
            names = {alias.name for alias in node.names}
            if base == "docket.forget" and "now" in names:
                return True
            if base == "docket":
                bound |= {alias.asname or alias.name for alias in node.names if alias.name == "forget"}
        elif isinstance(node, ast.Constant) and node.value == NOW:
            return True
    return any(isinstance(node, ast.Attribute) and node.attr == "now" and _the_package(node.value, bound)
               for node in ast.walk(module))  # fmt: skip


def _the_package(node: ast.expr, bound: set[str]) -> bool:
    """Whether an expression is the `docket.forget` package: a name bound to it, or `docket.forget` itself."""
    if isinstance(node, ast.Name):
        return node.id in bound
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "forget"
        and isinstance(node.value, ast.Name)
        and node.value.id == "docket"
    )


def test_only_the_command_calls_forget() -> None:
    src = Path(forget_now.__file__).resolve().parents[1]
    callers = sorted(
        rel
        for p in src.rglob("*.py")
        if (rel := p.relative_to(src).as_posix()) != "forget/now.py"
        and _imports_now(p.read_text(encoding="utf-8"), rel)
    )
    assert callers == ["cli.py"]


@pytest.mark.parametrize(
    "source",
    [
        "from docket.forget import apply, now",
        "from docket.forget import (\n    apply,\n    now,\n)",
        "from docket.forget import now as forgetting",
        "import docket.forget.now",
        "import docket.forget.now as forgetting",
        "from docket.forget.now import forget",
        "from . import now",
        "from .now import forget",
        "from docket import forget\nforget.now.forget(vault)",
        "from docket import forget as f\nf.now.forget(vault)",
        "import docket.forget as f\nf.now.forget(vault)",
        "import docket\ndocket.forget.now.forget(vault)",
        "import importlib\nimportlib.import_module('docket.forget.now')",
    ],
)
def test_the_scan_finds_each_way_of_reaching_forget(source: str) -> None:
    assert _imports_now(source, "forget/other.py")


def test_the_scan_lets_the_forget_s_other_modules_by() -> None:
    assert not _imports_now("from docket.forget import apply, plan\nfrom . import rewrite", "forget/other.py")
    assert not _imports_now("import datetime\ndatetime.datetime.now()", "service/clock.py")


# -- the dry run -------------------------------------------------------------------------------------------------------


def test_the_dry_run_works_in_claude_code_s_shell_and_writes_nothing(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    head = git(vault.root, "rev-parse", "HEAD")
    assert run(vault, CALL) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"forget    {CALL} and 1 record with it: 2 versions in 1 run file")
    assert "docs      1 line in 1 recording note (14)" in out and "removed   1 doc left with no line" in out
    assert f'next      run in your own terminal: docket forget "{CALL}" --vault "{vault.root}" --now' in out
    assert NOTE not in out and "crane passed" not in out
    assert "put it in by hand" not in out
    assert git(vault.root, "rev-parse", "HEAD") == head
    assert not (vault.root / forgotten.FORGOTTEN).exists()


@pytest.mark.parametrize(
    ("args", "said"),
    [
        ([CALL, "--held"], "one kind a call"),
        ([], "one kind a call"),
        (["--check-rewrite", "x", "--held"], "--check-rewrite takes a rewrite folder"),
        ([CALL, "--source", "plaud"], "--source goes with"),
    ],
)
def test_one_kind_a_call(vault: Vault, capsys: pytest.CaptureFixture[str], args: list[str], said: str) -> None:
    assert run(vault, *args) == 2
    assert said in capsys.readouterr().err


# -- the question and the writes ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["n", "q", "ABCD"])
def test_n_q_or_a_wrong_code_keeps_everything(
    vault: Vault, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str], answer: str
) -> None:
    head = git(vault.root, "rev-parse", "HEAD")
    person = owner(answer)
    assert run(vault, CALL, "--now") == 0
    assert "kept      nothing was forgotten" in capsys.readouterr().out
    assert git(vault.root, "rev-parse", "HEAD") == head
    assert vault.rawlog.read(CALL) != []
    assert "to forget all of it for good, n to keep everything, q to stop" in person.prompts[0]


def test_the_code_forgets_in_the_order_of_writes(
    vault: Vault, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    (stored,) = layout.stored_files(vault.root, CALL)
    owner("code")
    assert run(vault, CALL, "--now") == 0
    out = capsys.readouterr().out
    # The screen named the doc by its id; then what went.
    assert NOTE in out
    assert "forgot    2 record(s), 1 media file(s), 1 doc(s), 0 ledger op(s); 1 key(s) deleted here, committed" in out
    assert "index     rebuilt without what was forgotten" in out
    assert subjects(vault)[:2] == ["Forget: 2 record(s), 1 media file(s), 1 doc(s), 0 op(s) from test-box",
                                   "Forget: the list from test-box"]  # fmt: skip
    assert git(vault.root, "status", "--porcelain", "--", ".").strip() == ""
    assert not stored.exists() and KeyStore(vault.home).ids() == []
    assert index.status(vault).stale is None
    found = re.search(r'docket forget --check-rewrite "([^"]+)"', out)
    assert found is not None
    folder = Path(found.group(1))
    assert {p.name for p in folder.iterdir()} == set(rewrite.FILES)
    assert re.fullmatch(r"\d{8}T\d{6}Z", folder.name)
    assert "git clone --no-local" in out and "git filter-repo isn't Docket's dependency" in out
    # The rewrite leaves HEAD as it is.
    assert run(vault, "--check-rewrite", str(folder)) == 0
    assert "rewrite   HEAD and your files outside the vault stay as they are" in capsys.readouterr().out


def test_a_change_since_the_screen_stops_it_with_nothing_written(
    vault: Vault, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    def change() -> str:
        # Another version of the transcript arrives while the owner reads the screen.
        vault.write_run("plaud", [call(text="The crane failed its load test.")[1]])
        return "code"

    head_before: list[str] = []

    def answer() -> str:
        found = change()
        head_before.append(git(vault.root, "rev-parse", "HEAD"))
        return found

    owner(answer)
    assert run(vault, CALL, "--now") == 2
    assert forget_now.CHANGED in capsys.readouterr().err
    assert git(vault.root, "rev-parse", "HEAD") == head_before[0]
    assert not (vault.root / forgotten.FORGOTTEN).exists()


def test_a_rebuild_that_fails_is_said_and_the_index_stays_refused(
    vault: Vault, owner: Callable[..., AtConsole], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def busy(found: Vault) -> object:
        raise DocketError("the index is open in another process")

    monkeypatch.setattr(index, "rebuild", busy)
    owner("code")
    assert run(vault, CALL, "--now") == 0
    assert "index     refused until rebuilt: the index is open in another process" in capsys.readouterr().out
    assert index.status(vault).stale is not None


def test_what_stops_a_forget_is_shown_and_nothing_is_written(
    vault: Vault, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    (stored,) = layout.stored_files(vault.root, CALL)
    (vault.root / "state" / "media-cuts" / "plaud.jsonl").write_text("", encoding="utf-8")
    stored.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 9\n",
                      encoding="utf-8")  # fmt: skip
    owner("code")
    assert run(vault, CALL, "--now") == 2
    assert "is an LFS pointer and no cut log line names its key" in capsys.readouterr().err
    assert not (vault.root / forgotten.FORGOTTEN).exists()


def test_nothing_stored_says_so_and_asks_nothing(
    vault: Vault, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    person = owner("code")
    assert run(vault, "plaud:rec-zz", "--now") == 0
    assert capsys.readouterr().out == "nothing   nothing stored as plaud:rec-zz\n"
    assert person.prompts == []


# -- the rewrite check -------------------------------------------------------------------------------------------------


def test_check_rewrite_reads_every_branch_and_refuses_one_that_holds_a_line_the_files_would_blank(
    vault: Vault, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    before = git(vault.root, "rev-parse", "HEAD").strip()
    (run_file,) = [r.path for r in vault.rawlog.run_files() if r.source == "plaud"]
    rel = run_file.relative_to(vault.root).as_posix()
    owner("code")
    assert run(vault, CALL, "--now") == 0
    found = re.search(r'docket forget --check-rewrite "([^"]+)"', capsys.readouterr().out)
    assert found is not None
    folder = found.group(1)
    # A branch whose tip holds HEAD's very tree: nothing of its own to read.
    twin = git(vault.root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "A twin").strip()
    git(vault.root, "branch", "twin", twin)
    # A file written after the forget that quotes the rid is the owner's: a blob the files never name (DR-0598).
    later = vault.root / "processed" / "quoted.md"
    later.write_text(f"Kept by hand.\nIt said [r:{CALL}@0:01] once.\n", encoding="utf-8", newline="\n")
    git(vault.root, "add", "processed/quoted.md")
    git(vault.root, "commit", "-q", "-m", "A file by hand")
    assert run(vault, "--check-rewrite", folder) == 0
    capsys.readouterr()
    # A branch from before the forget still holds the run file's lines, and the rewrite rewrites every branch: named,
    # with what to do, but HEAD stays as it is, so no refusal (DR-0598).
    git(vault.root, "branch", "before", before)
    assert run(vault, "--check-rewrite", folder) == 0
    out = capsys.readouterr().out
    assert "branch    before still holds what the forget took: merge the forget into it, or delete it, first" in out
    assert f"          {rel}: line 1, 2" in out and "rewrite   HEAD and your files outside the vault stay" in out
    git(vault.root, "branch", "-D", "before")
    # HEAD, once a merge an older Docket made brings the lines back: refused, named by path alone.
    run_file.write_text(git(vault.root, "show", f"{before}:./{rel}"), encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-q", "-m", "An older Docket's merge", "--", rel)
    assert run(vault, "--check-rewrite", folder) == 1
    out = capsys.readouterr().out
    assert "rewrite   refused: it would change HEAD or a file outside the vault, so don't run it" in out
    assert f"          {rel}: line 1, 2" in out


def test_check_rewrite_refuses_a_folder_that_isn_t_one(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(vault, "--check-rewrite", str(tmp_path / "nowhere")) == 2
    assert "isn't a forget's rewrite folder" in capsys.readouterr().err


def test_a_hand_edit_the_take_in_refuses_is_left_and_named(
    vault: Vault, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    # A keyword added to never-store by hand: stricter, so in force at once.
    settings = vault.root / "vault.yaml"
    config = VaultConfig.load(settings)
    config.never_store.keywords.append("zanzibar")
    settings.write_text(config.dump(), encoding="utf-8", newline="\n")
    path = DocStore(vault).file(NOTE)
    edited = path.read_text(encoding="utf-8") + "- Zanzibar said so. [r:" + POLISHED + "@0:02]\n"
    path.write_text(edited, encoding="utf-8", newline="\n")
    owner("code")
    assert run(vault, CALL, "--now") == 0
    out = capsys.readouterr().out
    assert f"left      {NOTE}: the forget's rev wasn't written; fix the edit" in out
    assert path.read_text(encoding="utf-8") == edited
    # HEAD still holds the note as it was, which the rewrite strips: the check refuses until the edit is fixed.
    found = re.search(r'docket forget --check-rewrite "([^"]+)"', out)
    assert found is not None
    assert run(vault, "--check-rewrite", found.group(1)) == 1
    capsys.readouterr()
    git(vault.root, "checkout", "--", path.relative_to(vault.root).as_posix())
    Gate(vault).list()
    assert not path.exists()
    assert run(vault, "--check-rewrite", found.group(1)) == 0


# -- what stops the apply after the list's commit ----------------------------------------------------------------------


@pytest.mark.parametrize("stop", [DocketError("git commit failed: the index is locked"), KeyboardInterrupt()])
def test_an_apply_stopped_after_the_list_s_commit_says_so_and_still_writes_the_rewrite(
    vault: Vault,
    owner: Callable[..., AtConsole],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stop: BaseException,
) -> None:
    real = forget_apply.apply

    def stopped(found: Vault, keys: KeyStore, *, first: bool = False) -> forget_apply.Applied:
        if first:
            raise stop
        return real(found, keys, first=first)

    monkeypatch.setattr(forget_apply, "apply", stopped)
    owner("code")
    assert run(vault, CALL, "--now") == 2
    err = capsys.readouterr().err
    assert (str(stop) or "stopped") in err and forget_now.IN_FORCE in err
    found = re.search(r"the history rewrite's files are in (.+)$", err.strip(), re.MULTILINE)
    assert found is not None
    folder = Path(found.group(1))
    assert {p.name for p in folder.iterdir()} == set(rewrite.FILES)
    assert subjects(vault)[0] == "Forget: the list from test-box"
    assert vault.rawlog.read(CALL) == []
    monkeypatch.setattr(forget_apply, "apply", real)
    Gate(vault).list()
    assert subjects(vault)[0] == "Forget: applied on test-box"
    assert run(vault, "--check-rewrite", str(folder)) == 0


def test_a_list_whose_commit_fails_is_in_force_and_its_rewrite_is_written(
    vault: Vault, owner: Callable[..., AtConsole], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = forgotten.commit_pending

    def refused(found: Vault, message: str | None = None) -> str | None:
        if message is None:
            return real(found)
        raise DocketError("a hook refused the commit")

    owner("code")
    monkeypatch.setattr(forgotten, "commit_pending", refused)
    with pytest.raises(DocketError, match="a hook refused the commit") as caught:
        forget_now.forget(vault, "rids", [CALL])
    assert forget_now.IN_FORCE in str(caught.value) and "the history rewrite's files are in" in str(caught.value)
    assert vault.rawlog.read(CALL) == []


def test_an_error_nobody_expected_keeps_its_trace_and_gets_the_note(
    vault: Vault, owner: Callable[..., AtConsole], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(found: Vault, keys: KeyStore, *, first: bool = False) -> forget_apply.Applied:
        raise RuntimeError("a bug")

    monkeypatch.setattr(forget_apply, "apply", broken)
    owner("code")
    with pytest.raises(RuntimeError, match="a bug") as caught:
        forget_now.forget(vault, "rids", [CALL])
    (note,) = caught.value.__notes__
    assert note.startswith(forget_now.IN_FORCE) and "the history rewrite's files are in" in note


def test_rewrite_files_that_can_t_be_written_are_said_and_the_forget_stands(
    vault: Vault, owner: Callable[..., AtConsole], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def full(*_: object) -> rewrite.Written:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(rewrite, "write", full)
    owner("code")
    assert run(vault, CALL, "--now") == 0
    out = capsys.readouterr().out
    assert "history   the history rewrite's files couldn't be written: [Errno 28] No space left on device" in out
    assert vault.rawlog.read(CALL) == [] and subjects(vault)[0].startswith("Forget: 2 record(s)")


def test_a_rebuild_stopped_with_ctrl_c_is_said_and_the_rest_printed(
    vault: Vault, owner: Callable[..., AtConsole], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def stopped(found: Vault) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(index, "rebuild", stopped)
    owner("code")
    assert run(vault, CALL, "--now") == 0
    out = capsys.readouterr().out
    assert "index     refused until rebuilt: the rebuild was stopped; docket index rebuild finishes it" in out
    assert "docket forget --check-rewrite" in out
    assert index.status(vault).stale is not None


def test_the_next_line_leaves_a_rid_a_shell_reads_more_into_to_put_in_by_hand(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    odd = "chat:C0TEST/a<b>&c|d;$e"
    vault.write_run("chat", [make_record(9, rid=odd, source="chat", text="Unrelated words.")])
    assert run(vault, odd) == 0
    out = capsys.readouterr().out
    assert f'next      run in your own terminal: docket forget "<rid 1>" --vault "{vault.root}" --now' in out
    assert f"          <rid 1> is {odd}: put it in by hand, quoted as your shell reads it" in out


def test_a_doc_left_for_its_owner_keeps_its_head_blob_in_the_rewrite(
    vault: Vault, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    # Short anchors only, so no [r: token names the transcript: the rule alone finds the lines, in HEAD's blob too.
    short = "recording-note:2026-10-12-short"
    text = (
        f"---\nid: {short}\ntype: recording-note\ncovers:\n  - {POLISHED}\nby: claude-test, sync 2026-10-12\n---\n\n"
        "# Short\n\n- The crane cleared. @0:01\n"
    )
    recall.doc_put(vault, text)
    settings = vault.root / "vault.yaml"
    config = VaultConfig.load(settings)
    config.never_store.keywords.append("zanzibar")
    settings.write_text(config.dump(), encoding="utf-8", newline="\n")
    path = DocStore(vault).file(short)
    path.write_text(path.read_text(encoding="utf-8") + "- Zanzibar said so. @0:02\n", encoding="utf-8", newline="\n")
    owner("code")
    assert run(vault, POLISHED, "--now") == 0
    out = capsys.readouterr().out
    assert f"left      {short}" in out
    found = re.search(r'docket forget --check-rewrite "([^"]+)"', out)
    assert found is not None
    # HEAD's blob of the doc is the rewrite's too: the check refuses until the edit is fixed and the forget's rev lands.
    assert run(vault, "--check-rewrite", found.group(1)) == 1
    assert "the whole file, whose blob the rewrite strips" in capsys.readouterr().out
    git(vault.root, "checkout", "--", path.relative_to(vault.root).as_posix())
    Gate(vault).list()
    assert not path.exists()
    assert run(vault, "--check-rewrite", found.group(1)) == 0


def test_with_an_os_prompt_its_yes_forgets_and_its_no_keeps_everything(
    vault: Vault,
    owner: Callable[..., AtConsole],
    os_prompt: Callable[..., FakePrompt],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The OS prompt is the forget's yes where this machine has one, and the code is never asked then (DR-0572)."""
    hello = os_prompt(osprompt.Refused("cancelled"), osprompt.Verified())
    person = owner("", "")
    head = git(vault.root, "rev-parse", "HEAD")
    assert run(vault, CALL, "--now") == 0
    out = capsys.readouterr().out
    assert "Fake Hello didn't confirm it (cancelled); nothing was forgotten" in out
    assert git(vault.root, "rev-parse", "HEAD") == head and vault.rawlog.read(CALL) != []
    assert run(vault, CALL, "--now") == 0
    assert "forgot    2 record(s)" in capsys.readouterr().out and vault.rawlog.read(CALL) == []
    assert hello.acts == [forget_now.ACT, forget_now.ACT]
    assert all(p.startswith("Press Enter to forget all of it for good with Fake Hello") for p in person.prompts)
