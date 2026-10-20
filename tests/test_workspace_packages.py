"""Tests for scripts/workspace_packages.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "workspace_packages.py"
spec = importlib.util.spec_from_file_location("workspace_packages", SCRIPT)
wp = importlib.util.module_from_spec(spec)
sys.modules["workspace_packages"] = wp
spec.loader.exec_module(wp)


def _project(directory: Path, name: str, version: str | None = "1.0.0", extra: str = "") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    version_line = f'version = "{version}"\n' if version else ""
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\n{version_line}{extra}\n', encoding="utf-8"
    )


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    _project(root, "eng")
    (root / "pyproject.toml").write_text(
        (root / "pyproject.toml").read_text()
        + '[tool.uv.workspace]\nmembers = ["packages/*"]\nexclude = ["packages/skip"]\n',
        encoding="utf-8",
    )
    _project(root / "packages" / "tools", "eng_tools")
    _project(root / "packages" / "skip", "skipped")
    return root


def test_names_are_the_root_then_each_member_minus_exclude(workspace):
    assert [p.name for p in wp.workspace_packages(workspace)] == ["eng", "eng_tools"]


def test_a_repo_without_a_workspace_table_is_a_workspace_of_one(tmp_path):
    _project(tmp_path, "solo")
    assert [p.name for p in wp.workspace_packages(tmp_path)] == ["solo"]


def test_a_virtual_root_contributes_only_its_members(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers = ["pkgs/*"]\n', encoding="utf-8"
    )
    _project(tmp_path / "pkgs" / "a", "a")
    _project(tmp_path / "pkgs" / "b", "b")
    assert [p.name for p in wp.workspace_packages(tmp_path)] == ["a", "b"]


def test_a_dynamic_version_is_refused(workspace):
    _project(workspace / "packages" / "tools", "eng_tools", None, 'dynamic = ["version"]')
    with pytest.raises(wp.WorkspaceError, match="dynamic version"):
        wp.workspace_packages(workspace)


def test_a_member_directory_without_a_pyproject_is_refused(workspace):
    (workspace / "packages" / "empty").mkdir()
    with pytest.raises(wp.WorkspaceError, match="no pyproject.toml"):
        wp.workspace_packages(workspace)


def test_the_same_name_spelled_two_ways_is_refused(workspace):
    _project(workspace / "packages" / "twin", "Eng-Tools")
    with pytest.raises(wp.WorkspaceError, match="share a distribution name"):
        wp.workspace_packages(workspace)


def test_check_version_names_every_package_out_of_step(workspace):
    _project(workspace / "packages" / "tools", "eng_tools", "0.9.0")
    problems = wp.check_version(wp.workspace_packages(workspace), "1.0.0")
    assert problems == ["eng_tools is at 0.9.0, expected 1.0.0"]


def test_cli_names_and_check(workspace, capsys):
    assert wp.main(["--root", str(workspace), "names"]) == 0
    assert capsys.readouterr().out.split() == ["eng", "eng_tools"]
    assert wp.main(["--root", str(workspace), "check", "--version", "1.0.0"]) == 0
    assert wp.main(["--root", str(workspace), "check", "--version", "1.1.0"]) == 1
    err = capsys.readouterr().err
    assert "not in lockstep" in err and "eng is at 1.0.0" in err


def test_cli_reports_a_bad_workspace_as_an_error_not_a_traceback(tmp_path, capsys):
    assert wp.main(["--root", str(tmp_path), "names"]) == 1
    assert "::error::" in capsys.readouterr().err
