"""The index: a per-machine SQLite cache of the vault, and keyword search over it.

`rebuild`, `update`, `status`, `search` and `locate` are what the service (#8) and the CLI call; `labels` and
`summary_for` are what the web app (#9) adds around a search.
"""

from docket.index.build import (
    BuildReport,
    IndexStatus,
    RecordLocation,
    holds_kind,
    kept,
    locate,
    rebuild,
    status,
    stored_under,
    update,
)
from docket.index.query import (
    DocRef,
    EntityLabel,
    Group,
    Hit,
    QueryReading,
    SearchResult,
    labels,
    parse_when,
    search,
    summary_for,
)

__all__ = [
    "BuildReport",
    "DocRef",
    "EntityLabel",
    "Group",
    "Hit",
    "IndexStatus",
    "QueryReading",
    "RecordLocation",
    "SearchResult",
    "holds_kind",
    "kept",
    "labels",
    "locate",
    "parse_when",
    "rebuild",
    "search",
    "status",
    "stored_under",
    "summary_for",
    "update",
]
