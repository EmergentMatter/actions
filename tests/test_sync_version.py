"""Tests for scripts/sync_version.py.

Each test drives the CLI as a subprocess against a tmp_path fixture, since
that's exactly how the GitHub Actions workflow (and consumer CI) will
invoke it.
"""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "sync_version.py"


def write_pyproject(tmp_path: Path, version_files: list[str]) -> None:
    entries = ", ".join(f'"{f}"' for f in version_files)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "demo"\nversion = "0.0.0"\n\n'
        f"[tool.em-release]\nversion_files = [{entries}]\n"
    )


def run_sync_version(tmp_path: Path, *extra_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra_args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


def test_writes_multiple_declared_locations_in_one_pass(tmp_path: Path) -> None:
    """V4: every declared location gets written."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text('__version__ = "0.1.0"\n')
    (tmp_path / "manifest.json.py").write_text('version = "0.1.0"\nother = "unchanged"\n')
    write_pyproject(tmp_path, ["pkg/__init__.py:__version__", "manifest.json.py:version"])

    result = run_sync_version(tmp_path, "--version", "0.2.0")
    assert result.returncode == 0, result.stderr

    assert (tmp_path / "pkg" / "__init__.py").read_text() == '__version__ = "0.2.0"\n'
    manifest_text = (tmp_path / "manifest.json.py").read_text()
    assert 'version = "0.2.0"' in manifest_text
    assert 'other = "unchanged"' in manifest_text  # V5: nothing else is touched


def test_undeclared_location_is_never_touched(tmp_path: Path) -> None:
    """V5: locations not on the list are never touched."""
    (tmp_path / "declared.py").write_text('version = "0.1.0"\n')
    (tmp_path / "untouched.py").write_text('version = "0.1.0"\n')
    write_pyproject(tmp_path, ["declared.py:version"])

    result = run_sync_version(tmp_path, "--version", "0.2.0")
    assert result.returncode == 0, result.stderr

    assert (tmp_path / "declared.py").read_text() == 'version = "0.2.0"\n'
    assert (tmp_path / "untouched.py").read_text() == 'version = "0.1.0"\n'


def test_missing_declared_file_errors(tmp_path: Path) -> None:
    write_pyproject(tmp_path, ["does_not_exist.py:version"])
    result = run_sync_version(tmp_path, "--version", "0.2.0")
    assert result.returncode == 1
    assert "does_not_exist.py" in result.stderr


def test_declared_symbol_not_found_errors(tmp_path: Path) -> None:
    """A silent no-op here is the exact bug this script exists to prevent."""
    (tmp_path / "mod.py").write_text('other_symbol = "0.1.0"\n')
    write_pyproject(tmp_path, ["mod.py:__version__"])
    result = run_sync_version(tmp_path, "--version", "0.2.0")
    assert result.returncode == 1
    assert "__version__" in result.stderr
    # And the file must be left untouched on error.
    assert (tmp_path / "mod.py").read_text() == 'other_symbol = "0.1.0"\n'


def test_check_mode_passes_when_in_sync(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text('__version__ = "0.2.0"\n')
    write_pyproject(tmp_path, ["mod.py:__version__"])
    result = run_sync_version(tmp_path, "--version", "0.2.0", "--check")
    assert result.returncode == 0, result.stderr
    # --check writes nothing.
    assert (tmp_path / "mod.py").read_text() == '__version__ = "0.2.0"\n'


def test_check_mode_catches_hand_edited_drift(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text('__version__ = "0.1.0"\n')
    write_pyproject(tmp_path, ["mod.py:__version__"])
    result = run_sync_version(tmp_path, "--version", "0.2.0", "--check")
    assert result.returncode != 0
    assert "0.1.0" in result.stderr
    assert "0.2.0" in result.stderr
    # --check must never write, even when it finds drift.
    assert (tmp_path / "mod.py").read_text() == '__version__ = "0.1.0"\n'


def test_single_quoted_assignment_is_supported(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("__version__ = '0.1.0'\n")
    write_pyproject(tmp_path, ["mod.py:__version__"])
    result = run_sync_version(tmp_path, "--version", "0.2.0")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "mod.py").read_text() == "__version__ = '0.2.0'\n"


def test_no_declared_version_files_is_a_noop_success(tmp_path: Path) -> None:
    write_pyproject(tmp_path, [])
    result = run_sync_version(tmp_path, "--version", "0.2.0")
    assert result.returncode == 0, result.stderr
