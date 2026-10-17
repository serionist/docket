"""The door's scan over the whole package (DR-0506), as test_release.py scans for the release's writers: vault.yaml is
written in one place, its general entry is called by the CLI's two writers alone, those writers by the command line
alone, the web and the service import from vault.settings only what pages may use, and write vault.yaml no other
way: no settings dumped whole, no file write in code that names the file."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from docket.vault import settings

SRC = Path(settings.__file__).resolve().parents[1]


def _modules() -> list[tuple[str, ast.Module]]:
    return [
        (p.relative_to(SRC).as_posix(), ast.parse(p.read_text(encoding="utf-8"))) for p in sorted(SRC.rglob("*.py"))
    ]


def _called(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _writes(node: ast.AST) -> bool:
    """A file written here: `write_text`, `write_bytes`, or `open` with a mode that writes, builtin or a path's."""
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        name = _called(call)
        if name in ("write_text", "write_bytes"):
            return True
        if name == "open":
            at = 0 if isinstance(call.func, ast.Attribute) else 1
            modes = [call.args[at]] if len(call.args) > at else []
            modes += [k.value for k in call.keywords if k.arg == "mode"]
            if any(
                isinstance(m, ast.Constant) and isinstance(m.value, str) and set(m.value) & set("wax+") for m in modes
            ):
                return True
    return False


def _names_the_file(node: ast.AST) -> bool:
    """Code that names vault.yaml: `CONFIG_FILE`, or the name itself as a path's part."""
    for found in ast.walk(node):
        if isinstance(found, ast.Name) and found.id == "CONFIG_FILE":
            return True
        if isinstance(found, ast.Attribute) and found.attr == "CONFIG_FILE":
            return True
        if isinstance(found, ast.Constant) and found.value == "vault.yaml":
            return True
    return False


def _direct_writes(src: Path) -> list[str]:
    """In web/ and service/: each settings object dumped whole (`.dump()` with no argument), and each function, or a
    module's own statements, that both names vault.yaml and writes a file."""
    found: list[str] = []
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src).as_posix()
        if not rel.startswith(("web/", "service/")):
            continue
        module = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and _called(node) == "dump" and not node.args and not node.keywords:
                found.append(f"{rel}:{node.lineno}: a .dump() of the settings")
        defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        units: list[tuple[str, ast.AST]] = [
            (rel, ast.Module(body=[s for s in module.body if not isinstance(s, defs)], type_ignores=[]))
        ]
        units += [
            (f"{rel}:{n.lineno}", n) for n in ast.walk(module) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        found += [
            f"{where}: writes a file and names vault.yaml"
            for where, unit in units
            if _names_the_file(unit) and _writes(unit)
        ]
    return found


def _calls(name: str) -> set[tuple[str, str]]:
    """Where a function of this name is called: each file and the top-level function or class around the call."""
    found: set[tuple[str, str]] = set()
    for rel, module in _modules():
        for top in module.body:
            where = top.name if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else ""
            for node in ast.walk(top):
                if isinstance(node, ast.Call) and _called(node) == name:
                    found.add((rel, where))
    return found


def test_vault_yaml_is_written_only_by_its_one_writer() -> None:
    assert _calls("write_config") == {("vault/settings.py", "_write")}
    assert {c for c in _calls("_write") if c[0] == "vault/settings.py"} == {("vault/settings.py", "rewrite")}


def test_the_writer_s_general_entry_is_called_only_by_the_cli_s_two_writers_and_the_stricter_operations() -> None:
    assert _calls("rewrite") == {
        ("vault/redaction.py", "set_rule"),
        ("sources/scaffold.py", "_register"),
        ("models/api.py", "allow"),
        ("vault/settings.py", "add_listed"),
        ("vault/settings.py", "rule_on"),
        ("vault/settings.py", "stricter"),
        ("vault/settings.py", "turn_off"),
        ("media/sources.py", "set_media_list"),
    }


def test_the_writers_that_can_loosen_are_called_only_by_the_command_line() -> None:
    for name in ("set_rule", "new_source", "new_folder_source", "set_media_list"):
        assert {rel for rel, _ in _calls(name)} == {"cli.py"}, name
    # `docket models allow`: the api provider's opt-in (DR-0537), whose every change loosens.
    assert _attribute_calls("api_models", "allow") == {("cli.py", "models_allow")}


def test_pages_and_the_service_import_only_what_pages_may_use() -> None:
    for rel, module in _modules():
        if not rel.startswith(("web/", "service/")):
            continue
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                names = {a.name for a in node.names}
                if node.module == "docket.vault.settings":
                    assert names <= settings.FOR_PAGES, (rel, names - settings.FOR_PAGES)
                assert not (node.module == "docket.vault" and "settings" in names), rel
                assert not (node.module == "docket.sources" and "scaffold" in names), rel
                assert node.module != "docket.sources.scaffold", rel
                assert node.module != "docket.media.sources", rel
                assert not (node.module == "docket.media" and "sources" in names), rel
                assert not (node.module == "docket.vault.redaction" and "set_rule" in names), rel
                assert not (node.module == "docket.vault.config" and "write_config" in names), rel
            if isinstance(node, ast.Import):
                imported = {a.name for a in node.names}
                assert not imported & {"docket.vault.settings", "docket.sources.scaffold"}, rel


def test_pages_and_the_service_write_vault_yaml_no_other_way() -> None:
    assert _direct_writes(SRC) == []


SNEAKY = {
    "both": """
from docket.vault.config import CONFIG_FILE, VaultConfig


def credentials_off(vault):
    path = vault.root / CONFIG_FILE
    config = VaultConfig.load(path)
    looser = config.model_copy(update={"redaction": config.redaction.model_copy(update={"credentials": False})})
    path.write_text(looser.dump(), encoding="utf-8", newline="\\n")
""",
    "a write, the text built elsewhere": """
import yaml


def phone_off(vault, data):
    with (vault.root / "vault.yaml").open("w", encoding="utf-8") as out:
        out.write(yaml.safe_dump(data))
""",
    "a dump handed on": """
def credentials_off(config, put):
    put(config.model_copy(update={"redaction": {"credentials": False}}).dump())
""",
    "at the module's top": """
from pathlib import Path

from docket.vault.config import CONFIG_FILE

Path(CONFIG_FILE).write_bytes(b"redaction: {credentials: false}")
""",
}


@pytest.mark.parametrize("how", sorted(SNEAKY))
def test_a_page_that_writes_vault_yaml_around_the_writer_is_caught(how: str, tmp_path: Path) -> None:
    """The reviewer's probe: a module of the web that writes vault.yaml without write_config or rewrite, as Vault.init
    and the test kit write it, passes every other row; this one catches it."""
    sneaky = tmp_path / "docket" / "web" / "sneaky.py"
    sneaky.parent.mkdir(parents=True)
    sneaky.write_text(SNEAKY[how], encoding="utf-8")
    assert _direct_writes(tmp_path / "docket"), how


def test_a_file_written_elsewhere_or_named_in_words_isn_t_caught(tmp_path: Path) -> None:
    calm = tmp_path / "docket" / "service" / "calm.py"
    calm.parent.mkdir(parents=True)
    calm.write_text(
        '''
def history(path, line):
    """Appends a line; the vault's settings live in vault.yaml, which this never touches."""
    with path.open("a", encoding="utf-8") as out:
        out.write(line)


def read(vault, CONFIG_FILE):
    return (vault.root / CONFIG_FILE).read_text(encoding="utf-8")
''',
        encoding="utf-8",
    )
    assert _direct_writes(tmp_path / "docket") == []


def test_what_pages_may_import_is_there_and_never_the_general_entry() -> None:
    assert "rewrite" not in settings.FOR_PAGES
    assert all(hasattr(settings, name) for name in settings.FOR_PAGES)


def test_outside_the_writer_only_init_writes_vault_yaml_as_a_file() -> None:
    """A new vault's first vault.yaml is Vault.init's; every later change is rewrite's, through write_config, so a
    command added later can't write the file around the console's question (DR-0517)."""
    found = {
        (rel, node.name)
        for rel, module in _modules()
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _names_the_file(node) and _writes(node)
    }
    assert found == {("vault/vault.py", "init")}


def _importers(name: str) -> set[str]:
    """The package's files that import `docket.<name>`, in any form."""
    importers = set()
    for rel, module in _modules():
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom) and node.module == "docket" and any(a.name == name for a in node.names):
                importers.add(rel)
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(f"docket.{name}"):
                importers.add(rel)
            if isinstance(node, ast.Import) and any(a.name.startswith(f"docket.{name}") for a in node.names):
                importers.add(rel)
    return importers


def test_only_the_release_and_the_writer_ask_at_the_console() -> None:
    """No page, route or tool asks the owner's question or answers it (DR-0518), and media keys leave the clone only
    from the console's command (DR-0550). The CLI reads only which yes this machine asks, for docket vault status."""
    assert _importers("console") == {
        "sources/release.py",
        "vault/settings.py",
        "models/api.py",
        "media/backup.py",
        "media/confirm.py",
        "cli.py",
    }
    from_cli = {node.func.attr for rel, module in _modules() if rel == "cli.py" for node in ast.walk(module)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "console"}  # fmt: skip
    assert from_cli == {"which_yes"}
    # Only the console asks the OS prompt, so no page, route or tool asks it or answers it (DR-0572).
    assert {rel for rel in _importers("osprompt") if not rel.startswith("osprompt/")} == {"console.py"}
    exporters = {
        rel
        for rel, module in _modules()
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom)
        and (
            node.module == "docket.media.backup"
            or (node.module == "docket.media" and any(a.name == "backup" for a in node.names))
        )
    }
    assert exporters == {"cli.py"}
    # The api provider's machine choice and key only refuse outside the owner's console (DR-0539): no code is asked.
    asked = {node.func.attr for rel, module in _modules() if rel == "models/api.py" for node in ast.walk(module)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and isinstance(node.func.value, ast.Name) and node.func.value.id == "console"}  # fmt: skip
    assert asked == {"problem"}


def _attribute_calls(owner: str, name: str) -> set[tuple[str, str]]:
    """Where `owner.name(...)` is called: each file and the top-level function or class around the call."""
    found: set[tuple[str, str]] = set()
    for rel, module in _modules():
        for top in module.body:
            where = top.name if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else ""
            for node in ast.walk(top):
                func = node.func if isinstance(node, ast.Call) else None
                if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
                    continue
                if func.attr == name and func.value.id == owner:
                    found.add((rel, where))
    return found


def test_the_vault_and_the_gate_read_the_settings_in_force_through_one_loader() -> None:
    """What the jobs obey comes from inforce.load alone (DR-0521): Vault.open's config, and the gate's on every call."""
    # And the API provider's screen (DR-0538): the same settings in force, read once per search or batch with no git
    # call while HEAD stays, where opening the whole vault five times a search cost a git call each on a folder vault.
    assert _attribute_calls("inforce", "load") == {
        ("vault/vault.py", "Vault"),
        ("vault/gate.py", "Gate"),
        ("index/screen.py", "Screen"),
    }
    assert {c for c in _calls("effective") if c[0] != "vault/inforce.py"} == set()
