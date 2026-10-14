import pytest

from docket import __version__
from docket.cli import main


def test_version_prints_the_installed_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"docket {__version__}"


def test_no_arguments_succeeds() -> None:
    assert main([]) == 0


def test_python_dash_m_runs_the_command(capsys: pytest.CaptureFixture[str]) -> None:
    import runpy
    import sys

    argv = sys.argv
    sys.argv = ["docket", "--version"]
    try:
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_module("docket", run_name="__main__")
    finally:
        sys.argv = argv
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.startswith("docket ")
