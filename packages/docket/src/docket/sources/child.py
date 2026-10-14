"""The mapper's own process: load the consuming project's `mapper.py`, read the run from stdin, write what `map_run`
returns to stdout.

The runner starts it as `python -I -X utf8 -m docket.sources.child <mapper.py>`. Anything the mapper prints goes to
stderr, so a debugging print can't break the JSON on stdout.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import BinaryIO


def main(argv: list[str], stdin: BinaryIO, stdout: BinaryIO) -> int:
    if len(argv) != 1:
        print("usage: python -m docket.sources.child <mapper.py>", file=sys.stderr)
        return 2
    path = Path(argv[0])
    spec = importlib.util.spec_from_file_location("docket_source_mapper", path)
    if spec is None or spec.loader is None:
        print(f"{path} isn't a Python file", file=sys.stderr)
        return 2
    run = json.loads(stdin.read().decode("utf-8"))
    module = importlib.util.module_from_spec(spec)
    # So a mapper can keep helpers in modules next to it.
    sys.path.insert(0, str(path.parent))
    stdout_before = sys.stdout
    sys.stdout = sys.stderr
    try:
        spec.loader.exec_module(module)
        map_run = getattr(module, "map_run", None)
        if not callable(map_run):
            print("the mapper has no map_run(run) function", file=sys.stderr)
            return 3
        result = map_run(run)
    finally:
        sys.stdout = stdout_before
        sys.path.remove(str(path.parent))
    stdout.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - the mapper's process; main() is tested in-process
    sys.exit(main(sys.argv[1:], sys.stdin.buffer, sys.stdout.buffer))
