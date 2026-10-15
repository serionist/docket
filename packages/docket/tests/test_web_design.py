from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from docket_testkit import index_template
from docket_webkit import a11y_problems, html_problems, parse, signed_in
from starlette.testclient import TestClient

from docket.web import assets

TOKEN = re.compile(r"--([a-z0-9-]+):\s*light-dark\((#[0-9a-f]{6}),\s*(#[0-9a-f]{6})\)")
# (foreground, background, the least ratio): text 4.5:1; borders, the focus ring and icons 3:1 (WCAG 1.4.3, 1.4.11).
PAIRS = [
    *((ink, ground, 4.5) for ink in ("ink", "muted", "accent", "error", "warn", "ok") for ground in ("bg", "surface")),
    *((f"k-{kind}", "surface", 4.5) for kind in ("recording", "thread", "record", "event", "doc")),
    ("accent", "accent-soft", 4.5),
    ("accent-ink", "accent", 4.5),
    ("ink", "mark", 4.5),
    ("ink", "cited", 4.5),
    ("ink", "surface-2", 4.5),
    ("ink", "error-soft", 4.5),
    ("error", "error-soft", 4.5),
    ("ink", "warn-soft", 4.5),
    ("warn", "warn-soft", 4.5),
    ("muted", "hover", 4.5),
    ("line-strong", "surface", 3.0),
    ("line-strong", "bg", 3.0),
    ("focus", "surface", 3.0),
    ("focus", "bg", 3.0),
]


def tokens() -> dict[str, tuple[str, str]]:
    css = assets.ASSETS["css/tokens.css"].data.decode("utf-8")
    return {m.group(1): (m.group(2), m.group(3)) for m in TOKEN.finditer(css)}


def luminance(colour: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", [0, 1], ids=["light", "dark"])
@pytest.mark.parametrize(("fore", "back", "least"), PAIRS)
def test_the_tokens_contrast_enough_in_both_themes(theme: int, fore: str, back: str, least: float) -> None:
    found = tokens()
    ratio = contrast(found[fore][theme], found[back][theme])
    assert ratio >= least, f"{fore} on {back}: {ratio:.2f}"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


@pytest.mark.parametrize(
    ("cookies", "theme", "keys"),
    [
        ({}, None, "on"),
        ({"docket_theme": "dark"}, "dark", "on"),
        ({"docket_theme": "light", "docket_keys": "off"}, "light", "off"),
        ({"docket_theme": "purple", "docket_keys": "maybe"}, None, "on"),
    ],
)
def test_the_two_cookies_render_on_the_page(
    client: TestClient, cookies: dict[str, str], theme: str | None, keys: str
) -> None:
    for name, value in cookies.items():
        client.cookies.set(name, value)
    page = parse(client.get("/search").text).find("html")
    assert page.attrs.get("data-theme") == theme
    assert page.attrs["data-keys"] == keys
    switch = parse(client.get("/search").text).find("input", data_action="keys-switch")
    assert ("checked" in switch.attrs) == (keys == "on")


def test_the_checks_pass_on_the_shell_and_catch_what_they_look_for(client: TestClient) -> None:
    page = client.get("/search").text
    assert not html_problems(page)
    assert not a11y_problems(page)
    planted = page.replace('<main id="main"', '<main id="main" style="color: red" onclick="go()"')
    assert {p.split(":")[0] for p in html_problems(planted)} == {"a style attribute", "a handler attribute onclick"}
    planted = page.replace("</body>", '<script>go()</script><div hx-on:click="x" hx-trigger="keyup[ok]"></div></body>')
    assert len(html_problems(planted)) == 3
    planted = page.replace(
        '<link rel="icon"', '<link rel="stylesheet" href="https://cdn.example/x.css"><link rel="icon"'
    )
    assert html_problems(planted) == ["an asset from elsewhere: https://cdn.example/x.css"]
    planted = page.replace('<link rel="icon"', '<link rel="stylesheet" href="/static/gone.css"><link rel="icon"')
    assert html_problems(planted) == ["an asset that isn't served: /static/gone.css"]
    planted = page.replace('<a class="brand" href="/search">Docket</a>', '<a class="brand" href="/search"></a>')
    assert any("a link without a name" in p for p in a11y_problems(planted))
    planted = page.replace('<label class="visually-hidden" for="query">', '<label class="visually-hidden">')
    assert any("a field without a label" in p for p in a11y_problems(planted))
    planted = page.replace('<h1 class="visually-hidden">Search</h1>', "").replace('lang="en"', "")
    assert {"the page has no lang", "the page has 0 h1 elements"} <= set(a11y_problems(planted))
    planted = page.replace('aria-hidden="true" focusable="false"', "", 1).replace('href="#main"', 'href="#nowhere"')
    assert len(a11y_problems(planted)) == 2
    assert a11y_problems(page + '<p id="announce"></p>') == ["the id 'announce' appears twice"]
