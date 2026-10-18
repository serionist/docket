"""The commands Docket hands the user to copy, on a page or at the console: a value goes into one, inside double
quotes, only when every shell reads it as itself there (DR-0382); otherwise its placeholder stands in and the value is
listed beside the command."""

from __future__ import annotations

import re

# What a value in a copied command may hold, inside double quotes: letters and digits in any script, and these. An
# allow-list, since shells read more than they show: PowerShell takes the curly quotes for a quote too, so a held id
# holding one would end the string and run what follows it.
SAFE = frozenset(" ._-:/\\@+,~")
# On top: a backslash before the closing quote, or two of them, change what bash reads.
BACKSLASHES = re.compile(r"\\\\|\\$")


def filled(value: str, name: str, unfilled: list[tuple[str, str]]) -> str:
    """A value as the command can carry it in double quotes, or its placeholder when it holds anything but letters,
    digits and the few characters every shell reads as themselves: then the value is listed beside the command, to put
    in by hand."""
    if not all(ch.isalnum() or ch in SAFE for ch in value) or BACKSLASHES.search(value):
        unfilled.append((name, value))
        return f"<{name}>"
    return value
