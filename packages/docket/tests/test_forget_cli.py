"""`docket forget` (DR-0577, DR-0581, DR-0594): the dry run in any shell; `--now` refused under Claude Code, from a pipe
and to a redirect before anything is read; n and q keep everything; the code forgets in the order of writes, rebuilds
the index after the lock and writes the rewrite's files; a change since the screen stops it with nothing written;
only the command calls `forget()`; and `--check-rewrite` passes on a fresh forget and refuses once HEAD holds a line the
files would blank. Every vault is a temp one with a test key store; invented data only."""

from __future__ import annotations

import inspect
import re
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from docket_testkit import AtConsole, git
from media_kit import CALL, POLISHED, audio, call, in_raw, media_vault

from docket import cli, console, index, recall
from docket.cli import main
from docket.errors import DocketError
from docket.forget import now as forget_now
from docket.forget import rewrite
from docket.index import build
from docket.media import layout
from docket.media import put as media
from docket.media.keystore import KeyStore
from docket.vault import forgotten
from docket.vault.config import VaultConfig
from docket.vault.docstore import DocStore
from docket.vault.vault import Vault

NOTE = "recording-note:2026-10-12-crane-check"


def put(vault: Vault) -> None:
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "take").write_bytes(audio(4000))
    media.put(vault, CALL, path=str(drop / "take"))


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    """The call in raw with its audio stored, and a note on its transcript."""
    found = media_vault(tmp_path)
    put(found)
    in_raw(found)
    text = (
        f"---\nid: {NOTE}\ntype: recording-note\ncovers:\n  - {POLISHED}\nby: claude-test, sync 2026-10-12\n---\n\n"
        f"# Crane check\n\n- The crane passed. [r:{POLISHED}@0:01]\n"
    )
    recall.doc_put(found, text)
    build.rebuild(found)
    return found


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


def test_only_the_command_calls_forget() -> None:
    src = Path(forget_now.__file__).resolve().parents[1]
    callers = sorted(
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if p.name != "now.py" and re.search(r"forget(\.| import )now\b", p.read_text(encoding="utf-8"))
    )
    assert callers == ["cli.py"]


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
    assert f'next      run in your own terminal: docket forget {CALL} --vault "{vault.root}" --now' in out
    assert NOTE not in out and "crane passed" not in out
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
    assert "forgot    2 record(s), 1 media file(s), 1 doc(s); 1 key(s) deleted here, committed" in out
    assert "index     rebuilt without what was forgotten" in out
    assert subjects(vault)[:2] == ["Forget: 2 record(s), 1 media file(s), 1 doc(s) from test-box",
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
    assert "rewrite   HEAD stays as it is" in capsys.readouterr().out


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


def test_check_rewrite_refuses_once_head_holds_a_line_the_files_would_blank(
    vault: Vault, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    owner("code")
    assert run(vault, CALL, "--now") == 0
    found = re.search(r'docket forget --check-rewrite "([^"]+)"', capsys.readouterr().out)
    assert found is not None
    later = vault.root / "processed" / "quoted.md"
    later.write_text(f"Kept by hand.\nIt said [r:{CALL}@0:01] once.\n", encoding="utf-8", newline="\n")
    git(vault.root, "add", "processed/quoted.md")
    git(vault.root, "commit", "-q", "-m", "A file by hand")
    assert run(vault, "--check-rewrite", found.group(1)) == 1
    out = capsys.readouterr().out
    assert "rewrite   refused: it would change HEAD, so don't run it" in out
    assert "processed/quoted.md: line 2" in out


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
    assert f"left      {NOTE}: the forget's rev wasn't written; fix the edit" in capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == edited
