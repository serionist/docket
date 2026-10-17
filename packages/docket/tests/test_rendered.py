"""The people directory and the glossary rendered from the entities into the repo around the vault (#163, DR-0552 to
DR-0554): sorted and stable, a hand edit left as it is, a public repo refused, never-store values left out, rendered
after each curate write and by `docket render`, and the `rendered` key in force only once committed. Invented people
on a folder vault of the test kit's."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from docket_testkit import entity_vault, folder_vault, git

from docket.cli import main
from docket.errors import DocketError
from docket.vault import curatelog, host, rendered
from docket.vault.config import CONFIG_FILE, NeverStore, RenderedFile, VaultConfig
from docket.vault.entities import load_entities
from docket.vault.entitystore import EntityStore, load_changes
from docket.vault.host import Probe
from docket.vault.inforce import loosens
from docket.vault.rendered import people, render_all, written_by_docket
from docket.vault.vault import Vault

CHANGES = """\
changes:
  - create:
      id: ben-sample
      type: person
      name: {en: Ben Sample, hu: Sample Ben}
      role: site lead | north
      ids: {slack: U0BEN, email: ben@example.invalid}
      aliases: [{text: Benny}, {text: Ben Sampel, kind: asr}]
  - create: {id: ada-example, type: person, name: {en: Ada Example}}
  - create:
      id: northwind
      type: customer
      name: {en: Northwind, hu: Északi Szél}
      aliases: [{text: Nortwin, kind: asr}]
  - create: {id: fleet, type: project, name: {en: Fleet management}}
"""
PEOPLE = """\
# People

| Name (en) | Name (hu) | Role | Chat | Mail | Aliases |
|---|---|---|---|---|---|
| Ada Example |  |  |  |  |  |
| Ben Sample | Sample Ben | site lead \\| north | slack: U0BEN | ben@example.invalid | Benny, Ben Sampel (asr) |
"""
GLOSSARY = """\
# Glossary

| Term | Type | Other names | Spellings |
|---|---|---|---|
| Northwind | customer | Északi Szél | Nortwin (asr) |
| Fleet management | project |  |  |
"""


def top(vault: Vault) -> Path:
    return vault.home.top


def set_rendered(vault: Vault, *files: RenderedFile, commit: bool = True) -> Vault:
    """vault.yaml with these files under rendered, committed in the host by hand unless told not to."""
    path = vault.root / CONFIG_FILE
    config = VaultConfig.load(path).model_copy(update={"rendered": list(files)})
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    if commit:
        git(top(vault), "commit", "-qm", "Render the people", "--", f"{vault.home.prefix}{CONFIG_FILE}")
    return Vault.open(vault.root)


@pytest.fixture(autouse=True)
def fresh_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts as a new process would: no remote's answer kept."""
    monkeypatch.setattr(rendered, "_PROBED", {})


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    made = set_rendered(
        folder_vault(tmp_path),
        RenderedFile(kind="people", path="people.md"),
        RenderedFile(kind="glossary", path="glossary.md"),
    )
    changes = tmp_path / "changes.yaml"
    changes.write_text(CHANGES, encoding="utf-8", newline="\n")
    EntityStore(made).apply(load_changes(changes))
    return made


def body(path: Path) -> str:
    return path.read_text(encoding="utf-8").partition("\n")[2]


def test_a_curate_write_renders_both_files_sorted_and_marked(vault: Vault) -> None:
    people, glossary = top(vault) / "people.md", top(vault) / "glossary.md"
    assert body(people) == PEOPLE
    assert body(glossary) == GLOSSARY
    commit = git(vault.root, "log", "-1", "--format=%h", "--abbrev=12").strip()
    first = people.read_text(encoding="utf-8").partition("\n")[0]
    assert first.startswith(f"<!-- Rendered by Docket from the vault's entities at {commit}; a hand edit is reported")
    assert written_by_docket(people.read_text(encoding="utf-8"))
    # Written, never committed: the host's index holds neither file.
    assert git(top(vault), "status", "--porcelain", "--", "people.md", "glossary.md").split() == [
        "??", "glossary.md", "??", "people.md",
    ]  # fmt: skip


def test_an_unchanged_entity_set_rewrites_nothing(vault: Vault) -> None:
    people = top(vault) / "people.md"
    before = people.stat().st_mtime_ns, people.read_bytes()
    assert [o.state for o in render_all(vault)] == ["unchanged", "unchanged"]
    assert (people.stat().st_mtime_ns, people.read_bytes()) == before


def test_a_hand_edit_is_left_as_it_is_and_reported(vault: Vault, tmp_path: Path) -> None:
    people = top(vault) / "people.md"
    edited = people.read_text(encoding="utf-8") + "| Cleo Test | | | | | |\n"
    people.write_text(edited, encoding="utf-8")
    more = tmp_path / "more.yaml"
    more.write_text("changes:\n  - create: {id: cleo-test, type: person, name: {en: Cleo Test}}\n", encoding="utf-8")
    written = EntityStore(vault).apply(load_changes(more))
    assert any(line.startswith("left       people.md: it was edited by hand") for line in written.rendered)
    # The glossary's first line names the new commit, so it is written again; the people file is left.
    assert "rendered   glossary.md" in written.rendered
    assert people.read_text(encoding="utf-8") == edited


def test_a_hand_kept_file_at_the_path_is_never_overwritten(tmp_path: Path) -> None:
    made = folder_vault(tmp_path)
    kept = top(made) / "people.md"
    kept.write_text("# People\n\nKept by hand.\n", encoding="utf-8")
    made = set_rendered(made, RenderedFile(kind="people", path="people.md"))
    assert [(o.state, o.path) for o in render_all(made)] == [("edited", "people.md")]
    assert kept.read_text(encoding="utf-8") == "# People\n\nKept by hand.\n"


def test_a_repo_anyone_can_read_gets_no_file(tmp_path: Path) -> None:
    made = set_rendered(folder_vault(tmp_path), RenderedFile(kind="people", path="people.md"))
    [outcome] = render_all(made, probe=lambda url: "public")
    assert outcome.state == "refused"
    assert outcome.why.endswith("can be read without signing in; Docket renders nothing into a public repo")
    assert not (top(made) / "people.md").exists()


def test_a_path_out_of_the_repo_or_into_the_vault_is_refused(tmp_path: Path) -> None:
    made = folder_vault(tmp_path)
    inside = f"{made.home.prefix}people.md"
    made = set_rendered(
        made, RenderedFile(kind="people", path=inside), RenderedFile(kind="glossary", path="no/such.md")
    )
    states = [(o.state, o.why.split(",")[0]) for o in render_all(made)]
    assert states == [
        ("refused", f"it must stay in {top(made).resolve()} and outside the vault"),
        ("refused", f"its folder {top(made).resolve() / 'no'} doesn't exist"),
    ]


def test_an_undone_create_drops_its_row(vault: Vault) -> None:
    ada = next(line for line in curatelog.read_all(vault.root).lines if line.entity == "ada-example")
    written = EntityStore(vault).undo([ada.id])
    assert written.rendered == ("rendered   people.md", "rendered   glossary.md")
    assert "Ada Example" not in (top(vault) / "people.md").read_text(encoding="utf-8")


def test_a_never_store_value_is_left_out(vault: Vault) -> None:
    path = vault.root / CONFIG_FILE
    config = VaultConfig.load(path)
    lists = config.never_store.model_copy(update={"people": ["Ada Example"], "keywords": ["Sampel"]})
    # Stricter, so in force at once, uncommitted.
    path.write_text(config.model_copy(update={"never_store": lists}).dump(), encoding="utf-8", newline="\n")
    opened = Vault.open(vault.root)
    assert [o.state for o in render_all(opened)] == ["written", "unchanged"]
    text = (top(vault) / "people.md").read_text(encoding="utf-8")
    assert "Ada Example" not in text
    assert "Ben Sampel" not in text
    assert "| Ben Sample | Sample Ben |" in text
    assert "Benny |" in text


def test_the_key_is_a_later_key_in_force_only_once_committed(tmp_path: Path) -> None:
    made = folder_vault(tmp_path)
    before = made.config
    after = before.model_copy(update={"rendered": [RenderedFile(kind="people", path="people.md")]})
    assert loosens(before, after)
    assert not loosens(after, before.model_copy(update={"rendered": after.rendered}))
    waiting = set_rendered(made, *after.rendered, commit=False)
    assert waiting.config.rendered == []
    assert waiting.waiting
    assert render_all(waiting) == []
    # Without the key, vault.yaml is written as before: an older Docket still reads it.
    assert "rendered" not in before.dump()


@pytest.mark.parametrize(
    "path", ["../people.md", "/people.md", "C:/people.md", "docs\\people.md", "people.txt", "a/.md"]
)
def test_a_rendered_path_must_be_a_md_file_inside_the_repo(path: str) -> None:
    with pytest.raises(ValueError, match="rendered file"):
        RenderedFile(kind="people", path=path)


def test_a_render_that_fails_never_fails_the_curate_write(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def broken(_vault: Vault) -> list[rendered.Outcome]:
        raise DocketError("the disk is full")

    monkeypatch.setattr(rendered, "render_all", broken)
    more = tmp_path / "more.yaml"
    more.write_text("changes:\n  - create: {id: cleo-test, type: person, name: {en: Cleo Test}}\n", encoding="utf-8")
    written = EntityStore(vault).apply(load_changes(more))
    assert written.commit is not None
    assert written.rendered == ("render failed: the disk is full",)


def test_docket_render_prints_each_file_and_fails_on_one_left(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["render", "--vault", str(vault.root)]) == 0
    assert capsys.readouterr().out.splitlines() == ["unchanged  people.md", "unchanged  glossary.md"]
    people = top(vault) / "people.md"
    people.write_text("edited\n", encoding="utf-8")
    (vault.root / "entities" / "person" / "ada-example.yaml").unlink()
    assert main(["render", "--vault", str(vault.root)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("left       people.md")
    assert out[1] == "unchanged  glossary.md"


def test_a_vault_in_no_repo_and_a_path_named_twice_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    alone = entity_vault(tmp_path)
    assert main(["render", "--vault", str(alone.root)]) == 0
    assert capsys.readouterr().out.splitlines() == ["nothing to render: vault.yaml names no file under rendered"]
    path = alone.root / CONFIG_FILE
    config = VaultConfig.load(path).model_copy(update={"rendered": [RenderedFile(kind="people", path="people.md")]})
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    git(alone.root, "commit", "-qm", "Render the people", "--", CONFIG_FILE)
    assert main(["render", "--vault", str(alone.root)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        f"refused    people.md: {alone.root} isn't inside a git repo, so there is no repo to render into"
    ]
    twice = [{"kind": "people", "path": "people.md"}, {"kind": "glossary", "path": "people.md"}]
    with pytest.raises(ValueError, match=r"people.md is rendered twice"):
        VaultConfig.model_validate({**config.model_dump(mode="json"), "rendered": twice})


# -- the review of #168: M1 to M3 --------------------------------------------------------------------------------------


def test_a_file_that_isnt_utf8_is_hand_kept_and_never_fails_the_curate_write(tmp_path: Path) -> None:
    made = folder_vault(tmp_path)
    kept = top(made) / "people.md"
    kept.write_bytes("# People\r\n\r\nKept by hand in Notepad.\r\n".encode("utf-16"))
    made = set_rendered(made, RenderedFile(kind="people", path="people.md"))
    changes = tmp_path / "changes.yaml"
    changes.write_text(CHANGES, encoding="utf-8", newline="\n")
    written = EntityStore(made).apply(load_changes(changes))
    assert written.commit is not None
    assert [line.split(":")[0] for line in written.rendered] == ["left       people.md"]
    assert kept.read_bytes().startswith(b"\xff\xfe")


def test_a_folder_at_the_path_is_refused(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    (top(vault) / "dir.md").mkdir()
    made = set_rendered(vault, RenderedFile(kind="people", path="dir.md"))
    assert main(["render", "--vault", str(made.root)]) == 1
    assert capsys.readouterr().out.splitlines() == ["refused    dir.md: dir.md is a folder"]


def test_any_other_failure_is_a_line_never_raised(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_vault: Vault) -> list[rendered.Outcome]:
        raise RuntimeError("a value never shown")

    monkeypatch.setattr(rendered, "render_all", broken)
    assert rendered.safely(vault) == ["render failed (RuntimeError)"]


@pytest.mark.parametrize("listed_as", ["ben@example.invalid", "Benny", "Ben Sample <ben@example.invalid>"])
def test_a_person_the_people_list_names_by_any_value_is_left_out_whole(vault: Vault, listed_as: str) -> None:
    active = [e for e in load_entities(vault.root).entities if e.status == "active"]
    text = people(active, ["en", "hu"], NeverStore(people=[listed_as]))
    assert "Sample" not in text
    assert "| Ada Example |" in text


def test_a_keyword_drops_only_the_value_it_is_in(vault: Vault) -> None:
    active = [e for e in load_entities(vault.root).entities if e.status == "active"]
    text = people(active, ["en", "hu"], NeverStore(keywords=["Benny"]))
    assert "| Ben Sample | Sample Ben |" in text
    assert "Benny" not in text


def test_a_public_remote_is_reported_even_when_nothing_changes(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    # A new process: the fixture's curate write asked the remote already.
    monkeypatch.setattr(rendered, "_PROBED", {})
    asked: list[str] = []

    def public(url: str) -> Probe:
        asked.append(url)
        return "public"

    outcomes = render_all(vault, probe=public)
    assert [o.state for o in outcomes] == ["refused", "refused"]
    assert outcomes[0].why.endswith("can be read without signing in; Docket renders nothing into a public repo")
    render_all(vault, probe=public)
    # Asked once in the process, for the one remote the host has.
    assert len(asked) == 1


def test_a_remote_that_couldn_t_be_checked_keeps_a_file_unwritten_this_time(tmp_path: Path) -> None:
    made = set_rendered(folder_vault(tmp_path), RenderedFile(kind="people", path="people.md"))
    asked: list[str] = []

    def offline(url: str) -> Probe:
        asked.append(url)
        return "unreachable"

    [outcome] = render_all(made, probe=offline)
    assert (outcome.state, outcome.why.endswith("; not written")) == ("refused", True)
    assert outcome.why.startswith("couldn't check the remote ")
    assert not (top(made) / "people.md").exists()
    render_all(made, probe=offline)
    assert len(asked) == 2
    assert [o.state for o in render_all(made, probe=lambda url: "sign-in")] == ["written"]


@pytest.mark.parametrize(
    ("stderr", "found"),
    [
        ("remote: Repository not found.\nfatal: repository 'https://example.invalid/a/b/' not found", "sign-in"),
        ("fatal: could not read Username for 'https://example.invalid': terminal prompts disabled", "sign-in"),
        ("fatal: Authentication failed for 'https://example.invalid/a/b/'", "sign-in"),
        (
            "fatal: unable to access 'https://example.invalid/a/b/': Could not resolve host: example.invalid",
            "unreachable",
        ),
        (
            "fatal: unable to access 'https://example.invalid/a/b/': Failed to connect to example.invalid port 443",
            "unreachable",
        ),
    ],
)
def test_the_probe_tells_a_sign_in_from_a_server_not_reached(
    monkeypatch: pytest.MonkeyPatch, stderr: str, found: str
) -> None:
    def answer(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 128, "", stderr)

    monkeypatch.setattr(host.subprocess, "run", answer)
    assert host.probe_remote("https://example.invalid/a/b.git") == found
    assert host.remote_visibility("https://example.invalid/a/b.git") == "unknown"


def test_a_probe_that_times_out_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("git", 30)

    monkeypatch.setattr(host.subprocess, "run", slow)
    assert host.probe_remote("https://example.invalid/a/b.git") == "unreachable"
