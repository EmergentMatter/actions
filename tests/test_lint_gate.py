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
    assert "Lint is ENFORCED" in on
    # Everything else survives.
    assert "uv run ruff check ." in on
    assert "uv run pytest" in on
    assert "uv build" in on


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
    assert "Lint is ENFORCED" not in off
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
