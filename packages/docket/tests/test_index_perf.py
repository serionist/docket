"""A year's worth of chunks: queries answer well under a second without models.

The chunks are synthetic (a seeded vocabulary with Zipf frequencies, in English- and Hungarian-looking words), written
through the build's own insert path. Wall times are asserted only when no tracer runs: under coverage they are printed,
and the fuzzy walk's work is counted instead.
"""

from __future__ import annotations

import itertools
import random
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docket_testkit import index_config, template_with

from docket.index import build, matching, search
from docket.index.chunks import Chunk, iso
from docket.index.schema import create
from docket.index.text import Analyzer
from docket.vault.entities import Alias, Entities, Entity
from docket.vault.vault import Vault

CHUNKS = 100_000
WORDS_PER_CHUNK = 60
VOCAB = 40_000
ENTITIES = 300
ASR = 100
SECONDS = 1.0
NOW = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
SYLLABLES = [
    "ka", "ta", "mi", "né", "ro", "szé", "lo", "ber", "ga", "tó", "ül", "ve", "ri", "sa", "po", "lin", "dor", "fe",
    "kő", "mun", "var", "ten", "ish", "ack", "ple", "ond",
]  # fmt: skip


def traced() -> bool:
    if sys.gettrace() is not None:
        return True
    try:
        import coverage
    except ImportError:  # pragma: no cover - coverage comes with the dev tools
        return False
    return coverage.Coverage.current() is not None


def vocabulary(rng: random.Random, size: int) -> list[str]:
    words: set[str] = set()
    while len(words) < size:
        words.add("".join(rng.choice(SYLLABLES) for _ in range(rng.randint(2, 4))))
    return sorted(words)


def make_perf_vault(root: Path) -> Vault:
    vault = Vault.init(root / "vault", index_config(), "test-box")
    rng = random.Random(2026)  # noqa: S311 - seeded test words, not secrets
    vocab = vocabulary(rng, VOCAB)
    rng.shuffle(vocab)
    cumulative = list(itertools.accumulate(1 / (i + 1) for i in range(len(vocab))))
    db = build.db_path(vault)
    db.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db, isolation_level=None)
    langs = build.languages_of(vault)
    create(conn, langs)
    conn.execute("BEGIN")
    indexer = build.Indexer(conn, vault, Analyzer(langs), collect=False)
    start = NOW - timedelta(minutes=CHUNKS * 5)
    chunks = []
    for n in range(CHUNKS):
        text = " ".join(rng.choices(vocab, cum_weights=cumulative, k=WORDS_PER_CHUNK))
        source = ("calls", "chat", "mail")[n % 3]
        rid = f"{source}:perf-{n}"
        chunks.append(
            Chunk(
                owner=f"r {rid}", ordinal=0, kind="record", grp=rid, text=text, locator=f"r:{rid}", source=source,
                ts=iso(start + timedelta(minutes=n * 5)), rid=rid, rids=[rid],
            )
        )  # fmt: skip
    indexer.add_chunks(chunks)
    rare = vocab[5000 : 5000 + ENTITIES]
    entities = [
        Entity(
            id=f"e{n}", type="person" if n % 2 else "project", path=f"entities/x/e{n}.yaml", names={"en": name},
            aliases=(Alias(name + "x", "asr"),) if n < ASR else (),
        )
        for n, name in enumerate(rare)
    ]  # fmt: skip
    matching.write_entities(conn, Entities(entities, [], []), {})
    matching.refresh(conn, Analyzer(langs), full=True)
    conn.execute("COMMIT")
    conn.execute("INSERT INTO chunks_fts (chunks_fts) VALUES ('optimize')")
    conn.close()
    return vault


@pytest.fixture(scope="module")
def vault(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    started = time.monotonic()
    root = template_with(tmp_path_factory, make_perf_vault)
    print(f"\nbuilt {CHUNKS} chunks in {time.monotonic() - started:.1f} s")
    return Vault.open(root)


def timed(vault: Vault, query: str, **kwargs: object) -> tuple[float, int]:
    started = time.monotonic()
    result = search(vault, query, now=NOW, **kwargs)  # type: ignore[arg-type]
    return time.monotonic() - started, sum(len(g.hits) for g in result.groups)


def test_queries_answer_in_well_under_a_second(vault: Vault) -> None:
    conn = sqlite3.connect(build.db_path(vault))
    by_doc = conn.execute("SELECT term, doc FROM vocab WHERE col = 'folded' ORDER BY doc DESC").fetchall()
    names = [r[0] for r in conn.execute("SELECT text FROM aliases WHERE kind = 'name' LIMIT 2")]
    conn.close()
    common, rare = by_doc[0][0], by_doc[len(by_doc) // 2][0]
    shapes: dict[str, tuple[str, dict[str, object]]] = {
        "one word": (rare, {}),
        "three words": (" ".join(t for t, _ in by_doc[100:103]), {}),
        "a phrase": (f'"{by_doc[1][0]} {by_doc[2][0]}"', {}),
        "a common word": (common, {}),
        "a rare word": (by_doc[-1][0], {}),
        "any of them": (f"{rare} {by_doc[-1][0]} {by_doc[-2][0]}", {}),
        "a prefix": (rare[:-1], {}),
        "an entity": (names[0], {}),
        "a boost": (rare, {"people": [names[1]]}),
        "a source": (common, {"sources": ["mail"]}),
        "a time word": ("last week", {}),
        "a time filter": (f"{common} múlt héten", {}),
    }
    for name, (query, kwargs) in shapes.items():
        seconds, found = timed(vault, query, **kwargs)
        print(f"{name:15} {seconds * 1000:7.1f} ms  {found} hits  {query!r}")
        assert found, name
        if not traced():
            assert seconds < SECONDS, name
    result = search(vault, f"{common} múlt héten", now=NOW)
    since, until = result.query.since, result.query.until
    assert since is not None and until is not None
    assert all(h.ts is not None and since <= h.ts < until for g in result.groups for h in g.hits)


def test_the_fuzzy_walk_over_a_large_vocabulary() -> None:
    rng = random.Random(7)  # noqa: S311 - seeded test words, not secrets
    vocab = vocabulary(rng, 300_000)
    seeds = [w + "x" for w in rng.sample(vocab, ASR)]
    work = [0]
    started = time.monotonic()
    found = sum(len(matching.within(seed, matching.max_distance(seed), vocab, work)) for seed in seeds)
    seconds = time.monotonic() - started
    print(f"\nwalked {ASR} words over {len(vocab)} in {seconds:.1f} s: {work[0]} rows, {found} found")
    assert found >= ASR
    assert work[0] < ASR * sum(len(w) for w in vocab) / 20
    if not traced():
        assert seconds < 10
