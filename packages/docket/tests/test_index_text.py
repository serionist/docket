from __future__ import annotations

import sqlite3
import sys
import threading
import types

import pytest

from docket.index import matching, text
from docket.index.languages import code_of, hungarian_cases, hungarian_dative, language, resolve
from docket.index.text import Analyzer, Stemmer, any_of, fold, phrase, prefix, quote, words, words_and_compounds


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


HYPHEN, NON_BREAKING, EN_DASH = chr(0x2010), chr(0x2011), chr(0x2013)


@pytest.mark.parametrize(
    ("text", "joined"),
    [
        ("Orbex kick-off with Dana", ["kickoff"]),
        ("Írd meg az e-mail-címedet", ["email", "emailcímedet", "mailcímedet"]),
        ("state-of-the-art", ["stateoftheart"]),
        ("a1-b2-c3-d4-e5", ["a1b2c3d4e5"]),
        ("covid-19", ["covid19"]),
        ("2026-10-12 and +36-30-123", []),
        (f"kick{HYPHEN}off, kick{NON_BREAKING}off", ["kickoff", "kickoff"]),
        (f"kick - off, kick{EN_DASH}off, kick--off", []),
        ("-lead and tail-", []),
    ],
)
def test_a_hyphenated_compound_is_joined(text: str, joined: list[str]) -> None:
    ws, found = words_and_compounds(text)
    assert ws == words(text)
    assert [j.word for j in found] == joined


def test_the_joined_words_follow_the_chunk_s_own_in_every_column() -> None:
    text = "Megkaptam az e-mailt, the kick-offs"
    folded, en, hu = Analyzer(resolve(["en", "hu"])).index_values(text)
    assert folded == f"{text} emailt kickoffs"
    # The text's own words keep their places, so phrases match as before; the joined words are stemmed like any.
    assert fts_words(folded)[: len(words(text))] == [fold(w) for w in words(text)]
    assert en.split()[-1] == "kickoff" and hu.split()[-2] == "email"
    assert Analyzer(resolve(["en"])).index_values("no compound here") == ["no compound here", "no compound here"]


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


@pytest.mark.parametrize(
    ("inflected", "bare"),
    [("forintot", "forint"), ("forintban", "forint"), ("projektet", "projekt"), ("projektről", "projekt"),
     ("ajánlatot", "ajánlat"), ("ajánlatról", "ajánlat"), ("robotot", "robot"), ("robotok", "robot")],
)  # fmt: skip
def test_an_inflected_word_s_stem_stemmed_again_is_the_bare_word_s_stem(inflected: str, bare: str) -> None:
    analyzer = Analyzer(resolve(["en", "hu"]))
    hu = language("hu")
    assert analyzer.stem(bare, hu) not in analyzer.forms(inflected, hu)
    assert analyzer.restem(inflected, hu) == (fold(bare), analyzer.stem(bare, hu))


def test_only_a_language_that_stems_twice_restems_and_only_when_the_second_cut_differs() -> None:
    analyzer = Analyzer(resolve(["en", "hu"]))
    assert analyzer.restem("robotot", language("en")) is None
    assert analyzer.restem("robot", language("hu")) is None
    assert analyzer.restem("határidőről", language("hu")) is None


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


def test_hungarian_case_forms_follow_harmony_and_assimilation() -> None:
    acme = hungarian_cases("acme")
    # Back and front vowels both: a foreign name takes either harmony, its final vowel lengthened.
    assert {"acménak", "acmének", "acménél", "acmét", "acmével", "acméval", "acméhoz", "acméről"} <= set(acme)
    assert "acmet" not in acme
    mark = hungarian_cases("mark")
    assert {"marknak", "markkal", "markká", "markot", "markon", "marknál", "markról"} <= set(mark)
    # Back vowels only: front endings only where they start with a consonant, so the English market and marked are no
    # case form of Mark.
    assert not {"market", "marked", "marken", "marköt"} & set(mark)
    assert {"kováccsal", "kovácsot"} <= set(hungarian_cases("kovács"))
    assert {"bellel", "bellnek"} <= set(hungarian_cases("bell"))
    peter = hungarian_cases("péter")
    assert {"péternek", "péterrel", "pétert", "péteren"} <= set(peter)
    assert not any(form.endswith(("nak", "val", "ban", "on")) for form in peter)
    assert hungarian_cases("dóra")[:2] == ["dórának", "dóránál"]
    assert "same" not in hungarian_cases("sam")
    assert len(set(acme)) == len(acme)
    assert hungarian_cases("2026") == []
    assert language("hu").cases is hungarian_cases
    assert language("en").cases is None


def test_hungarian_case_forms_read_a_final_y_and_old_spellings() -> None:
    assert {"tonyval", "tonyn", "tonyvá", "tonyt", "tonynak"} <= set(hungarian_cases("tony"))
    assert {"szalayval", "szalayt", "szalayn"} <= set(hungarian_cases("szalay"))
    assert {"kellyvel", "kellyn"} <= set(hungarian_cases("kelly"))
    assert "kölcseyvel" in hungarian_cases("kölcsey")
    # A Hungarian ny still doubles: Bárány gives Báránnyal.
    assert {"báránnyal", "bárányt"} <= set(hungarian_cases("bárány"))
    assert {"babitscsal", "babitscsá"} <= set(hungarian_cases("babits"))
    assert "ráczcal" in hungarian_cases("rácz")
    assert "madáchcsal" in hungarian_cases("madách")
    assert "kossuthtal" in hungarian_cases("kossuth")
    assert {"maxszal", "maxot"} <= set(hungarian_cases("max"))
    old = {*hungarian_cases("babits"), *hungarian_cases("kossuth"), *hungarian_cases("max")}
    assert not {"babitssal", "kossuthhal", "maxxal"} & old
    # sh and ph are one sound, which the doubling writes: Nimishsel, Joshsal, Josephfel.
    assert {"nimishsel", "nimishnek"} <= set(hungarian_cases("nimish"))
    assert {"joshsal", "joshsel"} <= set(hungarian_cases("josh"))
    assert "josephfel" in hungarian_cases("joseph")
    assert not {"nimishhel", "joshhal", "josephhel"} & {*hungarian_cases("nimish"), *hungarian_cases("josh")}


def test_hungarian_case_forms_let_a_foreign_name_sound_front() -> None:
    # Hungarian inflects a foreign name by its sound: Sammel, Ryannel, Jasonnek, Frankkel, beside Sammal and Ryannak.
    for name, forms in {
        "sam": {"sammel", "samnek", "samhez", "sammal", "samnak"},
        "ryan": {"ryannel", "ryannek", "ryanről", "ryannal"},
        "jason": {"jasonnel", "jasonnek", "jasonben"},
        "frank": {"frankkel", "franknek"},
        # A final y read as a vowel, as Tonyval reads it: Andyvel, Garyvel.
        "andy": {"andyvel", "andyvé", "andynek", "andyval"},
        "gary": {"garyvel", "garynek"},
    }.items():
        assert forms <= set(hungarian_cases(name)), name
    # Only the endings that start with a consonant: Sam with -et would be samet, Jack's jacket.
    assert not {"samet", "samen", "jacket", "jacken"} & {*hungarian_cases("sam"), *hungarian_cases("jack")}
    # A word ending in a back vowel keeps back endings only.
    assert not any(form.endswith(("nek", "vel")) for form in hungarian_cases("dóra"))


def test_only_languages_with_case_forms_match_aliases_on_stems() -> None:
    # Without case forms an alias matches as written only (DR-0350).
    assert [lang.code for lang in matching.alias_langs(Analyzer(resolve(["en", "hu", "de"])))] == ["hu"]
    # Only the last word inflects.
    analyzer = Analyzer(resolve(["en", "hu"]))
    cases = matching.cases_of(analyzer, ["northwind", "traders"], language("hu"))
    assert cases.reached[0] == ("northwind", "tradersnak")
    assert all(form[0] == "northwind" for form in (*cases.reached, *cases.alone))
    assert matching.cases_of(analyzer, ["mark"], language("en")) == matching.Cases((), ())


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
