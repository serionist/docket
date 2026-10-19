from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from devkit import cli, gate, web
from devkit.repo import KitError

REAL_ROOT = Path(__file__).resolve().parents[3]


def put(root: Path, rel: str, data: bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def site(repo: Path, git: Callable[..., str]) -> Path:
    """A repo with a web folder: its inputs list, a source with CRLF line endings in the tree, and the stamp."""
    git(repo, "config", "core.autocrlf", "false")
    put(repo, web.LIST, b"# the inputs\nsrc\n:(exclude,glob)src/**/*.test.ts\n\nindex.html\n")
    put(repo, f"{web.WEB}/src/main.ts", b"one\r\ntwo\n")
    put(repo, f"{web.WEB}/src/main.test.ts", b"left out\n")
    put(repo, f"{web.WEB}/index.html", b"<!doctype html>\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Web")
    put(repo, f"{web.WEB}/src/untracked.ts", b"not yet added\n")
    return repo


def expected(*files: tuple[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, data in files:
        digest.update(f"{name}\n{len(data)}\n".encode())
        digest.update(data)
    return digest.hexdigest()


def test_the_hash_reads_the_listed_tracked_files_in_order_with_crlf_made_lf(site: Path) -> None:
    assert web.input_files(site) == ["index.html", "src/main.ts"]
    assert web.inputs_hash(site) == expected(("index.html", b"<!doctype html>\n"), ("src/main.ts", b"one\ntwo\n"))


def test_the_stamp_is_held_to_the_sources(site: Path) -> None:
    assert web.stamp_problem(site) == f"{web.STAMP} is missing: {web.REBUILD}"
    stamp = put(site, web.STAMP, b"not json")
    assert web.stamp_problem(site) == f"{web.STAMP} doesn't read: {web.REBUILD}"
    stamp.write_text(json.dumps({"inputs": web.inputs_hash(site)}), encoding="utf-8")
    assert web.stamp_problem(site) is None
    put(site, f"{web.WEB}/src/main.ts", b"one\ntwo\nthree\n")
    assert web.stamp_problem(site) == f"the web app's sources changed since its build: {web.REBUILD}"


def test_the_document_is_held_to_the_routes(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "schema_text", lambda _root: '{"paths": {}}\n')
    assert web.schema_problem(repo) is not None
    web.write_schema(repo)
    assert (repo / web.SCHEMA).read_bytes() == b'{"paths": {}}\n'
    assert web.schema_problem(repo) is None


def test_the_committed_build_and_document_match_this_repo_s_sources() -> None:
    # The Python half of the hash agrees with the Node half that wrote build.json, on the real files.
    assert web.stamp_problem(REAL_ROOT) is None
    assert web.schema_problem(REAL_ROOT) is None


def test_a_document_that_doesn_t_build_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "SCHEMA_CODE", "import sys; sys.exit('the routes failed to import')")
    with pytest.raises(KitError, match="didn't build: the routes failed to import"):
        web.schema_text(tmp_path)


def test_the_gate_s_web_step_names_each_problem(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    ctx = gate.Ctx(root=repo, lane="quick", ci=False, base=None, pr=None)
    monkeypatch.setattr(web, "stamp_problem", lambda _root: "the build is stale")
    monkeypatch.setattr(web, "schema_problem", lambda _root: None)
    out = gate.step_web(ctx)
    assert out.status == "fail" and out.detail == ["the build is stale"]
    monkeypatch.setattr(web, "stamp_problem", lambda _root: None)
    assert gate.step_web(ctx).status == "ok"


def test_the_command_writes_the_document_or_says_whether_it_is_current(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(web, "schema_text", lambda _root: '{"paths": {}}\n')
    assert cli.main(["web-schema", "--check"]) == 1
    assert "isn't what the /api/web routes say" in capsys.readouterr().err
    assert cli.main(["web-schema"]) == 0
    assert cli.main(["web-schema", "--check"]) == 0
    assert f"{web.SCHEMA} is current" in capsys.readouterr().out


def test_a_git_hook_s_variables_change_nothing(site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A pre-push hook runs the gate with GIT_DIR set, which would make git take the web folder for the work tree.
    before = web.inputs_hash(site)
    monkeypatch.setenv("GIT_DIR", str(site / ".git"))
    assert web.input_files(site) == ["index.html", "src/main.ts"]
    assert web.inputs_hash(site) == before


def test_a_folder_git_can_t_read_says_so(tmp_path: Path) -> None:
    put(tmp_path, web.LIST, b"src\n")
    with pytest.raises(KitError, match="git ls-files"):
        web.input_files(tmp_path)


def test_the_real_list_moves_the_hash_on_a_tsconfig_option_and_not_on_a_test(
    repo: Path, git: Callable[..., str]
) -> None:
    # This repo's build-inputs.txt over a web folder of its shape: what the build reads moves the hash, a test doesn't.
    git(repo, "config", "core.autocrlf", "false")
    put(repo, web.LIST, (REAL_ROOT / web.LIST).read_bytes())
    for name in ("tsconfig.json", "tsconfig.app.json", "src/main.tsx", "src/lib/keys.test.tsx", "src/test/setup.ts"):
        put(repo, f"{web.WEB}/{name}", b"{}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Web")
    before = web.inputs_hash(repo)
    put(repo, f"{web.WEB}/src/lib/keys.test.tsx", b'it("a test renamed", () => {});\n')
    put(repo, f"{web.WEB}/src/test/setup.ts", b"// changed\n")
    assert web.inputs_hash(repo) == before
    for name in ("tsconfig.app.json", "tsconfig.json"):
        put(repo, f"{web.WEB}/{name}", b'{"compilerOptions": {"jsx": "preserve"}}\n')
        assert web.inputs_hash(repo) != before, name
        put(repo, f"{web.WEB}/{name}", b"{}\n")


def test_the_build_s_inputs_take_both_tsconfigs_and_tailwind_reads_no_test() -> None:
    files = web.input_files(REAL_ROOT)
    assert {"tsconfig.json", "tsconfig.app.json"} <= set(files)
    assert [f for f in files if ".test." in f or f.startswith("src/test/")] == []
    # Tailwind makes classes from the words it scans, so the tests the hash leaves out stay out of its scan too.
    css = (REAL_ROOT / web.WEB / "src/styles/app.css").read_text(encoding="utf-8")
    assert '@source not "../../src/**/*.test.*";' in css
    assert '@source not "../../src/test";' in css
