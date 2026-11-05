"""verify-wheel keeps an organization name off the public index.

Passing a declared `explicit = true` index as `--index` makes it an ordinary index, so a name
that is NOT on it would fall through to the default one. The action therefore resolves first
and refuses an organization package that came from somewhere it must not. These run the
action's own shell against toy `file://` indexes: the declared one, and a stand-in for the
public index that carries a package of the same organization name.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import MATERIALS, _uv_build, _write_leaf, simple_index_from

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
GHOST = "emergent-matter-toy-ghost"


def _run_block() -> str:
    text = (ROOT / "verify-wheel" / "action.yml").read_text(encoding="utf-8")
    body = text.split("      run: |\n", 1)[1]
    return "\n".join(
        line[8:] if line.startswith("        ") else line for line in body.splitlines()
    )


def _project(tmp_path: Path, declared_index: str, deps: list[str]) -> Path:
    project = tmp_path / "proj"
    (project / "src" / "gw_app").mkdir(parents=True)
    (project / "src" / "gw_app" / "__init__.py").write_text("")
    deps_toml = ", ".join(f'"{d}"' for d in deps)
    (project / "pyproject.toml").write_text(
        f"""\
[project]
name = "gw-app"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = [{deps_toml}]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/gw_app"]

[[tool.uv.index]]
name = "em"
url = "{declared_index}"
explicit = true
"""
    )
    return project


def _run_action(project: Path, default_index: str, tmp_path: Path) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)
    script = tmp_path / "run.sh"
    script.write_text(_run_block())
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "UV_DEFAULT_INDEX": default_index,
        "DIST_DIR": "dist",
        "PACKAGE_NAME": "",
        "PY_VERSION": "3.13",
        "ALL_PACKAGES": "false",
        "VERIFY_SCRIPT": str(ROOT / "scripts" / "verify_wheel.py"),
        "INDEXES_SCRIPT": str(ROOT / "scripts" / "declared_indexes.py"),
        "SOURCES_SCRIPT": str(ROOT / "scripts" / "check_lock_sources.py"),
        "PRIVATE_INDEX": "em-codeartifact",
    }
    return subprocess.run(
        ["bash", str(script)], cwd=project, env=env, capture_output=True, text=True, check=False
    )


@pytest.fixture()
def indexes(toy_index, tmp_path):
    """(declared index with the toy org packages, default index that also carries a ghost)."""
    declared = simple_index_from(toy_index, tmp_path / "declared")
    src, ghost_wheels = tmp_path / "ghost-src", tmp_path / "ghost-wheels"
    ghost_wheels.mkdir()
    _uv_build(_write_leaf(src, GHOST, "1.0.0"), ghost_wheels)
    return declared, simple_index_from(ghost_wheels, tmp_path / "default")


def test_an_org_name_absent_from_the_declared_index_fails_instead_of_using_the_default(
    indexes, tmp_path
):
    declared, default = indexes
    project = _project(tmp_path, declared, [f"{MATERIALS}>=1.0,<2", GHOST])
    _uv_build(project, project / "dist")
    done = _run_action(project, default, tmp_path)
    assert done.returncode != 0
    assert re.search(
        rf"{GHOST} 1\.0\.0 came from \S+, an index this project does not declare", done.stderr
    )
    assert "Installed" not in done.stdout + done.stderr


def test_a_wheel_whose_org_dependencies_are_all_on_the_declared_index_installs(indexes, tmp_path):
    declared, default = indexes
    project = _project(tmp_path, declared, [f"{MATERIALS}>=1.0,<2"])
    _uv_build(project, project / "dist")
    done = _run_action(project, default, tmp_path)
    assert done.returncode == 0, done.stderr
    assert "imported gw_app" in done.stdout
