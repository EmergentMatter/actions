"""Integration tests for em-dev.py's two-pass install, against a real local
package index built from actual `uv build` output (see conftest.py) --
never hand-picked wheels. Slow (each test builds a real `.venv-local`), so
marked `integration`: deselect with `pytest -m "not integration"`.

Pins the plan's "Done when" checklist for Piece 1's install path: editable
`__file__` resolution, third-party pins matching the lock, untouched
tracked files, the missing/wrong-name/out-of-range/newdep sibling cases,
idempotence, rebuild-on-lock-change, `off`, and `package = false`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import CORE, EXPORTDEP, MATERIALS, NEWDEP, TESTTOOL, make_consumer

pytestmark = pytest.mark.integration


def _copy_sibling(dst_root: Path, src: Path, dest_name: str) -> Path:
    dest = dst_root / dest_name
    shutil.copytree(src, dest)
    return dest


def _venv_python(repo: Path, em_dev) -> Path:
    venv = repo / em_dev.VENV_LOCAL
    return venv / ("Scripts/python.exe" if em_dev.os.name == "nt" else "bin/python")


def _import_file(python: Path, module: str) -> str:
    import subprocess

    proc = subprocess.run(
        [str(python), "-c", f"import {module}; print({module}.__file__)"],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _snapshot(repo: Path) -> dict[str, bytes]:
    return {
        "pyproject.toml": (repo / "pyproject.toml").read_bytes(),
        "uv.lock": (repo / "uv.lock").read_bytes(),
    }


@pytest.fixture()
def sidecar_repo(tmp_path: Path, toy_index: Path) -> Path:
    """A `emergent-matter-toy-sidecar`-shaped consumer: depends on
    core[export] and materials, with a dev group. Its sibling root
    (`tmp_path`) starts with no sibling folders present."""
    repo = tmp_path / "sidecar"
    repo.mkdir()
    make_consumer(
        repo,
        toy_index,
        name="emergent-matter-toy-sidecar",
        dependencies=[f"{CORE}[export]>=1.0,<2", f"{MATERIALS}>=1.0,<2"],
        dev_group=[f"{TESTTOOL}>=1.0"],
    )
    return repo


def test_missing_sibling_folder_uses_published_and_exits_zero(em_dev, sidecar_repo, capsys):
    exit_code = em_dev.make_local(sidecar_repo)
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "not found -- using published version" in out
    python = _venv_python(sidecar_repo, em_dev)
    location = _import_file(python, "emergent_matter_toy_core")
    assert str(sidecar_repo.parent / CORE) not in location  # came from the index, not a sibling


def test_local_sibling_resolves_editable_and_thirdparty_matches_lock(
    em_dev, sidecar_repo, toy_sibling_src, capsys
):
    _copy_sibling(sidecar_repo.parent, toy_sibling_src / CORE, CORE)
    before = _snapshot(sidecar_repo)

    exit_code = em_dev.make_local(sidecar_repo)

    assert exit_code == 0
    assert _snapshot(sidecar_repo) == before, "pyproject.toml/uv.lock must be untouched"
    assert not (sidecar_repo / ".venv").exists(), "the real .venv must never be created"

    python = _venv_python(sidecar_repo, em_dev)
    core_file = _import_file(python, "emergent_matter_toy_core")
    assert str(sidecar_repo.parent / CORE) in core_file

    materials_file = _import_file(python, "emergent_matter_toy_materials")
    assert str(sidecar_repo.parent / MATERIALS) not in materials_file  # published, not a sibling

    lock = em_dev.toml_load(sidecar_repo / "uv.lock")
    assert em_dev.locked_version(lock, MATERIALS) == "1.0.0"


def test_wrong_name_folder_is_skipped_with_warning(em_dev, sidecar_repo, toy_sibling_src, capsys):
    wrong = sidecar_repo.parent / CORE
    shutil.copytree(toy_sibling_src / MATERIALS, wrong)  # declares MATERIALS, not CORE

    exit_code = em_dev.make_local(sidecar_repo)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "declares" in out and "using published version" in out
    python = _venv_python(sidecar_repo, em_dev)
    location = _import_file(python, "emergent_matter_toy_core")
    assert str(wrong) not in location


def test_out_of_range_sibling_installs_via_fallback_with_pip_check_warning(
    em_dev, sidecar_repo, toy_sibling_src, capsys
):
    _copy_sibling(sidecar_repo.parent, toy_sibling_src / "core-out-of-range", CORE)

    exit_code = em_dev.make_local(sidecar_repo)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "retrying with siblings installed --no-deps" in out
    python = _venv_python(sidecar_repo, em_dev)
    core_file = _import_file(python, "emergent_matter_toy_core")
    assert str(sidecar_repo.parent / CORE) in core_file
    check = em_dev.pip_check(sidecar_repo, sidecar_repo / em_dev.VENV_LOCAL)
    assert check, "uv pip check should report the version conflict"


def test_sibling_with_unpinned_new_dependency_is_installed_and_warned(
    em_dev, sidecar_repo, toy_sibling_src, capsys
):
    _copy_sibling(sidecar_repo.parent, toy_sibling_src / "core-with-newdep", CORE)

    exit_code = em_dev.make_local(sidecar_repo)

    assert exit_code == 0
    python = _venv_python(sidecar_repo, em_dev)
    # The unpinned dependency must actually be importable (it got installed)...
    _import_file(python, "emergent_matter_toy_newdep")
    # ...and named explicitly, since nothing in uv.lock ever pinned it --
    # this can succeed in a single pass (no version conflict here), so the
    # warning can't ride on the two-pass fallback path.
    out = capsys.readouterr().out
    assert "no uv.lock pin at all" in out
    assert NEWDEP in out


def test_idempotent_rerun_does_not_reinstall(em_dev, sidecar_repo, toy_sibling_src, monkeypatch):
    _copy_sibling(sidecar_repo.parent, toy_sibling_src / CORE, CORE)
    assert em_dev.make_local(sidecar_repo) == 0

    def _boom(*args, **kwargs):
        raise AssertionError("uv should not be invoked on an up-to-date re-run")

    monkeypatch.setattr(em_dev, "run_uv", _boom)
    assert em_dev.make_local(sidecar_repo) == 0


def test_lock_change_triggers_rebuild(em_dev, sidecar_repo, toy_sibling_src, toy_index):
    _copy_sibling(sidecar_repo.parent, toy_sibling_src / CORE, CORE)
    assert em_dev.make_local(sidecar_repo) == 0
    state_before = em_dev.read_state(sidecar_repo / em_dev.VENV_LOCAL)

    # Relock against a pyproject that now also needs testtool directly --
    # a real (if trivial) lock content change.
    pyproject = sidecar_repo / "pyproject.toml"
    text = pyproject.read_text().replace(
        f'"{TESTTOOL}>=1.0"', f'"{TESTTOOL}>=1.0", "{EXPORTDEP}>=1.0"'
    )
    pyproject.write_text(text)
    import subprocess

    subprocess.run(["uv", "lock"], cwd=sidecar_repo, check=True, capture_output=True)

    assert em_dev.make_local(sidecar_repo) == 0
    state_after = em_dev.read_state(sidecar_repo / em_dev.VENV_LOCAL)
    assert state_after["uv_lock_hash"] != state_before["uv_lock_hash"]


def test_off_removes_venv_local_and_is_a_noop_when_absent(em_dev, sidecar_repo, toy_sibling_src):
    assert em_dev.turn_off(sidecar_repo) == 0  # never run yet -- must not raise
    assert not (sidecar_repo / em_dev.VENV_LOCAL).exists()

    _copy_sibling(sidecar_repo.parent, toy_sibling_src / CORE, CORE)
    assert em_dev.make_local(sidecar_repo) == 0
    assert (sidecar_repo / em_dev.VENV_LOCAL).exists()

    assert em_dev.turn_off(sidecar_repo) == 0
    assert not (sidecar_repo / em_dev.VENV_LOCAL).exists()


def test_only_filters_to_the_requested_sibling(em_dev, sidecar_repo, toy_sibling_src):
    _copy_sibling(sidecar_repo.parent, toy_sibling_src / MATERIALS, MATERIALS)
    _copy_sibling(sidecar_repo.parent, toy_sibling_src / CORE, CORE)

    assert em_dev.make_local(sidecar_repo, only=["materials"]) == 0

    python = _venv_python(sidecar_repo, em_dev)
    materials_file = _import_file(python, "emergent_matter_toy_materials")
    core_file = _import_file(python, "emergent_matter_toy_core")
    assert str(sidecar_repo.parent / MATERIALS) in materials_file
    assert str(sidecar_repo.parent / CORE) not in core_file  # excluded by --only


@pytest.fixture()
def ui_repo(tmp_path: Path, toy_index: Path) -> Path:
    """A `package = false` consumer, shaped like sdm-ui: a dev group that
    pulls core and materials, no importable code of its own."""
    repo = tmp_path / "ui"
    repo.mkdir()
    make_consumer(
        repo,
        toy_index,
        name="emergent-matter-toy-ui",
        dependencies=[],
        dev_group=[f"{CORE}>=1.0,<2", f"{MATERIALS}>=1.0,<2"],
        package=False,
    )
    return repo


def test_package_false_project_installs_no_phantom_package(em_dev, ui_repo, toy_sibling_src):
    _copy_sibling(ui_repo.parent, toy_sibling_src / CORE, CORE)

    exit_code = em_dev.make_local(ui_repo)

    assert exit_code == 0
    site_packages = em_dev.site_packages_dir(ui_repo / em_dev.VENV_LOCAL)
    assert em_dev.find_dist_info(site_packages, "emergent-matter-toy-ui") is None
    python = _venv_python(ui_repo, em_dev)
    core_file = _import_file(python, "emergent_matter_toy_core")
    assert str(ui_repo.parent / CORE) in core_file


# ------------------------------------------------------------------- CLI (main)


def test_main_default_command_builds_venv_local(em_dev, sidecar_repo, toy_sibling_src, monkeypatch):
    _copy_sibling(sidecar_repo.parent, toy_sibling_src / CORE, CORE)
    monkeypatch.chdir(sidecar_repo)
    assert em_dev.main([]) == 0
    assert (sidecar_repo / em_dev.VENV_LOCAL / "pyvenv.cfg").is_file()


def test_main_only_flag_is_parsed_and_applied(em_dev, sidecar_repo, toy_sibling_src, monkeypatch):
    _copy_sibling(sidecar_repo.parent, toy_sibling_src / MATERIALS, MATERIALS)
    _copy_sibling(sidecar_repo.parent, toy_sibling_src / CORE, CORE)
    monkeypatch.chdir(sidecar_repo)
    assert em_dev.main(["--only", "materials"]) == 0
    python = _venv_python(sidecar_repo, em_dev)
    core_file = _import_file(python, "emergent_matter_toy_core")
    assert str(sidecar_repo.parent / CORE) not in core_file


def test_main_run_executes_in_local_venv(
    em_dev, sidecar_repo, toy_sibling_src, monkeypatch, capsys
):
    _copy_sibling(sidecar_repo.parent, toy_sibling_src / CORE, CORE)
    monkeypatch.chdir(sidecar_repo)
    assert em_dev.main([]) == 0
    script = "import emergent_matter_toy_core; print('ran')"
    assert em_dev.main(["run", "python", "-c", script]) == 0


def test_main_status_and_off(em_dev, sidecar_repo, toy_sibling_src, monkeypatch, capsys):
    _copy_sibling(sidecar_repo.parent, toy_sibling_src / CORE, CORE)
    monkeypatch.chdir(sidecar_repo)
    assert em_dev.main([]) == 0
    assert em_dev.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "local" in out
    assert em_dev.main(["off"]) == 0
    assert not (sidecar_repo / em_dev.VENV_LOCAL).exists()
