"""The history rewrite a forget writes for a fresh clone and never runs (DR-0594), and `--check-rewrite`, which applies
it to every branch tip read-only: it refuses a change to HEAD or outside the vault, and names each older branch.

`<private>/forget/<stamp>/`, the stamp a UTC time with no colon so the folder is valid on Windows:

| File | Holds |
|---|---|
| `lines.txt` | `--replace-text` expressions, each blanking one raw run file line on a rid forgotten whole |
| `scoped.txt`, `scoped.py` | every other line, by blob and line hash, and the `--blob-callback` blanking them |
| `blobs.txt` | for `--strip-blobs-with-ids`: every blob of each removed doc, and each removed media file's pointer |

A rid forgotten whole never comes back, so its run files' lines are matched by their leading `rid` field. Every other
line is blanked only in the blobs that held it: a version forgotten alone, which a release can write again just as it
was; each doc line the docs' rule drops in any blob any doc's file has had, deleted docs' too, each judged on its own
rev once its bytes name the family or cite a doc built on it; and each line of the vault's docs holding an `[r:` token
on a rid forgotten whole (DR-0598). So is each line of an op the list masks that isn't its mask, in any blob any op file
has had, deleted files' too, read raw and only in the blobs whose bytes hold a listed op's id (DR-0697): HEAD holds
only masks once the apply committed. Files outside the vault are the owner's own, as a copy anywhere else is: only a
copy of a forgotten record's own raw line there is blanked.

Nothing in them is text from the family: rids and versions are ids, lines are named by their hashes.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import subprocess
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from docket.childproc import console_flags
from docket.errors import DocketError
from docket.forget import apply as forget_apply
from docket.forget.plan import DocCut, Plan
from docket.ledger import ops as ledger_ops
from docket.media import layout
from docket.vault import forgetdocs, forgotten
from docket.vault.anchors import encode_rid
from docket.vault.docstore import DocStore
from docket.vault.processed import ProcessedDoc, doc_path, parse_doc
from docket.vault.spans import RawLookup
from docket.vault.staging import rid_hash
from docket.vault.vault import Vault

FOLDER = "forget"
LINES = "lines.txt"
SCOPED = "scoped.txt"
CALLBACK = "scoped.py"
BLOBS = "blobs.txt"
FILES = (LINES, SCOPED, CALLBACK, BLOBS)
# filter-repo leaves a blob alone when its first bytes hold a NUL, as a binary file's do.
BINARY_PEEK = 8192
# A tree entry or a raw diff's side that names nothing: a file added, or deleted.
NO_BLOB = "0" * 40
CALLBACK_BODY = """# Docket's scoped blanking (DR-0594): each blob the table names loses the lines whose sha256 it
# lists, keeping their newlines. Read once, from the file beside this one.
import contextlib
import hashlib

table = globals().get("_docket_scoped")
if table is None:
    table = {}
    with open({path!r}, "rb") as found:
        for row in found.read().split(b"\\n"):
            if row.strip():
                blob_id, digest = row.split()
                table.setdefault(blob_id, set()).add(digest)
    globals()["_docket_scoped"] = table
wanted = table.get(blob.original_id)
if wanted:
    blob.data = b"\\n".join(
        b"" if hashlib.sha256(line).hexdigest().encode() in wanted else line for line in blob.data.split(b"\\n")
    )
"""


def stamp(at: datetime) -> str:
    return f"{at.astimezone(UTC):%Y%m%dT%H%M%SZ}"


def line_hash(line: str | bytes) -> str:
    return hashlib.sha256(line if isinstance(line, bytes) else line.encode("utf-8")).hexdigest()


def _json(text: str) -> str:
    """A string as a run file's line holds it, inside its quotes."""
    return json.dumps(text, ensure_ascii=False)[1:-1]


def expressions(plan: Plan) -> list[str]:
    """`--replace-text` expressions, each blanking one raw run file line on a rid forgotten whole, by its leading `rid`
    field, and keeping its newline."""
    return [f'regex:(?m)^\\{{"rid":"{re.escape(_json(rid))}(?:/[^"]*)?".*$==>' for rid in sorted(plan.rids)]


def _token(rids: Iterable[str]) -> re.Pattern[bytes] | None:
    """A doc line's own `[r:` token on one of these rids or a rid under it, as its file's bytes hold it."""
    names = [re.escape(encode_rid(rid).encode("utf-8")) for rid in sorted(rids)]
    if not names:
        return None
    return re.compile(rb"\[r:(?:" + b"|".join(names) + rb")(?:[/@#][^\]]*)?\]")


def raw_rows(vault: Vault, plan: Plan) -> set[tuple[str, str]]:
    """Each version forgotten alone, by every blob its run file has had on any branch and its line's hash, read before
    the apply takes it out: what the rewrite blanks there and nowhere else. A run file changes only by a forget or a
    merge, so its history is a few blobs."""
    if not plan.pairs:
        return set()
    refs: list[str] = []
    for rel in sorted(plan.runs):
        commits = vault.git.out("log", "--all", "--no-show-signature", "--format=%H", "--", rel).split()
        refs += [f"{sha}:./{rel}" for sha in commits]
    rows: set[tuple[str, str]] = set()
    blobs = sorted(set(_blob_ids(vault, refs).values()))
    for blob, data in zip(blobs, _cat(vault, blobs), strict=True):
        for line in data.split(b"\n"):
            if _pair(line) in plan.pairs:
                rows.add((blob, line_hash(line)))
    return rows


def _pair(line: bytes) -> tuple[object, object] | None:
    """A run file line's rid and version, or None for a line that isn't a record's."""
    try:
        found: object = json.loads(line)
    except ValueError:
        return None
    return (found.get("rid"), found.get("v")) if isinstance(found, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]


@dataclass(frozen=True)
class Written:
    folder: Path
    expressions: int
    scoped: int
    blobs: int


def _blob_ids(vault: Vault, refs: Iterable[str]) -> dict[str, str]:
    """Each `<commit>:./<path>` that names a blob, with the blob's id, from one batch."""
    wanted = list(dict.fromkeys(refs))
    if not wanted:
        return {}
    proc = vault.git.run("cat-file", "--batch-check=%(objectname) %(objecttype)", stdin="\n".join(wanted) + "\n")
    out: dict[str, str] = {}
    for ref, row in zip(wanted, proc.stdout.split("\n"), strict=False):
        fields = row.split()
        if len(fields) == 2 and fields[1] == "blob":
            out[ref] = fields[0]
    return out


def doc_blobs(vault: Vault) -> dict[str, set[str]]:
    """Every blob each doc's file has had in any commit on any ref, merges' sides included, by its path in the vault:
    the vault's doc blobs, which the rewrite reads and nothing outside them."""
    return blobs_under(vault, "processed")


def blobs_under(vault: Vault, folder: str) -> dict[str, set[str]]:
    """Every blob each file under a folder of the vault has had in any commit on any ref, merges' sides included, a
    deleted file's too, by its path in the vault."""
    git = vault.git
    out = git.out("log", "--all", "-m", "--no-renames", "--format=", "--raw", "--no-abbrev", "-z", "--", folder)
    found: dict[str, set[str]] = {}
    fields = out.split("\0")
    for n, info in enumerate(fields[:-1]):
        parts = info.strip("\n").split()
        if len(parts) != 5 or not parts[0].startswith(":") or parts[1] not in ("100644", "100755"):
            continue
        blob = parts[3]
        if blob != NO_BLOB:
            found.setdefault(fields[n + 1].removeprefix(git.prefix), set()).add(blob)
    return found


def op_rows(vault: Vault, ids: Collection[str]) -> set[tuple[str, str]]:
    """Each line of a listed op that isn't its mask, by every blob an op file has had on any ref, deleted files' too,
    and the line's hash (DR-0697): read raw, never through the masking parse, and only in a blob whose bytes hold a
    listed id. A blob that isn't UTF-8 is left, as every reader of op files leaves it."""
    if not ids:
        return set()
    needle = re.compile(b"|".join(re.escape(op_id.encode("ascii")) for op_id in sorted(ids)))
    blobs = sorted({blob for found in blobs_under(vault, ledger_ops.OPS_DIR).values() for blob in found})
    rows: set[tuple[str, str]] = set()
    for blob, data in zip(blobs, _cat(vault, blobs), strict=True):
        if needle.search(data) is None:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        lines = data.split(b"\n")
        rows |= {(blob, line_hash(lines[n - 1])) for n, raw, mask in ledger_ops.listed_lines(text, ids) if raw != mask}
    return rows


@dataclass(frozen=True)
class Before:
    """What the rewrite needs from HEAD before the apply takes it out: each removed media file's pointer blob, and each
    version forgotten alone by its run file's blob and its line's hash."""

    pointers: frozenset[str] = frozenset()
    raw: frozenset[tuple[str, str]] = frozenset()


def before(vault: Vault, plan: Plan) -> Before:
    """Read from HEAD, before the apply."""
    pointers = _blob_ids(vault, [f"HEAD:./{rel}" for rel in sorted(plan.stored)])
    return Before(frozenset(pointers.values()), frozenset(raw_rows(vault, plan)))


def _earlier(base: Path, folder: Path) -> tuple[list[str], set[tuple[str, str]], set[str]]:
    """What the earlier forgets' folders on this clone hold: their blob ids name the history as it was before any
    rewrite, so the newest folder holds them all, and is the one to run."""
    expressions: list[str] = []
    scoped: set[tuple[str, str]] = set()
    stripped: set[str] = set()
    for earlier in sorted(p for p in base.iterdir() if p.is_dir() and p != folder and (p / LINES).is_file()):
        with contextlib.suppress(DocketError):
            files = _read(earlier)
            expressions += [e for e in (earlier / LINES).read_text(encoding="utf-8").split("\n") if e.strip()]
            scoped |= {(blob, digest) for blob, found in files.scoped.items() for digest in found}
            stripped |= files.stripped
    return expressions, scoped, stripped


class _Judged:
    """The docs' rule on a doc as one of its blobs holds it (DR-0580): raw as the forget leaves it, and what a ref
    leads to read through the store, an earlier rev from git, each doc and rev read once for every blob judged."""

    def __init__(self, vault: Vault, plan: Plan) -> None:
        self.store = DocStore(vault)
        self.listed = forgotten.load(vault.root, vault.home)
        # The family's rids count as taken, so a version forget's rid reads as gone even before the apply's state.
        taken = {*forget_apply.skipped(vault.home), *(rid_hash(rid) for rid in plan.family)}
        self.raw = forget_apply.Left(RawLookup(vault.root), self.listed, taken)
        self._current: dict[str, ProcessedDoc | None] = {}
        self._older: dict[tuple[str, int], ProcessedDoc | None] = {}

    def current(self, wanted: Collection[str]) -> dict[str, ProcessedDoc]:
        for doc_id in set(wanted) - set(self._current):
            try:
                self._current[doc_id] = self.store.get(doc_id)
            except DocketError:
                self._current[doc_id] = None
        return {doc_id: doc for doc_id in wanted if (doc := self._current[doc_id]) is not None}

    def older(self, wanted: Collection[tuple[str, int]]) -> dict[tuple[str, int], ProcessedDoc]:
        for key in set(wanted) - set(self._older):
            try:
                self._older[key] = self.store.at_rev(*key)
            except DocketError:
                self._older[key] = None
        return {key: doc for key in wanted if (doc := self._older[key]) is not None}

    def dropped(self, data: bytes, cut: DocCut | None) -> set[int]:
        """The numbers of the lines the rule drops from this blob, judged on its own rev; for a blob that doesn't read
        as a doc, the lines holding the very words the forget dropped from the current rev of a doc it touched."""
        lines = data.split(b"\n")
        try:
            doc = parse_doc(data.decode("utf-8"), lenient=True)
        except (DocketError, UnicodeDecodeError):
            hashes = cut.hashes if cut is not None else frozenset[str]()
            return {n for n, line in enumerate(lines, start=1) if line_hash(line) in hashes}
        fate = forgetdocs.fates([doc], self.raw, self.listed, current=self.current, older=self.older).get(doc.meta.id)
        return set(fate.dropped) if fate is not None else set()


# A doc-line ref's cited doc, as a blob's bytes hold it: what the pre-filter follows.
_CITED = re.compile(rb"\[d:([^@#\]\s]+)[@#]")


def _doc_id(rel: str) -> str:
    """A doc's id from its path in the vault, as `doc_path` builds the path from it."""
    parts = PurePosixPath(rel).parts
    return f"{parts[1]}:{PurePosixPath(rel).stem}" if len(parts) > 2 else rel


def _needles(plan: Plan) -> list[bytes]:
    """What a blob holds when a line of it may read on the family: a family rid, or a rid one sits under, as a covers
    line holds it and as an anchor's token does."""
    rids = {owner for rid in {*plan.family, *plan.rids} for owner in layout.owners(rid)}
    return sorted({form.encode("utf-8") for rid in rids for form in (rid, encode_rid(rid))})


def write(vault: Vault, plan: Plan, held: Before, at: datetime) -> Written:
    """The rewrite's files for this forget, with every earlier forget's on this clone: each blob of the vault's docs,
    deleted docs' too, in every commit on every ref, HEAD's before the apply included, read once, and those the bytes
    pre-filter keeps read again to be judged, so a forget whose apply failed or left a doc for its owner still writes
    them; `held` is what HEAD held before the apply."""
    base = vault.home.private / FOLDER
    base.mkdir(parents=True, exist_ok=True)
    folder = base / stamp(at)
    # Two forgets in one second: the later one's folder takes the next number, and still sorts after.
    n = 1
    while folder.exists():
        n += 1
        folder = base / f"{stamp(at)}-{n}"
    folder.mkdir()
    found, scoped, stripped = _earlier(base, folder)
    scoped |= held.raw
    stripped |= held.pointers
    cuts = {doc_path(c.id).as_posix(): c for c in plan.docs}
    by_blob: dict[str, set[str]] = {}
    for rel, blobs in doc_blobs(vault).items():
        for blob in blobs:
            by_blob.setdefault(blob, set()).add(rel)
    token = _token(plan.rids)
    needles = _needles(plan)
    # Every doc blob once, deleted docs' too: the token lines, and what the pre-filter needs of each.
    cites: dict[str, set[str]] = {}
    named: set[str] = set()
    order = sorted(by_blob)
    for blob, data in zip(order, _cat(vault, order), strict=True):
        if any(cuts[rel].removed for rel in by_blob[blob] if rel in cuts):
            stripped.add(blob)
            continue
        if token is not None:
            scoped |= {(blob, line_hash(line)) for line in data.split(b"\n") if token.search(line)}
        cites[blob] = {m.group(1).decode("utf-8", errors="replace") for m in _CITED.finditer(data)}
        if any(needle in data for needle in needles):
            named.add(blob)
    # The blobs the rule can drop a line from: a touched doc's, one naming the family, and one citing a doc among
    # those, to a fixed point, so an earlier rev built on a dropped line goes though its doc's HEAD rev cites none.
    affected = {c.id for c in plan.docs}
    candidates = {b for b in cites if b in named or any(rel in cuts for rel in by_blob[b])}
    while True:
        affected |= {_doc_id(rel) for b in candidates for rel in by_blob[b]}
        more = {b for b, ids in cites.items() if b not in candidates and ids & affected}
        if not more:
            break
        candidates |= more
    judged = _Judged(vault, plan)
    order = sorted(candidates)
    for blob, data in zip(order, _cat(vault, order), strict=True):
        cut = next((cuts[rel] for rel in sorted(by_blob[blob]) if rel in cuts), None)
        lines = data.split(b"\n")
        scoped |= {(blob, line_hash(lines[n - 1])) for n in judged.dropped(data, cut) if 0 < n <= len(lines)}
    # Every op the list masks, this forget's and the earlier ones', which an ops-only line may have listed since.
    scoped |= op_rows(vault, forgotten.load(vault.root, vault.home).ops | plan.ledger.new)
    found = list(dict.fromkeys([*found, *expressions(plan)]))
    (folder / LINES).write_text("".join(f"{e}\n" for e in found), encoding="utf-8", newline="\n")
    (folder / SCOPED).write_text("".join(f"{b} {h}\n" for b, h in sorted(scoped)), encoding="utf-8", newline="\n")
    body = CALLBACK_BODY.replace("{path!r}", repr((folder / SCOPED).as_posix()))
    (folder / CALLBACK).write_text(body, encoding="utf-8", newline="\n")
    (folder / BLOBS).write_text("".join(f"{b}\n" for b in sorted(stripped)), encoding="utf-8", newline="\n")
    return Written(folder, len(found), len(scoped), len(stripped))


def history_lines(vault: Vault, written: Written) -> list[str]:
    """What the forget prints last: the history keeps the text, and how to take it out, in order."""
    folder = written.folder
    repo = vault.home.top
    fresh = repo.parent / f"{repo.name}-rewrite"
    run = (
        f'git filter-repo --replace-text "{folder / LINES}" --blob-callback "{folder / CALLBACK}" '
        f'--strip-blobs-with-ids "{folder / BLOBS}"'
    )
    return [
        f"history   the text stays in the history of {repo}: Docket never rewrites it. To take it out:",
        "  the files hold every earlier forget's on this clone too: run only the newest folder's",
        "  1. check it leaves HEAD and your files outside the vault as they are, and which branches to merge the "
        f'forget into first: docket forget --check-rewrite "{folder}" --vault "{vault.root}"',
        "  2. back the repo up",
        f'  3. git clone --no-local "{repo}" "{fresh}", then in "{fresh}": {run}',
        "     never in the working repo: filter-repo refuses a clone that isn't fresh, and --force there risks your "
        "uncommitted work",
        "  4. filter-repo rewrites every branch and tag the clone has and removes its origin remote: add it back "
        f'(git remote add origin <url>) in "{fresh}", force-push every branch and tag (git push --force --all, then '
        "--tags), then clone again on every machine, this one too",
        "  a branch only the remote has isn't in the clone and keeps the text: check it out here before step 1, or "
        "delete it there",
        "  the remote keeps the commits a force-push leaves behind, reachable by their ids: on GitHub until its "
        "support purges them, and in forks and pull request refs",
        "  git filter-repo isn't Docket's dependency: install it to run this",
    ]


# -- the check ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Files:
    regexes: list[tuple[re.Pattern[bytes], bytes]]
    scoped: dict[str, set[str]]
    stripped: set[str]


def _read(folder: Path) -> _Files:
    """The rewrite's files, the expressions as filter-repo's Python reads them."""
    missing = [name for name in (LINES, SCOPED, BLOBS) if not (folder / name).is_file()]
    if missing:
        raise DocketError(f"{folder} isn't a forget's rewrite folder: it lacks {', '.join(missing)}")
    regexes: list[tuple[re.Pattern[bytes], bytes]] = []
    for raw in (folder / LINES).read_bytes().split(b"\n"):
        if not raw.strip():
            continue
        pattern, _, replacement = raw.rpartition(b"==>") if b"==>" in raw else (raw, b"", b"***REMOVED***")
        if pattern.startswith(b"regex:"):
            regexes.append((re.compile(pattern[len(b"regex:") :]), replacement))
        else:
            regexes.append((re.compile(re.escape(pattern.removeprefix(b"literal:"))), replacement))
    scoped: dict[str, set[str]] = {}
    for row in (folder / SCOPED).read_text(encoding="utf-8").split("\n"):
        if row.strip():
            blob, digest = row.split()
            scoped.setdefault(blob, set()).add(digest)
    stripped = {row.strip() for row in (folder / BLOBS).read_text(encoding="utf-8").split("\n") if row.strip()}
    return _Files(regexes, scoped, stripped)


def _rewritten(data: bytes, blob: str, files: _Files) -> bytes:
    if blob in files.scoped:
        wanted = files.scoped[blob]
        data = b"\n".join(b"" if line_hash(line) in wanted else line for line in data.split(b"\n"))
    if b"\0" in data[:BINARY_PEEK]:
        return data
    for regex, replacement in files.regexes:
        data = regex.sub(replacement, data)
    return data


def _tree(vault: Vault, commit: str) -> list[tuple[str, str]]:
    """A commit's whole tree, the repo's as filter-repo rewrites it: (blob, path) for each file."""
    listing = vault.git.out("ls-tree", "-r", "-z", "--full-tree", commit)
    entries: list[tuple[str, str]] = []
    for entry in listing.split("\0"):
        info, _, path = entry.partition("\t")
        fields = info.split()
        if len(fields) == 3 and fields[1] == "blob":
            entries.append((fields[2], path))
    return entries


def _tips(vault: Vault) -> list[tuple[str, str]]:
    """HEAD and each branch tip, (name, commit), HEAD first with no name: what a fresh clone of the repo has, every
    branch of which the rewrite rewrites."""
    head = vault.git.out("rev-parse", "--verify", "HEAD").strip()
    tips = [("", head)]
    for row in vault.git.out("for-each-ref", "--format=%(objectname) %(refname:short)", "refs/heads").split("\n"):
        commit, _, name = row.partition(" ")
        if name and commit != head:
            tips.append((name, commit))
    return tips


@dataclass(frozen=True)
class Checked:
    """What the rewrite would change in the trees there are now. `refused` stops it: a change to HEAD, or to a file
    outside the vault on any branch tip, the owner's own. `branches` names each other branch whose tip still holds, in
    the vault, what the forget took: merging the forget into it, or deleting it, clears it (DR-0598)."""

    refused: list[str]
    branches: dict[str, list[str]]


def check(vault: Vault, folder: Path) -> Checked:
    """Each path and line number of HEAD's tree and of each other branch tip the rewrite would change, the repo's whole
    tree as filter-repo rewrites it. A file of a branch whose blob HEAD holds at a place as much the vault's is read
    once, under HEAD. Read-only."""
    files = _read(folder)
    prefix = vault.git.prefix
    refused: list[str] = []
    branches: dict[str, list[str]] = {}
    seen: set[tuple[str, bool]] = set()
    for name, commit in _tips(vault):
        entries = [(b, path) for b, path in _tree(vault, commit) if (b, _outside(path, prefix)) not in seen]
        seen |= {(b, _outside(path, prefix)) for b, path in entries}
        for (blob, path), data in zip(entries, _cat(vault, [b for b, _ in entries]), strict=True):
            change = _change(data, blob, files)
            if change is None:
                continue
            said = f"{path}: {change}"
            if not name:
                refused.append(said)
            elif _outside(path, prefix):
                refused.append(f"{name}: {said}")
            else:
                branches.setdefault(name, []).append(said)
    return Checked(refused, branches)


def _outside(path: str, prefix: str) -> bool:
    """Whether a path from the repo's top lies outside the vault: only a folder vault has an outside."""
    return bool(prefix) and not path.startswith(prefix)


def _change(data: bytes, blob: str, files: _Files) -> str | None:
    """What the rewrite would change in one file: the whole of it, or its line numbers; None when nothing."""
    if blob in files.stripped:
        return "the whole file, whose blob the rewrite strips"
    after = _rewritten(data, blob, files)
    if after == data:
        return None
    pairs = zip(data.split(b"\n"), after.split(b"\n"), strict=False)
    return f"line {_first([str(n) for n, (old, new) in enumerate(pairs, start=1) if old != new])}"


def _first(numbers: list[str]) -> str:
    return ", ".join(numbers[:20]) + (f" and {len(numbers) - 20} more" if len(numbers) > 20 else "")


def _cat(vault: Vault, blobs: list[str]) -> Iterator[bytes]:
    """Each blob's bytes in turn, from one `git cat-file --batch` asked a blob at a time: one is held at once."""
    if not blobs:
        return
    proc = subprocess.Popen(  # noqa: S603 - git with fixed arguments, the blob ids on stdin
        ["git", f"--git-dir={vault.home.git_dir}", "cat-file", "--batch"],  # noqa: S607 - git on PATH, as everywhere
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        cwd=vault.root,
        creationflags=console_flags(),
    )
    if proc.stdin is None or proc.stdout is None:  # pragma: no cover - both are pipes, asked for above
        raise DocketError("git cat-file gave no pipes")
    try:
        for blob in blobs:
            proc.stdin.write(f"{blob}\n".encode("ascii"))
            proc.stdin.flush()
            size = int(proc.stdout.readline().split()[2])
            data = proc.stdout.read(size)
            proc.stdout.read(1)
            yield data
    finally:
        proc.stdin.close()
        proc.stdout.close()
        proc.wait()
