"""The CLI's one question before a vault.yaml change, turning credentials redaction off (DR-0601): refused in Claude
Code's shell and without a console, then the change shown path by path, never a never-store value, and made only on
the yes; an uncommitted edit that turns it off is asked about in the same question. Every other change asks nothing,
and its commit says who made it (DR-0599). Invented values only."""

from __future__ import annotations

import inspect
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from docket_testkit import (
    AtConsole,
    FakePrompt,
    cant_place,
    consuming_project,
    empty_vault,
    git,
    make_config,
    sh,
)
from pydantic import Field

from docket import console, osprompt
from docket.cli import main
from docket.errors import DocketError
from docket.vault import settings
from docket.vault.config import CONFIG_FILE, NeverStore, Redaction, RedactionPattern, SourceConfig, VaultConfig
from docket.vault.vault import Vault, remove_tree

Edit = Callable[[VaultConfig], VaultConfig]
BY_CLAUDE = f"\n\n{console.BY_CLAUDE}\n"


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    config = make_config()
    config.never_store = NeverStore(people=["Dora Example", "Ben Sample"])
    config.sources["memo"] = SourceConfig()
    return empty_vault(tmp_path, config)


def text(vault: Vault) -> str:
    return (vault.root / CONFIG_FILE).read_text(encoding="utf-8")


def head(vault: Vault) -> str:
    return sh(vault.root, "git", "rev-parse", "HEAD").strip()


def bodies(vault: Vault, n: int) -> list[str]:
    """The last n commits' messages, newest first, each with its trailing newline."""
    out = sh(vault.root, "git", "log", f"-{n}", "--format=%B%x00").split("\x00")[:n]
    return [m.lstrip("\n") for m in out]


def phone(on: bool) -> Edit:
    return lambda c: c.model_copy(update={"redaction": c.redaction.model_copy(update={"phone": on})})


def credentials(on: bool) -> Edit:
    return lambda c: c.model_copy(update={"redaction": c.redaction.model_copy(update={"credentials": on})})


def memo(**update: object) -> Edit:
    return lambda c: c.model_copy(
        update={"sources": {**c.sources, "memo": c.sources["memo"].model_copy(update=update)}}
    )


def by_hand(vault: Vault, change: Edit) -> None:
    path = vault.root / CONFIG_FILE
    path.write_text(change(VaultConfig.load(path)).dump(), encoding="utf-8", newline="\n")


def cli(vault: Vault, change: Edit, **kwargs: object) -> settings.Rewritten:
    return settings.rewrite(vault, change, "Config: a change", via="cli", **kwargs)  # type: ignore[arg-type]


def test_turning_credentials_off_is_refused_in_claude_code_s_shell_and_without_a_console(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    before, at = text(vault), head(vault)
    with pytest.raises(DocketError, match=r"turning credentials redaction off asks you at a console"):
        cli(vault, credentials(False))
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(DocketError, match=r"this shell is Claude Code's \(CLAUDECODE is set\)"):
        cli(vault, credentials(False))
    assert (text(vault), head(vault)) == (before, at)


@pytest.mark.parametrize("answer", ["n", "y", "q"])
def test_anything_but_the_code_writes_nothing(vault: Vault, owner: Callable[..., AtConsole], answer: str) -> None:
    before, at = text(vault), head(vault)
    owner(answer)
    assert cli(vault, credentials(False)).declined
    assert (text(vault), head(vault)) == (before, at)


def test_with_an_os_prompt_its_yes_makes_the_change_and_its_no_writes_nothing(
    vault: Vault, owner: Callable[..., AtConsole], os_prompt: Callable[..., FakePrompt]
) -> None:
    """Credentials off asks the OS prompt where this machine has one, and never the code then (DR-0572, DR-0574)."""
    before, at = text(vault), head(vault)
    hello = os_prompt(osprompt.Refused("cancelled"), osprompt.Verified())
    person = owner("", "")
    assert cli(vault, credentials(False)).declined
    assert (text(vault), head(vault)) == (before, at)
    assert cli(vault, credentials(False)).changed
    assert bodies(vault, 1) == ["Config: a change\n\nconfirmed at the console\n"]
    assert hello.acts == ["turn credentials redaction off"] * 2
    assert not any("Type" in p for p in person.prompts)


def test_the_code_writes_and_commits_saying_it_was_confirmed(
    vault: Vault, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    person = owner("code")
    done = cli(vault, credentials(False), warnings=["think twice"])
    assert done.changed and not done.declined
    assert bodies(vault, 1) == ["Config: a change\n\nconfirmed at the console\n"]
    out = capsys.readouterr().out
    assert "this command changes vault.yaml:\n  redaction.credentials: true -> false\nnote     think twice\n" in out
    assert person.prompts == [person.prompts[0]]
    assert person.prompts[0].endswith("to make this change, n to leave vault.yaml as it is, q to stop: ")


def test_every_other_change_asks_nothing_and_its_commit_says_who(
    vault: Vault, owner: Callable[..., AtConsole], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A looser change is made on the owner's yes, given before the command: from Claude Code's shell it commits saying
    Claude made it with the owner's OK, from the owner's own terminal with nothing more (DR-0599, DR-0601)."""
    person = owner()
    monkeypatch.setenv("CLAUDECODE", "1")
    assert cli(vault, memo(sensitivity="mixed", tools=["get_memo"])).changed
    assert bodies(vault, 1) == [f"Config: a change{BY_CLAUDE}"]
    monkeypatch.delenv("CLAUDECODE")
    assert cli(vault, phone(True)).changed
    assert bodies(vault, 1) == ["Config: a change\n"]
    assert person.prompts == []


def test_a_file_changed_while_the_owner_reads_writes_nothing(vault: Vault, owner: Callable[..., AtConsole]) -> None:
    def edits_then_answers() -> str:
        (vault.root / CONFIG_FILE).write_text(text(vault) + "# mine\n", encoding="utf-8")
        return "code"

    owner(edits_then_answers)
    with pytest.raises(settings.StaleError, match="changed while you were asked"):
        cli(vault, credentials(False))


def test_a_commit_of_vault_yaml_while_asked_writes_nothing_and_of_another_file_doesn_t_stop_it(
    vault: Vault, owner: Callable[..., AtConsole]
) -> None:
    by_hand(vault, memo(sensitivity="private"))

    def commits_settings() -> str:
        git(vault.root, "commit", "-qm", "mine", "--", CONFIG_FILE)
        by_hand(vault, memo(sensitivity="private"))
        return "code"

    owner(commits_settings)
    with pytest.raises(settings.StaleError, match="changed while you were asked"):
        cli(vault, credentials(False))

    def commits_another_file() -> str:
        (vault.root / "mine.txt").write_text("mine\n", encoding="utf-8")
        git(vault.root, "add", "mine.txt")
        git(vault.root, "commit", "-qm", "a file of mine")
        return "code"

    owner(commits_another_file)
    assert cli(vault, credentials(False)).changed


def test_a_hand_edited_file_is_asked_about_and_edited_in_place(vault: Vault, owner: Callable[..., AtConsole]) -> None:
    path = vault.root / CONFIG_FILE
    path.write_text(text(vault) + "# my own note\n", encoding="utf-8")
    git(vault.root, "commit", "-qm", "a note of mine", "--", CONFIG_FILE)
    person = owner("code")
    assert cli(vault, credentials(False)).changed
    assert len(person.prompts) == 1
    assert text(vault).endswith("# my own note\n") and "credentials: false" in text(vault)


def test_a_change_docket_can_t_place_gets_its_line_and_no_code_but_is_still_refused_in_claude_code(
    vault: Vault, owner: Callable[..., AtConsole], monkeypatch: pytest.MonkeyPatch
) -> None:
    cant_place(monkeypatch)
    path = vault.root / CONFIG_FILE
    path.write_text(text(vault) + "# my own note\n", encoding="utf-8")
    git(vault.root, "commit", "-qm", "a note of mine", "--", CONFIG_FILE)
    real = console.problem
    person = owner()
    assert cli(vault, credentials(False)).by_hand
    assert person.prompts == []
    monkeypatch.setattr(console, "problem", real)
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(DocketError, match="CLAUDECODE is set"):
        cli(vault, credentials(False))


def test_nothing_answers_the_question_for_the_owner() -> None:
    assert list(inspect.signature(settings.rewrite).parameters) == [
        "vault",
        "edit",
        "message",
        "via",
        "shown",
        "warnings",
    ]


# -- an uncommitted edit -----------------------------------------------------------------------------------------------


def test_an_uncommitted_credentials_off_is_asked_about_before_any_command_and_goes_in_first(
    vault: Vault, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    by_hand(vault, lambda c: c.model_copy(update={"never_store": NeverStore(people=["Ben Sample", "Cleo Test"])}))
    by_hand(vault, memo(mapper=".claude/skills/sources/memo/mapper.py", tools=["list_memos", "get_memo"]))
    by_hand(vault, credentials(False))
    at = head(vault)
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(DocketError, match=settings.UNCOMMITTED_CREDENTIALS_OFF):
        cli(vault, phone(True))
    assert head(vault) == at
    monkeypatch.delenv("CLAUDECODE")
    owner("n")
    assert cli(vault, phone(True)).declined
    assert head(vault) == at
    out = capsys.readouterr().out
    assert "vault.yaml has an edit that isn't in force until you commit it; it goes in first:" in out
    assert "  never_store.people: 1 taken off, 1 added\n" in out
    # Every other change the edit holds is on the same screen, each as the file has it.
    assert "  sources.memo.mapper: none -> .claude/skills/sources/memo/mapper.py\n" in out
    assert "  sources.memo.tools: [] -> [list_memos, get_memo]\n" in out
    assert "  redaction.credentials: true -> false\n" in out
    assert "Dora" not in out and "Cleo" not in out
    assert "this command changes vault.yaml" not in out
    owner("code")
    done = cli(vault, phone(True))
    assert done.taken_in is not None and done.changed
    assert bodies(vault, 2) == ["Config: a change\n", "Config: vault.yaml as edited\n\nconfirmed at the console\n"]


def test_any_other_uncommitted_edit_goes_in_first_unasked_saying_who(
    vault: Vault, owner: Callable[..., AtConsole], monkeypatch: pytest.MonkeyPatch
) -> None:
    by_hand(vault, lambda c: c.model_copy(update={"never_store": NeverStore(people=["Ben Sample", "Cleo Test"])}))
    by_hand(vault, memo(tools=["get_memo"]))
    person = owner()
    monkeypatch.setenv("CLAUDECODE", "1")
    done = cli(vault, phone(True))
    assert done.taken_in is not None and done.changed
    assert bodies(vault, 2) == [f"Config: a change{BY_CLAUDE}", f"Config: vault.yaml as edited{BY_CLAUDE}"]
    assert person.prompts == []


def test_an_edit_and_credentials_off_take_one_code_and_two_commits(
    vault: Vault, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    by_hand(vault, memo(sensitivity="mixed"))
    by_hand(
        vault,
        lambda c: c.model_copy(
            update={"sources": {**c.sources, "calls": c.sources["calls"].model_copy(update={"sensitivity": "work"})}}
        ),
    )
    person = owner("code")
    assert cli(vault, credentials(False)).changed
    assert len(person.prompts) == 1
    out = capsys.readouterr().out
    assert "  redaction.credentials: true -> false\n" in out
    assert "sources.calls" not in out
    assert bodies(vault, 2) == ["Config: a change\n\nconfirmed at the console\n", "Config: vault.yaml as edited\n"]


def test_an_edit_that_only_keeps_more_out_goes_in_unasked(vault: Vault, owner: Callable[..., AtConsole]) -> None:
    by_hand(vault, memo(sensitivity="private"))
    person = owner()
    done = cli(vault, phone(True))
    assert done.taken_in is not None
    assert person.prompts == []


def test_a_head_without_a_readable_vault_yaml_is_refused_at_a_console_too(
    vault: Vault, owner: Callable[..., AtConsole]
) -> None:
    good = text(vault)
    (vault.root / CONFIG_FILE).write_text(good + "never_store: [\n", encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qm", "broken", "--", CONFIG_FILE)
    (vault.root / CONFIG_FILE).write_text(good, encoding="utf-8", newline="\n")
    owner("code")
    with pytest.raises(DocketError, match="isn't in the last commit"):
        settings.rewrite(vault, phone(True), "Config: a change", via="cli")


def test_redaction_set_shows_the_credentials_warning_before_the_code(
    vault: Vault, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    owner("n")
    assert main(["redaction", "set", "credentials", "off", "--vault", str(vault.root)]) == 1
    out = capsys.readouterr().out
    warning = out.index("note     from the next sync, passwords, keys and tokens are stored as written")
    assert warning < out.index("redact   credentials not changed: nothing was confirmed")


# -- each command -------------------------------------------------------------------------------------------------


def project_vault(tmp_path: Path) -> Vault:
    """A vault nested in a project, as setup makes one: `docket source new` writes the skill into the project."""
    found = consuming_project(tmp_path, {})
    path = found.root / CONFIG_FILE
    config = VaultConfig.load(path)
    config.redaction = Redaction(phone=True, patterns=[RedactionPattern(name="ticket-id", pattern=r"TCK-\d{6}")])
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    git(found.root, "commit", "-qm", "Settings", "--", CONFIG_FILE)
    (found.root.parent / "papers").mkdir()
    return Vault.open(found.root)


CHANGES = {
    "phone off": ["redaction", "set", "phone", "off"],
    "a pattern off": ["redaction", "set", "ticket-id", "off"],
    "a source added": ["source", "new", "diary"],
    "a folder source added": ["source", "new", "papers", "--folder", "papers"],
    "a media folder added": ["media", "folder", "add", "chat", "papers"],
    "the api provider allowed": ["models", "allow", "voyage"],
}


@pytest.mark.parametrize("what", sorted(CHANGES))
def test_each_other_command_commits_from_claude_code_s_shell_saying_so(
    tmp_path: Path, what: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = project_vault(tmp_path)
    at = head(vault)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.chdir(vault.root.parent)
    assert main([*CHANGES[what], "--vault", str(vault.root)]) == 0, capsys.readouterr()
    assert head(vault) != at
    assert bodies(vault, 1)[0].endswith(BY_CLAUDE)


def test_credentials_off_is_refused_in_claude_code_s_shell_writing_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = project_vault(tmp_path)
    before, at = text(vault), head(vault)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert main(["redaction", "set", "credentials", "off", "--vault", str(vault.root)]) == 2
    assert "this shell is Claude Code's (CLAUDECODE is set)" in capsys.readouterr().err
    assert (text(vault), head(vault)) == (before, at)


@pytest.mark.parametrize("what", sorted(CHANGES))
def test_a_no_to_an_uncommitted_credentials_off_leaves_each_command_undone(
    tmp_path: Path,
    what: str,
    owner: Callable[..., AtConsole],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vault = project_vault(tmp_path)
    by_hand(vault, credentials(False))
    before, at = text(vault), head(vault)
    monkeypatch.chdir(vault.root.parent)
    owner("n")
    main([*CHANGES[what], "--vault", str(vault.root)])
    assert "nothing was confirmed" in capsys.readouterr().out
    assert (text(vault), head(vault)) == (before, at)
    assert not (vault.root.parent / ".claude" / "skills" / "sources" / "diary").exists()


def test_source_new_after_a_no_writes_its_folder_again_on_a_yes(
    tmp_path: Path, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    vault = project_vault(tmp_path)
    by_hand(vault, credentials(False))
    folder = vault.root.parent / ".claude" / "skills" / "sources" / "diary"
    owner("n")
    assert main(["source", "new", "diary", "--vault", str(vault.root)]) == 1
    assert "source   diary not added: nothing was confirmed" in capsys.readouterr().out
    assert not folder.exists()
    owner("code")
    assert main(["source", "new", "diary", "--vault", str(vault.root)]) == 0
    assert "diary:" in text(vault)
    remove_tree(folder)
    person = owner()
    assert main(["source", "new", "diary", "--vault", str(vault.root)]) == 0
    assert person.prompts == []


def test_turning_credentials_off_during_a_conflict_refuses_before_any_question(
    vault: Vault, owner: Callable[..., AtConsole]
) -> None:
    """An unmerged path, as a stash pop that conflicts leaves one: the write refuses before the owner is asked
    (DR-0528)."""
    blob = sh(vault.root, "git", "hash-object", "-w", "--", CONFIG_FILE).strip()
    info = "".join(f"100644 {blob} {n}\tconflict.txt\n" for n in (1, 2, 3))
    proc = subprocess.run(
        ["git", "update-index", "--index-info"], cwd=vault.root, input=info.encode("utf-8"), check=False
    )
    assert proc.returncode == 0
    before, at = text(vault), head(vault)
    person = owner("code")
    with pytest.raises(DocketError, match=r"conflict.txt in .* has a conflict; resolve it before Docket writes"):
        cli(vault, credentials(False))
    assert person.prompts == []
    assert (text(vault), head(vault)) == (before, at)


# -- the screen, entry by entry ---------------------------------------------------------------------------------------


class LaterNeverStore(NeverStore):
    domains: list[str] = Field(default_factory=list[str])


class Later(VaultConfig):
    never_store: LaterNeverStore = Field(default_factory=LaterNeverStore)  # pyright: ignore[reportIncompatibleVariableOverride]


def test_the_screen_names_each_entry_added_or_removed_and_never_a_never_store_value() -> None:
    before = make_config()
    before.sources["notes"] = SourceConfig()
    before.redaction = Redaction(
        patterns=[RedactionPattern(name="ticket", pattern="TCK"), RedactionPattern(name="x", pattern="X")]
    )
    after = Later.model_validate(
        {
            **before.model_dump(),
            "sources": {k: v for k, v in before.model_dump()["sources"].items() if k != "notes"},
            "redaction": {"patterns": [{"name": "ticket", "pattern": "TCK"}]},
            "never_store": {"domains": ["dora.invalid"]},
        }
    )
    assert settings.changes(before, after) == [
        "sources.notes: removed",
        "never_store.domains: added",
        "  never_store.domains: 1 added",
        "redaction.patterns[x]: removed",
    ]
    back = settings.changes(after, before)
    assert "redaction.patterns[x]: added" in back
    assert "  redaction.patterns[x].name: x" in back


def test_head_s_vault_yaml_gone_while_the_owner_reads_writes_nothing(
    vault: Vault, owner: Callable[..., AtConsole]
) -> None:
    def takes_it_out_of_head() -> str:
        git(vault.root, "rm", "-q", "--cached", CONFIG_FILE)
        git(vault.root, "commit", "-qm", "no settings")
        return "code"

    owner(takes_it_out_of_head)
    with pytest.raises(settings.StaleError, match="changed while you were asked"):
        cli(vault, credentials(False))


def test_the_screen_matches_patterns_by_name_so_a_reorder_says_nothing() -> None:
    """The screen pairs patterns as the settings in force do (DR-0521): by name, not by place."""
    tck, acct = RedactionPattern(name="tck", pattern="TCK"), RedactionPattern(name="acct", pattern=r"ACCT-\d+")
    before = make_config()
    before.redaction = Redaction(patterns=[tck, acct])
    after = before.model_copy(
        update={"redaction": Redaction(patterns=[acct, tck.model_copy(update={"enabled": False})])}
    )
    assert settings.changes(before, after) == ["redaction.patterns[tck].enabled: true -> false"]
    twice = before.model_copy(update={"redaction": Redaction(patterns=[tck, acct, tck])})
    assert settings.changes(before, twice) == [
        "redaction.patterns[tck 2]: added",
        "  redaction.patterns[tck 2].name: tck",
        "  redaction.patterns[tck 2].pattern: TCK",
        "  redaction.patterns[tck 2].enabled: true",
    ]


def test_already_so_by_a_hand_edit_not_in_force_says_it_waits(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    """The file holds the value by hand, HEAD doesn't: nothing to write, and the line says the edit waits for its
    commit, as the gate's refusals do."""
    on = VaultConfig.load(vault.root / CONFIG_FILE)
    on.redaction.phone = True
    (vault.root / CONFIG_FILE).write_text(on.dump(), encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qm", "phone on", "--", CONFIG_FILE)
    by_hand(vault, phone(False))
    assert main(["redaction", "set", "phone", "off", "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "redact   phone is off already; vault.yaml has an edit that isn't in force until you commit it" in out
    by_hand(vault, phone(True))
    assert main(["redaction", "set", "phone", "on", "--vault", str(vault.root)]) == 0
    assert capsys.readouterr().out == "redact   phone is on already\n"


def test_a_source_registered_as_it_stands_by_a_hand_edit_says_it_waits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = project_vault(tmp_path)
    mapper = ".claude/skills/sources/diary/mapper.py"
    by_hand(
        vault,
        lambda c: c.model_copy(
            update={"sources": {**c.sources, "diary": SourceConfig(sensitivity="private", mapper=mapper)}}
        ),
    )
    at = head(vault)
    assert main(["source", "new", "diary", "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "source   diary in vault.yaml: private, mapper " in out
    assert "tools none yet; vault.yaml has an edit that isn't in force until you commit it" in out
    assert head(vault) == at
