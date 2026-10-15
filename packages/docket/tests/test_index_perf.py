"""A year's worth of chunks: queries answer well under a second without models.

The chunks are synthetic (a seeded vocabulary with Zipf frequencies, in English- and Hungarian-looking words), each
with a title of five words so the title order runs on every candidate (DR-0231), and 1% of their words hyphenated
compounds from a pool of 2,000, repeating as prose repeats them (DR-0280), written through the build's own insert path.
Wall times are asserted only when no tracer runs: under coverage they are printed, and the fuzzy walk's work and the
word check's split scans are counted instead.
"""

from __future__ import annotations

import functools
import itertools
import random
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docket_testkit import fts_and_check, index_config, query_words, stem_every_word, template_with

from docket.index import build, matching, search
from docket.index import text as text_module
from docket.index.chunks import Chunk, iso
from docket.index.schema import create
from docket.index.terms import Checker, Word, phrase_of
from docket.index.text import Analyzer, fold, words_and_compounds
from docket.vault.entities import Alias, Entities, Entity
from docket.vault.vault import Vault

CHUNKS = 100_000
WORDS_PER_CHUNK = 60
TITLE_WORDS = 5
VOCAB = 40_000
ENTITIES = 300
ASR = 100
SECONDS = 1.0
COMPOUNDS = 2_000
COMPOUND_SHARE = 0.01
# The word check's load, as DR-0232 measured it: 200 candidates of 350 words, here six query words each with a split.
CANDIDATES = 200
CANDIDATE_WORDS = 350
SPLIT_WORDS = 6
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
    pool = [
        f"{a}-{b}"
        for a, b in zip(rng.sample(vocab[:5000], COMPOUNDS), rng.sample(vocab[:5000], COMPOUNDS), strict=True)
    ]
    pool_weights = list(itertools.accumulate(1 / (i + 1) for i in range(len(pool))))
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
        words = rng.choices(vocab, cum_weights=cumulative, k=WORDS_PER_CHUNK)
        hyphenated = [rng.random() < COMPOUND_SHARE for _ in words]
        if any(hyphenated):
            words = [
                rng.choices(pool, cum_weights=pool_weights)[0] if h else w
                for w, h in zip(words, hyphenated, strict=True)
            ]
        text = " ".join(words)
        title = " ".join(rng.choices(vocab, cum_weights=cumulative, k=TITLE_WORDS))
        source = ("calls", "chat", "mail")[n % 3]
        rid = f"{source}:perf-{n}"
        chunks.append(
            Chunk(
                owner=f"r {rid}", ordinal=0, kind="record", grp=rid, text=text, locator=f"r:{rid}", source=source,
                ts=iso(start + timedelta(minutes=n * 5)), rid=rid, rids=[rid], title=title,
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
    plain = [t for t, _ in by_doc if t in own_words()]
    hyphenated = compound_of(vault)
    joins = joining(by_doc, plain[:200])[0]
    middle = plain[5] + plain[12]
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
        # Every word stays however common (DR-0230): BM25 counts each common form's chunks once per query.
        "common and rare": (f"{common} {rare}", {}),
        "3 common, 1 rare": (f"{by_doc[0][0]} {by_doc[5][0]} {by_doc[12][0]} {rare}", {}),
        "5 common, 1 none": (f"{' '.join(t for t, _ in by_doc[:5])} zzqqxx", {}),
        # Compounds (DR-0281): a hyphenated word, two words joining into a word in most chunks, and words split into
        # two in half and a third of the chunks, or in most of them, where the split leaves the query.
        "hyphen and rare": (f"{hyphenated} {rare}", {}),
        "two words join": (joins, {}),
        "a split": (middle, {}),
        "3 splits, 1 rare": (f"{middle} {plain[7] + plain[20]} {plain[9] + plain[25]} {rare}", {}),
        "common split": (f"{plain[0] + plain[1]} {rare}", {}),
    }
    for name, (query, kwargs) in shapes.items():
        seconds, found = timed(vault, query, **kwargs)
        print(f"{name:16} {seconds * 1000:7.1f} ms  {found} hits  {query!a}")
        assert found, name
        if not traced():
            assert seconds < SECONDS, name
    result = search(vault, f"{common} múlt héten", now=NOW)
    since, until = result.query.since, result.query.until
    assert since is not None and until is not None
    assert all(h.ts is not None and since <= h.ts < until for g in result.groups for h in g.hits)


@functools.cache
def own_words() -> frozenset[str]:
    """The perf vault's own words, without the compounds' joined words."""
    return frozenset(vocabulary(random.Random(2026), VOCAB))  # noqa: S311 - seeded test words, not secrets


def compound_of(vault: Vault) -> str:
    """The perf vault's most used hyphenated compound."""
    conn = sqlite3.connect(build.db_path(vault))
    try:
        texts = [r[0] for r in conn.execute("SELECT text FROM chunks WHERE id % 50 = 0")]
    finally:
        conn.close()
    found = [w for t in texts for w in t.split() if "-" in w]
    return max(sorted(set(found)), key=found.count)


def joining(by_doc: list[tuple[str, int]], words: list[str]) -> list[str]:
    """Two words of the vault whose joined form is one of the words given, split as a split may be."""
    held = {term for term, n in by_doc if n}
    return [f"{t[:at]} {t[at:]}" for t in words for at in range(3, len(t) - 1) if {t[:at], t[at:]} <= held]


def test_the_word_check_agrees_with_stemming_every_word_on_a_sample(vault: Vault) -> None:
    conn = sqlite3.connect(build.db_path(vault))
    try:
        texts = [r[0] for r in conn.execute("SELECT text FROM chunks WHERE id % 400 = 0 ORDER BY id")]
    finally:
        conn.close()
    analyzer = Analyzer(build.languages_of(vault))
    rng = random.Random(57)  # noqa: S311 - seeded test words, not secrets
    phrases = [phrase_of(analyzer, " ".join(text.split()[i : i + 2])) for text in rng.sample(texts, 10) for i in (0, 7)]
    for joined in (False, True):
        words = query_words(analyzer, texts, [], seed=57, sample=100, compounds=joined)
        checker = Checker(analyzer, words, phrases)
        assert checker.firsts is not None
        for text in texts:
            want_words, want_phrases, want_split = stem_every_word(analyzer, words, phrases, text)
            held = checker.held(text)
            assert (set(held.words), set(held.phrases), set(held.split_only)) == (want_words, want_phrases, want_split)
            assert checker.examine(text) == (len(want_words) + len(want_phrases) + len(want_split), bool(want_split))


def test_the_word_check_agrees_with_fts5_on_a_sample(vault: Vault) -> None:
    conn = sqlite3.connect(build.db_path(vault))
    by_doc = conn.execute("SELECT term, doc FROM vocab WHERE col = 'folded' ORDER BY doc DESC").fetchall()
    conn.close()
    plain = [t for t, _ in by_doc if t in own_words()]
    pairs = joining(by_doc, plain[200:400])
    # A hyphenated word, two pairs of words joining into one, words split in two, and a plain word.
    queries = [compound_of(vault), pairs[0], pairs[1], plain[5] + plain[12], plain[300] + plain[301], plain[40]]
    for query in queries:
        found, held = fts_and_check(vault, query, " AND c.id % 400 = 0")
        assert found == held, query


def test_the_word_check_with_splits_stays_fast(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = sqlite3.connect(build.db_path(vault))
    try:
        rows = [r[0] for r in conn.execute("SELECT text FROM chunks WHERE id % 97 = 0 ORDER BY id LIMIT 1200")]
        by_doc = conn.execute("SELECT term, doc FROM vocab WHERE col = 'folded' ORDER BY doc DESC").fetchall()
    finally:
        conn.close()
    per = CANDIDATE_WORDS // WORDS_PER_CHUNK + 1
    texts = [" ".join(" ".join(rows[i * per : (i + 1) * per]).split()[:CANDIDATE_WORDS]) for i in range(CANDIDATES)]
    plain = [t for t, _ in by_doc if t in own_words()]
    # Six words that each split into two words of the vault, the halves from a twentieth of the chunks to most.
    analyzer = Analyzer(build.languages_of(vault))
    pairs = [(plain[i], plain[j]) for i, j in ((3, 40), (8, 25), (15, 60), (30, 90), (50, 120), (70, 200))]
    words = [Word(a + b, frozenset([("folded", fold(a + b))]), (phrase_of(analyzer, f"{a} {b}"),)) for a, b in pairs]
    assert len(words) == SPLIT_WORDS
    # A fresh `docket search` meets every word with a cold stem cache, printed. The limit holds the warm pass: the
    # synthetic words start with few letters, so the first-letter screen lets nearly every word through and the cold
    # cache costs as much with no split at all; a split's own cost is the same cold or warm.
    monkeypatch.setattr(text_module, "_STEMMERS", {})
    times: list[float] = []
    for _ in ("cold", "warm"):
        checker = Checker(analyzer, words, [])
        started = time.monotonic()
        for candidate in texts:
            checker.examine(candidate)
        times.append(time.monotonic() - started)
    # The gate: a text is scanned for a split only when it holds a term of each half in one column.
    gated = 0
    for candidate in texts:
        ws, joined = words_and_compounds(candidate)
        terms = {term for w in [*ws, *(j.word for j in joined)] for term in checker.terms(w)}
        for word in words:
            if not word.forms & terms:
                gated += any(all((col, t) in terms for t in seq) for col, seq in word.splits[0].columns)
    print(
        f"\nword check: {CANDIDATES} texts of {CANDIDATE_WORDS} words, {SPLIT_WORDS} splits, "
        f"{times[0] * 1000:.0f} ms cold, {times[1] * 1000:.0f} ms warm, {checker.scans} scans of "
        f"{CANDIDATES * SPLIT_WORDS}"
    )
    assert checker.scans == gated < CANDIDATES * SPLIT_WORDS
    if not traced():
        assert times[1] < SECONDS


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
