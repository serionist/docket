"""A stand-in for the audio cutter's child, for test_media_cut: it speaks the child's protocol, and misbehaves as its
mode asks. Started as `python media_fake_child.py --mode <mode> --memory <bytes>`."""

from __future__ import annotations

import json
import os
import sys
import time

FACTS = {"duration_ms": 1000, "onset_ms": 0, "codec": "mp3", "rate": 16000, "channels": 1, "bit_rate": None}


def say(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main() -> None:
    args = sys.argv[1:]
    mode = args[args.index("--mode") + 1]
    memory = int(args[args.index("--memory") + 1])
    if mode == "silent":
        time.sleep(60)
    if mode == "other":
        say(json.dumps({"ready": False}))
        time.sleep(60)
    # The real child's own cap, as the real child sets it before its hello; "uncapped" says ready without one.
    from docket.childproc import limit_memory

    got = None if mode == "uncapped" else limit_memory(memory)
    say(json.dumps({"ready": True, "pid": os.getpid(), "memory": got}))
    request = json.loads(sys.stdin.readline())
    if mode == "hang":
        # Mid-cut: the output half written, then nothing more.
        with open(request["output"], "wb") as out:  # noqa: PTH123 - a test's stand-in, kept plain
            out.write(f"half {os.getpid()}".encode())
        time.sleep(60)
    elif mode == "exit":
        sys.exit(3)
    elif mode == "junk":
        say("not json")
    elif mode == "baddone":
        say(json.dumps({"done": {"codec": "mp3"}}))
    else:
        say(json.dumps({"done": FACTS}))


if __name__ == "__main__":
    main()
