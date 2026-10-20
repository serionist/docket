"""Redaction. Secret-shaped strings are assembled from parts at runtime, so the repo's secret scan sees no literal."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass

import pytest
from docket_testkit import best_of, make_record

from docket.vault import derived
from docket.vault.config import Redaction, RedactionPattern
from docket.vault.records import Attachment, RawRecord
from docket.vault.redact import MARKER, Redactor, marker, markers, slug

GH_TOKEN = "gh" + "p_" + "a1B2" * 9
GH_PAT = "github" + "_pat_" + "A1b2C3d4" * 3
SLACK_TOKEN = "xo" + "xb-" + "1234567890-abcdefghij"
AI_KEY = "s" + "k-" + "ant-" + "Q" * 30
AWS_ID = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
AWS_HIDDEN = "wJalrXUtnFEMI/K7MDENG/" + "bPxRfiCYEXAMPLEKEY"
GOOGLE_KEY = "AI" + "za" + "B" * 35
JWT = "ey" + "J" + "hbGciOiJIUzI1" + "." + "ey" + "J" + "zdWIiOiIxMjM0" + "." + "SflKxwRJSMeKKF2QT4fwpM"
KEY_BLOCK = "PRIVATE " + "KEY-----"
PEM = "-----" + "BEGIN RSA " + KEY_BLOCK + "\nMIIEow" + "IBAAKCAQEA\n-----" + "END RSA " + KEY_BLOCK
PEM_CUT_OFF = "-----" + "BEGIN " + KEY_BLOCK + "\nMIIEvQ" + "IBADANBgkq and the rest was lost"

HIDDEN = "hunter2-Example"
NBSP = chr(0x00A0)
NARROW_NBSP = chr(0x202F)
APOSTROPHE = chr(0x2019)


@pytest.fixture
def redactor() -> Redactor:
    return Redactor.from_config(Redaction(phone=True))


def redacted(redactor: Redactor, text: str) -> tuple[str, Counter[str]]:
    counts = Counter[str]()
    return redactor.text(text, counts), counts


def pattern(name: str, regex: str, enabled: bool = True) -> RedactionPattern:
    return RedactionPattern(name=name, pattern=regex, enabled=enabled)


def patterns(*pairs: tuple[str, str], credentials: bool = False, phone: bool = False) -> Redactor:
    return Redactor.from_config(
        Redaction(credentials=credentials, phone=phone, patterns=[pattern(n, p) for n, p in pairs])
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"password: {HIDDEN} and more", "password: [redacted:password] and more"),
        (f'{{"password": "{HIDDEN}", "user": "ada"}}', '{"password": "[redacted:password]", "user": "ada"}'),
        (f"'client_secret' = '{HIDDEN}'", "'client_secret' = '[redacted:api-key]'"),
        (f"DB_PASSWORD={HIDDEN}", "DB_PASSWORD=[redacted:password]"),
        (f"AWS_SECRET_ACCESS_KEY={AWS_HIDDEN}", "AWS_SECRET_ACCESS_KEY=[redacted:api-key]"),
        (f"client-secret: {HIDDEN}", "client-secret: [redacted:api-key]"),
        (f'clientSecret: "{HIDDEN}"', 'clientSecret: "[redacted:api-key]"'),
        (f"apiKey = {HIDDEN}", "apiKey = [redacted:api-key]"),
        (f'"refresh_token": "{HIDDEN}"', '"refresh_token": "[redacted:api-key]"'),
        (f"<b>Password:</b>&nbsp;{HIDDEN}", "<b>Password:</b>&nbsp;[redacted:password]"),
        (f"jelszó: {HIDDEN}", "jelszó: [redacted:password]"),
        (f"mot de passe = {HIDDEN}", "mot de passe = [redacted:password]"),
        (f"conn: Server=db;Password={HIDDEN};User=ada", "conn: Server=db;Password=[redacted:password];User=ada"),
        (f"postgres://ada:{HIDDEN}@db.example.invalid/x", "postgres://ada:[redacted:password]@db.example.invalid/x"),
    ],
)
def test_a_label_in_every_form_hides_its_value_and_keeps_the_label(
    redactor: Redactor, text: str, expected: str
) -> None:
    out, counts = redacted(redactor, text)
    assert out == expected
    assert counts.total() == 1


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"*Password:* {HIDDEN}", "*Password:* [redacted:password]"),
        (f"*Password*: {HIDDEN}", "*Password*: [redacted:password]"),
        (f"**Password:** {HIDDEN}", "**Password:** [redacted:password]"),
        (f"_Password:_ {HIDDEN}", "_Password:_ [redacted:password]"),
        (f"**API key:** {HIDDEN}", "**API key:** [redacted:api-key]"),
        (f"| Password: | {HIDDEN} |", "| Password: | [redacted:password] |"),
        # A mark right after the value goes with it: a password may end in one, and losing a mark beats showing it.
        (f"`password`: `{HIDDEN}`", "`password`: `[redacted:password]"),
    ],
)
def test_emphasis_and_table_marks_around_a_label_are_never_taken_for_its_value(
    redactor: Redactor, text: str, expected: str
) -> None:
    out, counts = redacted(redactor, text)
    assert out == expected
    assert counts.total() == 1


@pytest.mark.parametrize(
    "text",
    [
        f"<td>Password:</td>\r\n<td>{HIDDEN}</td>",
        f"<td>Password:</td>\n  <td>{HIDDEN}</td>",
        f"<b>Password:</b><span\r\nstyle='color:red'>{HIDDEN}</span>",
        "<b>Password:</b><span style='" + "x" * 500 + f"'>{HIDDEN}</span>",
        f"Password:<br>\n{HIDDEN}",
        f"Password:\n{HIDDEN}",
        "<p class=MsoNormal>Password:<o:p></o:p></p>\r\n</td>\r\n<td width=200 style='width:150.0pt'>\r\n"
        f"<p class=MsoNormal>{HIDDEN}<o:p></o:p></p>",
    ],
)
def test_a_value_on_the_next_line_or_behind_long_tags_is_redacted(redactor: Redactor, text: str) -> None:
    out, counts = redacted(redactor, text)
    assert HIDDEN not in out
    assert counts == Counter({"password": 1})


@pytest.mark.parametrize(
    "text",
    ["the passwordless login: works", "passwordHash: abc", "password: ***", "Password:\n\n## Next"],
)
def test_what_isn_t_a_labelled_secret_stays(redactor: Redactor, text: str) -> None:
    assert redacted(redactor, text) == (text, Counter())


# -- a password of several words, and a dial-in PIN (#256) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Temporary password: Kq7 x!Lm 22", "Temporary password: [redacted:password]"),
        ("PIN-kód: 123 456 789#", "PIN-kód: [redacted:password]"),
        ("PIN-kod: 123 456 789#", "PIN-kod: [redacted:password]"),
        ("Conference PIN: 4812 7731", "Conference PIN: [redacted:password]"),
        ("PIN code: 4812 7731", "PIN code: [redacted:password]"),
        ("pin=4812", "pin=[redacted:password]"),
        ("conferencePin: 4812 7731", "conferencePin: [redacted:password]"),
        # The words after the first go only while they hold a digit or a symbol: prose after them stays.
        ("PIN: 4812 7731 then press # and wait", "PIN: [redacted:password] then press # and wait"),
        ("PIN: 4812 7731, then press #", "PIN: [redacted:password], then press #"),
        (
            "Password: Kq7 x!Lm 22 (changed at first sign-in)",
            "Password: [redacted:password] (changed at first sign-in)",
        ),
        (f"PIN: 4812 7731 don{APOSTROPHE}t share it", f"PIN: [redacted:password] don{APOSTROPHE}t share it"),
    ],
)
def test_a_password_of_several_words_and_a_pin_go_whole(redactor: Redactor, text: str, expected: str) -> None:
    out, counts = redacted(redactor, text)
    assert out == expected
    assert counts == Counter({"password": 1})


@pytest.mark.parametrize(
    "text", ["my opinion: 4812 7731", "spinning: 4812 7731", "pinned: 4812 7731", "Pinterest: 4812", "PINs: 4812"]
)
def test_pin_is_a_label_only_as_a_word_of_its_own(redactor: Redactor, text: str) -> None:
    assert redacted(redactor, text) == (text, Counter())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("| Password: | Kq7 x!Lm 22 | Valid: 24 h |", "| Password: | [redacted:password] | Valid: 24 h |"),
        ("Password: Kq7 x!Lm 22|kept", "Password: [redacted:password]|kept"),
        (
            "<td>Password:</td><td>Kq7 x!Lm 22</td><td>kept</td>",
            "<td>Password:</td><td>[redacted:password]</td><td>kept</td>",
        ),
        ("<b>PIN:</b> 4812 7731<br>kept 2", "<b>PIN:</b> [redacted:password]<br>kept 2"),
        ("Password: Kq7 x!Lm 22\nkept 2", "Password: [redacted:password]\nkept 2"),
    ],
)
def test_a_value_of_several_words_stops_at_a_tag_a_cell_or_the_line_s_end(
    redactor: Redactor, text: str, expected: str
) -> None:
    out, counts = redacted(redactor, text)
    assert out == expected
    assert counts == Counter({"password": 1})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Password: Kq7 x!Lm 22 Username: ada7", "Password: [redacted:password] Username: ada7"),
        ("PIN: 4812 7731 room=B12", "PIN: [redacted:password] room=B12"),
        ("PIN: 4812 7731 at 10:30", "PIN: [redacted:password] at 10:30"),
        ("PIN: 4812 7731 ID2: 55 66", "PIN: [redacted:password] ID2: 55 66"),
    ],
)
def test_a_value_of_several_words_stops_at_the_next_label(redactor: Redactor, text: str, expected: str) -> None:
    out, counts = redacted(redactor, text)
    assert out == expected
    assert counts == Counter({"password": 1})


def test_two_values_on_one_line_are_two_markers(redactor: Redactor) -> None:
    out, counts = redacted(redactor, "Meeting ID: 811 2233 4455 Passcode: 7Hq2 x9 PIN: 4812 7731#")
    assert out == "Meeting ID: 811 2233 4455 Passcode: [redacted:password] PIN: [redacted:password]"
    assert counts == Counter({"password": 2})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Password: Kq7x", "Password: [redacted:password]"),
        (f"password: {HIDDEN} and more", "password: [redacted:password] and more"),
        ("the password: is in the vault, ask Ben", "the password: [redacted:password] in the vault, ask Ben"),
        ("PIN: 4812 for the door", "PIN: [redacted:password] for the door"),
        ("token: abc123 def456", "token: [redacted:api-key] def456"),
    ],
)
def test_a_one_word_value_and_an_api_key_go_as_before(redactor: Redactor, text: str, expected: str) -> None:
    out, counts = redacted(redactor, text)
    assert out == expected
    assert counts.total() == 1


@pytest.mark.perf
def test_long_runs_of_filler_are_read_in_linear_time(redactor: Redactor) -> None:
    n = 50_000
    inputs = [
        "Password:" + "*" * n,
        "password: " + "%" * n,
        ("password " + " " * 1000) * 100,
        "Password:" + "<" + "a" * 1999 + "x" * n,
        "Password" + "\r\n" * n + ": x",
        "password: " * 20_000,
        "Password:" + "<b>" * n + "x",
        "token" + "_" * n,
        "password" + NBSP * n,
        "tel" + NBSP * n,
        "+" + "1 " * n,
        # A password of many words, words that read as prose only at their end, and labels one after another.
        "PIN: " + "4812 " * n,
        "password: x " + "a-b." * n + "1",
        "password: x " + "a" * n + "1",
        "password: x " + "a" * n + ":",
        ("PIN: 1 " + "x!y " * 10) * (n // 10),
    ]
    # A pattern that finds a few more characters each pass, after a marker: the cap stops it (DR-0525).
    chain = Redactor.from_config(Redaction(patterns=[pattern("x", "X"), pattern("hash", r"\b[0-9a-f]{8}")]))

    def read_all() -> None:
        for text in inputs:
            redactor.text(text)
        chain.text("X" + "0123456789abcdef" * (n // 16))

    run = best_of(3, read_all)
    print(f"long runs of filler: {run.seconds:.2f} s ({run.runs()})")
    assert run.seconds < 5


@pytest.mark.parametrize("token", [GH_TOKEN, GH_PAT, SLACK_TOKEN, AI_KEY, AWS_ID, GOOGLE_KEY, JWT])
def test_token_shapes_are_redacted_without_a_label(redactor: Redactor, token: str) -> None:
    out, counts = redacted(redactor, f"see {token} here")
    assert out == "see [redacted:api-key] here"
    assert counts == Counter({"api-key": 1})


def test_a_bearer_token_keeps_the_word_bearer(redactor: Redactor) -> None:
    out, _ = redacted(redactor, "Authorization: Bearer abcdefghijklmnop1234")
    assert out == "Authorization: Bearer [redacted:api-key]"


def test_a_private_key_block_goes_whole_with_or_without_its_end(redactor: Redactor) -> None:
    out, counts = redacted(redactor, f"before\n{PEM}\nafter")
    assert out == "before\n[redacted:private-key]\nafter"
    assert counts == Counter({"private-key": 1})
    out, _ = redacted(redactor, f"pasted: {PEM_CUT_OFF}")
    assert out == "pasted: [redacted:private-key]"


@pytest.mark.parametrize(
    "text",
    [
        "call me at +1 555 010 0199 today",
        "or +44 7700 900123.",
        "mobile: +1 (555) 010-0142",
        "Tel: 06 30 123 4567",
        "phone 555-010-0177",
    ],
)
def test_phone_numbers_need_a_plus_or_a_label(redactor: Redactor, text: str) -> None:
    out, counts = redacted(redactor, text)
    assert counts == Counter({"phone": 1})
    assert "[redacted:phone]" in out
    assert not any(ch.isdigit() for ch in out)


@pytest.mark.parametrize(
    "text",
    [
        "the price was 10 millió forint",
        "1 500 000 Ft",
        "on 2026-10-14 at 09:30",
        "ts 1791000000.000100",
        "order 123456789",
        "v1.2.3",
        "passwordHash: abc",
        "token_count: 5",
        "Tokens: 5",
        "the risk-assessment-for-the-quarterly-plan",
        "Mobile 2026 roadmap",
        "budget +12 500 000 Ft vs plan",
        "revenue +25.000.000 EUR",
        "delta: +1 500 000 Ft",
        "up +12345678 USD, +87654321$, +11223344 € and +99887766% on last year",
        "+12345678 huf",
        f"+12 500 000{NBSP}Ft",
        "budget +12 500 000 000 Ft vs plan",
        "capex +125 000 000 000 HUF",
        "deal +12.500.000.000 EUR",
        "+1 250 000 000 Ft",
        f"+12 500 000 000{NBSP}Ft",
    ],
)
def test_numbers_and_words_docket_keeps(redactor: Redactor, text: str) -> None:
    assert redacted(redactor, text) == (text, Counter())


@pytest.mark.parametrize(
    "text", ["+44 7700 900123", "call +36 30 555 0100.", "+1 555 010 0199 0123", "+1 555 010 0199 then 12 Ft"]
)
def test_a_phone_number_stays_redacted_beside_the_amount_rule(redactor: Redactor, text: str) -> None:
    out, counts = redacted(redactor, text)
    assert counts == Counter({"phone": 1})
    assert not any(part in out for part in ("0199 0123", "900123", "555 0100"))


@pytest.mark.parametrize("space", [NBSP, NARROW_NBSP])
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Password:{s}" + HIDDEN, "Password:{s}[redacted:password]"),
        ("<b>Password:</b>{s}" + HIDDEN, "<b>Password:</b>{s}[redacted:password]"),
        ("Mot de passe{s}: " + HIDDEN, "Mot de passe{s}: [redacted:password]"),
        ("Mot{s}de{s}passe{s}:{s}" + HIDDEN, "Mot{s}de{s}passe{s}:{s}[redacted:password]"),
        ("Authorization: Bearer{s}abcdefghijklmnop1234", "Authorization: Bearer{s}[redacted:api-key]"),
        ("Tel.{s}:{s}06 30 123 4567", "Tel.{s}:{s}[redacted:phone]"),
    ],
)
def test_a_no_break_space_is_a_space(redactor: Redactor, space: str, text: str, expected: str) -> None:
    out, counts = redacted(redactor, text.format(s=space))
    assert out == expected.format(s=space)
    assert counts.total() == 1


def test_redacting_twice_is_redacting_once(redactor: Redactor) -> None:
    once, _ = redacted(redactor, f"password: {HIDDEN}, {GH_TOKEN}, +1 555 010 0199")
    twice, counts = redacted(redactor, once)
    assert twice == once
    assert counts == Counter()
    # Generated: patterns with \b that end on a boundary too, built-ins on and off, as a text and as segments.
    for case in cases(1471, (*PIECES, *BOUNDED)):
        r = case.redactor()
        once = r.text(case.text)
        assert r.text(once) == once, case
        said = r.segment_texts(list(case.words), Counter[str]())
        assert r.segment_texts(said, Counter[str]()) == said, case


def test_a_user_pattern_replaces_its_match_or_only_its_secret_group() -> None:
    config = Redaction(
        patterns=[
            RedactionPattern(name="Project Code!", pattern=r"\bPRJ-\d{4}\b"),
            RedactionPattern(name="door", pattern=r"door code (?P<secret>\d+)"),
            RedactionPattern(name="!!", pattern=r"nothing(?P<secret>)"),
        ]
    )
    redactor = Redactor.from_config(config)
    out, counts = redacted(redactor, "PRJ-1234 and the door code 4711, and nothing else")
    assert out == "[redacted:project-code] and the door code [redacted:door], and nothing else"
    assert counts == Counter({"project-code": 1, "door": 1})
    assert slug("!!") == "pattern"


def test_credentials_off_leaves_only_the_user_s_patterns() -> None:
    redactor = Redactor.from_config(Redaction(credentials=False, patterns=[RedactionPattern(name="x", pattern="zzz")]))
    out, _ = redacted(redactor, f"password: {HIDDEN} zzz")
    assert out == f"password: {HIDDEN} [redacted:x]"
    record, _ = redactor.record(make_record(native={"password": HIDDEN}))
    assert record.native == {"password": HIDDEN}


def test_a_record_is_redacted_everywhere_but_its_ids(redactor: Redactor) -> None:
    record = make_record(
        author="+1 555 010 0100",
        to=["+1 555 010 0101"],
        title=f"password: {HIDDEN}",
        text=f"the key is {GH_TOKEN}",
        attachments=[Attachment(name="+1 555 010 0102.vcf", ref=f"https://files.example.invalid/x?token={HIDDEN}")],
        native={
            "body": {"html": f"<p>Password:</p>&nbsp;{HIDDEN}"},
            "headers": [{"name": "x", "value": f"Bearer {HIDDEN}1234567890"}],
            "password": HIDDEN,
            "clientSecret": 1234567,
            "nested": {"apiKey": HIDDEN, "enabled": True, "count": 3, "missing": None, "empty": ""},
            "flags": {"pwd": True, "secret": None, "token": ""},
            "mobile": "+1 555 010 0103",
            "cell": "B12",
            "+1 555 010 0104": "a key that is a number",
            "+1 555 010 0105": "another",
        },
    )
    out, counts = redactor.record(record)
    dump = out.model_dump_json()
    assert HIDDEN not in dump
    assert GH_TOKEN not in dump
    assert out.author == record.author
    assert out.to == record.to
    assert out.title == "password: [redacted:password]"
    assert out.attachments[0].name == "[redacted:phone].vcf"
    assert out.attachments[0].ref == "https://files.example.invalid/x?token=[redacted:api-key]"
    assert out.native["password"] == "[redacted:password]"  # noqa: S105 - a marker, not a password
    assert out.native["clientSecret"] == "[redacted:api-key]"
    assert out.native["nested"] == {
        "apiKey": "[redacted:api-key]",
        "enabled": True,
        "count": 3,
        "missing": None,
        "empty": "",
    }
    assert out.native["mobile"] == "[redacted:phone]"
    assert out.native["cell"] == "B12"
    assert out.native["[redacted:phone]"] == "a key that is a number"
    assert out.native["[redacted:phone]#2"] == "another"
    assert out.native["flags"] == {"pwd": True, "secret": None, "token": ""}
    assert counts["phone"] == 4
    assert markers(out) == counts
    again, more = redactor.record(out)
    assert again == out
    assert more == Counter()


def test_a_record_without_a_title_or_attachment_ref_keeps_them_absent(redactor: Redactor) -> None:
    record = make_record(attachments=[Attachment(name="notes.txt")])
    out, _ = redactor.record(record)
    assert out.title is None
    assert out.attachments[0].ref is None


def test_a_transcript_s_segments_are_redacted_like_its_text(redactor: Redactor) -> None:
    record = make_record(
        rid="calls:rec-1",
        source="calls",
        kind="recording",
        text=f"the key is {GH_TOKEN}",
        segments=[
            {"start_ms": 0, "end_ms": 4000, "speaker": "Ben Sample", "text": f"the key is {GH_TOKEN}"},
            {"start_ms": 4000, "end_ms": 9000, "speaker": "+1 555 010 0106", "text": f"password: {HIDDEN}"},
            {"start_ms": 9000, "end_ms": 9500, "text": "bye"},
        ],
    )
    out, counts = redactor.record(record)
    assert GH_TOKEN not in out.model_dump_json()
    assert HIDDEN not in out.model_dump_json()
    segments = out.segments or []
    assert [s.text for s in segments] == ["the key is [redacted:api-key]", "password: [redacted:password]", "bye"]
    assert [s.speaker for s in segments] == ["Ben Sample", "[redacted:phone]", None]
    assert [(s.start_ms, s.end_ms) for s in segments] == [(0, 4000), (4000, 9000), (9000, 9500)]
    assert markers(out) == counts
    assert redactor.record(make_record())[0].segments is None


def transcript(*said: str) -> RawRecord:
    segments = [{"start_ms": 1000 * n, "end_ms": 1000 * n + 900, "text": t} for n, t in enumerate(said)]
    return make_record(rid="calls:rec-1", source="calls", kind="recording", text=" ".join(said), segments=segments)


def test_a_label_and_its_value_in_two_segments_are_still_redacted(redactor: Redactor) -> None:
    out, counts = redactor.record(transcript("call my mobile", "06 30 123 4567 after five"))
    assert "4567" not in out.model_dump_json()
    assert [s.text for s in out.segments or []] == ["call my mobile", "[redacted:phone] after five"]
    assert markers(out) == counts


def test_a_match_over_a_boundary_takes_each_segment_it_touches_whole(redactor: Redactor) -> None:
    head, body, tail = PEM.split("\n")
    out, counts = redactor.record(
        transcript("call me on +36 30 123 4567, the key is", f"{head}\n{body}", tail, "thanks, bye")
    )
    assert [s.text for s in out.segments or []] == [
        "call me on [redacted:phone], the key is",
        "[redacted:private-key]",
        "[redacted:private-key]",
        "thanks, bye",
    ]
    assert markers(out) == counts


# -- every rule reads the same text (#147) ---------------------------------------------------------------------


@pytest.mark.parametrize("tck", [False, True])
def test_a_rule_turned_on_never_splits_another_s_match(tck: bool) -> None:
    redactor = Redactor.from_config(Redaction(patterns=[pattern("tck", "TCK", tck), pattern("tck-id", r"TCK-\d{6}")]))
    assert redacted(redactor, "ticket TCK-123456 is open") == ("ticket [redacted:tck-id] is open", {"tck-id": 1})


@pytest.mark.parametrize("on", [False, True])
def test_a_built_in_rule_never_splits_a_user_pattern(on: bool) -> None:
    dial_in = patterns(("dial-in", r"Dial-in: \+[\d ]+ PIN \d+"), phone=on)
    assert redacted(dial_in, "Dial-in: +1 555 010 0199 PIN 482913#") == ("[redacted:dial-in]#", {"dial-in": 1})
    dsn = patterns(("dsn", r"postgres://\S+"), credentials=on)
    text = f"use postgres://ada:{HIDDEN}@db7.example.invalid/orders today"
    assert redacted(dsn, text) == ("use [redacted:dsn] today", {"dsn": 1})


def test_overlapping_spans_merge_under_the_longest() -> None:
    nested = [("inner", "TCK"), ("outer", r"TCK-\d{6}")]
    crossing = [("acme-id", r"ACME-\d{4}"), ("date", r"\d{4}-\d{2}")]
    for pairs in (nested, nested[::-1]):
        assert redacted(patterns(*pairs), "ticket TCK-123456 is open") == (
            "ticket [redacted:outer] is open",
            {"outer": 1},
        )
    for pairs in (crossing, crossing[::-1]):
        assert redacted(patterns(*pairs), "ref ACME-2026-10 done") == ("ref [redacted:acme-id] done", {"acme-id": 1})
    chain = patterns(("a", "abcde"), ("b", "efgh"), ("c", "hijklmn"))
    assert redacted(chain, "abcdefghijklmn!") == ("[redacted:c]!", {"c": 1})
    # The same span: a tie, so the rule that comes first names it, a built-in before the user's patterns.
    same = patterns(("hunter", r"hunter2-\w+"), credentials=True)
    assert redacted(same, f"password: {HIDDEN}") == ("password: [redacted:password]", {"password": 1})
    assert redacted(patterns(("one", "AB"), ("two", "AB")), "xABx") == ("x[redacted:one]x", {"one": 1})


def test_spans_that_only_touch_stay_two_markers() -> None:
    assert redacted(patterns(("a", "AB"), ("b", "CD")), "xABCDx") == ("x[redacted:a][redacted:b]x", {"a": 1, "b": 1})


def test_a_merged_span_over_a_boundary_takes_each_segment_whole() -> None:
    redactor = patterns(("tck", "TCK"), ("tck-id", r"TCK-\s*\d{6}"))
    out, counts = redactor.record(transcript("the ticket is TCK-", "123456 and it is open", "bye"))
    assert [s.text for s in out.segments or []] == ["[redacted:tck-id]", "[redacted:tck-id]", "bye"]
    assert markers(out) == counts
    doc = derived.redact("recording-note", "the ticket is TCK- @00:10\n123456 and it is open @00:20", redactor)
    assert doc.body == "[redacted:tck-id] @00:10\n[redacted:tck-id] @00:20"
    assert doc.lines == [0, 1]


def test_a_match_a_marker_makes_is_redacted_the_first_time() -> None:
    # Today's order found `code` only when the text was redacted again, after X's marker: the gate's two redactions.
    code = patterns(("code", r"\bcode\b"), ("x", "X"))
    assert code.text("see Xcode now") == "see [redacted:x][redacted:code] now"
    # A match today's order found after an earlier marker, found as before.
    tck = patterns(("tck", "TCK"), ("num", r"\b\d{6}\b"))
    assert tck.text("see TCK123456 now") == "see [redacted:tck][redacted:num] now"
    # A match an earlier rule's marker cut off from what it looks at: the password after a token as a URL's user.
    url = Redactor.from_config(Redaction())
    out = url.text(f"https://{GH_TOKEN}:x-oauth-basic@git.example.invalid/r")
    assert out == "https://[redacted:api-key]:[redacted:password]@git.example.invalid/r"


def test_a_chain_past_the_cap_stops_at_four_passes() -> None:
    # Each pass finds 8 more characters after the marker before them; the fourth pass is the last (DR-0525).
    chain = patterns(("x", "X"), ("hash", r"\b[0-9a-f]{8}"))
    once = chain.text("X" + "0123456789abcdef" * 4)
    assert once == "[redacted:x]" + "[redacted:hash]" * 3 + "89abcdef" + "0123456789abcdef" * 2
    assert chain.text(once) != once


# -- the properties, on generated cases: invented words, a label and a phone number among them -------------------

WORDS = (
    "ticket", "TCK-123456", "TCK", "x", "id7", "ACME-2026-10", "+1 555 010 0199", "tel:", "06 1 555 0123", "password:",
    "Example-1", "token=", "abcdef0123456789", "PIN", "482913", "-", "+", ":", "@", "7", "a1b2", "/", "x+15550100199",
)  # fmt: skip
# Without \b, anchors or lookarounds: a match found only at another rule's marker needs one of those (DR-0525).
PIECES = (
    r"\d+", r"\d{3}", r"[A-Z]+", r"[a-z]+", r"-\d", r"\+\d", "TCK", r"TCK-\d{6}", "x", r"\d \d", r"[a-z]\d", r"\S+",
    r"PIN \d+", r":\S", r"@[a-z]", r"\d{4}-\d{2}", r"[0-9a-f]{4}",
)  # fmt: skip
# With \b at both ends: one with no end boundary (\b\w{2}) chains past the cap.
BOUNDED = (r"\b\d+\b", r"\b[a-z]+\b", r"\b\w{2}\b", r"\b\d{2}\b")
CASES = 2000


@dataclass(frozen=True)
class Case:
    words: tuple[str, ...]
    text: str
    patterns: tuple[str, ...]
    on: tuple[bool, ...]
    credentials: bool
    phone: bool

    def redactor(self, on: tuple[bool, ...] | None = None, credentials: bool = False, phone: bool = False) -> Redactor:
        enabled = on if on is not None else self.on
        return Redactor.from_config(
            Redaction(
                credentials=self.credentials or credentials,
                phone=self.phone or phone,
                patterns=[pattern(f"p{i}", p, e) for i, (p, e) in enumerate(zip(self.patterns, enabled, strict=True))],
            )
        )

    def turned_on(self) -> list[Redactor]:
        """The redactor with one more rule on, for each rule that is off: a pattern, credentials, phone."""
        out = [self.redactor(tuple(o or j == k for j, o in enumerate(self.on))) for k, o in enumerate(self.on) if not o]
        out += [self.redactor(credentials=True)] if not self.credentials else []
        return out + ([self.redactor(phone=True)] if not self.phone else [])


def cases(seed: int, pieces: tuple[str, ...], builtins: bool = True) -> list[Case]:
    rng = random.Random(seed)  # noqa: S311 - seeded test cases, not secrets
    out: list[Case] = []
    for _ in range(CASES):
        words = tuple(rng.choice(WORDS) for _ in range(rng.randint(3, 9)))
        text = (" " if rng.random() < 0.7 else "").join(words)
        regexes = tuple("".join(rng.choice(pieces) for _ in range(rng.randint(1, 2))) for _ in range(rng.randint(1, 5)))
        on = tuple(rng.random() < 0.6 for _ in regexes)
        credentials, phone = (rng.random() < 0.7, rng.random() < 0.5) if builtins else (False, False)
        out.append(Case(words, text, regexes, on, credentials, phone))
    return out


def rule_spans(redactor: Redactor, text: str) -> list[list[tuple[int, int]]]:
    """What each rule matches on the text as given, found with re alone: each match whole, or its secret groups."""
    out: list[list[tuple[int, int]]] = []
    for rule in redactor.rules:
        found: list[tuple[int, int]] = []
        for regex in rule.patterns:
            for m in regex.finditer(text):
                secrets = [m.span(g) for g, v in m.groupdict().items() if g.startswith("secret") and v is not None]
                found += [(a, b) for a, b in secrets or [m.span()] if a != b]
        out.append(found)
    return out


def matched(redactor: Redactor, text: str) -> set[int]:
    return {i for found in rule_spans(redactor, text) for a, b in found for i in range(a, b)}


def covered(redactor: Redactor, text: str) -> set[int]:
    return {i for s in redactor.spans(text) for i in range(s.start, s.end)}


def covered_segments(redactor: Redactor, words: tuple[str, ...]) -> set[int]:
    """The characters of the segments joined by line breaks that their markers replace."""
    out: set[int] = set()
    start = 0
    for word, spans in zip(words, redactor.segment_spans(list(words)), strict=True):
        out |= {start + i for s in spans for i in range(s.start, s.end)}
        start += len(word) + 1
    return out


def test_what_any_rule_matches_stays_redacted_when_a_rule_is_turned_on() -> None:
    for case in cases(147, PIECES):
        joined = "\n".join(case.words)
        breaks = {i for i, ch in enumerate(joined) if ch == "\n"}
        before = case.redactor()
        hidden, hidden_segments = matched(before, case.text), matched(before, joined) - breaks
        for redactor in [before, *case.turned_on()]:
            assert hidden <= covered(redactor, case.text), case
            assert hidden_segments <= covered_segments(redactor, case.words), case


def before_147(redactor: Redactor, text: str) -> str:
    """The redactor as it was before #147, the reference: each rule in order, reading only the text between the
    markers the rules before it left."""
    for rule in redactor.rules:
        for regex in rule.patterns:
            out: list[str] = []
            start = last = 0
            for m in [*MARKER.finditer(text), None]:
                end = len(text) if m is None else m.start()
                for found in regex.finditer(text[start:end]):
                    if found.end() > found.start():
                        out += [text[last : start + found.start()], marker(rule.name)]
                        last = start + found.end()
                start = end if m is None else m.end()
            text = "".join([*out, text[last:]])
    return text


def test_text_where_nothing_overlaps_redacts_as_before() -> None:
    checked = 0
    for case in cases(1470, PIECES, builtins=False):
        redactor = case.redactor()
        spans = [(a, b, k) for k, found in enumerate(rule_spans(redactor, case.text)) for a, b in found]
        if any(k != j and a < d and c < b for a, b, k in spans for c, d, j in spans):
            continue
        checked += 1
        assert redactor.text(case.text) == before_147(redactor, case.text), case
    assert checked > CASES // 4
