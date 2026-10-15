"""The index: a per-machine SQLite cache of the vault, and keyword search over it.

`rebuild`, `update`, `status`, `search` and `locate` are what the service (#8) and the CLI call.
"""

from docket.index.build import BuildReport, IndexStatus, RecordLocation, locate, rebuild, status, update
from docket.index.query import Group, Hit, QueryReading, SearchResult, parse_when, search

__all__ = [
    "BuildReport",
    "Group",
    "Hit",
    "IndexStatus",
    "QueryReading",
    "RecordLocation",
    "SearchResult",
    "locate",
    "parse_when",
    "rebuild",
    "search",
    "status",
    "update",
]
