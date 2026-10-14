from __future__ import annotations

import sqlite3
import sys
import threading
import types

import pytest

from docket.index import text
from docket.index.languages import code_of, hungarian_dative, language, resolve
from docket.index.text import Analyzer, Stemmer, any_of, fold, phrase, prefix, quote, words


def fts_words(value: str) -> list[str]:
    """How FTS5's unicode61 tokenizer with remove_diacritics 2 stores a text's words."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='unicode61 remove_diacritics 2')")
    conn.execute("CREATE VIRTUAL TABLE v USING fts5vocab(t, 'instance')")
    conn.execute("INSERT INTO t (x) VALUES (?)", (value,))
    return [r[0] for r in conn.execute("SELECT term FROM v ORDER BY offset")]


@pytest.mark.parametrize(
    "sample",
    ["snake_case word", "don't stop", "room 101, 3.5 kg", "Árvíztűrő tükörfúrógép", "ÉLŐ ŐSZ", "naïve café"],
)
def test_words_split_and_fold_as_fts5_stores_them(sample: str) -> None:
    assert [fold(w) for w in words(sample)] == fts_words(sample)


def test_words_keep_accents_and_lowercase_for_the_stemmers() -> None:
    assert words("Határidőről, KÉT nap!") == ["határidőről", "két", "nap"]
    assert fold("Határidő") == "hatarido"


@pytest.mark.parametrize(
    ("word", "stem", "dative"),
    [
        ("forint", "forin", "forint"),
        ("consultant", "consultan", "consultant"),
        ("hatarido", "hatar", "hatarido"),
        ("marco", "marc", "marco"),
        ("kata", "kat", "kat"),
        ("robot", "rob", "rob"),
        ("dóra", "do", "dor"),
        ("mark", "mar", "mar"),
    ],
)
def test_a_bare_word_s_forms_are_its_stem_itself_and_its_dative_s_stem(word: str, stem: str, dative: str) -> None:
    hu = language("hu")
    analyzer = Analyzer(resolve(["hu"]))
    assert analyzer.stem(word, hu) == stem
    assert analyzer.forms(word, hu) == text.dedupe([stem, fold(word), dative])


@pytest.mark.parametrize(
    ("word", "stem"),
    [("forintot", "forint"), ("consultantnek", "consultant"), ("határidőről", "hatarido"), ("marcónak", "marco"),
     ("katának", "kat"), ("dórának", "dor"), ("bálintnak", "balint"), ("már", "mar"), ("nortvinnál", "nortvin")],
)  # fmt: skip
def test_inflected_hungarian_stems_pinned(word: str, stem: str) -> None:
    assert Analyzer(resolve(["hu"])).stem(word, language("hu")) == stem


def test_a_word_s_forms_meet_its_inflected_forms() -> None:
    analyzer = Analyzer(resolve(["en", "hu"]))
    hu = language("hu")
    for bare, inflected in (("forint", "forintot"), ("consultant", "consultantnek"), ("hatarido", "határidőről")):
        assert analyzer.stem(inflected, hu) in analyzer.forms(bare, hu)
    assert analyzer.stem("consulting", language("en")) == analyzer.stem("consultant", language("en"))


def test_the_hungarian_dative_lengthens_a_final_vowel_and_follows_harmony() -> None:
    assert hungarian_dative("dóra") == ["dórának"]
    assert hungarian_dative("marco") == ["marcónak"]
    assert hungarian_dative("bálint") == ["bálintnak"]
    assert hungarian_dative("szélvár") == ["szélvárnak"]
    assert hungarian_dative("kert") == ["kertnek"]
    assert hungarian_dative("xyz") == ["xyznek"]
    assert hungarian_dative("2026") == []


def test_the_seeds_are_the_dative_stem_and_the_word() -> None:
    analyzer = Analyzer(resolve(["hu"]))
    assert analyzer.seeds("nortwin", language("hu")) == ["nortwin"]
    assert analyzer.seeds("dóra", language("hu")) == ["dor", "dora"]
    assert analyzer.seeds("printer", language("en")) == ["printer"]


@pytest.mark.parametrize(
    ("given", "code"),
    [("en", "en"), ("HU", "hu"), (" Hungarian ", "hu"), ("german", "de"), ("nb", "no"), ("nn", "no"), ("xx", None)],
)
def test_a_language_is_placed_from_a_code_or_a_name(given: str, code: str | None) -> None:
    assert code_of(given) == code


def test_the_columns_follow_the_owner_s_languages() -> None:
    assert resolve(["en", "hu"]).columns == ("folded", "stem_en", "stem_hu")
    assert resolve(["hu"]).columns == ("folded", "stem_en", "stem_hu")
    assert resolve(["de", "en"]).columns == ("folded", "stem_en", "stem_de")
    langs = resolve(["ja", "Hungarian", "hu"])
    assert langs.codes == ("en", "hu")
    assert len(langs.problems) == 1 and "'ja'" in langs.problems[0]


def test_every_snowball_language_loads_and_stems() -> None:
    langs = resolve(["ar", "de", "fr", "no", "ru", "es", "tr"])
    analyzer = Analyzer(langs)
    assert len(analyzer.index_values("Running quickly")) == len(langs.columns)


def test_the_pure_python_class_is_used_even_when_pystemmer_is_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("Stemmer")
    fake.algorithms = lambda: ["english"]  # type: ignore[attr-defined]
    fake.Stemmer = lambda lang: (_ for _ in ()).throw(AssertionError("PyStemmer was used"))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "Stemmer", fake)
    assert Stemmer(language("en")).stem("running") == "run"


def test_a_stemmer_is_safe_from_two_threads() -> None:
    stemmer = Stemmer(language("hu"))
    found: list[str] = []
    workers = [threading.Thread(target=lambda w=w: found.append(stemmer.stem(w))) for w in ["forintot"] * 8]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    assert found == ["forint"] * 8


@pytest.mark.parametrize("hostile", ['say "hi"', "a*", "NEAR(a b)", "a AND b", "-x", "col: y", "^start", "(a OR b)"])
def test_quoting_makes_any_text_plain_words(hostile: str) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(folded)")
    conn.execute("INSERT INTO t (folded) VALUES (?)", (hostile,))
    assert conn.execute("SELECT count(*) FROM t WHERE t MATCH ?", (quote(hostile),)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM t WHERE t MATCH ?", (phrase("folded", words(hostile)),)).fetchone()[0]


def test_expression_helpers() -> None:
    assert prefix("folded", "cons") == 'folded : "cons" *'
    assert any_of(["a"]) == "a"
    assert any_of(["a", "b"]) == "(a OR b)"
