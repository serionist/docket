"""How every page link the service builds encodes its query string: one rule, which the React app's router encodes by
too, so a URL the server writes and one the app writes are the same bytes (DR-0792).

ASCII letters and digits, `_ . - ~` and `URL_SAFE` stay as they are, a space is `+`, and everything else is
percent-encoded in upper-case hex, `* ! ' ( )` included: the standard library's `urlencode` with `quote_plus`.
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlencode

# Kept as they are in a query string, so a locator, a ref and a path read in the address bar as they do everywhere else.
URL_SAFE = ":@/,"


def query(pairs: Sequence[tuple[str, str]]) -> str:
    """The pairs as a query string, in their order, without the `?`."""
    return urlencode(pairs, safe=URL_SAFE)


def link(path: str, pairs: Sequence[tuple[str, str]]) -> str:
    """A path with its query string, or the path alone when there are no pairs."""
    found = query(pairs)
    return f"{path}?{found}" if found else path
