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


# ------------------------------------------------ multi-package (all-packages)


def _write_wheel(path: Path, name: str, version: str) -> Path:
    import zipfile

    dist_info = f"{name.replace('-', '_')}-{version}.dist-info"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{name.replace('-', '_')}/__init__.py", "")
        zf.writestr(
            f"{dist_info}/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        )
    return path


def test_wheel_identity_reads_the_name_from_metadata_not_the_filename(tmp_path):
    wheel = _write_wheel(tmp_path / "whatever-0-py3-none-any.whl", "my-dist", "1.2.3")
    assert verify_wheel.wheel_identity(wheel) == ("my-dist", "1.2.3")


def test_identify_lists_every_wheel(tmp_path):
    a = _write_wheel(tmp_path / "a.whl", "pkg-a", "1.0.0")
    b = _write_wheel(tmp_path / "b.whl", "pkg-b", "1.0.0")
    ok, message = verify_wheel.identify([a, b], lockstep=True)
    assert ok is True
    assert message.splitlines() == ["pkg-a 1.0.0", "pkg-b 1.0.0"]


def test_identify_lockstep_refuses_two_versions(tmp_path):
    a = _write_wheel(tmp_path / "a.whl", "pkg-a", "1.0.0")
    b = _write_wheel(tmp_path / "b.whl", "pkg-b", "1.1.0")
    ok, message = verify_wheel.identify([a, b], lockstep=True)
    assert ok is False
    assert "lockstep" in message and "pkg-a 1.0.0" in message and "pkg-b 1.1.0" in message
    # Without the flag the same wheels are merely listed.
    assert verify_wheel.identify([a, b], lockstep=False)[0] is True


def test_identify_lockstep_refuses_the_same_name_twice(tmp_path):
    a = _write_wheel(tmp_path / "a.whl", "pkg_a", "1.0.0")
    b = _write_wheel(tmp_path / "b.whl", "pkg-a", "1.0.0")
    assert verify_wheel.identify([a, b], lockstep=True)[0] is False


def test_identify_reports_an_unreadable_wheel(tmp_path):
    bad = tmp_path / "bad.whl"
    bad.write_bytes(b"not a zip")
    ok, message = verify_wheel.identify([bad], lockstep=False)
    assert ok is False and "cannot read wheel metadata" in message


def test_main_identify_cli(tmp_path, capsys):
    a = _write_wheel(tmp_path / "a.whl", "pkg-a", "2.0.0")
    assert verify_wheel.main(["--identify", "--lockstep", str(a)]) == 0
    assert capsys.readouterr().out.strip() == "pkg-a 2.0.0"
    assert verify_wheel.main(["--identify"]) == 2


def test_main_checks_every_name_and_fails_if_any_fails():
    assert verify_wheel.main(["pytest", "pytest"]) == 0
    assert verify_wheel.main(["pytest", "this-distribution-does-not-exist-xyz"]) == 1
    assert verify_wheel.main([]) == 2
