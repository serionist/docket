"""The models' worker pins against the dev group's: the tests run the worker's code on the dev group's packages, and
Dependabot bumps the two in separate PRs (uv for the dev group, pip for the requirements files), so the gate holds them
equal. The CUDA file's onnxruntime-gpu counts as onnxruntime."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

MODELS = "packages/docket/src/docket/models"
SHARED = ("onnxruntime", "tokenizers", "numpy")
PIN = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*==\s*([^\s;\\#]+)")


def pins(lines: Iterable[str]) -> dict[str, str]:
    """Each `name[extras]==version; marker` line's name and version, markers and extras left out."""
    found: dict[str, str] = {}
    for line in lines:
        m = PIN.match(line)
        if m:
            found[m.group(1).lower().replace("_", "-").removesuffix("-gpu")] = m.group(2)
    return found


def problems(root: Path) -> list[str]:
    data: Any = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dev = pins(str(d) for d in data.get("dependency-groups", {}).get("dev", []))
    out: list[str] = []
    sources = sorted((root / MODELS).glob("requirements-*.in"))
    if not sources:
        return [f"no requirements-*.in in {MODELS}"]
    for source in sources:
        wanted = pins(source.read_text(encoding="utf-8").splitlines())
        compiled = source.with_suffix(".txt")
        built = pins(compiled.read_text(encoding="utf-8").splitlines()) if compiled.is_file() else {}
        for name in SHARED:
            if name not in wanted:
                out.append(f"{source.name} doesn't pin {name}")
            elif wanted[name] != dev.get(name):
                out.append(f"{source.name} pins {name} {wanted[name]}, the dev group {dev.get(name) or 'nothing'}")
            elif built.get(name) != wanted[name]:
                out.append(
                    f"{compiled.name} has {name} {built.get(name) or 'nowhere'}, not {wanted[name]}: "
                    "run uv run devkit models-lock"
                )
    return out
