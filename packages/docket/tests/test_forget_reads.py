"""Every reader of run files skips what the forgotten list takes (DR-0579), so a line a union merge brought back reads
as gone at once: `RawLog`, `RawLookup`'s load, `vault.status` and `vault check`, the gate's cut scan, the notes import
and the folder scan's holdings. The caches of what was read are keyed by a run file's size and modification time, since
a forget changes a run file in place. A scan holds every place that opens a run file to this list. Invented data only.
"""

from __future__ import annotations

import ast
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import PLAUD, POLISHED, copy_vault, make_config, make_record, plaud_records, template_vault
from forget_kit import AT, forget, stored, write_raw_file

from docket.documents import records as documents
from docket.documents import scan
from docket.forget import apply as forget_apply
from docket.imports import notes
from docket.vault import cursors, rawlog
from docket.vault.config import SourceConfig
from docket.vault.cuts import CUT_MARKER, Cut
from docket.vault.gate import Gate
from docket.vault.rawlog import RawLog
from docket.vault.records import TRANSCRIBED_VIA, RawRecord
from docket.vault.spans import RawLookup
from docket.vault.vault import Vault

SRC = Path(rawlog.__file__).resolve().parents[1]
RUN = "raw/plaud/2026/10/2026-10-14T1200-test-box.jsonl"


@pytest.fixture
def vault(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Vault:
    """The fixture vault with a Plaud recording in raw: the recording, its transcript and its notes."""
    found = copy_vault(template_vault(tmp_path_factory), tmp_path)
    found.write_run("plaud", plaud_records("transaction_polish"))
    return found


def run_file(vault: Vault) -> Path:
    (found,) = [run.path for run in vault.rawlog.run_files() if run.source == "plaud"]
    return found


# -- every route -------------------------------------------------------------------------------------------------------


def test_the_raw_log_reads_a_listed_line_as_gone(vault: Vault) -> None:
    other = RawLog(vault.root, "other-box", vault.lock)
    transcript = stored(plaud_records("transaction_polish")[1])
    assert other.contains(transcript) and POLISHED in other.rids()
    forget(vault, rids=[POLISHED])
    for log in (vault.rawlog, other):
        assert not log.contains(transcript) and POLISHED not in log.rids()
        assert log.read(POLISHED) == [] and log.latest(POLISHED) is None
        assert POLISHED not in {r.rid for _, _, r in log.iter_records()}
        assert log.latest(PLAUD) is not None


def test_the_raw_log_reads_a_run_file_changed_in_place_again(vault: Vault) -> None:
    log = RawLog(vault.root, "other-box", vault.lock)
    notes_record = stored(plaud_records("transaction_polish")[2])
    assert log.contains(notes_record)
    path = run_file(vault)
    kept = [line for line in path.read_text(encoding="utf-8").splitlines() if "/summary" not in line]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
    assert not log.contains(notes_record)


def test_a_raw_log_with_no_raw_folder_lists_no_run_file(vault: Vault, tmp_path: Path) -> None:
    for name in ("no-raw", "raw-a-file"):
        root = tmp_path / name
        root.mkdir()
        if name == "raw-a-file":
            (root / "raw").write_text("", encoding="utf-8")
        log = RawLog(root, "other-box", vault.lock)
        assert log.stamped() == [] and log.run_files() == [] and not log.contains(stored(make_record(1)))


def test_the_lookup_s_load_reads_a_listed_line_as_gone(vault: Vault) -> None:
    forget(vault, rids=[POLISHED])
    lookup = RawLookup(vault.root)
    assert lookup.versions(POLISHED) == [] and not lookup.has(POLISHED)
    assert [r.rid for r in lookup.under(PLAUD)] == [f"{PLAUD}/summary"]
    assert lookup.nested(PLAUD) == [PLAUD, f"{PLAUD}/summary"]
    assert lookup.transcripts(PLAUD) == []


def test_status_counts_no_listed_line(vault: Vault) -> None:
    before = vault.status().records["plaud"]
    forget(vault, rids=[PLAUD])
    assert before == 3 and vault.status().records["plaud"] == 0


def test_check_never_names_a_listed_line(vault: Vault) -> None:
    twice = [stored(make_record(7)), stored(make_record(7)), stored(make_record(8)), stored(make_record(8))]
    write_raw_file(vault.root, "raw/chat/2026/10/2026-10-14T1300-test-box.jsonl", twice)
    forget(vault, rids=[twice[0].rid])
    messages = [str(p) for p in vault.check()]
    assert any(twice[2].rid in m for m in messages)
    assert not any(twice[0].rid in m for m in messages)


def _cut(text: str) -> RawRecord:
    return stored(make_record(1, rid="calls:rec-0700", source="calls", kind="transcript", container=None, text=text))


def test_the_cut_scan_skips_a_listed_cut_version_and_reads_a_changed_file_again(vault: Vault) -> None:
    cut = _cut(f"Ben: hello {CUT_MARKER}")
    vault.write_run("calls", [cut])
    gate = Gate(vault)
    gate.stage([_cut("Ben: hello, and the rest")])
    # A cut version in raw makes a new version wait for the owner's hand.
    assert set(gate.list(source="calls").why.values()) == {"by-hand"}
    forget(vault, versions=[cut])
    assert set(gate.list(source="calls").why.values()) == {"mixed"}
    other = Gate(vault)
    vault.write_run("calls", [_cut(f"Ben: hello again {CUT_MARKER}")])
    assert set(other.list(source="calls").why.values()) == {"by-hand"}
    runs = [run.path for run in vault.rawlog.run_files() if run.source == "calls"]
    (path,) = [p for p in runs if "hello again" in p.read_text(encoding="utf-8")]
    path.write_text("", encoding="utf-8")
    assert set(other.list(source="calls").why.values()) == {"mixed"}


CALL = "plaud:rec-follow"
UNDER = {"source": "plaud", "container": CALL, "thread": CALL, "author": None}
SAID = [
    {"start_ms": 0, "end_ms": 9000, "speaker": "Ada Example", "text": "The crane passed its load test."},
    {"start_ms": 10_000, "end_ms": 20_000, "speaker": "Ben Sample", "text": "My dentist moved to Friday."},
]


def test_a_forgotten_family_gets_no_whisper_version_from_the_follow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config()
    config.sources = {"plaud": SourceConfig()}
    vault = Vault.init(tmp_path / "vault", config, "test-box")
    cursors.save(vault, "plaud", cursors.Cursor())
    transcript = f"{CALL}/transaction_polish"
    said = "\n".join(f"{s['speaker']}: {s['text']}" for s in SAID)
    heard = make_record(
        rid=f"{CALL}/whisper", kind="transcript", segments=SAID, text=said, via=f"{TRANSCRIBED_VIA} {'ab' * 32}",
        fidelity="derived", engine="whisper-fake@0123456", **UNDER,
    )  # fmt: skip
    gate = Gate(vault)
    gate.stage([make_record(rid=CALL, kind="recording", title="Crane check", text="", **UNDER)])
    gate.stage([make_record(rid=transcript, kind="transcript", segments=SAID, text=said, **UNDER), heard])
    gate.promote()
    # A later version of the transcript, cut by hand: without the list, the follow would cut Whisper's to match. It is
    # staged before the forget, since intake drops it after.
    newer = [*SAID, {"start_ms": 30_000, "end_ms": 31_000, "speaker": "Ada Example", "text": "Bye."}]
    gate.stage(
        [make_record(rid=transcript, kind="transcript", segments=newer, text=said + "\nAda Example: Bye.", **UNDER)]
    )
    gate.classify(transcript, "work", cuts=[Cut(start=10_000, end=20_000, unit="ms")])
    forget(vault, rids=[CALL])
    # The apply off, so the listed lines stay in the run files as a merge brings one back: the cut scan reads them.
    monkeypatch.setattr(forget_apply, "apply", lambda vault, keys, first=False: forget_apply.Applied())
    # followed == 0 alone would hold through RawLookup too: the follow reads each family's latest version through it,
    # and it skips the listed Whisper line.
    assert gate.promote().followed == 0
    # What pins the cut scan's own skip: it never read the forgotten Whisper transcript's marks, so the follow had no
    # family to cut.
    assert f"{CALL}/whisper" not in gate._seen.latest  # pyright: ignore[reportPrivateUsage]
    lines = [line for run in vault.rawlog.stamped() for line in run[0].path.read_text(encoding="utf-8").splitlines()]
    assert sum(f'"rid":"{CALL}/whisper"' in line for line in lines) == 1


def test_the_notes_import_names_no_listed_record(vault: Vault) -> None:
    def named(v: Vault) -> set[str]:
        latest = notes._latest(v)  # pyright: ignore[reportPrivateUsage]
        return set(notes.Names(latest, ZoneInfo("Europe/Budapest")).light)

    assert POLISHED in named(vault)
    forget(vault, rids=[POLISHED])
    assert POLISHED not in named(vault) and PLAUD in named(vault)


def test_the_scan_holds_no_forgotten_file_or_its_parts(vault: Vault) -> None:
    sha = "c0" * 32

    def record(rid: str, kind: str, **native: object) -> RawRecord:
        base = {"rid": rid, "source": "docs", "kind": kind, "via": documents.VIA, "ts": AT, "fetched": AT}
        return stored(RawRecord.model_validate({**base, "native": native, "text": "said"}))

    copy = [record("docs:aaaaaaaaaaaa", "document", sha256=sha), record("docs:aaaaaaaaaaaa/page1", "page")]
    other = [record("docs:bbbbbbbbbbbb", "document", sha256="e2" * 32), record("docs:bbbbbbbbbbbb/page1", "page")]
    write_raw_file(vault.root, "raw/docs/2026/10/2026-10-18T0900-test-box.jsonl", [*copy, *other])
    forget(vault, files=[f"sha256:{sha}"])
    held = scan._Holdings(vault, "docs")  # pyright: ignore[reportPrivateUsage]
    assert set(held.newest) == {"docs:bbbbbbbbbbbb", "docs:bbbbbbbbbbbb/page1"}


# -- every opener of a run file is one of these ------------------------------------------------------------------------

# The calls that find or open run files.
OPENERS = {"parse_run_file", "run_files", "stamped", "run_stamps", "uncommitted_runs", "iter_records", "read_run"}
# Each place that makes one, and what it does with what it finds: skips through the list, or never reads a line.
PLACES = {
    ("vault/rawlog.py", "RawLog.stamped"): "lists run files",
    ("vault/rawlog.py", "RawLog.run_files"): "lists run files",
    ("vault/rawlog.py", "RawLog._refresh"): "skips: contains, rids and write_run's own check",
    ("vault/rawlog.py", "RawLog.iter_records"): "skips: read, latest and every caller of it",
    ("vault/rawlog.py", "RawLog.read"): "through iter_records",
    ("vault/spans.py", "RawLookup._source"): "skips: the lookup's load",
    ("index/build.py", "Indexer._order"): "a run file's place in run order, from its name",
    ("index/build.py", "Indexer.add_runs"): "skips: the index build",
    ("index/build.py", "_rebuild"): "lists run files for add_runs",
    ("index/build.py", "_changed_runs"): "sizes and times only",
    ("index/lookup.py", "ReadIndex._raw_check"): "sizes and times only",
    ("index/lookup.py", "ReadIndex._order"): "a run file's place in run order, from its name",
    ("vault/vault.py", "Vault.commit"): "names run files to commit",
    ("vault/vault.py", "Vault.check"): "skips: a listed line is never named",
    ("vault/vault.py", "Vault.status"): "skips: the counts",
    ("vault/gate.py", "Gate._refresh_cuts"): "skips: the cut scan",
    ("vault/gate.py", "Gate._uncommitted_runs_with"): "looks for a rid in what the next commit takes, listed or not",
    ("documents/scan.py", "_Holdings.__init__"): "skips: the folder scan's holdings",
    ("imports/notes.py", "_latest"): "through RawLog.iter_records",
    ("forget/apply.py", "_apply"): "skips: takes the listed lines out of the run files that moved",
    ("forget/plan.py", "_held"): "through RawLog.iter_records",
    ("forget/plan.py", "_After.__init__"): "through RawLog.iter_records, under the list with the forget's line",
}


class _Calls(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.stack: list[str] = []
        self.found: set[tuple[str, str]] = set()

    def _scoped(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _scoped  # noqa: N815 - ast's own names

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if name in OPENERS and self.stack:
            self.found.add((self.rel, ".".join(self.stack)))
        self.generic_visit(node)


def test_every_place_that_opens_a_run_file_is_known() -> None:
    found: set[tuple[str, str]] = set()
    for path in sorted(SRC.rglob("*.py")):
        visitor = _Calls(path.relative_to(SRC).as_posix())
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        found |= visitor.found
    assert found == set(PLACES)
