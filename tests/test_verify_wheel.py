"""Tests for scripts/verify_wheel.py.

The end-to-end behaviour was confirmed against real wheels: a hatchling
config with `exclude = ["*.py"]` builds SUCCESSFULLY and produces a wheel
containing only `.dist-info` metadata, which this rejects. These cover the
file-classification logic that decision rests on.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_wheel.py"
spec = importlib.util.spec_from_file_location("verify_wheel", SCRIPT)
verify_wheel = importlib.util.module_from_spec(spec)
sys.modules["verify_wheel"] = verify_wheel
spec.loader.exec_module(verify_wheel)

top = verify_wheel.top_level_modules


def test_a_metadata_only_wheel_has_no_importable_modules():
    """The real failure mode: uv build succeeds, wheel ships no code."""
    files = [
        "wheeltest_pkg-0.1.0.dist-info/METADATA",
        "wheeltest_pkg-0.1.0.dist-info/WHEEL",
        "wheeltest_pkg-0.1.0.dist-info/RECORD",
    ]
    assert top(files) == set()


def test_finds_a_package_directory():
    files = ["goodpkg/__init__.py", "goodpkg/core.py", "p-1.0.dist-info/METADATA"]
    assert top(files) == {"goodpkg"}


def test_finds_a_single_module_distribution():
    assert top(["lonely.py", "p-1.0.dist-info/RECORD"]) == {"lonely"}


def test_ignores_data_directories():
    files = ["pkg/__init__.py", "p-1.0.data/scripts/tool", "p-1.0.dist-info/METADATA"]
    assert top(files) == {"pkg"}


def test_ignores_pth_files():
    assert top(["_editable.pth", "pkg/__init__.py"]) == {"pkg"}


def test_ignores_private_top_levels():
    """Failing to import a vendored `_internal` would be a false alarm."""
    assert top(["pkg/__init__.py", "_vendor/thing.py"]) == {"pkg"}


def test_handles_windows_separators():
    assert top(["pkg\\__init__.py"]) == {"pkg"}


def test_multiple_packages_are_all_reported():
    files = ["one/__init__.py", "two/__init__.py", "p-1.0.dist-info/RECORD"]
    assert top(files) == {"one", "two"}


def test_verify_reports_a_missing_distribution_clearly():
    ok, message = verify_wheel.verify("this-distribution-does-not-exist-xyz")
    assert ok is False
    assert "not installed" in message


@pytest.mark.parametrize("name", ["pytest"])
def test_verify_passes_for_a_real_installed_distribution(name):
    """Guards against the check being vacuously strict."""
    ok, message = verify_wheel.verify(name)
    assert ok is True, message
    assert "imported" in message
