"""A pytest plugin the gate loads for `--shard K/N`: the run keeps one share of the test files, every Nth in path
order starting at the Kth, so the shares of one CI job's tests run on runners of their own (DR-0495).

A file stays whole in one share: its module fixtures, and a timed test's vault that an untimed test of the same file
builds in the parallel run (DR-0492), never cross to another runner. The share is taken before any other filter, so
the parallel run and the timed run of one share keep the same files.
"""

from __future__ import annotations

import re

import pytest

_SPEC = re.compile(r"^([1-9]\d*)/([1-9]\d*)$")


def parse(spec: str) -> tuple[int, int]:
    """`K/N` as (K, N): share K of N, 1 <= K <= N."""
    found = _SPEC.match(spec)
    if found is None or not 1 <= int(found.group(1)) <= int(found.group(2)):
        raise ValueError(f"--shard takes K/N with 1 <= K <= N, such as 1/2, not {spec!r}")
    return int(found.group(1)), int(found.group(2))


def share(files: set[str], k: int, n: int) -> set[str]:
    """The files of share K of N: every Nth in sorted order, from the Kth."""
    return {f for i, f in enumerate(sorted(files)) if i % n == k - 1}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shard", default=None, metavar="K/N", help="run share K of N of the test files (DR-0495)")


def pytest_configure(config: pytest.Config) -> None:
    spec = config.getoption("--shard")
    if spec is not None:
        try:
            parse(spec)
        except ValueError as e:
            raise pytest.UsageError(str(e)) from e


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    spec = config.getoption("--shard")
    if spec is None:
        return
    k, n = parse(spec)
    files = {item.path.relative_to(config.rootpath).as_posix() for item in items}
    kept = share(files, k, n)
    selected = [item for item in items if item.path.relative_to(config.rootpath).as_posix() in kept]
    deselected = [item for item in items if item.path.relative_to(config.rootpath).as_posix() not in kept]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
