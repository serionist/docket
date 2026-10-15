"""The check that no test wrote into a shared template (DR-0220). It lives here, not in the tests' own conftest.py,
because under xdist the controller never collects the tests and never loads that one; this one it loads, since it sits
above every test of the packages."""

from __future__ import annotations

import pytest
from docket_testkit import template_changes


def pytest_sessionfinish(session: pytest.Session) -> None:
    # Once, after every worker has finished: the controller under xdist, or the one process without it.
    if hasattr(session.config, "workerinput"):
        return
    root = session.config._tmp_path_factory.getbasetemp()  # type: ignore[attr-defined]
    problems = template_changes(root)
    if problems:
        print("\ntests wrote into shared templates, which only their copies may take:")
        for line in problems:
            print(f"  {line}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
