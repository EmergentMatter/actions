"""Tests for scripts/onboard.py.

Covers CI detection, version-file proposal, and towncrier marker placement.
The copying itself is `shutil.copyfile`; the value is in what the script
decides to do, not in whether Python can copy a file.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
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
    (pkg / "__init__.py").write_text('__version__ = "2.0.0"\n__schema_version__ = "2.0.0"\n')
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


# -------------------------------------------------- per-section duplication

# CRITICAL bug this section pins: render_config_block used to append the
# whole snippet verbatim, so a repo that already declared [tool.mypy] or
# [tool.pytest.ini_options] got a second copy of that table appended --
# invalid TOML, and `uv sync` failed with "Cannot declare (...) twice"
# while onboard.py itself had already exited 0.


def test_missing_config_sections_is_everything_for_a_bare_pyproject():
    assert onboard.missing_config_sections({}) == set(onboard.SNIPPET_SECTIONS)


def test_missing_config_sections_excludes_only_whats_declared():
    data = {"tool": {"mypy": {}, "pytest": {"ini_options": {}}}}
    assert onboard.missing_config_sections(data) == {"towncrier", "em-release"}


def test_render_skips_mypy_when_it_already_exists():
    missing = onboard.missing_config_sections({"tool": {"mypy": {"strict": True}}})
    block = onboard.render_config_block(["x:y"], "v1", missing)
    assert "[tool.mypy]" not in block


def test_render_skips_pytest_when_it_already_exists():
    missing = onboard.missing_config_sections(
        {"tool": {"pytest": {"ini_options": {"testpaths": ["tests"]}}}}
    )
    block = onboard.render_config_block(["x:y"], "v1", missing)
    assert "[tool.pytest.ini_options]" not in block


def test_render_skips_both_when_both_already_exist():
    data = {"tool": {"mypy": {}, "pytest": {"ini_options": {}}}}
    block = onboard.render_config_block(["x:y"], "v1", onboard.missing_config_sections(data))
    assert "[tool.mypy]" not in block
    assert "[tool.pytest.ini_options]" not in block
    # towncrier/em-release weren't declared, so those still get written.
    assert "[tool.towncrier]" in block
    assert "[tool.em-release]" in block


def test_render_writes_every_section_when_none_already_exist():
    block = onboard.render_config_block(["x:y"], "v1", onboard.missing_config_sections({}))
    for header in (
        "[tool.towncrier]",
        "[tool.em-release]",
        "[tool.mypy]",
        "[tool.pytest.ini_options]",
    ):
        assert header in block


def test_full_apply_never_produces_a_toml_file_with_a_duplicate_table(tmp_path):
    """End-to-end repro of the live failure from review: onboard a repo
    whose pyproject.toml already has [tool.mypy], then confirm the result
    still parses -- a duplicate table raises on tomllib.loads, the same
    way `uv sync` refused it."""
    repo = tmp_path
    (repo / ".git").mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "1.0.0"\n\n[tool.mypy]\ndisallow_untyped_defs = true\n'
    )
    plan = onboard.build_plan(repo, ["x:y"])
    onboard.apply_plan(plan, ["x:y"], "v1")
    data = tomllib.loads((repo / "pyproject.toml").read_text())
    assert data["tool"]["mypy"] == {"disallow_untyped_defs": True}


def test_plan_note_names_both_the_skipped_and_appended_sections(tmp_path):
    repo = tmp_path
    (repo / ".git").mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "1.0.0"\n\n[tool.mypy]\ndisallow_untyped_defs = true\n'
    )
    plan = onboard.build_plan(repo, ["x:y"])
    note = next(a.note for a in plan.actions if a.target == "pyproject.toml")
    assert "mypy" in note and "already present" in note
    assert "towncrier" in note


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
    with pytest.raises(onboard.OnboardError, match="sometimes"):
        onboard.load_manifest(manifest)


def test_real_manifest_declares_only_ci_and_ruff_as_seed_once():
    """Guards the shape docs/onboarding.md and CONTRACT.md assume: every
    template is kept current by sync.py except ci.yml (a repo owns its CI
    outright once it exists) and ruff.toml (a repo's local additions on
    top of the shared ruff-base.toml, per STYLE.md's Python style section)."""
    entries = onboard.load_manifest()
    seed_once = [e for e in entries if e.policy == "seed-once"]
    assert {e.dest for e in seed_once} == {onboard.CI_DEST, "ruff.toml"}


def test_real_manifest_entries_have_source_files_on_disk_or_are_new_health_files():
    """Every entry either already ships a template, or is one of the new
    repo-health files still being written alongside this change."""
    known_pending = {
        "SECURITY.md",
        "SUPPORT.md",
        "CODE_OF_CONDUCT.md",
        "NOTICE",
        "LICENSE",
        "CODEOWNERS",
        "dependabot.yml",
        "PULL_REQUEST_TEMPLATE.md",
        "ISSUE_TEMPLATE/bug_report.yml",
        "ISSUE_TEMPLATE/feature_request.yml",
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


# ------------------------- current_templates_version never returns a moving alias


@pytest.mark.parametrize("order", ["alias_first", "point_release_first"])
def test_current_templates_version_never_returns_the_moving_v1_alias(tmp_path, order):
    """Regression: `git describe --tags --exact-match`'s tie-break between
    several tags on the same commit depends on ref packing, not on
    anything this code controls -- it must not decide this. Proven both
    ways: the answer must be the point release regardless of which tag was
    created first."""
    repo = tmp_path / "r"
    _init_repo(repo)
    if order == "alias_first":
        _git(repo, "tag", "v1")
        _git(repo, "tag", "v1.5.0")
    else:
        _git(repo, "tag", "v1.5.0")
        _git(repo, "tag", "v1")
    assert onboard.current_templates_version(repo) == "v1.5.0"


def test_current_templates_version_picks_the_highest_point_release_at_head(tmp_path):
    """Not strictly the reported bug, but the same enumerate-and-choose
    logic must break ties among several *real* point releases sensibly,
    not by tag-creation order either."""
    repo = tmp_path / "r"
    _init_repo(repo)
    _git(repo, "tag", "v1.5.0")
    _git(repo, "tag", "v1.10.0")  # lexicographically < v1.5.0, numerically greater
    _git(repo, "tag", "v1.9.0")
    assert onboard.current_templates_version(repo) == "v1.10.0"


def test_parse_point_release_excludes_the_moving_alias():
    assert onboard.parse_point_release("v1") is None
    assert onboard.parse_point_release("v1.5.0") == (1, 5, 0)
    assert onboard.parse_point_release("latest") is None


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
        "# ── towncrier ──\n"
        "[tool.towncrier]\n"
        'directory = "changelog.d"\n'
        'start_string = "<!-- towncrier release notes start -->\\n"\n\n'
        "# ── em-release ──\n"
        "[tool.em-release]\nversion_files = [\n]\n\n"
        "# ── mypy ──\n"
        "[tool.mypy]\ndisallow_untyped_defs = true\n\n"
        "# ── pytest ──\n"
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n\n'
        "# ── dev dependency group additions ──\n"
        "# pytest>=8.0, ruff>=0.16, mypy>=1.14\n"
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


# ---------------------------------------------------- private vulnerability reporting


def _fake_gh_pvr(*, enabled: bool, put_ok: bool = True):
    """A stand-in for onboard._gh that answers the PVR "is it enabled"
    check and the enabling PUT, distinguished by whether -X/PUT is in the
    args -- mirrors how ensure_label's own `gh` calls are shaped."""

    def fake(args: list[str]) -> tuple[int, str, str]:
        if "-X" in args and "PUT" in args:
            return (0, "", "") if put_ok else (1, "", "insufficient scope")
        return (0, "true\n" if enabled else "false\n", "")

    return fake


def test_ensure_pvr_skips_a_private_repo_without_calling_gh(monkeypatch):
    """Private is the normal path (most repos onboard private, go public
    later), not an error -- and needs no gh call at all, since the caller
    already resolved visibility before calling this."""

    def exploding_gh(args):
        raise AssertionError("must not call gh for a private repo")

    monkeypatch.setattr(onboard, "_gh", exploding_gh)
    msg = onboard.ensure_pvr("o/r", "private", dry_run=False)
    assert "skipped" in msg
    assert "private" in msg


def test_ensure_pvr_reports_when_visibility_is_unknown(monkeypatch):
    def exploding_gh(args):
        raise AssertionError("must not call gh when visibility couldn't be read")

    monkeypatch.setattr(onboard, "_gh", exploding_gh)
    msg = onboard.ensure_pvr("o/r", None, dry_run=False)
    assert "could not check visibility" in msg


def test_ensure_pvr_is_idempotent_when_already_enabled(monkeypatch):
    calls = []
    already_enabled = _fake_gh_pvr(enabled=True)

    def fake(args):
        calls.append(args)
        return already_enabled(args)

    monkeypatch.setattr(onboard, "_gh", fake)
    msg = onboard.ensure_pvr("o/r", "public", dry_run=False)
    assert "already enabled" in msg
    assert not any("-X" in c for c in calls), "already-enabled must not attempt a PUT"


def test_ensure_pvr_dry_run_reports_without_enabling(monkeypatch):
    calls = []

    def fake(args):
        calls.append(args)
        if "-X" in args:
            raise AssertionError("dry-run must never PUT")
        return (0, "false\n", "")

    monkeypatch.setattr(onboard, "_gh", fake)
    msg = onboard.ensure_pvr("o/r", "public", dry_run=True)
    assert "would enable" in msg
    assert calls, "the check itself should still run under dry-run, just not the write"


def test_ensure_pvr_enables_a_public_repo_that_lacks_it(monkeypatch):
    monkeypatch.setattr(onboard, "_gh", _fake_gh_pvr(enabled=False, put_ok=True))
    msg = onboard.ensure_pvr("o/r", "public", dry_run=False)
    assert msg == f"{onboard.PVR_NAME} enabled"


def test_ensure_pvr_reports_failure_without_raising(monkeypatch):
    """Requirement: a failed enable (no admin on the target repo, most
    commonly) must not abort onboarding -- it's reported like a failed
    label create, and the caller keeps going."""
    monkeypatch.setattr(onboard, "_gh", _fake_gh_pvr(enabled=False, put_ok=False))
    msg = onboard.ensure_pvr("o/r", "public", dry_run=False)
    assert "failed" in msg
    assert "insufficient scope" in msg


def test_ensure_pvr_reports_when_the_enabled_check_itself_fails(monkeypatch):
    monkeypatch.setattr(onboard, "_gh", lambda args: (1, "", "not found"))
    msg = onboard.ensure_pvr("o/r", "public", dry_run=False)
    assert "could not check" in msg


def test_repo_visibility_reads_the_gh_response(monkeypatch):
    monkeypatch.setattr(onboard, "_gh", lambda args: (0, "public\n", ""))
    assert onboard.repo_visibility("o/r") == "public"


def test_repo_visibility_returns_none_when_gh_fails(monkeypatch):
    monkeypatch.setattr(onboard, "_gh", lambda args: (1, "", "not found"))
    assert onboard.repo_visibility("o/r") is None


def _blank_plan() -> onboard.Plan:
    return onboard.Plan(repo_path=Path(), package_name="p", version="1.0.0")


def test_print_next_steps_reminds_about_pvr_for_a_private_repo(capsys):
    onboard.print_next_steps(_blank_plan(), [], private_repo=True)
    out = capsys.readouterr().out
    assert onboard.PVR_NAME in out
    assert "private" in out.lower()


def test_print_next_steps_is_silent_about_pvr_by_default(capsys):
    onboard.print_next_steps(_blank_plan(), [])
    out = capsys.readouterr().out
    assert onboard.PVR_NAME not in out
