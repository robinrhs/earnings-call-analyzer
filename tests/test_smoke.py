"""Smoke test for the project skeleton: the package imports and its metadata is consistent."""

from importlib.metadata import version

import earnings_call_analyzer


def test_package_imports_and_reports_installed_version():
    assert earnings_call_analyzer.__version__ == version("earnings-call-analyzer")
