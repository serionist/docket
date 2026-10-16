"""A stand-in for the extraction child, for test_documents_extract: it speaks the child's protocol, and misbehaves as
the file's stem asks. Started as `python documents_fake_child.py [--mode <mode>] --memory <bytes>`."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

FACTS = {
    "format": "txt",
    "engine": "fake",
    "text": "",
    "creator": None,
    "last_modified_by": None,
    "authors": [],
    "note": None,
    "sha256": "0" * 64,
    "size": 0,
}


def say(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def answer(stem: str) -> None:
    if stem == "hang":
        time.sleep(60)
    elif stem == "exit":
        sys.exit(3)
    elif stem == "junk":
        say("not json")
    elif stem == "list":
        say("[1]")
    elif stem == "two":
        say(json.dumps({"done": FACTS, "part": {}}))
    elif stem == "weird":
        say(json.dumps({"weird": 1}))
    elif stem == "badpart":
        say(json.dumps({"part": {"kind": "chapter"}}))
    elif stem == "baddone":
        say(json.dumps({"done": {"format": "pdf"}}))
    elif stem == "nonewline":
        sys.stdout.write("{")
        sys.stdout.flush()
        sys.exit(0)
    elif stem == "long":
        say(json.dumps({"part": {"kind": "page", "n": 1, "title": "page 1", "text": "x" * 500}}))
        say(json.dumps({"done": FACTS}))
    elif stem == "noisy":
        sys.stderr.write("Traceback: the text of a secret clause\n")
        sys.stderr.flush()
        say(json.dumps({"done": FACTS}))
    elif stem == "alloc":
        bytearray(1024 * 1024 * 1024)
        say(json.dumps({"done": FACTS}))
    else:
        say(json.dumps({"done": FACTS}))


def main() -> None:
    args = sys.argv[1:]
    mode = args[args.index("--mode") + 1] if "--mode" in args else "normal"
    memory = int(args[args.index("--memory") + 1])
    if mode == "silent":
        time.sleep(60)
    if mode == "other":
        say(json.dumps({"ready": False}))
        time.sleep(60)
    # The real child's own cap, as the real child sets it before its hello; "uncapped" says ready without one.
    from docket.documents.child import limit_memory

    got = None if mode == "uncapped" else limit_memory(memory)
    say(json.dumps({"ready": True, "pid": os.getpid(), "memory": got}))
    for raw in sys.stdin:
        answer(Path(json.loads(raw)["path"]).stem)
    if mode == "stubborn":
        time.sleep(60)


if __name__ == "__main__":
    main()
