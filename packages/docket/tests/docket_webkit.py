"""What the web app's tests share: a signed-in client on a copy of the index fixture vault, a small HTML tree built
with the standard library's parser, and the two checks every rendered state passes: the HTML check (nothing the
page CSP would refuse, nothing that needs htmx's eval) and the structural accessibility rules."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from starlette.testclient import TestClient

from docket.service.app import create_app
from docket.service.sessions import COOKIE, Sessions
from docket.service.state import Settings
from docket.web import assets

PORT = 8741
OURS = f"http://127.0.0.1:{PORT}"
TOKEN = "t0ken-for-tests-only"  # noqa: S105 - an invented token
VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})
# The htmx attributes the pages may use; none of them runs code. Filters in hx-trigger, hx-on and js: values do.
HTMX_ATTRIBUTES = frozenset(
    {"hx-get", "hx-target", "hx-trigger", "hx-headers", "hx-history-elt", "hx-swap", "hx-push-url", "hx-replace-url"}
)


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    children: list[Element | str] = field(default_factory=list["Element | str"])
    parent: Element | None = None

    def find_all(self, tag: str | None = None, **attrs: str) -> list[Element]:
        """Every element below, in document order, with this tag and these attribute values (`class_` for class,
        matching one of its classes)."""
        out: list[Element] = []
        for child in self.children:
            if isinstance(child, Element):
                if child.matches(tag, attrs):
                    out.append(child)
                out += child.find_all(tag, **attrs)
        return out

    def find(self, tag: str | None = None, **attrs: str) -> Element:
        found = self.find_all(tag, **attrs)
        assert found, f"no <{tag or '*'} {attrs}>"
        return found[0]

    def matches(self, tag: str | None, attrs: dict[str, str]) -> bool:
        if tag is not None and self.tag != tag:
            return False
        for name, value in attrs.items():
            if name == "class_":
                if value not in self.attrs.get("class", "").split():
                    return False
            elif self.attrs.get(name.replace("_", "-")) != value:
                return False
        return True

    def text(self) -> str:
        return " ".join("".join(c if isinstance(c, str) else f" {c.text()} " for c in self.children).split())

    def walk(self) -> Iterator[Element]:
        for child in self.children:
            if isinstance(child, Element):
                yield child
                yield from child.walk()

    def inside(self, tag: str) -> bool:
        node = self.parent
        while node is not None:
            if node.tag == tag:
                return True
            node = node.parent
        return False


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#root", {})
        self.at = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, {k: v or "" for k, v in attrs}, parent=self.at)
        self.at.children.append(element)
        if tag not in VOID:
            self.at = element

    def handle_endtag(self, tag: str) -> None:
        node: Element | None = self.at
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.at = node.parent

    def handle_data(self, data: str) -> None:
        self.at.children.append(data)


def parse(html: str) -> Element:
    builder = _Builder()
    builder.feed(html)
    builder.close()
    return builder.root


def html_problems(html: str) -> list[str]:
    """What the page CSP would refuse, or what would need htmx's eval: each a line naming the element."""
    problems: list[str] = []
    for el in parse(html).walk():
        where = f"<{el.tag} {el.attrs}>"
        if el.tag == "script" and "src" not in el.attrs:
            problems.append(f"an inline script: {where}")
        if el.tag == "style":
            problems.append(f"a style element: {where}")
        for name, value in el.attrs.items():
            if name == "style":
                problems.append(f"a style attribute: {where}")
            if name.startswith("on"):
                problems.append(f"a handler attribute {name}: {where}")
            if value.strip().lower().startswith(("javascript:", "js:")):
                problems.append(f"a script in {name}: {where}")
            if name.startswith(("hx-", "data-hx-")) and name.removeprefix("data-") not in HTMX_ATTRIBUTES:
                problems.append(f"an htmx attribute outside the list, {name}: {where}")
            if name == "hx-trigger" and "[" in value:
                problems.append(f"an htmx event filter, which needs eval: {where}")
        for name in ("src", "href"):
            target = el.attrs.get(name, "")
            served = target.removeprefix("/static/") in assets.ASSETS
            if el.tag in ("script", "link") and target.startswith("/static/") and not served:
                problems.append(f"an asset that isn't served: {target}")
            if el.tag in ("script", "link") and target.startswith(("http:", "https:", "//")):
                problems.append(f"an asset from elsewhere: {target}")
    return problems


def _named(el: Element) -> bool:
    return bool(el.text() or el.attrs.get("aria-label") or el.attrs.get("title") or el.attrs.get("aria-labelledby"))


def a11y_problems(html: str, *, whole: bool = True) -> list[str]:
    """The structural accessibility rules: on a whole page its language, one h1, the landmarks and a skip link that
    lands; on any part, labelled fields, named buttons and links, decorative icons hidden, ids unique."""
    tree = parse(html)
    problems: list[str] = []
    elements = list(tree.walk())
    ids = [el.attrs["id"] for el in elements if "id" in el.attrs]
    problems += [f"the id {i!r} appears twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
    if whole:
        page = tree.find("html")
        if not page.attrs.get("lang"):
            problems.append("the page has no lang")
        if len(tree.find_all("h1")) != 1:
            problems.append(f"the page has {len(tree.find_all('h1'))} h1 elements")
        for landmark in ("main", "header"):
            if not tree.find_all(landmark):
                problems.append(f"no <{landmark}>")
        skips = tree.find_all("a", class_="skip")
        if not skips or skips[0].attrs.get("href", "#")[1:] not in ids:
            problems.append("no skip link, or it lands nowhere")
    labelled = {el.attrs.get("for") for el in elements if el.tag == "label"}
    for el in elements:
        if el.tag == "input" and el.attrs.get("type") != "hidden":
            ok = el.attrs.get("id") in labelled or el.inside("label") or el.attrs.get("aria-label")
            if not ok:
                problems.append(f"a field without a label: {el.attrs}")
        if el.tag in ("button", "a") and not _named(el):
            problems.append(f"a {'link' if el.tag == 'a' else 'button'} without a name: {el.attrs}")
        if el.tag == "svg" and el.attrs.get("aria-hidden") != "true" and not el.attrs.get("aria-label"):
            problems.append(f"an svg neither hidden nor named: {el.attrs}")
    return problems


@contextmanager
def signed_in(template: Path, tmp_path: Path, sessions: Sessions | None = None) -> Iterator[TestClient]:
    """A client of the service on a copy of the fixture vault, holding a session cookie."""
    shutil.copytree(template, tmp_path / "vault")
    sessions = sessions or Sessions()
    app = create_app(Settings(port=PORT, vault=tmp_path / "vault", token=lambda: TOKEN), sessions=sessions)
    with TestClient(app, base_url=OURS, follow_redirects=False) as client:
        started = sessions.redeem(sessions.new_code())
        assert started is not None
        client.cookies.set(COOKIE, started)
        yield client
