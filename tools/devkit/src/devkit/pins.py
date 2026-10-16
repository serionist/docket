"""The models' worker pins: equal to the dev group's, and installable on every system their markers name.

The tests run the worker's code on the dev group's packages, and Dependabot bumps the two in separate PRs (uv for the
dev group, pip for the requirements files), so the gate holds them equal. The CUDA file's onnxruntime-gpu counts as
onnxruntime.

A universal compile pins by metadata, not by the files a release has: nvidia-cublas 13.8.1.7 came for Linux only while
its marker named Windows too, and the CUDA install failed there (#109). `devkit models-lock` writes each pin's files by
sha256 to `tools/devkit/models-wheels.json`, from the same resolution as the compiled files; the gate holds the
compiled hashes to that list and each pin to a wheel for every system its marker names, with no network.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from packaging.markers import Marker
from packaging.tags import Tag
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename

MODELS = "packages/docket/src/docket/models"
WHEELS = "tools/devkit/models-wheels.json"
SHARED = ("onnxruntime", "tokenizers", "numpy")
PIN = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*==\s*([^\s;\\#]+)")
# The environments are built on Docket's own Python, and compiled for 3.12 (`devkit models-lock`).
PYTHON_MINOR = 12
# Each pin's files by hash, per device: {device: {"name==version": {"sha256:<hex>": filename}}}. The hash is written
# as requirements files write it; a bare one after a file name like tokenizers-... reads as a key to the secret scan.
Files = dict[str, dict[str, dict[str, str]]]


def _env(sys_platform: str, system: str, machine: str, os_name: str) -> dict[str, str]:
    return {
        "implementation_name": "cpython",
        "implementation_version": "3.12.0",
        "os_name": os_name,
        "platform_machine": machine,
        "platform_release": "",
        "platform_system": system,
        "platform_version": "",
        "python_full_version": "3.12.0",
        "platform_python_implementation": "CPython",
        "python_version": "3.12",
        "sys_platform": sys_platform,
    }


# The systems the models' environments are built on (DR-0332): each one's marker environment, and the wheel platform
# tags that install there. The install refuses Intel Macs and Windows on Arm.
SYSTEMS: dict[str, tuple[dict[str, str], re.Pattern[str]]] = {
    "Windows on x86-64": (_env("win32", "Windows", "AMD64", "nt"), re.compile(r"^win_amd64$")),
    "Linux on x86-64": (_env("linux", "Linux", "x86_64", "posix"), re.compile(r"^manylinux\w*_x86_64$")),
    "Linux on arm64": (_env("linux", "Linux", "aarch64", "posix"), re.compile(r"^manylinux\w*_aarch64$")),
    "macOS on Apple silicon": (
        _env("darwin", "Darwin", "arm64", "posix"),
        re.compile(r"^macosx_\d+_\d+_(arm64|universal2)$"),
    ),
}


def pins(lines: Iterable[str]) -> dict[str, str]:
    """Each `name[extras]==version; marker` line's name and version, markers and extras left out."""
    found: dict[str, str] = {}
    for line in lines:
        m = PIN.match(line)
        if m:
            found[m.group(1).lower().replace("_", "-").removesuffix("-gpu")] = m.group(2)
    return found


@dataclass(frozen=True)
class Pinned:
    """One requirement of a compiled file: its pin, its marker and the sha256 of each file it allows."""

    name: str
    version: str
    marker: str | None
    hashes: frozenset[str]

    @property
    def key(self) -> str:
        return f"{self.name}=={self.version}"


def compiled(text: str) -> list[Pinned]:
    """The requirements of a file `uv pip compile --generate-hashes` wrote."""
    out: list[Pinned] = []
    for raw in text.replace("\\\r\n", " ").replace("\\\n", " ").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        spec, *hashes = line.split("--hash=")
        req, _, marker = spec.partition(";")
        name, _, version = req.strip().partition("==")
        out.append(
            Pinned(
                canonicalize_name(name.split("[")[0]),
                version.strip(),
                marker.strip() or None,
                frozenset(h.strip() for h in hashes),
            )
        )
    return out


def lock_files(text: str) -> dict[str, dict[str, str]]:
    """Each package's files by hash, from a `pylock.toml` of the same resolution."""
    data: Any = tomllib.loads(text)
    out: dict[str, dict[str, str]] = {}
    for package in cast(list[dict[str, Any]], data.get("packages", [])):
        files = cast(list[dict[str, Any]], package.get("wheels", []))
        if "sdist" in package:
            files = [*files, cast(dict[str, Any], package["sdist"])]
        out[f"{canonicalize_name(str(package['name']))}=={package['version']}"] = {
            f"sha256:{f['hashes']['sha256']}": str(f.get("name") or str(f["url"]).rsplit("/", 1)[-1]) for f in files
        }
    return out


def _python(tag: Tag) -> bool:
    """Whether a wheel's interpreter and ABI tags install on CPython 3.12."""
    if tag.abi == "none":
        return tag.interpreter in ("py3", f"py3{PYTHON_MINOR}", f"cp3{PYTHON_MINOR}")
    minor = tag.interpreter[3:]
    if tag.abi == "abi3":
        return tag.interpreter.startswith("cp3") and minor.isdigit() and int(minor) <= PYTHON_MINOR
    return tag.abi == tag.interpreter == f"cp3{PYTHON_MINOR}"


def installs_on(filename: str, platform: re.Pattern[str]) -> bool:
    """Whether a file is a wheel for CPython 3.12 on a system whose platform tags `platform` matches."""
    try:
        tags = parse_wheel_filename(filename)[3]
    except InvalidWheelFilename:
        return False
    return any(_python(t) and (t.platform == "any" or platform.match(t.platform)) for t in tags)


def missing_systems(pin: Pinned, files: Mapping[str, str]) -> list[str]:
    """The systems a pin's marker names that none of its files installs on."""
    return [
        system
        for system, (env, platform) in SYSTEMS.items()
        if (pin.marker is None or Marker(pin.marker).evaluate(env))
        and not any(installs_on(f, platform) for f in files.values())
    ]


def load_files(root: Path) -> Files | None:
    path = root / WHEELS
    if not path.is_file():
        return None
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): v for k, v in cast(dict[str, Any], data).items() if not str(k).startswith("$")}


def wheel_problems(root: Path, sources: list[Path]) -> list[str]:
    """Each compiled file against the files list: the same pins, each listed file among the pin's hashes, and a wheel
    for every system the pin's marker names."""
    files = load_files(root)
    if files is None:
        return [f"no {WHEELS}: run uv run devkit models-lock"]
    out: list[str] = []
    for source in sources:
        target = source.with_suffix(".txt")
        if not target.is_file():
            continue
        known = files.get(source.stem.removeprefix("requirements-"), {})
        pinned = compiled(target.read_text(encoding="utf-8"))
        stale = sorted({p.key for p in pinned} ^ set(known))
        # The compiled file hashes every file of a release; the list only those for Python 3.12 and up.
        stale += [p.key for p in pinned if p.key in known and not set(known[p.key]) <= p.hashes]
        if stale:
            out.append(f"{target.name} and {WHEELS} differ on {', '.join(stale)}: run uv run devkit models-lock")
            continue
        out += [
            f"{target.name} pins {pin.name} {pin.version}, which has no wheel for {system}, a system its marker names: "
            "run uv run devkit models-lock"
            for pin in pinned
            for system in missing_systems(pin, known[pin.key])
        ]
    return out


def problems(root: Path) -> list[str]:
    data: Any = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dev = pins(str(d) for d in data.get("dependency-groups", {}).get("dev", []))
    out: list[str] = []
    sources = sorted((root / MODELS).glob("requirements-*.in"))
    if not sources:
        return [f"no requirements-*.in in {MODELS}"]
    for source in sources:
        wanted = pins(source.read_text(encoding="utf-8").splitlines())
        target = source.with_suffix(".txt")
        built = pins(target.read_text(encoding="utf-8").splitlines()) if target.is_file() else {}
        for name in SHARED:
            if name not in wanted:
                out.append(f"{source.name} doesn't pin {name}")
            elif wanted[name] != dev.get(name):
                out.append(f"{source.name} pins {name} {wanted[name]}, the dev group {dev.get(name) or 'nothing'}")
            elif built.get(name) != wanted[name]:
                out.append(
                    f"{target.name} has {name} {built.get(name) or 'nowhere'}, not {wanted[name]}: "
                    "run uv run devkit models-lock"
                )
    return out + wheel_problems(root, sources)
