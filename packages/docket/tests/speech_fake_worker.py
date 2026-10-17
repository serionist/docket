"""A stand-in for the speech worker, for the speech tests: it speaks the worker's protocol and misbehaves as its plan
asks. Started as `python speech_fake_worker.py '<plan>' '<spec>'`, the plan a JSON object:

| Key | What |
|---|---|
| `behave` | ok, hang (after hello), crash, junk, bad (a segment short of its keys), error (mid-job), missing, silent |
| `segments` | `[start_ms, end_ms, text]` triples to answer, on the recording's timeline |
| `language` | the language it says it used; the spec's when absent |
| `spec_out` | a file to write the spec it got into, so a test reads the prompt and the language |

The duration it says is the PCM file's, so a test proves the decoder ran.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def say(line: object) -> None:
    sys.stdout.write(json.dumps(line) + "\n")
    sys.stdout.flush()


def main() -> None:
    plan = json.loads(sys.argv[1])
    spec = json.loads(sys.argv[2])
    behave = plan.get("behave", "ok")
    if plan.get("spec_out"):
        Path(plan["spec_out"]).write_text(json.dumps(spec), encoding="utf-8")
    if behave == "silent":
        time.sleep(60)
    if behave == "missing":
        say({"error": "ModelMissingError"})
        return
    sys.stderr.write("a library's chatter, kept and never logged\n")
    say({"hello": {"device": spec["device"], "compute": spec["compute"]}})
    if behave == "hang":
        time.sleep(60)
    if behave == "crash":
        sys.exit(3)
    if behave == "junk":
        sys.stdout.write("not json\n")
        sys.stdout.flush()
        return
    if behave == "bad":
        say({"segment": {"start_ms": 0}})
        return
    duration = Path(spec["pcm"]).stat().st_size // 4 * 1000 // 16_000
    for start, end, text in plan.get("segments", []):
        say({"segment": {"start_ms": start, "end_ms": end, "text": text, "logprob": -0.2, "no_speech": 0.01}})
    if behave == "error":
        say({"error": "RuntimeError"})
        return
    say({"progress": duration})
    say({"done": {"duration_ms": duration, "language": plan.get("language", spec["language"])}})


if __name__ == "__main__":
    main()
