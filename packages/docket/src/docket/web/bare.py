"""The pages the service writes itself, as plain HTML with no template and no script (DR-0793): the sign-in page a
browser without a session gets, the used-link page a used or stale one-time link gets, and the page a failure
answers on a page path. Each must work before, and without, the app's script: they name the one command to run, or
say that something failed, and nothing else.

They name neither the vault's folder nor the machine, link only the app's stylesheet and the icon, both static files
that need no session (DR-0793), and carry a policy stricter than the app's: no script at all, no form. A failure's
page says no more than that something failed: never the error, a path or a trace.
"""

from __future__ import annotations

import html

from starlette.responses import HTMLResponse

from docket.service.sessions import open_command
from docket.web.display import FAILED

# The app's stylesheet and the icon: both under /static/, which the Guard serves without a session.
STYLESHEET = "/static/app/app.css"
ICON = "/static/icon.svg"
CSP = (
    "default-src 'none'; style-src 'self'; font-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)


def document(title: str, body: str) -> str:
    """A whole page: the brand in a header, then `body`, already escaped, as its main part."""
    return (
        '<!doctype html>\n<html lang="en" data-theme="system">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f"<title>{html.escape(title)} - Docket</title>\n"
        f'<link rel="icon" href="{ICON}" type="image/svg+xml">\n'
        f'<link rel="stylesheet" href="{STYLESHEET}">\n'
        '</head>\n<body class="bare">\n'
        '<a class="skip" href="#main">Skip to the main content</a>\n'
        '<header class="bare-head"><span class="bare-brand"><span class="bare-mark" aria-hidden="true">D</span>'
        "Docket</span></header>\n"
        f'<main id="main" class="bare-main">\n{body}</main>\n</body>\n</html>\n'
    )


def answer(page: str, status: int) -> HTMLResponse:
    """The page with its policy, and `no-store`: the command names the page asked for, the user's own words."""
    return HTMLResponse(page, status_code=status, headers={"Content-Security-Policy": CSP, "Cache-Control": "no-store"})


def _command(text: str) -> str:
    # Selected whole on a click, so the one line copies without a script.
    return f'<pre class="bare-command"><code>{html.escape(text)}</code></pre>\n'


def sign_in_page(wanted: str) -> str:
    """What a browser without a session gets: the command that opens the page it asked for."""
    body = (
        "<h1>Open Docket from a terminal on this machine</h1>\n"
        + _command(open_command(wanted))
        + '<p class="bare-note">It opens this page through a link that works once, for a minute. A session lasts 12 '
        "hours, and ends when the service restarts.</p>\n"
    )
    return document("Sign in", body)


def used_link_page(after: str) -> str:
    """What a used or stale one-time link gets: run `docket open` again, for the same page."""
    body = (
        "<h1>This link was used, or is over a minute old</h1>\n"
        "<p>Run it again from a terminal on this machine:</p>\n" + _command(open_command(after))
    )
    return document("Link used", body)


def failed_page() -> str:
    """What a failure on a page path answers: that something failed, and where its words are, never what they are."""
    head, _, rest = FAILED.partition(". ")
    body = (
        f"<h1>{html.escape(head)}</h1>\n<p>{html.escape(rest)}</p>\n"
        '<p><a href="/">Back to Today</a>, or reload the page to try again.</p>\n'
    )
    return document("Something failed", body)


def sign_in(wanted: str) -> HTMLResponse:
    return answer(sign_in_page(wanted), 401)


def used_link(after: str) -> HTMLResponse:
    return answer(used_link_page(after), 401)


def failed() -> HTMLResponse:
    return answer(failed_page(), 500)
