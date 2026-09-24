"""Shared fixtures for the em-dev integration and tool-mode test files.

Building the toy package index (real `uv build` calls) is slow and its
output is identical for every test that needs it, so it is built once per
session here rather than per test file -- the "genuinely shared across
files" case STYLE.md's testing section calls out for a conftest.py.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# em-dev only ever treats an "emergent-matter-*" name as one of "ours" (see
# em-dev.py's OURS_PREFIX) -- these toy packages use the real prefix so
# discovery is exercised exactly as it runs in a real repo, with names that
# can't be mistaken for one.
MATERIALS = "emergent-matter-toy-materials"
CORE = "emergent-matter-toy-core"
EXPORTDEP = "emergent-matter-toy-exportdep"
NEWDEP = "emergent-matter-toy-newdep"
TESTTOOL = "emergent-matter-toy-testtool"


def _dist(name: str) -> str:
    return name.replace("-", "_")


def _write_leaf_at(pkg_dir: Path, name: str, version: str, deps: list[str] | None = None) -> Path:
    """A minimal hatchling-backed leaf package with one importable module,
    written at the exact directory `pkg_dir` (not derived from `name`) --
    what lets a test build a differently-named sibling-folder variant of
    the same package without a spurious extra nesting level."""
    dist = _dist(name)
    (pkg_dir / "src" / dist).mkdir(parents=True)
    deps_toml = ", ".join(f'"{d}"' for d in (deps or []))
    (pkg_dir / "pyproject.toml").write_text(
        textwrap.dedent(f"""\
            [project]
            name = "{name}"
            version = "{version}"
            requires-python = ">=3.9"
            dependencies = [{deps_toml}]

            [build-system]
            requires = ["hatchling"]
            build-backend = "hatchling.build"

            [tool.hatch.build.targets.wheel]
            packages = ["src/{dist}"]
            """)
    )
    (pkg_dir / "src" / dist / "__init__.py").write_text(f'__version__ = "{version}"\n')
    return pkg_dir


def _write_leaf(src_root: Path, name: str, version: str, deps: list[str] | None = None) -> Path:
    """`_write_leaf_at`, at `src_root / name` -- the common case."""
    return _write_leaf_at(src_root / name, name, version, deps)


def _write_core_at(
    pkg_dir: Path, *, version: str = "1.0.0", extra_deps: list[str] | None = None
) -> Path:
    """`emergent-matter-toy-core`: depends on materials, with an "export"
    extra pulling exportdep -- mirrors the real sidecar's `sdm-core[export]`
    dependency. `extra_deps` simulates a sibling that grew a new,
    unpinned third-party dependency (the "leaked new dep" scenario).
    Written at the exact directory `pkg_dir`, same reason as
    `_write_leaf_at`: a sibling-folder variant (e.g. an out-of-range
    version) needs its own directory name, not one derived from CORE."""
    dist = _dist(CORE)
    (pkg_dir / "src" / dist).mkdir(parents=True)
    deps = [f"{MATERIALS}>=1.0,<2", *(extra_deps or [])]
    deps_toml = ", ".join(f'"{d}"' for d in deps)
    (pkg_dir / "pyproject.toml").write_text(
        textwrap.dedent(f"""\
            [project]
            name = "{CORE}"
            version = "{version}"
            requires-python = ">=3.9"
            dependencies = [{deps_toml}]

            [project.optional-dependencies]
            export = ["{EXPORTDEP}>=1.0"]

            [build-system]
            requires = ["hatchling"]
            build-backend = "hatchling.build"

            [tool.hatch.build.targets.wheel]
            packages = ["src/{dist}"]
            """)
    )
    (pkg_dir / "src" / dist / "__init__.py").write_text(f'__version__ = "{version}"\n')
    return pkg_dir


def _write_core(
    src_root: Path, *, version: str = "1.0.0", extra_deps: list[str] | None = None
) -> Path:
    """`_write_core_at`, at `src_root / CORE` -- the common case."""
    return _write_core_at(src_root / CORE, version=version, extra_deps=extra_deps)


def _uv_build(pkg_dir: Path, out_dir: Path) -> None:
    subprocess.run(
        ["uv", "build", "--out-dir", str(out_dir), str(pkg_dir)],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="session")
def toy_index(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A flat local package index (wheels + sdists) for every published toy
    dependency: materials, core (at the "published" version, 1.0.0),
    exportdep, newdep, testtool. Consumer fixtures point `[tool.uv]
    find-links` at this directory (see `make_consumer`)."""
    root = tmp_path_factory.mktemp("toy-index-src")
    index_dir = tmp_path_factory.mktemp("toy-index")

    _uv_build(_write_leaf(root, MATERIALS, "1.0.0"), index_dir)
    _uv_build(_write_core(root), index_dir)
    _uv_build(_write_leaf(root, EXPORTDEP, "1.0.0"), index_dir)
    _uv_build(_write_leaf(root, NEWDEP, "1.0.0"), index_dir)
    _uv_build(_write_leaf(root, TESTTOOL, "1.0.0"), index_dir)
    return index_dir


@pytest.fixture(scope="session")
def toy_sibling_src(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Unbuilt source checkouts to copy into a sibling folder location:
    `materials/` and `core/` at the published version, and
    `core-out-of-range/` and `core-with-newdep/` for the two-pass fallback
    scenarios. Kept separate from `toy_index`'s build inputs so a test can
    mutate its own copy without touching another test's."""
    root = tmp_path_factory.mktemp("toy-sibling-src")
    _write_leaf(root, MATERIALS, "1.0.0")
    _write_core(root)
    _write_core_at(root / "core-out-of-range", version="9.9.9")
    _write_core_at(root / "core-with-newdep", extra_deps=[NEWDEP])
    return root


def make_consumer(
    repo_dir: Path,
    toy_index: Path,
    *,
    name: str,
    dependencies: list[str],
    dev_group: list[str] | None = None,
    package: bool = True,
    extra_pyproject: str = "",
) -> Path:
    """Write a consumer project's pyproject.toml pointed at `toy_index`,
    give it a minimal src package (unless `package=False`, mirroring
    sdm-ui), and `uv lock` it. Returns `repo_dir`."""
    dist = _dist(name)
    deps_toml = ", ".join(f'"{d}"' for d in dependencies)
    group_block = ""
    if dev_group:
        group_toml = ", ".join(f'"{d}"' for d in dev_group)
        group_block = f"\n[dependency-groups]\ndev = [{group_toml}]\n"

    # A single [tool.uv] table -- a second one is a TOML "duplicate key"
    # parse error, so `package = false` has to join `find-links` here
    # rather than in its own later block.
    package_line = "package = false\n" if not package else ""
    build_system_block = ""
    if package:
        (repo_dir / "src" / dist).mkdir(parents=True)
        (repo_dir / "src" / dist / "__init__.py").write_text('__version__ = "0.1.0"\n')
        build_system_block = textwrap.dedent(f"""
            [build-system]
            requires = ["hatchling"]
            build-backend = "hatchling.build"

            [tool.hatch.build.targets.wheel]
            packages = ["src/{dist}"]
            """)

    (repo_dir / "pyproject.toml").write_text(
        textwrap.dedent(f"""\
            [project]
            name = "{name}"
            version = "0.1.0"
            requires-python = ">=3.9"
            dependencies = [{deps_toml}]
            {group_block}
            [tool.uv]
            find-links = ["file://{toy_index}"]
            {package_line}""")
        + extra_pyproject
        + build_system_block
    )

    subprocess.run(["uv", "lock"], cwd=repo_dir, check=True, capture_output=True, text=True)
    return repo_dir


@pytest.fixture()
def em_dev(tmp_path: Path):
    """The em-dev module, freshly loaded (module-level state is limited to
    constants, but a fresh load keeps tests from being order-dependent)."""
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "templates" / "scripts" / "em-dev.py"
    spec = importlib.util.spec_from_file_location("em_dev", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["em_dev"] = module
    spec.loader.exec_module(module)
    return module
