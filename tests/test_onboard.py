"""Tests for scripts/onboard.py.

Covers CI detection, version-file proposal, and towncrier marker placement.
The copying itself is `shutil.copyfile`; the value is in what the script
decides to do, not in whether Python can copy a file.
"""

from __future__ import annotations

import importlib.util
import subprocess
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
    block = onboard.render_config_block(["src/p/__init__.py:__version__"], "v1.5.0")
    assert '"src/p/__init__.py:__version__",' in block
    assert "[tool.em-release]" in block
    assert "[tool.towncrier]" in block


def test_config_block_never_declares_pyproject_version():
    """`uv version --bump` writes that field; listing it would double-write."""
    block = onboard.render_config_block(["src/p/__init__.py:__version__"], "v1.5.0")
    version_files = block.split("version_files = [")[1].split("]")[0]
    assert "pyproject.toml" not in version_files


@pytest.mark.parametrize("bad", ["", "no-colon"])
def test_render_handles_empty_declaration_lists(bad):
    block = onboard.render_config_block([bad] if bad else [], "v1.5.0")
    assert "[tool.em-release]" in block


def test_config_block_stamps_the_templates_version():
    """The provenance stamp sync.py later reads as its merge base."""
    block = onboard.render_config_block([], "v1.5.0")
    assert 'templates_version = "v1.5.0"' in block
    # Must land inside [tool.em-release], above version_files, per CONTRACT.
    em_release = block.split("[tool.em-release]")[1]
    assert em_release.index("templates_version") < em_release.index("version_files")


def test_config_block_stamp_survives_a_short_sha():
    """A repo synced from an untagged commit gets the short SHA -- still a
    valid ref for `git show` later."""
    block = onboard.render_config_block([], "32ed6e0")
    assert 'templates_version = "32ed6e0"' in block


# ------------------------------------------------------------------ manifest


def test_load_manifest_reads_declared_entries(tmp_path):
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        '[[template]]\nsource = "a.md"\ndest = "A.md"\npolicy = "managed"\n\n'
        '[[template]]\nsource = "b/c.yml"\ndest = ".github/b/c.yml"\npolicy = "seed-once"\n'
    )
    entries = onboard.load_manifest(manifest)
    assert entries == [
        onboard.TemplateEntry("a.md", "A.md", "managed"),
        onboard.TemplateEntry("b/c.yml", ".github/b/c.yml", "seed-once"),
    ]


def test_load_manifest_rejects_an_unknown_policy(tmp_path):
    """Silent no-ops are the bug -- an unrecognised policy must fail loudly,
    not be treated as one of the two known ones."""
    manifest = tmp_path / "manifest.toml"
    manifest.write_text('[[template]]\nsource = "a.md"\ndest = "A.md"\npolicy = "sometimes"\n')
    with pytest.raises(onboard.OnboardError):
        onboard.load_manifest(manifest)


def test_real_manifest_declares_ci_as_the_only_seed_once_entry():
    """Guards the shape docs/onboarding.md and CONTRACT.md assume: every
    template is kept current by sync.py except ci.yml, which a repo owns
    outright once it exists."""
    entries = onboard.load_manifest()
    seed_once = [e for e in entries if e.policy == "seed-once"]
    assert [e.dest for e in seed_once] == [onboard.CI_DEST]


def test_real_manifest_entries_have_source_files_on_disk_or_are_new_health_files():
    """Every entry either already ships a template, or is one of the new
    repo-health files still being written alongside this change."""
    known_pending = {
        "SECURITY.md", "SUPPORT.md", "CODE_OF_CONDUCT.md", "NOTICE", "LICENSE",
        "CODEOWNERS", "dependabot.yml", "PULL_REQUEST_TEMPLATE.md",
        "ISSUE_TEMPLATE/bug_report.yml", "ISSUE_TEMPLATE/feature_request.yml",
        "ISSUE_TEMPLATE/config.yml",
    }
    for entry in onboard.load_manifest():
        if (onboard.TEMPLATES / entry.source).is_file():
            continue
        assert entry.source in known_pending, f"unexpected missing template: {entry.source}"


# ----------------------------------------------------- current_templates_version


def _git(repo, *args):
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def _init_repo(repo):
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "f").write_text("1\n")
    _git(repo, "add", "f")
    _git(repo, "commit", "-q", "-m", "one")


def test_current_templates_version_uses_the_exact_tag_at_head(tmp_path):
    repo = tmp_path / "r"
    _init_repo(repo)
    _git(repo, "tag", "v9.9.9")
    assert onboard.current_templates_version(repo) == "v9.9.9"


def test_current_templates_version_falls_back_to_short_sha(tmp_path):
    """HEAD is one commit past the last tag -- there is no exact tag, so this
    must fall back to the short SHA rather than reporting a stale tag."""
    repo = tmp_path / "r"
    _init_repo(repo)
    _git(repo, "tag", "v1.0.0")
    (repo / "f").write_text("2\n")
    _git(repo, "add", "f")
    _git(repo, "commit", "-q", "-m", "two")
    version = onboard.current_templates_version(repo)
    assert version != "v1.0.0"
    full_sha = _git(repo, "rev-parse", "HEAD")
    assert full_sha.startswith(version)


# ------------------------------------------------------- manifest-driven plan


def _write_fixture_templates(dir_: Path):
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "manifest.toml").write_text(
        '[[template]]\nsource = "managed.md"\ndest = "MANAGED.md"\npolicy = "managed"\n\n'
        '[[template]]\nsource = "ci.yml"\ndest = ".github/workflows/ci.yml"\npolicy = "seed-once"\n'
    )
    (dir_ / "managed.md").write_text("template content\n")
    (dir_ / "ci.yml").write_text(
        "name: CI\non:\n  workflow_call:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
    )
    (dir_ / "pyproject-snippet.toml").write_text(
        "[tool.towncrier]\n"
        'directory = "changelog.d"\n'
        'start_string = "<!-- towncrier release notes start -->\\n"\n\n'
        "[tool.em-release]\nversion_files = [\n]\n"
    )


def test_build_and_apply_plan_uses_the_manifest_for_both_policies(tmp_path, monkeypatch):
    """A fresh onboarding installs every manifest entry regardless of
    policy -- seed-once vs managed only matters to sync.py, later."""
    fixture_templates = tmp_path / "fixture_templates"
    _write_fixture_templates(fixture_templates)
    monkeypatch.setattr(onboard, "TEMPLATES", fixture_templates)

    repo = tmp_path / "target"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "1.0.0"\n')

    plan = onboard.build_plan(repo, ["src/p/__init__.py:__version__"])
    targets = {a.target for a in plan.actions if a.kind == "create"}
    assert "MANAGED.md" in targets
    assert ".github/workflows/ci.yml" in targets

    written = onboard.apply_plan(plan, ["src/p/__init__.py:__version__"], "v0.0.1-test")
    assert (repo / "MANAGED.md").read_text() == "template content\n"
    assert (repo / ".github/workflows/ci.yml").is_file()
    assert "pyproject.toml" in written

    pyproject_text = (repo / "pyproject.toml").read_text()
    assert 'templates_version = "v0.0.1-test"' in pyproject_text


def test_second_onboard_run_is_idempotent(tmp_path, monkeypatch):
    fixture_templates = tmp_path / "fixture_templates"
    _write_fixture_templates(fixture_templates)
    monkeypatch.setattr(onboard, "TEMPLATES", fixture_templates)

    repo = tmp_path / "target"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "1.0.0"\n')

    plan = onboard.build_plan(repo, ["x:y"])
    onboard.apply_plan(plan, ["x:y"], "v1")

    plan2 = onboard.build_plan(repo, ["x:y"])
    kinds = {a.target: a.kind for a in plan2.actions}
    assert kinds["MANAGED.md"] == "skip"
    assert kinds["pyproject.toml"] == "skip"
