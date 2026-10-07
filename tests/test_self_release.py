"""Tests for how this repo releases itself: release.yml's shape and the major guard.

release.yml runs with write access to the repo and calls the same
version.yml consumers call, so these read the real files as text and pin the
properties that must not drift: the grants, the absence of inherited secrets
and risky triggers, and the stub it mirrors.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE = REPO_ROOT / ".github" / "workflows" / "release.yml"
SELF_CI = REPO_ROOT / ".github" / "workflows" / "self-ci.yml"
CHANGELOG_CHECK = REPO_ROOT / ".github" / "workflows" / "changelog-check.yml"
STUB_VERSION = REPO_ROOT / "templates" / "stub-version.yml"
GUARD = REPO_ROOT / "scripts" / "check_major_ceiling.py"
VERSION = REPO_ROOT / ".github" / "workflows" / "version.yml"


def _code(path: Path) -> str:
    """The file with comment-only lines removed, so prose cannot satisfy or trip a check."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _job_block(text: str, job: str) -> str:
    match = re.search(rf"^  {re.escape(job)}:\n((?:    .*\n|\n)*)", text + "\n", re.MULTILINE)
    assert match, f"job {job!r} not found"
    return match.group(1)


def _permissions(block: str) -> dict[str, str]:
    inline = re.search(r"^    permissions: \{(.*)\}", block, re.MULTILINE)
    if inline:
        pairs = [p.split(":") for p in inline.group(1).split(",")]
        return {k.strip(): v.strip() for k, v in pairs}
    multi = re.search(r"^    permissions:\n((?:      .*\n)+)", block, re.MULTILINE)
    assert multi, "no permissions block"
    return {
        k.strip(): v.strip() for k, v in (line.split(":") for line in multi.group(1).splitlines())
    }


# -------------------------------------------------------------- release.yml


def test_release_calls_the_local_version_workflow_not_the_pin():
    block = _job_block(_code(RELEASE), "version")
    assert "uses: ./.github/workflows/version.yml" in block
    assert "@v1" not in _code(RELEASE)


def test_release_version_job_grants_exactly_what_the_stub_grants():
    """The caller's permissions are a ceiling for version.yml; the shipped stub's grant is the
    contract for what it needs, and this repo must neither fall short of it nor go beyond."""
    ours = _permissions(_job_block(_code(RELEASE), "version"))
    stub = _permissions(_job_block(_code(STUB_VERSION), "version"))
    assert ours == stub == {"contents": "write", "pull-requests": "write"}


def test_release_has_no_id_token_anywhere():
    assert "id-token" not in _code(RELEASE)


def test_release_does_not_publish_or_build_a_wheel():
    block = _job_block(_code(RELEASE), "version")
    assert re.search(r"^      has-wheel: false$", block, re.MULTILINE)
    assert "publish" not in block


def test_release_inherits_no_secrets_and_uses_no_risky_trigger():
    code = _code(RELEASE)
    assert "secrets:" not in code
    assert "pull_request_target" not in code
    assert "workflow_run" not in code
    assert re.search(r"^on:\n  push:\n    branches: \[main\]\n", code, re.MULTILINE)


def test_release_scripts_ref_is_the_commit_being_released():
    assert "actions-ref: ${{ github.sha }}" in _code(RELEASE)


def test_release_version_waits_for_ci_and_the_major_guard():
    block = _job_block(_code(RELEASE), "version")
    assert "needs: [ci, major-guard]" in block


def test_major_guard_runs_after_ci():
    assert re.search(r"^    needs: ci$", _job_block(_code(RELEASE), "major-guard"), re.MULTILINE)


def test_ci_job_calls_the_local_self_ci_workflow():
    block = _job_block(_code(RELEASE), "ci")
    assert re.search(r"^    uses: \./\.github/workflows/self-ci\.yml$", block, re.MULTILINE)


def test_guard_step_pins_major_one():
    block = _job_block(_code(RELEASE), "major-guard")
    assert re.search(
        r"^        run: python3 scripts/check_major_ceiling\.py --major 1$", block, re.MULTILINE
    )


def test_guard_defaults_match_what_version_yml_reads():
    """The guard step passes no paths, so its defaults must be where version.yml looks."""
    source = GUARD.read_text(encoding="utf-8")
    guard_notes = re.search(r'"--notes-dir", default="([^"]+)"', source)
    guard_pyproject = re.search(r'"--pyproject", default="([^"]+)"', source)
    assert guard_notes and guard_pyproject
    version = _code(VERSION)
    notes = re.search(
        r"^      notes-dir:\n(?:        .*\n)*?        default: (\S+)$", version, re.MULTILINE
    )
    assert notes
    assert guard_notes.group(1) == notes.group(1)
    assert guard_pyproject.group(1) == "pyproject.toml"


def test_self_ci_is_callable_and_keeps_its_triggers():
    code = _code(SELF_CI)
    on = code.split("\njobs:")[0]
    for trigger in ("pull_request:", "push:", "workflow_call:"):
        assert trigger in on


def test_self_ci_concurrency_is_keyed_by_the_calling_workflow():
    """Run alone and run by release.yml on the same push, it must not cancel itself."""
    assert "group: self-ci-${{ github.workflow }}-${{ github.ref }}" in _code(SELF_CI)


def test_changelog_check_runs_the_local_action_after_a_checkout():
    code = _code(CHANGELOG_CHECK)
    assert "uses: ./changelog-check" in code
    assert "changelog-check@v1" not in code
    assert code.index("actions/checkout") < code.index("./changelog-check")


# -------------------------------------------------------------- major guard


def _repo(tmp_path: Path, version: str, *notes: str) -> None:
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "demo"\nversion = "{version}"\n')
    notes_dir = tmp_path / "changelog.d"
    notes_dir.mkdir()
    for name in notes:
        (notes_dir / name).write_text("something changed\n")


def _guard(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUARD), *extra],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "notes",
    [(), ("+a.patch.md",), ("+a.minor.md", "+b.patch.md")],
    ids=["no-notes", "patch", "minor"],
)
def test_guard_passes_below_a_major(tmp_path: Path, notes: tuple[str, ...]):
    _repo(tmp_path, "1.15.0", *notes)
    result = _guard(tmp_path)
    assert result.returncode == 0, result.stderr


def test_guard_fails_on_a_major_note(tmp_path: Path):
    _repo(tmp_path, "1.15.0", "+a.minor.md", "+b.major.md")
    result = _guard(tmp_path)
    assert result.returncode == 1
    assert "2.0.0" in result.stderr
    assert "RELEASING.md" in result.stderr


def test_guard_fails_when_the_current_major_is_not_the_expected_one(tmp_path: Path):
    _repo(tmp_path, "2.0.0")
    assert _guard(tmp_path).returncode == 1


def test_guard_major_is_configurable(tmp_path: Path):
    _repo(tmp_path, "2.3.0", "+a.minor.md")
    assert _guard(tmp_path, "--major", "2").returncode == 0


def test_guard_rejects_an_unknown_note_level_rather_than_ignoring_it(tmp_path: Path):
    _repo(tmp_path, "1.15.0", "+a.huge.md")
    assert _guard(tmp_path).returncode == 1
