"""The curate miner on a year's worth of chunks: a round's mining well under the 30 seconds its budget gives it.

The chunks are synthetic, as the index's perf vault's are, but hold what the miner works on: sentences that start with
a capital, names among the words (2,000 single ones and 500 of two words, a few misheard by a letter), and every other
chunk a transcript whose turns carry a speaker's label. Wall time is asserted only when no tracer runs.

Only the timed test carries `perf`. The vault is a shared template the untimed test asks for too, so in a lane
without coverage the parallel run builds it and the timed run, alone, only times; both read it where it was built, and
the miner writes nothing (DR-0492).
"""

from __future__ import annotations

import itertools
import random
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docket_testkit import best_of, index_config, template_with, traced

from docket.curate.propose import mine
from docket.index import build, matching
from docket.index.chunks import Chunk, iso
from docket.index.schema import create
from docket.index.text import Analyzer
from docket.vault.curatelog import fold
from docket.vault.entities import Entities
from docket.vault.vault import Vault

CHUNKS = 100_000
WORDS_PER_CHUNK = 60
SENTENCE = 12
VOCAB = 40_000
NAMES = 2_000
FULL_NAMES = 500
NAME_SHARE = 0.03
SECONDS = 30.0
SYLLABLES = ["ka", "lo", "mi", "ter", "sza", "ne", "ro", "bel", "fa", "gyo", "an", "ve", "tu", "kor", "pi", "das"]
NOW = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)


def _word(rng: random.Random, low: int, high: int) -> str:
    return "".join(rng.choice(SYLLABLES) for _ in range(rng.randint(low, high)))


def _text(rng: random.Random, vocab: list[str], weights: list[float], names: list[str]) -> str:
    words = [
        rng.choice(names) if rng.random() < NAME_SHARE else w
        for w in rng.choices(vocab, cum_weights=weights, k=WORDS_PER_CHUNK)
    ]
    sentences = [words[i : i + SENTENCE] for i in range(0, len(words), SENTENCE)]
    return " ".join(" ".join([s[0][:1].upper() + s[0][1:], *s[1:]]) + "." for s in sentences)


def make_named_vault(root: Path) -> Vault:
    vault = Vault.init(root / "vault", index_config(), "test-box")
    rng = random.Random(2026)  # noqa: S311 - seeded test words, not secrets
    vocab = sorted({_word(rng, 2, 4) for _ in range(VOCAB)})
    rng.shuffle(vocab)
    weights = list(itertools.accumulate(1 / (i + 1) for i in range(len(vocab))))
    singles = sorted({_word(rng, 2, 3).capitalize() for _ in range(NAMES)})
    full = [f"{a} {_word(rng, 2, 3).capitalize()}" for a in rng.sample(singles, FULL_NAMES)]
    misheard = [n[:-1] + "x" for n in rng.sample(singles, 200)]
    names = singles + full + misheard
    db = build.db_path(vault)
    db.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db, isolation_level=None)
    langs = build.languages_of(vault)
    create(conn, langs)
    conn.execute("BEGIN")
    indexer = build.Indexer(conn, vault, Analyzer(langs), collect=False)
    start = NOW - timedelta(minutes=CHUNKS * 5)
    chunks: list[Chunk] = []
    for n in range(CHUNKS):
        rid = f"calls:perf-{n // 4}" if n % 2 else f"mail:perf-{n}"
        source = "calls" if n % 2 else "mail"
        speakers = [rng.choice(full), "Speaker 2"] if n % 2 else []
        text = _text(rng, vocab, weights, names)
        if speakers:
            text = f"{speakers[0]}: {text}\n{speakers[1]}: {_text(rng, vocab, weights, names)}"
        chunks.append(
            Chunk(
                owner=f"r {rid}/{n}", ordinal=0, kind="transcript" if speakers else "record", grp=rid, text=text,
                locator=f"r:{rid}#{n}", source=source, ts=iso(start + timedelta(minutes=n * 5)), rid=rid, rids=[rid],
                speakers=speakers,
            )
        )  # fmt: skip
    indexer.add_chunks(chunks)
    matching.write_entities(conn, Entities([], [], []), {})
    matching.refresh(conn, Analyzer(langs), full=True)
    conn.execute("COMMIT")
    conn.execute("INSERT INTO chunks_fts (chunks_fts) VALUES ('optimize')")
    conn.close()
    return vault


@pytest.fixture(scope="module")
def vault(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    """The template, read where it was built, its index file read through once: the parallel run may have built it
    minutes before, and the memory it took since leaves it cold, which is no mining's time."""
    v = Vault.open(template_with(tmp_path_factory, make_named_vault))
    with build.db_path(v).open("rb") as index_file:
        while index_file.read(1 << 24):
            pass
    return v


def read_only(vault: Vault) -> sqlite3.Connection:
    return sqlite3.connect(f"{build.db_path(vault).as_uri()}?mode=ro", uri=True)


def test_the_named_vault_holds_its_chunks_and_labels(vault: Vault) -> None:
    with closing(read_only(vault)) as conn:
        chunks, labelled = conn.execute("SELECT count(*), sum(speakers != '[]') FROM chunks").fetchone()
    assert (chunks, labelled) == (CHUNKS, CHUNKS // 2)


@pytest.mark.perf
def test_a_round_s_mining_takes_well_under_thirty_seconds(vault: Vault) -> None:
    with closing(read_only(vault)) as conn:
        # Best of two: one round takes over 3 s on CI.
        run = best_of(2, lambda: mine(conn, vault, fold([])))
    found, elapsed = run.result, run.seconds
    print(f"curate miner: {elapsed:.1f} s ({run.runs()}) on {CHUNKS:,} chunks, {len(found.cards)} cards")
    assert len(found.cards) == 15
    assert found.left_out["common words"] > 0
    if not traced():
        assert elapsed < SECONDS, f"the miner took {elapsed:.1f} s on {CHUNKS:,} chunks"
