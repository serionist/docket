"""Every Docket module imports on its own, first: a child process, a source skill's own tests or a tool may import
any one of them before the rest, and an import cycle shows only then. Each is imported in a child interpreter with
every Docket module dropped before it, so the order is the one a fresh process meets."""

from __future__ import annotations

import json
import subprocess
import sys

SCRIPT = """
import importlib, json, pkgutil, sys
import docket
names = sorted(m.name for m in pkgutil.walk_packages(docket.__path__, "docket.") if not m.name.endswith("__main__"))
failed = []
for name in names:
    for loaded in [m for m in sys.modules if m == "docket" or m.startswith("docket.")]:
        del sys.modules[loaded]
    try:
        importlib.import_module(name)
    except ImportError:
        failed.append(name)
print(json.dumps(failed))
"""

# Cycles from before this test, each through the vault package importing the gate, which imports these back. A fix
# takes its module off the list, and the test fails until it does, as it fails on any module added to it.
KNOWN = [
    "docket.media.backup",
    "docket.media.confirm",
    "docket.media.keystore",
    "docket.media.layout",
    "docket.media.ranges",
    "docket.media.screen",
    "docket.media.status",
]


def test_every_module_imports_first_in_a_fresh_interpreter() -> None:
    done = subprocess.run(
        [sys.executable, "-I", "-c", SCRIPT], capture_output=True, text=True, encoding="utf-8", check=False
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1]) == KNOWN
