"""Redaction. Secret-shaped strings are assembled from parts at runtime, so the repo's secret scan sees no literal."""

from __future__ import annotations

import time
from collections import Counter

import pytest
from docket_testkit import make_record

from docket.vault.config import Redaction, RedactionPattern
from docket.vault.records import Attachment
from docket.vault.redact import Redactor, markers, slug

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


@pytest.fixture
def redactor() -> Redactor:
    return Redactor.from_config(Redaction())


def redacted(redactor: Redactor, text: str) -> tuple[str, Counter[str]]:
    counts = Counter[str]()
    return redactor.text(text, counts), counts


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
    ]
    start = time.perf_counter()
    for text in inputs:
        redactor.text(text)
    assert time.perf_counter() - start < 5


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
    ],
)
def test_numbers_and_words_docket_keeps(redactor: Redactor, text: str) -> None:
    assert redacted(redactor, text) == (text, Counter())


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


def test_built_ins_off_leaves_only_the_user_s_patterns() -> None:
    redactor = Redactor.from_config(Redaction(builtin=False, patterns=[RedactionPattern(name="x", pattern="zzz")]))
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
