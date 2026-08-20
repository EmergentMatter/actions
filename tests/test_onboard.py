"""Tests for scripts/onboard.py.

Covers CI detection, version-file proposal, and towncrier marker placement.
The copying itself is `shutil.copyfile`; the value is in what the script
decides to do, not in whether Python can copy a file.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "onboard.py"
spec = importlib.util.spec_from_file_location("onboard", SCRIPT)
onboard = importlib.util.module_from_spec(spec)
sys.modules["onboard"] = onboard
spec.loader.exec_module(onboard)


# ------------------------------------------------------------- CI detection


CI_WITH_CALL = """\
name: CI
on:
  pull_request:
  workflow_call:
jobs:
  lint:
    runs-on: ubuntu-latest
  test:
    runs-on: ubuntu-latest
"""

# emergent-matter-materials' actual shape: no workflow_call, one job.
CI_MATERIALS = """\
name: CI
on:
  push:
    branches: [main]
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""


def test_detects_workflow_call_when_present():
    has_call, jobs = onboard.parse_ci(CI_WITH_CALL)
    assert has_call is True
    assert jobs == ["lint", "test"]


def test_detects_missing_workflow_call():
    """version.yml calls ci.yml by reference and fails at parse time without it."""
    has_call, jobs = onboard.parse_ci(CI_MATERIALS)
    assert has_call is False
    assert jobs == ["test"]


def test_job_names_stop_at_the_end_of_the_jobs_block():
    ci = CI_WITH_CALL + "\npermissions:\n  contents: read\n"
    _, jobs = onboard.parse_ci(ci)
    assert jobs == ["lint", "test"], "picked up a key outside jobs:"


def test_steps_are_not_mistaken_for_jobs():
    _, jobs = onboard.parse_ci(CI_MATERIALS)
    assert "steps" not in jobs
    assert "uses" not in jobs


# --------------------------------------------------- workflow_call insert


def test_adds_workflow_call_preserving_existing_triggers():
    out = onboard.add_workflow_call(CI_MATERIALS)
    has_call, jobs = onboard.parse_ci(out)
    assert has_call is True
    assert "push:" in out and "branches: [main]" in out and "pull_request:" in out
    assert jobs == ["test"], "jobs disturbed"


def test_adding_workflow_call_is_idempotent():
    assert onboard.add_workflow_call(CI_WITH_CALL) is None


def test_declines_the_inline_trigger_form_rather_than_guessing():
    """`on: [push, pull_request]` has too many shapes to rewrite safely."""
    inline = "name: CI\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
    assert onboard.add_workflow_call(inline) is None


def test_workflow_call_lands_inside_the_on_block_not_among_jobs():
    out = onboard.add_workflow_call(CI_MATERIALS)
    lines = out.split("\n")
    call_at = next(i for i, ln in enumerate(lines) if "workflow_call:" in ln)
    jobs_at = next(i for i, ln in enumerate(lines) if ln.startswith("jobs:"))
    assert call_at < jobs_at


# -------------------------------------------------- version-file proposal


def test_proposes_only_assignments_matching_the_current_version(tmp_path):
    pkg = tmp_path / "src" / "p"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        '__version__ = "1.10.0"\n'
        '__catalog_version__ = "1.10.0"\n'
        '__schema_version__ = "3.0.0"\n'  # unrelated, different value
    )
    found = onboard.propose_version_files(tmp_path, "1.10.0")
    symbols = {s for _, s in found}
    assert symbols == {"__version__", "__catalog_version__"}
    assert "__schema_version__" not in symbols


def test_proposal_ignores_the_virtualenv(tmp_path):
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "mod.py").write_text('__version__ = "1.10.0"\n')
    assert onboard.propose_version_files(tmp_path, "1.10.0") == []


def test_proposal_is_candidates_not_a_decision(tmp_path):
    """A schema version on its own release schedule must be left off.

    The script surfaces both and refuses to choose -- see the worked example
    in docs/onboarding.md, which exists to prevent exactly that mistake.
    """
    pkg = tmp_path / "p"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        '__version__ = "2.0.0"\n__schema_version__ = "2.0.0"\n'
    )
    found = onboard.propose_version_files(tmp_path, "2.0.0")
    assert len(found) == 2, "both surfaced; the human picks"


# ------------------------------------------------------- towncrier marker


def test_marker_goes_above_existing_history_not_below():
    """towncrier writes BELOW the marker, so a marker at the bottom would
    bury every future release under the hand-written history."""
    changelog = (
        "# Changelog\n\nSome intro prose.\n\n"
        "## v1.10.0 — newest existing\n\nstuff\n\n"
        "## v1.9.0 — older\n\nmore stuff\n"
    )
    out = onboard.insert_marker(changelog)
    assert out.index(onboard.TOWNCRIER_MARKER) < out.index("## v1.10.0")
    assert "Some intro prose." in out
    assert "## v1.9.0 — older" in out


def test_marker_insertion_is_idempotent():
    changelog = f"# Changelog\n\n{onboard.TOWNCRIER_MARKER}\n\n## v1.0.0\n"
    assert onboard.insert_marker(changelog) is None


def test_marker_handles_a_changelog_with_no_releases_yet():
    out = onboard.insert_marker("# Changelog\n\nNothing released yet.\n")
    assert onboard.TOWNCRIER_MARKER in out
    assert out.index("# Changelog") < out.index(onboard.TOWNCRIER_MARKER)


def test_marker_matches_the_snippet_byte_for_byte():
    """It has to equal `start_string` in [tool.towncrier] exactly, or
    towncrier silently writes to the wrong place."""
    snippet = Path(__file__).resolve().parents[1] / "templates" / "pyproject-snippet.toml"
    assert onboard.TOWNCRIER_MARKER in snippet.read_text()


# ------------------------------------------------------------ config block


def test_config_block_declares_exactly_the_given_version_files():
    block = onboard.render_config_block(["src/p/__init__.py:__version__"])
    assert '"src/p/__init__.py:__version__",' in block
    assert "[tool.em-release]" in block
    assert "[tool.towncrier]" in block


def test_config_block_never_declares_pyproject_version():
    """`uv version --bump` writes that field; listing it would double-write."""
    block = onboard.render_config_block(["src/p/__init__.py:__version__"])
    version_files = block.split("version_files = [")[1].split("]")[0]
    assert "pyproject.toml" not in version_files


@pytest.mark.parametrize("bad", ["", "no-colon"])
def test_render_handles_empty_declaration_lists(bad):
    block = onboard.render_config_block([bad] if bad else [])
    assert "[tool.em-release]" in block
