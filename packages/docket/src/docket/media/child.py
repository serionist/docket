"""The audio cutter's own process (DR-0559): `python -I -X utf8 -m docket.media.child --memory <bytes>`.

It caps its own memory first, where the system enforces a cap, keeps numpy out and refuses Python's own sockets, and
says `ready` with its pid and the cap it got, as the documents' reader does (DR-0485). Then it takes one JSON line and
answers one:

| Line | When |
|---|---|
| `{"ready": true, "pid": <n>, "memory": <bytes or null>}` | once, at the start |
| `{"done": {...}}` | decoded whole and written cut: its duration, onset, codec, rate, channels, bit rate, size |
| `{"not_read": {"reason": ..., "detail": ...}}` | no audio, damaged, too long or too large, in Docket's words |

The request names the input file, its demuxer (from the sniffed type, never guessed by FFmpeg), the output file and
the ranges to silence, in milliseconds, already padded and merged. FFmpeg opens the one file through the file
protocol alone, so no playlist or reference in it reaches anything else (DR-0560). The audio is decoded a frame at a
time to 48 kHz mono, the samples in each range set to zero, and encoded as WebM Opus as it goes, so memory stays flat
for a long call. Nothing is removed: every time in the transcript still points at the same moment.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from array import array
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any, BinaryIO, TextIO, cast

from docket.childproc import limit_memory

RATE = 48_000
BIT_RATE = 32_000
# The first sound: the first 50 ms window louder than -45 dBFS.
WINDOW = RATE // 20
ONSET = 32768 * 10 ** (-45 / 20)
# The demuxer for each type the sniff gives an audio file: never left to FFmpeg's own probe.
DEMUXERS = {
    "mp3": "mp3",
    "m4a": "mov",
    "aac": "aac",
    "ogg": "ogg",
    "opus": "ogg",
    "wav": "wav",
    "flac": "flac",
    "webm": "matroska",
}


class NotReadError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def keep_out(modules: dict[str, Any] | None = None) -> None:
    """Make `import numpy` fail in this process: its BLAS reserves memory for every core as it loads, past the cap
    on a machine with many (DR-0485). Only the child's own process calls this."""
    target = cast(dict[str, Any], sys.modules) if modules is None else modules
    target["numpy"] = None


def no_sockets(event: str, args: tuple[object, ...]) -> None:
    """An audit hook: Python's own sockets refuse to connect. FFmpeg's are kept off by the file protocol alone."""
    if event in ("socket.connect", "socket.getaddrinfo"):
        raise RuntimeError("the audio cutter opens no connection")


class _Cutter:
    """One file's frames, silenced where the ranges say and encoded as they come."""

    def __init__(self, output: Any, ranges: Sequence[tuple[int, int]], max_samples: int) -> None:
        self.output = output
        self.stream = output.add_stream("libopus", rate=RATE, layout="mono")
        self.stream.bit_rate = BIT_RATE
        self.stream.codec_context.options = {"application": "voip", "vbr": "on", "frame_duration": "20"}
        self.spans = [(start * RATE // 1000, end * RATE // 1000) for start, end in ranges]
        self.max_samples = max_samples
        self.position = 0
        self.onset: int | None = None

    def frame(self, frame: Any) -> None:
        n = int(frame.samples)
        if self.position + n > self.max_samples:
            hours = self.max_samples // RATE // 3600
            raise NotReadError("long", f"it runs longer than {hours} hours")
        plane = frame.planes[0]
        data = bytearray(bytes(plane))
        if self.onset is None:
            self._find_onset(data, n)
        for start, end in self.spans:
            first, last = max(start, self.position), min(end, self.position + n)
            if first < last:
                data[(first - self.position) * 2 : (last - self.position) * 2] = bytes((last - first) * 2)
        plane.update(bytes(data))
        frame.pts = self.position
        frame.time_base = Fraction(1, RATE)
        self.position += n
        self._mux(frame)

    def _find_onset(self, data: bytearray, n: int) -> None:
        samples = array("h", bytes(data[: n * 2]))
        for at in range(0, n, WINDOW):
            window = samples[at : at + WINDOW]
            if math.sqrt(math.sumprod(window, window) / len(window)) > ONSET:
                self.onset = (self.position + at) * 1000 // RATE
                return

    def flush(self) -> None:
        self._mux(None)

    def _mux(self, frame: Any) -> None:
        for packet in self.stream.encode(frame):
            self.output.mux(packet)


def cut(request: dict[str, Any]) -> dict[str, Any]:
    """One file's answer line."""
    import av  # noqa: PLC0415 - only once main has capped the memory and kept numpy out
    import av.error  # noqa: PLC0415

    source, output = str(request["input"]), str(request["output"])
    demuxer = DEMUXERS.get(str(request["format"]))
    try:
        if demuxer is None:
            raise NotReadError("type", "it isn't audio Docket can cut")
        ranges = [(int(start), int(end)) for start, end in request["cuts"]]
        max_samples = int(request["max_ms"]) * RATE // 1000
        try:
            done = _decode(av, source, demuxer, output=output, ranges=ranges, max_samples=max_samples)
        except av.error.FFmpegError as e:
            raise NotReadError("damaged", f"it doesn't decode as {request['format']} ({type(e).__name__})") from None
        size = Path(output).stat().st_size
        if size > int(request["max_bytes"]):
            raise NotReadError("size", "its cut audio is over the media cap")
    except NotReadError as e:
        return {"not_read": {"reason": e.reason, "detail": e.detail}}
    return {"done": {**done, "bytes": size}}


def _decode(
    av: Any, source: str, demuxer: str, *, output: str, ranges: list[tuple[int, int]], max_samples: int
) -> dict[str, Any]:
    options = {"protocol_whitelist": "file"}
    with av.open(source, "r", format=demuxer, options=options) as found:
        stream = next((s for s in found.streams if s.type == "audio"), None)
        if stream is None:
            raise NotReadError("no-audio", "it holds no audio")
        context = stream.codec_context
        facts = {
            "codec": str(context.name),
            "rate": int(context.sample_rate or 0),
            "channels": int(context.layout.nb_channels),
            "bit_rate": int(context.bit_rate or found.bit_rate or 0) or None,
        }
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        with av.open(output, "w", format="webm", options=options) as written:
            cutter = _Cutter(written, ranges, max_samples)
            for packet in found.demux(stream):
                for frame in packet.decode():
                    for out in resampler.resample(frame):
                        cutter.frame(out)
            for out in resampler.resample(None):
                cutter.frame(out)
            cutter.flush()
    if cutter.position == 0:
        raise NotReadError("no-audio", "it holds no audio")
    return {**facts, "duration_ms": cutter.position * 1000 // RATE, "onset_ms": cutter.onset}


def _write(out: BinaryIO, line: dict[str, Any]) -> None:
    out.write(json.dumps(line, ensure_ascii=False).encode("utf-8") + b"\n")
    out.flush()


def main(argv: list[str], stdin: BinaryIO, stdout: BinaryIO, stderr: TextIO) -> int:
    parser = argparse.ArgumentParser(prog="docket.media.child")
    parser.add_argument("--memory", type=int, required=True)
    args = parser.parse_args(argv)
    # Before anything is read; a cap the system refuses raises here, and the child never says ready.
    memory = limit_memory(args.memory)
    stdout_before = sys.stdout
    sys.stdout = stderr
    try:
        _write(stdout, {"ready": True, "pid": os.getpid(), "memory": memory})
        if line := stdin.readline():
            _write(stdout, cut(json.loads(line)))
    finally:
        sys.stdout = stdout_before
    return 0


if __name__ == "__main__":  # pragma: no cover - the child's process; main() is tested in-process
    keep_out()
    sys.addaudithook(no_sockets)
    sys.exit(main(sys.argv[1:], sys.stdin.buffer, sys.stdout.buffer, sys.stderr))
