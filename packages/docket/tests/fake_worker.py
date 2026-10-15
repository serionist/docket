"""A models worker for tests: Docket's own protocol loop over the fake backend, run as a process with Docket's Python.

usage: python fake_worker.py '<spec as JSON>'
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from models_kit import FakeBackend

from docket.models.worker import serve


def main() -> int:
    spec = json.loads(sys.argv[1])
    real = sys.stdout
    sys.stdout = sys.stderr
    print("fake worker starting", file=sys.stderr, flush=True)
    if spec.get("exit_at_start"):
        return 4
    for line in spec.get("junk", []):
        real.write(line + "\n")
    real.flush()
    code = serve(FakeBackend(spec), sys.stdin, real)
    if spec.get("ignore_eof"):
        time.sleep(60)
    return code


if __name__ == "__main__":
    sys.exit(main())
