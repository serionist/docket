from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from devkit import gate, pins

DEV = """[dependency-groups]
dev = [
  "pytest==9.1.1",
  "onnxruntime==1.30.0; sys_platform != 'darwin' or platform_machine == 'arm64'",
  "tokenizers==0.23.2",
  "numpy==2.5.3",
]
"""


def models_repo(tmp_path: Path, cuda_in: str, cuda_txt: str) -> Path:
    folder = tmp_path / pins.MODELS
    folder.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(DEV, encoding="utf-8")
    (folder / "requirements-cpu.in").write_text(
        "# a comment\nonnxruntime==1.30.0\ntokenizers==0.23.2\nnumpy==2.5.3\n", encoding="utf-8"
    )
    (folder / "requirements-cpu.txt").write_text(
        "numpy==2.5.3 \\\n    --hash=sha256:00\nonnxruntime==1.30.0 \\\n    --hash=sha256:11\ntokenizers==0.23.2 \\\n",
        encoding="utf-8",
    )
    (folder / "requirements-cuda.in").write_text(cuda_in, encoding="utf-8")
    (folder / "requirements-cuda.txt").write_text(cuda_txt, encoding="utf-8")
    return tmp_path


CUDA_IN = 'onnxruntime-gpu[cuda,cudnn]==1.30.0; sys_platform == "win32" or sys_platform == "linux"\n'
CUDA_TXT = "numpy==2.5.3 \\\nonnxruntime-gpu==1.30.0 ; sys_platform == 'linux' or sys_platform == 'win32' \\\n"


def test_the_models_pins_match_the_dev_groups(tmp_path: Path) -> None:
    root = models_repo(tmp_path, CUDA_IN + "tokenizers==0.23.2\nnumpy==2.5.3\n", CUDA_TXT + "tokenizers==0.23.2 \\\n")
    assert pins.problems(root) == []


@pytest.mark.parametrize(
    ("cuda_in", "cuda_txt", "said"),
    [
        (
            CUDA_IN.replace("1.30.0", "1.31.0") + "tokenizers==0.23.2\nnumpy==2.5.3\n",
            CUDA_TXT + "tokenizers==0.23.2 \\\n",
            "requirements-cuda.in pins onnxruntime 1.31.0, the dev group 1.30.0",
        ),
        (
            CUDA_IN + "numpy==2.5.3\n",
            CUDA_TXT + "tokenizers==0.23.2 \\\n",
            "requirements-cuda.in doesn't pin tokenizers",
        ),
        (
            CUDA_IN + "tokenizers==0.23.2\nnumpy==2.5.3\n",
            CUDA_TXT,
            "requirements-cuda.txt has tokenizers nowhere, not 0.23.2: run uv run devkit models-lock",
        ),
    ],
    ids=["another version", "no pin", "not compiled"],
)
def test_a_pin_apart_from_the_dev_groups_fails_the_lock_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cuda_in: str, cuda_txt: str, said: str
) -> None:
    root = models_repo(tmp_path, cuda_in, cuda_txt)
    assert pins.problems(root) == [said]
    monkeypatch.setattr(shutil, "which", lambda name: name)
    out = gate.step_lock(gate.Ctx(root=root, lane="quick", ci=False, base=None, pr=None))
    assert out.status == "fail" and out.detail == [said]


def test_no_requirements_files_is_a_problem(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(DEV, encoding="utf-8")
    assert pins.problems(tmp_path) == [f"no requirements-*.in in {pins.MODELS}"]


def test_this_repos_pins_agree() -> None:
    assert pins.problems(Path(__file__).resolve().parents[3]) == []
