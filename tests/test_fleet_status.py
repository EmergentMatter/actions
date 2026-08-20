"""Tests for scripts/fleet_status.py.

Covers the pure evaluation logic. The `gh` wrappers aren't exercised — they
are thin shells over the CLI, and the value here is that a repo in a bad
state is reported as bad, with the right severity.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fleet_status.py"
spec = importlib.util.spec_from_file_location("fleet_status", SCRIPT)
fleet_status = importlib.util.module_from_spec(spec)
sys.modules["fleet_status"] = fleet_status
spec.loader.exec_module(fleet_status)


GOOD_STUB = """\
name: Changelog
on:
  pull_request:
jobs:
  changelog:
    runs-on: ubuntu-latest
    steps:
      - uses: EmergentMatter/actions/changelog-check@v1
"""

REMOVED_PATH_STUB = """\
name: Changelog
on:
  pull_request:
jobs:
  changelog:
    uses: EmergentMatter/actions/.github/workflows/changelog-check.yml@v1
"""

CI_LINT_OFF = """\
name: CI
on:
  pull_request:
jobs:
  lint:
    name: lint
    runs-on: ubuntu-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@abc # v7.0.1
"""

CI_LINT_ON = CI_LINT_OFF.replace("    continue-on-error: true\n", "")

HEALTHY_CONTEXTS = ["test", "build", "changelog"]


def severities(findings, check):
    return [f.severity for f in findings if f.check == check]


def messages(findings, check):
    return " ".join(f.message for f in findings if f.check == check)


# ----------------------------------------------------------------- the stub


def test_removed_workflow_path_is_broken_not_merely_stale():
    """v1.1.0 deleted that path, so a repo still on it 404s on every PR."""
    findings = fleet_status.check_stub(REMOVED_PATH_STUB)
    assert severities(findings, "stub") == ["broken"]
    assert "404" in messages(findings, "stub")


def test_composite_action_stub_is_clean():
    assert fleet_status.check_stub(GOOD_STUB) == []


def test_missing_stub_is_a_warning():
    assert severities(fleet_status.check_stub(None), "stub") == ["warn"]


# ------------------------------------------------------------------ the gate


def test_inconsistent_gate_is_broken():
    findings = fleet_status.check_gate(CI_LINT_OFF, ["lint", *HEALTHY_CONTEXTS])
    assert severities(findings, "gate") == ["broken"]
    assert "INCONSISTENT" in messages(findings, "gate")


@pytest.mark.parametrize(
    ("ci", "contexts", "expected"),
    [
        (CI_LINT_OFF, HEALTHY_CONTEXTS, "OFF"),
        (CI_LINT_ON, ["lint", *HEALTHY_CONTEXTS], "ON"),
    ],
)
def test_consistent_gates_report_their_state_as_info(ci, contexts, expected):
    findings = fleet_status.check_gate(ci, contexts)
    assert severities(findings, "gate") == ["info"]
    assert expected in messages(findings, "gate")


def test_repo_without_a_lint_job_is_not_flagged_as_broken():
    ci = "name: CI\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
    assert severities(fleet_status.check_gate(ci, HEALTHY_CONTEXTS), "gate") == ["info"]


# ------------------------------------------------------------------ the rest


def test_node20_pin_is_flagged():
    ci = CI_LINT_OFF.replace("abc", "330a01c490aca151604b8cf639adc76d48f6c5d4")
    assert severities(fleet_status.check_pins(ci), "pins") == ["warn"]
    assert "Node 20" in messages(fleet_status.check_pins(ci), "pins")


def test_current_pins_are_clean():
    assert fleet_status.check_pins(CI_LINT_ON) == []


def test_missing_workflow_name_is_info_not_a_warning():
    """Cosmetic: it changes how checks display, not whether they gate."""
    unnamed = CI_LINT_OFF.replace("name: CI\n", "", 1)
    assert severities(fleet_status.check_naming(unnamed), "naming") == ["info"]


def test_lint_is_not_expected_among_required_contexts():
    """Onboarding requires test/build/changelog; lint is opt-in later."""
    assert fleet_status.check_contexts(HEALTHY_CONTEXTS) == []


def test_missing_required_contexts_are_named():
    findings = fleet_status.check_contexts(["test"])
    assert severities(findings, "contexts") == ["warn"]
    assert "build" in messages(findings, "contexts")
    assert "changelog" in messages(findings, "contexts")


def test_unprotected_branch_is_a_warning():
    assert severities(fleet_status.check_contexts(None), "contexts") == ["warn"]


# ------------------------------------------------------------- roll-up logic


def test_a_healthy_repo_has_no_actionable_findings():
    findings = fleet_status.evaluate(CI_LINT_OFF, GOOD_STUB, HEALTHY_CONTEXTS)
    assert [f for f in findings if f.severity != "info"] == []


def test_broken_outranks_warn_in_the_roll_up():
    report = fleet_status.RepoReport(
        "o/r",
        [
            fleet_status.Finding("pins", "warn", "x"),
            fleet_status.Finding("stub", "broken", "y"),
            fleet_status.Finding("gate", "info", "z"),
        ],
    )
    assert report.worst == "broken"


def test_info_only_repo_rolls_up_as_ok():
    report = fleet_status.RepoReport("o/r", [fleet_status.Finding("gate", "info", "OFF")])
    assert report.worst == "info"
    assert fleet_status.MARK[report.worst] == "ok"


def test_render_lists_broken_repos_before_healthy_ones():
    reports = [
        fleet_status.RepoReport("o/healthy", [fleet_status.Finding("gate", "info", "OFF")]),
        fleet_status.RepoReport("o/bad", [fleet_status.Finding("stub", "broken", "404s")]),
    ]
    out = fleet_status.render(reports, show_info=True)
    assert out.index("o/bad") < out.index("o/healthy")
    assert "1 broken" in out
