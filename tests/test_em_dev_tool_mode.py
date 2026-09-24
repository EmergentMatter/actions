"""Integration tests for em-dev.py's tool mode: reinstalling the sidecar's
console script from local code with `uv tool install --editable`, and back.

Every test isolates UV_TOOL_DIR and UV_TOOL_BIN_DIR into a tmp_path via
monkeypatch -- none of this may ever touch the real `uv tool` installs on
the machine running these tests, per the plan's "Done when" for Piece 1.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from conftest import CORE, MATERIALS, make_consumer

pytestmark = pytest.mark.integration


@pytest.fixture()
def isolated_tool_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    tool_dir = tmp_path / "uv-tool-dir"
    bin_dir = tmp_path / "uv-tool-bin"
    tool_dir.mkdir()
    bin_dir.mkdir()
    monkeypatch.setenv("UV_TOOL_DIR", str(tool_dir))
    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(bin_dir))
    return tool_dir, bin_dir


@pytest.fixture()
def sidecar_tool_repo(tmp_path: Path, toy_index: Path) -> Path:
    """A `[tool.em-dev] tool = true` consumer with a console script, shaped
    like emergent-matter-sdm-sidecar."""
    repo = tmp_path / "sidecar"
    repo.mkdir()
    dist = "emergent_matter_toy_sidecar"
    make_consumer(
        repo,
        toy_index,
        name="emergent-matter-toy-sidecar",
        dependencies=[f"{CORE}[export]>=1.0,<2", f"{MATERIALS}>=1.0,<2"],
        extra_pyproject=textwrap.dedent(f"""
            [project.scripts]
            toy-sidecar = "{dist}.cli:main"

            [tool.em-dev]
            tool = true
            """),
    )
    cli_dir = repo / "src" / dist
    (cli_dir / "cli.py").write_text("def main() -> None:\n    print('toy-sidecar ok')\n")
    return repo


def test_tool_on_installs_editable_console_script(
    em_dev, sidecar_tool_repo, toy_sibling_src, isolated_tool_env
):
    shutil.copytree(toy_sibling_src / CORE, sidecar_tool_repo.parent / CORE)
    pyproject = em_dev.toml_load(sidecar_tool_repo / "pyproject.toml")
    tool_cfg = em_dev.em_dev_tool_config(pyproject)
    assert tool_cfg is not None

    exit_code = em_dev.tool_on(sidecar_tool_repo, pyproject, tool_cfg)
    assert exit_code == 0

    tool_dir, bin_dir = isolated_tool_env
    shim = bin_dir / "toy-sidecar"
    assert shim.exists()
    proc = subprocess.run([str(shim)], capture_output=True, text=True, check=True)
    assert "toy-sidecar ok" in proc.stdout

    env_dir = tool_dir / "emergent-matter-toy-sidecar"
    site_packages = em_dev.site_packages_dir(env_dir)
    dist_info = em_dev.find_dist_info(site_packages, "emergent-matter-toy-core")
    assert em_dev.is_editable_direct_url(dist_info) is True


def test_tool_status_detects_editable_and_reverted(
    em_dev, sidecar_tool_repo, toy_sibling_src, isolated_tool_env
):
    shutil.copytree(toy_sibling_src / CORE, sidecar_tool_repo.parent / CORE)
    pyproject = em_dev.toml_load(sidecar_tool_repo / "pyproject.toml")
    tool_cfg = em_dev.em_dev_tool_config(pyproject)

    em_dev.tool_on(sidecar_tool_repo, pyproject, tool_cfg)
    lines = em_dev.tool_status(sidecar_tool_repo, pyproject, tool_cfg)
    assert any("local (editable)" in line for line in lines)

    # Simulate a reinstall that reverts to a non-editable (the default,
    # unless --editable is passed) install of the sidecar's OWN package
    # from the same local checkout -- the console script itself stays,
    # only its own dist stops being editable. Mirrors what a stray `uv
    # tool upgrade` would do in the real world.
    subprocess.run(
        ["uv", "tool", "install", "--force", *em_dev.index_args(pyproject), str(sidecar_tool_repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    lines = em_dev.tool_status(sidecar_tool_repo, pyproject, tool_cfg)
    assert any("reverted" in line for line in lines)


def test_tool_on_refuses_while_serve_is_running(
    em_dev, sidecar_tool_repo, toy_sibling_src, isolated_tool_env, monkeypatch
):
    monkeypatch.setattr(em_dev, "sidecar_serve_running", lambda port=None: True)
    pyproject = em_dev.toml_load(sidecar_tool_repo / "pyproject.toml")
    tool_cfg = em_dev.em_dev_tool_config(pyproject)
    with pytest.raises(em_dev.EmDevError, match="already listening"):
        em_dev.tool_on(sidecar_tool_repo, pyproject, tool_cfg)


def test_tool_off_refuses_while_serve_is_running(
    em_dev, sidecar_tool_repo, toy_sibling_src, isolated_tool_env, monkeypatch
):
    # `off` reinstalls the published command over the same locked
    # executable `on` would -- the guard has to apply to both directions.
    monkeypatch.setattr(em_dev, "sidecar_serve_running", lambda port=None: True)
    pyproject = em_dev.toml_load(sidecar_tool_repo / "pyproject.toml")
    tool_cfg = em_dev.em_dev_tool_config(pyproject)
    with pytest.raises(em_dev.EmDevError, match="already listening"):
        em_dev.tool_off(sidecar_tool_repo, pyproject, tool_cfg)


def test_turn_off_refuses_before_removing_venv_local_when_serve_running(
    em_dev, sidecar_tool_repo, toy_sibling_src, isolated_tool_env, monkeypatch
):
    # The refusal must happen BEFORE .venv-local is removed, so a refused
    # `em-dev off` leaves the repo exactly as it was, not half switched-off
    # (.venv-local gone, but the tool still on local code, or vice versa).
    shutil.copytree(toy_sibling_src / CORE, sidecar_tool_repo.parent / CORE)
    assert em_dev.make_local(sidecar_tool_repo) == 0
    venv_dir = sidecar_tool_repo / em_dev.VENV_LOCAL
    assert venv_dir.exists()

    monkeypatch.setattr(em_dev, "sidecar_serve_running", lambda port=None: True)
    with pytest.raises(em_dev.EmDevError, match="already listening"):
        em_dev.turn_off(sidecar_tool_repo)

    assert venv_dir.exists(), ".venv-local must survive a refused `off`"


def test_tool_on_refuses_when_script_removed_from_local_checkout(
    em_dev, sidecar_tool_repo, isolated_tool_env
):
    # `pyproject` still says the script exists (the caller's already-loaded
    # view); the checkout ON DISK has since dropped it. `tool_on` reads the
    # checkout itself for this specific check rather than trusting its
    # caller's `pyproject`, precisely so a stale view can't slip past it.
    pyproject_path = sidecar_tool_repo / "pyproject.toml"
    stale_pyproject = em_dev.toml_load(pyproject_path)
    text = pyproject_path.read_text().replace(
        '[project.scripts]\ntoy-sidecar = "emergent_matter_toy_sidecar.cli:main"', ""
    )
    pyproject_path.write_text(text)
    tool_cfg = em_dev.em_dev_tool_config(stale_pyproject)
    with pytest.raises(em_dev.EmDevError, match="no longer declares"):
        em_dev.tool_on(sidecar_tool_repo, stale_pyproject, tool_cfg)


def test_tool_off_reinstalls_pinned_published_version(
    em_dev, sidecar_tool_repo, toy_sibling_src, toy_index, isolated_tool_env
):
    shutil.copytree(toy_sibling_src / CORE, sidecar_tool_repo.parent / CORE)
    # `off` reinstalls the pinned PUBLISHED version, so the sidecar's own
    # package (never built into toy_index by the shared fixture -- only its
    # dependencies are) needs to actually exist on the index for this test.
    subprocess.run(
        ["uv", "build", "--out-dir", str(toy_index), str(sidecar_tool_repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    pyproject = em_dev.toml_load(sidecar_tool_repo / "pyproject.toml")
    tool_cfg = em_dev.em_dev_tool_config(pyproject)
    em_dev.tool_on(sidecar_tool_repo, pyproject, tool_cfg)

    exit_code = em_dev.tool_off(sidecar_tool_repo, pyproject, tool_cfg)
    assert exit_code == 0

    tool_dir, bin_dir = isolated_tool_env
    env_dir = tool_dir / "emergent-matter-toy-sidecar"
    site_packages = em_dev.site_packages_dir(env_dir)
    dist_info = em_dev.find_dist_info(site_packages, "emergent-matter-toy-sidecar")
    assert dist_info is not None
    assert em_dev.is_editable_direct_url(dist_info) in (False, None)
