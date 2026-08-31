"""Tests for scripts/lint_gate.py.

Covers the pure half: locating the lint job, detecting the flag, adding and
removing it, and deriving the four possible states. The `gh` calls are not
exercised here -- they are thin wrappers over the CLI.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "lint_gate.py"
spec = importlib.util.spec_from_file_location("lint_gate", SCRIPT)
lint_gate = importlib.util.module_from_spec(spec)
sys.modules["lint_gate"] = lint_gate
spec.loader.exec_module(lint_gate)


CI_OFF = """\
name: CI

on:
  pull_request:
  workflow_call:

jobs:
  lint:
    name: lint
    runs-on: ubuntu-latest
    # STAGED ROLLOUT. `continue-on-error` does NOT make lint non-blocking.
    # Managed by scripts/lint_gate.py.
    continue-on-error: true
    steps:
      - run: uv run ruff check .

  test:
    name: test
    runs-on: ubuntu-latest
    steps:
      - run: uv run pytest

  build:
    name: build
    runs-on: ubuntu-latest
    steps:
      - run: uv build
"""


def test_finds_the_lint_job_and_stops_at_the_next_job():
    start, end = lint_gate.find_lint_job_block(CI_OFF)
    block = CI_OFF.split("\n")[start:end]
    assert block[0].strip() == "lint:"
    assert any("ruff check" in line for line in block)
    assert not any("pytest" in line for line in block), "bled into the test job"


def test_missing_lint_job_is_a_readable_error():
    with pytest.raises(lint_gate.LintGateError, match="No `lint:` job"):
        lint_gate.find_lint_job_block("jobs:\n  test:\n    runs-on: ubuntu-latest\n")


def test_detects_the_flag_when_present():
    assert lint_gate.has_continue_on_error(CI_OFF) is True


def test_ignores_the_flag_on_a_different_job():
    """A continue-on-error on `test` must not read as lint being staged off."""
    ci = CI_OFF.replace("    continue-on-error: true\n", "", 1).replace(
        "  test:\n    name: test\n",
        "  test:\n    name: test\n    continue-on-error: true\n",
        1,
    )
    assert lint_gate.has_continue_on_error(ci) is False


def test_ignores_a_commented_out_flag():
    ci = CI_OFF.replace(
        "    continue-on-error: true", "    # continue-on-error: true   # disabled by hand"
    )
    assert lint_gate.has_continue_on_error(ci) is False


def test_turning_on_removes_flag_and_its_rollout_comment():
    on = lint_gate.strip_continue_on_error(CI_OFF)
    assert lint_gate.has_continue_on_error(on) is False
    assert "STAGED ROLLOUT" not in on
    assert "# Enforced:" in on
    # Everything else survives.
    assert "uv run ruff check ." in on
    assert "uv run pytest" in on
    assert "uv build" in on


def test_turning_on_replaces_the_pre_v1_1_0_rollout_comment():
    """Repos onboarded before v1.1.0 carry the old template's wording.

    Missing it leaves "non-blocking until the backlog is cleared" sitting
    directly above "Enforced:" -- a file that contradicts itself.
    Caught on the first real run against a live consuming repo.
    """
    legacy = CI_OFF.replace(
        "    # STAGED ROLLOUT. `continue-on-error` does NOT make lint non-blocking.\n"
        "    # Managed by scripts/lint_gate.py.\n",
        "    # Staged rollout: non-blocking until the existing lint backlog is\n"
        "    # cleared in its own PR. Remove this line once that's done.\n",
    )
    assert "Staged rollout: non-blocking" in legacy, "fixture did not apply"

    on = lint_gate.strip_continue_on_error(legacy)
    assert "Staged rollout: non-blocking" not in on
    assert "# Enforced:" in on


def test_turning_on_leaves_an_unrelated_comment_alone():
    ci = CI_OFF.replace(
        "    continue-on-error: true",
        "    # TODO: revisit once the monorepo split lands\n    continue-on-error: true",
    ).replace("    # STAGED ROLLOUT. `continue-on-error` does NOT make lint non-blocking.\n", "")
    on = lint_gate.strip_continue_on_error(ci)
    assert "TODO: revisit once the monorepo split lands" in on, "deleted someone else's comment"


def test_round_trip_off_on_off_is_stable():
    on = lint_gate.strip_continue_on_error(CI_OFF)
    off = lint_gate.add_continue_on_error(on, lint_gate.DEFAULT_OFF_COMMENT)
    assert lint_gate.has_continue_on_error(off) is True
    assert "# Enforced:" not in off
    again = lint_gate.strip_continue_on_error(off)
    assert lint_gate.has_continue_on_error(again) is False


def test_adding_is_idempotent():
    assert lint_gate.add_continue_on_error(CI_OFF, lint_gate.DEFAULT_OFF_COMMENT) == CI_OFF


def test_removing_when_absent_is_a_no_op():
    on = lint_gate.strip_continue_on_error(CI_OFF)
    assert lint_gate.strip_continue_on_error(on) == on


def test_the_flag_lands_inside_the_lint_job_not_a_sibling():
    on = lint_gate.strip_continue_on_error(CI_OFF)
    off = lint_gate.add_continue_on_error(on, lint_gate.DEFAULT_OFF_COMMENT)
    start, end = lint_gate.find_lint_job_block(off)
    assert any("continue-on-error: true" in line for line in off.split("\n")[start:end])


@pytest.mark.parametrize(
    ("has_line", "required", "expected"),
    [
        (True, False, "OFF"),
        (False, True, "ON"),
        (False, False, "INCONSISTENT"),
        (True, True, "INCONSISTENT"),
    ],
)
def test_state_derivation(has_line, required, expected):
    assert lint_gate.State(has_line, required).name == expected


def test_the_two_inconsistent_states_are_described_differently():
    """They fail in opposite directions, so the same message would misdirect."""
    stalls_release = lint_gate.State(has_line=False, lint_required=False).detail
    skips_release = lint_gate.State(has_line=True, lint_required=True).detail
    assert stalls_release != skips_release
    assert "stall" in stalls_release
    assert "sails through" in skips_release


def test_the_shipped_template_is_in_the_off_state():
    """The template must onboard repos with lint advisory, per docs/onboarding.md."""
    template = (Path(__file__).resolve().parents[1] / "templates" / "ci.yml").read_text()
    assert lint_gate.has_continue_on_error(template) is True


# --------------------------------------------------- generalized to --job

CI_THREE_JOBS = """\
name: CI

on:
  pull_request:
  workflow_call:

jobs:
  lint:
    name: lint
    runs-on: ubuntu-latest
    # STAGED ROLLOUT. `continue-on-error` does NOT make lint non-blocking.
    # Managed by scripts/lint_gate.py.
    continue-on-error: true
    steps:
      - run: uv run ruff check .

  format:
    name: format
    runs-on: ubuntu-latest
    # STAGED ROLLOUT. `continue-on-error` does NOT make format non-blocking.
    # Managed by scripts/lint_gate.py.
    continue-on-error: true
    steps:
      - run: uv run ruff format --check .

  typecheck:
    name: typecheck
    runs-on: ubuntu-latest
    # STAGED ROLLOUT. `continue-on-error` does NOT make typecheck non-blocking.
    # Managed by scripts/lint_gate.py.
    continue-on-error: true
    steps:
      - run: uv run mypy src

  test:
    name: test
    runs-on: ubuntu-latest
    steps:
      - run: uv run pytest

  build:
    name: build
    runs-on: ubuntu-latest
    steps:
      - run: uv build
"""


def test_default_job_is_still_lint():
    """Every pre-existing call site (no `job` argument) must keep behaving
    exactly as it did before format/typecheck existed."""
    assert lint_gate.find_lint_job_block(CI_THREE_JOBS) == lint_gate.find_lint_job_block(
        CI_THREE_JOBS, "lint"
    )
    assert lint_gate.has_continue_on_error(CI_THREE_JOBS) is True


@pytest.mark.parametrize("job", ["lint", "format", "typecheck"])
def test_each_job_block_is_found_independently(job):
    start, end = lint_gate.find_lint_job_block(CI_THREE_JOBS, job)
    block = CI_THREE_JOBS.split("\n")[start:end]
    assert block[0].strip() == f"{job}:"
    others = {"lint", "format", "typecheck"} - {job}
    for other in others:
        assert f"{other}:" not in "\n".join(line.strip() for line in block[1:]), (
            f"{job} block bled into {other}"
        )


@pytest.mark.parametrize("job", ["lint", "format", "typecheck"])
def test_turning_a_job_on_only_touches_that_job(job):
    on = lint_gate.strip_continue_on_error(CI_THREE_JOBS, job)
    assert lint_gate.has_continue_on_error(on, job) is False
    for other in {"lint", "format", "typecheck"} - {job}:
        assert lint_gate.has_continue_on_error(on, other) is True
    assert f"`{job}` is a required status" in on


@pytest.mark.parametrize("job", ["lint", "format", "typecheck"])
def test_every_jobs_restore_hint_carries_its_own_job_flag(job):
    """No special-casing for the default job: `--job <job>` appears even
    for lint, so the hint is always copy-pasteable as shown."""
    on = lint_gate.strip_continue_on_error(CI_THREE_JOBS, job)
    assert f"--job {job} off" in on


def test_default_off_comment_names_the_job():
    assert "`format` failure" in lint_gate.default_off_comment("format")
    assert "`typecheck` failure" in lint_gate.default_off_comment("typecheck")
    assert "--job format on" in lint_gate.default_off_comment("format")
    assert "--job typecheck on" in lint_gate.default_off_comment("typecheck")


@pytest.mark.parametrize(
    ("job", "has_line", "required", "expected"),
    [
        ("format", True, False, "OFF"),
        ("typecheck", False, True, "ON"),
        ("format", False, False, "INCONSISTENT"),
    ],
)
def test_state_derivation_is_job_agnostic(job, has_line, required, expected):
    assert lint_gate.State(has_line, required, job).name == expected


def test_state_detail_names_the_job_not_lint():
    state = lint_gate.State(has_line=False, lint_required=True, job="typecheck")
    assert "typecheck" in state.detail.lower()
    assert "lint" not in state.detail.lower()


def test_backlog_check_command_differs_per_job():
    assert lint_gate.BACKLOG_CHECK_CMD["lint"] == ["uv", "run", "ruff", "check", "."]
    assert lint_gate.BACKLOG_CHECK_CMD["format"] == ["uv", "run", "ruff", "format", "--check", "."]
    assert lint_gate.BACKLOG_CHECK_CMD["typecheck"] == ["uv", "run", "mypy", "src"]


def test_cli_job_flag_defaults_to_lint(capsys, monkeypatch):
    """`--job` must default to `lint` so every existing invocation is unaffected."""
    monkeypatch.setattr(lint_gate, "read_ci", lambda repo_path: (Path("ci.yml"), CI_THREE_JOBS))
    monkeypatch.setattr(lint_gate, "detect_repo", lambda repo_path: "o/r")
    monkeypatch.setattr(lint_gate, "default_branch", lambda repo: "main")
    monkeypatch.setattr(lint_gate, "required_contexts", lambda repo, branch: ["test", "build"])
    lint_gate.main(["status"])
    out = capsys.readouterr().out
    assert "job:               lint" in out


# ------------------------------------------ replace, don't stack, on flip

# The exact bug this section pins: `on` used to walk backward through
# whatever comment lines were CONTIGUOUS above continue-on-error, then
# delete that whole run only if a sentinel appeared somewhere in it. A
# genuinely blank line inside a real staged-rollout comment (not a `#`
# line, an actual blank one) stopped that walk partway through, leaving
# the top of the old comment sitting above the new "Enforced:" text.


def _job_block(text, job):
    start, end = lint_gate.find_lint_job_block(text, job)
    return "\n".join(text.split("\n")[start:end])


CI_STAGED_COMMENT_WITH_BLANK_LINE = """\
name: CI
jobs:
  format:
    name: format
    runs-on: ubuntu-latest
    # STAGED ROLLOUT. Not a required check yet.

    # Second paragraph, separated by a genuinely blank line.
    continue-on-error: true
    steps:
      - run: uv run ruff format --check .
"""


def test_a_blank_line_inside_the_staged_comment_no_longer_leaves_a_leftover():
    on = lint_gate.strip_continue_on_error(CI_STAGED_COMMENT_WITH_BLANK_LINE, "format")
    assert "Not a required check yet" not in on
    assert "Second paragraph" not in on
    assert "# Enforced:" in on


CI_ALREADY_ENFORCED = """\
name: CI
jobs:
  typecheck:
    name: typecheck
    runs-on: ubuntu-latest
    # Typecheck is ENFORCED: this job has no continue-on-error, and `typecheck` is
    # a required status check. To stage it back off, run lint_gate.py --job typecheck off
    # -- which restores both halves together. See docs/onboarding.md.
    steps:
      - run: uv run mypy src
"""


def test_off_replaces_an_already_enforced_pre_this_fix_comment():
    """A file whose `typecheck` job is enforced with the OLD three-line
    "<Job> is ENFORCED" wording must not end up with that old comment
    sitting above the newly restored staged comment."""
    off = lint_gate.add_continue_on_error(
        CI_ALREADY_ENFORCED, lint_gate.default_off_comment("typecheck"), "typecheck"
    )
    assert "is ENFORCED" not in off
    assert "# Staged:" in off
    assert lint_gate.has_continue_on_error(off, "typecheck") is True


@pytest.mark.parametrize("job", ["lint", "format", "typecheck"])
def test_on_produces_the_exact_enforced_comment(job):
    on = lint_gate.strip_continue_on_error(CI_THREE_JOBS, job)
    block = _job_block(on, job)
    assert (
        f"    # Enforced: no continue-on-error, and `{job}` is a required status\n"
        f"    # check. Stage it back to advisory with `lint_gate.py --job {job} off`.\n"
        f"    steps:"
    ) in block + "\n"


@pytest.mark.parametrize("job", ["lint", "format", "typecheck"])
def test_off_produces_the_exact_staged_comment(job):
    on = lint_gate.strip_continue_on_error(CI_THREE_JOBS, job)
    off = lint_gate.add_continue_on_error(on, lint_gate.default_off_comment(job), job)
    assert lint_gate.default_off_comment(job) in _job_block(off, job)


@pytest.mark.parametrize("job", ["lint", "format", "typecheck"])
def test_on_off_on_round_trip_produces_identical_text_each_time(job):
    """The actual idempotency requirement: flipping repeatedly converges to
    a stable two-state cycle instead of accumulating comment layers."""
    on_1 = lint_gate.strip_continue_on_error(CI_THREE_JOBS, job)
    off_1 = lint_gate.add_continue_on_error(on_1, lint_gate.default_off_comment(job), job)
    on_2 = lint_gate.strip_continue_on_error(off_1, job)
    off_2 = lint_gate.add_continue_on_error(on_2, lint_gate.default_off_comment(job), job)

    assert _job_block(on_1, job) == _job_block(on_2, job)
    assert _job_block(off_1, job) == _job_block(off_2, job)

    # No leftover fragments from either comment anywhere in the job block,
    # at any point in the cycle.
    for text in (on_1, off_1, on_2, off_2):
        block = _job_block(text, job)
        assert block.count("# Enforced:") <= 1
        assert block.count("# Staged:") <= 1
        assert "STAGED ROLLOUT" not in block


@pytest.mark.parametrize("job", ["lint", "format", "typecheck"])
def test_flipping_on_against_the_real_template_leaves_no_leftovers(job):
    """The actual shipped templates/ci.yml, not a fixture -- this is what
    every consuming repo's ci.yml starts from."""
    template = (Path(__file__).resolve().parents[1] / "templates" / "ci.yml").read_text()
    on = lint_gate.strip_continue_on_error(template, job)
    block = _job_block(on, job)
    assert "STAGED ROLLOUT" not in block
    assert block.count("# Enforced:") == 1
