from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest

from devkit import cli, gate, pins

DEV = """[dependency-groups]
dev = [
  "pytest==9.1.1",
  "onnxruntime==1.30.0; sys_platform != 'darwin' or platform_machine == 'arm64'",
  "tokenizers==0.23.2",
  "numpy==2.5.3",
]
"""

EVERYWHERE = ("win_amd64", "manylinux_2_28_x86_64", "manylinux_2_28_aarch64", "macosx_14_0_arm64")
CUDA_MARKER = "sys_platform == 'linux' or sys_platform == 'win32'"
# name, version, marker, files
Release = tuple[str, str, str | None, tuple[str, ...]]


def release(
    name: str, version: str, *platforms: str, marker: str | None = None, python: str = "cp312-cp312"
) -> Release:
    stem = f"{name.replace('-', '_')}-{version}"
    return (name, version, marker, tuple(f"{stem}-{python}-{p}.whl" for p in platforms))


def sha(filename: str) -> str:
    return hashlib.sha256(filename.encode()).hexdigest()


def txt(releases: Sequence[Release]) -> str:
    """A compiled file as `uv pip compile --generate-hashes --no-annotate` writes it."""
    out = []
    for name, version, marker, files in releases:
        head = f"{name}=={version}" + (f" ; {marker}" if marker else "")
        out.append(head + "".join(f" \\\n    --hash=sha256:{sha(f)}" for f in files) + "\n")
    return "".join(out)


def listed(releases: Sequence[Release]) -> dict[str, dict[str, str]]:
    return {f"{n}=={v}": {f"sha256:{sha(f)}": f for f in files} for n, v, _, files in releases}


NUMPY = release("numpy", "2.5.3", "any", python="py3-none")
TOKENIZERS = release("tokenizers", "0.23.2", *EVERYWHERE, python="cp39-abi3")
CPU = [NUMPY, release("onnxruntime", "1.30.0", *EVERYWHERE), TOKENIZERS]
ORT_GPU = release("onnxruntime-gpu", "1.30.0", *EVERYWHERE[:3], marker=CUDA_MARKER)
CUDA = [NUMPY, ORT_GPU, TOKENIZERS]
CUDA_IN = 'onnxruntime-gpu[cuda,cudnn]==1.30.0; sys_platform == "win32" or sys_platform == "linux"\n'


def models_repo(tmp_path: Path, cuda_in: str, cuda: Sequence[Release], wheels: object = None) -> Path:
    folder = tmp_path / pins.MODELS
    folder.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(DEV, encoding="utf-8")
    (folder / "requirements-cpu.in").write_text(
        "# a comment\nonnxruntime==1.30.0\ntokenizers==0.23.2\nnumpy==2.5.3\n", encoding="utf-8"
    )
    (folder / "requirements-cpu.txt").write_text(txt(CPU), encoding="utf-8")
    (folder / "requirements-cuda.in").write_text(cuda_in, encoding="utf-8")
    (folder / "requirements-cuda.txt").write_text(txt(cuda), encoding="utf-8")
    if wheels is None:
        wheels = {"$comment": "files", "cpu": listed(CPU), "cuda": listed(cuda)}
    (tmp_path / pins.WHEELS).parent.mkdir(parents=True)
    (tmp_path / pins.WHEELS).write_text(json.dumps(wheels), encoding="utf-8")
    return tmp_path


def test_the_models_pins_match_the_dev_groups(tmp_path: Path) -> None:
    root = models_repo(tmp_path, CUDA_IN + "tokenizers==0.23.2\nnumpy==2.5.3\n", CUDA)
    assert pins.problems(root) == []


@pytest.mark.parametrize(
    ("cuda_in", "cuda", "said"),
    [
        (
            CUDA_IN.replace("1.30.0", "1.31.0") + "tokenizers==0.23.2\nnumpy==2.5.3\n",
            CUDA,
            "requirements-cuda.in pins onnxruntime 1.31.0, the dev group 1.30.0",
        ),
        (CUDA_IN + "numpy==2.5.3\n", CUDA, "requirements-cuda.in doesn't pin tokenizers"),
        (
            CUDA_IN + "tokenizers==0.23.2\nnumpy==2.5.3\n",
            CUDA[:2],
            "requirements-cuda.txt has tokenizers nowhere, not 0.23.2: run uv run devkit models-lock",
        ),
        (
            CUDA_IN + "tokenizers==0.23.2\nnumpy==2.5.3\n",
            [*CUDA, release("nvidia-cublas", "13.8.1.7", *EVERYWHERE[1:3], marker=CUDA_MARKER, python="py3-none")],
            "requirements-cuda.txt pins nvidia-cublas 13.8.1.7, which has no wheel for Windows on x86-64, a system its "
            "marker names: run uv run devkit models-lock",
        ),
    ],
    ids=["another version", "no pin", "not compiled", "no wheel for a system"],
)
def test_a_pin_apart_from_the_dev_groups_or_without_a_wheel_fails_the_lock_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cuda_in: str, cuda: list[Release], said: str
) -> None:
    root = models_repo(tmp_path, cuda_in, cuda)
    assert pins.problems(root) == [said]
    monkeypatch.setattr(shutil, "which", lambda name: name)
    out = gate.step_lock(gate.Ctx(root=root, lane="quick", ci=False, base=None, pr=None))
    assert out.status == "fail" and out.detail == [said]


def test_a_pin_needs_a_wheel_only_where_its_marker_holds(tmp_path: Path) -> None:
    windows_only = release("colorama", "0.4.6", "win_amd64", marker="sys_platform == 'win32'", python="py3-none")
    two_systems = release("hf-xet", "1.7.0", "win_amd64", "manylinux_2_17_x86_64", python="cp37-abi3")
    root = models_repo(tmp_path, CUDA_IN + "tokenizers==0.23.2\nnumpy==2.5.3\n", [*CUDA, windows_only, two_systems])
    assert pins.problems(root) == [
        f"requirements-cuda.txt pins hf-xet 1.7.0, which has no wheel for {system}, a system its marker names: "
        "run uv run devkit models-lock"
        for system in ("Linux on arm64", "macOS on Apple silicon")
    ]


@pytest.mark.parametrize(
    ("filename", "system", "installs"),
    [
        ("a-1.0-py3-none-any.whl", "macOS on Apple silicon", True),
        ("a-1.0-cp312-cp312-win_amd64.whl", "Windows on x86-64", True),
        ("a-1.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", "Linux on x86-64", True),
        ("a-1.0-py3-none-manylinux_2_34_aarch64.whl", "Linux on arm64", True),
        ("a-1.0-cp312-cp312-macosx_10_13_universal2.whl", "macOS on Apple silicon", True),
        ("a-1.0-cp313-cp313-win_amd64.whl", "Windows on x86-64", False),
        ("a-1.0-cp313-abi3-win_amd64.whl", "Windows on x86-64", False),
        ("a-1.0-cp312-cp312-win_arm64.whl", "Windows on x86-64", False),
        ("a-1.0-cp312-cp312-musllinux_1_2_x86_64.whl", "Linux on x86-64", False),
        ("a-1.0-cp312-cp312-macosx_11_0_x86_64.whl", "macOS on Apple silicon", False),
        ("a-1.0-py2-none-any.whl", "Windows on x86-64", False),
        ("a-1.0.tar.gz", "Windows on x86-64", False),
        ("not-a-wheel.whl", "Windows on x86-64", False),
    ],
)
def test_which_files_install_on_python_3_12_on_a_system(filename: str, system: str, installs: bool) -> None:
    assert pins.installs_on(filename, pins.SYSTEMS[system][1]) is installs


@pytest.mark.parametrize(
    ("wheels", "said"),
    [
        (None, "no tools/devkit/models-wheels.json: run uv run devkit models-lock"),
        ({"cpu": listed(CPU), "cuda": listed(CUDA[1:])}, "differ on numpy==2.5.3"),
        ({"cpu": listed(CPU), "cuda": listed([*CUDA, ("numpy", "2.5.3", None, ("x.whl",))])}, "differ on numpy==2.5.3"),
        ({"cpu": listed(CPU), "cuda": {**listed(CUDA), "torch==9.0": {}}}, "differ on torch==9.0"),
    ],
    ids=["no list", "a pin missing", "a file the compiled file doesn't hash", "a pin no longer compiled"],
)
def test_the_wheel_list_must_describe_the_compiled_files(tmp_path: Path, wheels: object, said: str) -> None:
    root = models_repo(tmp_path, CUDA_IN + "tokenizers==0.23.2\nnumpy==2.5.3\n", CUDA, wheels or {})
    if wheels is None:
        (root / pins.WHEELS).unlink()
    [found] = pins.problems(root)
    assert said in found and (wheels is None or found.startswith("requirements-cuda.txt and "))


def test_compiled_reads_extras_markers_hashes_and_crlf() -> None:
    text = "# header\r\nonnxruntime_GPU[cuda]==1.30.0 ; sys_platform == 'win32' \\\r\n    --hash=sha256:aa \\\r\n"
    text += "    --hash=sha256:bb\r\n\r\nnumpy==2.5.3\r\n"
    assert pins.compiled(text) == [
        pins.Pinned("onnxruntime-gpu", "1.30.0", "sys_platform == 'win32'", frozenset({"sha256:aa", "sha256:bb"})),
        pins.Pinned("numpy", "2.5.3", None, frozenset()),
    ]


def pylock(releases: Sequence[Release], sdist: bool = False) -> str:
    out = ['lock-version = "1.0"\n']
    for name, version, _, files in releases:
        out.append(f'[[packages]]\nname = "{name}"\nversion = "{version}"\n')
        if sdist:
            url = f"https://h.example/{name}-{version}.tar.gz"
            out.append(f'sdist = {{ url = "{url}", hashes = {{ sha256 = "{sha(name)}" }} }}\n')
        wheels = ", ".join(f'{{ url = "https://h.example/p/{f}", hashes = {{ sha256 = "{sha(f)}" }} }}' for f in files)
        out.append(f"wheels = [{wheels}]\n")
    return "".join(out)


def test_lock_files_reads_each_packages_wheels_and_sdist() -> None:
    got = pins.lock_files(pylock([NUMPY, ORT_GPU], sdist=True))
    assert got["numpy==2.5.3"] == {
        f"sha256:{sha('numpy')}": "numpy-2.5.3.tar.gz",
        f"sha256:{sha(NUMPY[3][0])}": NUMPY[3][0],
    }
    assert set(got["onnxruntime-gpu==1.30.0"].values()) == {*ORT_GPU[3], "onnxruntime-gpu-1.30.0.tar.gz"}


CUBLAS_NEW = release("nvidia-cublas", "13.8.1.7", *EVERYWHERE[1:3], marker=CUDA_MARKER, python="py3-none")
CUBLAS_OLD = release("nvidia-cublas", "13.8.0.4", *EVERYWHERE[:3], marker=CUDA_MARKER, python="py3-none")


def fake_uv(resolve: dict[str, list[Release]], calls: list[list[str]], disagree: bool = False) -> object:
    """A `uv pip compile` that resolves each source to its releases, leaving out a release a constraint excludes."""

    def run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        excluded = set()
        for i, arg in enumerate(cmd):
            if arg == "-c" and cmd[i + 1].endswith("constraints.txt"):
                excluded = set(Path(cmd[i + 1]).read_text(encoding="utf-8").split())
        options = resolve[Path(cmd[3]).name]
        chosen: list[Release] = []
        for rel in options:
            if f"{rel[0]}!={rel[1]}" in excluded or any(c[0] == rel[0] for c in chosen):
                continue
            chosen.append(rel)
        out = Path(cmd[-1])
        if out.suffix == ".txt":
            out.write_text(txt(chosen), encoding="utf-8")
        else:
            out.write_text(pylock(chosen[1:] if disagree else chosen), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return run


def lock_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / cli.MODELS
    folder.mkdir(parents=True)
    (tmp_path / "tools" / "devkit").mkdir(parents=True)
    for device in ("cpu", "cuda"):
        (folder / f"requirements-{device}.in").write_text("numpy==2.5.3\n", encoding="utf-8")
    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 15))
    return tmp_path


def test_models_lock_leaves_out_a_release_without_a_wheel_for_a_system_and_lists_each_pins_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = lock_repo(tmp_path, monkeypatch)
    calls: list[list[str]] = []
    resolve = {"requirements-cpu.in": CPU, "requirements-cuda.in": [*CUDA, CUBLAS_NEW, CUBLAS_OLD]}
    monkeypatch.setattr(subprocess, "run", fake_uv(resolve, calls))
    assert cli.main(["models-lock"]) == 0
    cuda_txt = (root / cli.MODELS / "requirements-cuda.txt").read_text(encoding="utf-8")
    assert "nvidia-cublas==13.8.0.4" in cuda_txt and "13.8.1.7" not in cuda_txt
    out = capsys.readouterr().out
    assert "requirements-cuda.in: nvidia-cublas 13.8.1.7 has no wheel for Windows on x86-64, so it is left out" in out
    assert "from versions published before 2026-10-08" in out
    # Two compiles a round: the .txt, then a pylock held to its pins. The CPU file took one round, the CUDA file two.
    assert [(Path(c[3]).name, Path(c[-1]).name) for c in calls] == [
        ("requirements-cpu.in", "requirements-cpu.txt"),
        ("requirements-cpu.in", "pylock.models.toml"),
        *[("requirements-cuda.in", "requirements-cuda.txt"), ("requirements-cuda.in", "pylock.models.toml")] * 2,
    ]
    assert all(c[c.index("--exclude-newer") + 1] == "2026-10-08" and "--generate-hashes" in c for c in calls)
    assert all(re.search(r"requirements-\w+\.txt$", c[c.index("-c", c.index("-c") + 1) + 1]) for c in calls[1::2])
    wheels = json.loads((root / pins.WHEELS).read_text(encoding="utf-8"))
    assert wheels["cuda"] == listed([*CUDA, CUBLAS_OLD]) and wheels["cpu"] == listed(CPU) and "$comment" in wheels
    assert pins.wheel_problems(root, sorted((root / cli.MODELS).glob("requirements-*.in"))) == []


def test_no_requirements_files_is_a_problem(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(DEV, encoding="utf-8")
    assert pins.problems(tmp_path) == [f"no requirements-*.in in {pins.MODELS}"]


def test_models_lock_stops_when_its_two_compiles_differ_or_no_release_has_every_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lock_repo(tmp_path, monkeypatch)
    resolve = {"requirements-cpu.in": CPU, "requirements-cuda.in": CUDA}
    monkeypatch.setattr(subprocess, "run", fake_uv(resolve, [], disagree=True))
    assert cli.main(["models-lock"]) == 2
    assert (
        "requirements-cpu.in: the requirements and the pylock compiles differ on numpy==2.5.3"
        in capsys.readouterr().err
    )
    resolve = {"requirements-cpu.in": [*CPU, CUBLAS_NEW], "requirements-cuda.in": CUDA}
    monkeypatch.setattr(subprocess, "run", fake_uv(resolve, []))
    monkeypatch.setattr(cli, "LOCK_ROUNDS", 1)
    assert cli.main(["models-lock"]) == 2
    assert "requirements-cpu.in: a pin still lacks a wheel for a system after 1 rounds" in capsys.readouterr().err


def test_this_repos_pins_agree() -> None:
    assert pins.problems(Path(__file__).resolve().parents[3]) == []
