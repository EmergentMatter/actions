"""Tests for scripts/fleet_status.py.

Covers the pure evaluation logic. The `gh` wrappers aren't exercised: they
are thin shells over the CLI, and the value here is that a repo in a bad
state is reported as bad, with the right severity.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
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


# ------------------------------------------------------- workflow_call


def test_ci_without_workflow_call_is_broken():
    """version.yml fails at PARSE time, so every push to main errors."""
    ci = CI_LINT_OFF.replace("on:\n  pull_request:\n", "on:\n  pull_request:\n  push:\n")
    findings = fleet_status.check_workflow_call(ci)
    assert severities(findings, "workflow_call") == ["broken"]
    assert "parse time" in messages(findings, "workflow_call")


def test_ci_with_workflow_call_is_clean():
    ci = CI_LINT_OFF.replace("  pull_request:\n", "  pull_request:\n  workflow_call:\n")
    assert fleet_status.check_workflow_call(ci) == []


# ----------------------------------------------------------- verify-wheel

CI_MATERIALS_STYLE = "name: CI\njobs:\n  test:\n    steps:\n      - run: uv run pytest\n"


def test_build_without_verify_wheel_is_flagged():
    ci = "jobs:\n  build:\n    steps:\n      - run: uv build\n"
    findings = fleet_status.check_verify_wheel(ci)
    assert severities(findings, "verify") == ["warn"]
    assert "verify-wheel@v1" in messages(findings, "verify")


def test_build_with_verify_wheel_is_clean():
    ci = (
        "jobs:\n  build:\n    steps:\n      - run: uv build\n"
        "      - uses: EmergentMatter/actions/verify-wheel@v1\n"
    )
    assert fleet_status.check_verify_wheel(ci) == []


def test_a_repo_with_no_build_job_is_not_nagged():
    """Repos keeping their own CI may have no build stage at all."""
    assert fleet_status.check_verify_wheel(CI_MATERIALS_STYLE) == []


# -------------------------------------------------------------- templates

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGESET_TEMPLATE = (REPO_ROOT / "templates" / "changeset.py").read_text()

MANAGED_ENTRY = fleet_status.TemplateEntry(
    source="changeset.py", dest="scripts/changeset.py", policy="managed"
)
SEED_ONCE_ENTRY = fleet_status.TemplateEntry(
    source="ci.yml", dest=".github/workflows/ci.yml", policy="seed-once"
)

# Matches the real releases tagged on this repo (see `git tag --list`); used
# as a realistic, deterministic fixture -- the checks take tags as data, so
# nothing here actually shells out to git.
ALL_TAGS = ["v1", "v1.0.0", "v1.0.2", "v1.1.0", "v1.2.0", "v1.3.0", "v1.4.0", "v1.5.0"]


def test_load_manifest_parses_the_documented_shape(tmp_path):
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        """\
[[template]]
source = "PULL_REQUEST_TEMPLATE.md"
dest   = ".github/PULL_REQUEST_TEMPLATE.md"
policy = "managed"
"""
    )
    entries = fleet_status.load_manifest(manifest)
    assert entries == [
        fleet_status.TemplateEntry(
            "PULL_REQUEST_TEMPLATE.md", ".github/PULL_REQUEST_TEMPLATE.md", "managed"
        )
    ]


def test_missing_manifest_raises_loudly():
    """A missing manifest.toml is a broken repo, not zero templates -- treating it as
    empty would make every templates/stamp finding vanish while the sweep exits 0."""
    with pytest.raises(OSError):
        fleet_status.load_manifest(Path("/no/such/manifest.toml"))


def test_malformed_manifest_raises_loudly(tmp_path):
    manifest = tmp_path / "manifest.toml"
    manifest.write_text("this is not valid toml [[[")
    with pytest.raises(tomllib.TOMLDecodeError):
        fleet_status.load_manifest(manifest)


def test_entry_with_no_policy_key_raises_loudly(tmp_path):
    """onboard.py's loader requires `policy` -- fleet_status.py must use that same
    loader (not its own), so the two tools can never quietly disagree on this again.
    A typo'd/omitted `policy` must fail loudly, not default to "managed" and turn a
    seed-once file (customisable on purpose) into a false-positive drift finding."""
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        """\
[[template]]
source = "PULL_REQUEST_TEMPLATE.md"
dest   = ".github/PULL_REQUEST_TEMPLATE.md"
"""
    )
    with pytest.raises(KeyError):
        fleet_status.load_manifest(manifest)


def test_managed_template_matching_is_clean():
    findings = fleet_status.check_templates(
        [MANAGED_ENTRY], {"scripts/changeset.py": CHANGESET_TEMPLATE}, stamp_status=None
    )
    assert findings == []


def test_managed_template_differing_is_flagged():
    findings = fleet_status.check_templates(
        [MANAGED_ENTRY], {"scripts/changeset.py": "# an older copy\n"}, stamp_status=None
    )
    assert severities(findings, "templates") == ["warn"]
    assert "scripts/changeset.py differs from templates/changeset.py" in messages(
        findings, "templates"
    )


def test_seed_once_template_differing_produces_no_finding():
    """ci.yml is legitimately customised per repo -- a diff there is not a finding."""
    findings = fleet_status.check_templates(
        [SEED_ONCE_ENTRY], {".github/workflows/ci.yml": "totally different\n"}, stamp_status=None
    )
    assert findings == []


def test_missing_managed_template_is_flagged():
    findings = fleet_status.check_templates([MANAGED_ENTRY], {}, stamp_status=None)
    assert severities(findings, "templates") == ["warn"]
    assert "not installed" in messages(findings, "templates")


def test_declared_template_missing_from_this_repo_is_broken():
    """A manifest entry whose source file doesn't exist under templates/ is a defect in
    THIS repo -- onboard.py/sync.py will fail on it too -- not something to skip past."""
    entry = fleet_status.TemplateEntry(
        source="does-not-exist.md", dest="docs/DOES_NOT_EXIST.md", policy="managed"
    )
    findings = fleet_status.check_templates([entry], {"docs/DOES_NOT_EXIST.md": "anything"}, None)
    assert severities(findings, "templates") == ["broken"]
    assert "does-not-exist.md" in messages(findings, "templates")
    assert "declared in manifest.toml but missing" in messages(findings, "templates")


def test_diff_with_current_stamp_is_info_not_warn():
    """A current stamp plus a diff is a deliberate edit -- surfaced, not flagged as a mistake."""
    findings = fleet_status.check_templates(
        [MANAGED_ENTRY], {"scripts/changeset.py": "# customised\n"}, stamp_status="current"
    )
    assert severities(findings, "templates") == ["info"]
    assert "deliberate local edit" in messages(findings, "templates")


def test_diff_with_stale_stamp_stays_a_warning():
    findings = fleet_status.check_templates(
        [MANAGED_ENTRY], {"scripts/changeset.py": "# old copy\n"}, stamp_status="stale"
    )
    assert severities(findings, "templates") == ["warn"]


# ------------------------------------------------------------------ stamp


def test_stale_stamp_reports_how_many_versions_behind():
    findings = fleet_status.check_templates_version("v1.2.0", ALL_TAGS)
    assert severities(findings, "stamp") == ["warn"]
    assert "3 versions behind (v1.2.0 -> v1.5.0)" in messages(findings, "stamp")
    assert "sync.py" in messages(findings, "stamp")


def test_current_stamp_is_clean():
    assert fleet_status.check_templates_version("v1.5.0", ALL_TAGS) == []


def test_missing_stamp_is_reported_as_its_own_state():
    """Not 'up to date', not 'stale' -- a repo onboarded before the stamp existed."""
    findings = fleet_status.check_templates_version(None, ALL_TAGS)
    assert severities(findings, "stamp") == ["info"]
    assert "no templates_version stamp" in messages(findings, "stamp")


def test_unrecognised_stamp_is_flagged():
    """`v1` is the moving alias (see .github/RELEASING.md) -- not something onboard.py
    ever writes as a stamp, and sync.py's usable_stamp() deliberately rejects it too
    (a moving ref could resolve to a different commit than what was actually synced).
    This is the case the check exists for."""
    findings = fleet_status.check_templates_version("v1", ALL_TAGS)
    assert severities(findings, "stamp") == ["warn"]
    assert "not a recognised release tag" in messages(findings, "stamp")


def test_sha_stamp_produces_no_warning():
    """onboard.py's current_templates_version() falls back to a short commit SHA
    whenever HEAD isn't tagged -- true for every repo synced during development off
    an unmerged branch. A SHA has no position in the tag sequence, so there is
    nothing to report; it must NOT be treated as an unrecognised/invalid stamp."""
    findings = fleet_status.check_templates_version("e39bbda", ALL_TAGS)
    assert findings == []


def test_well_formed_but_untagged_point_release_is_also_silent_not_warned():
    """A vX.Y.Z-shaped stamp that isn't among this repo's local tags (e.g. an
    incomplete shallow fetch) is still a usable ref per sync.py's usable_stamp() --
    fleet_status.py must trust that same definition rather than re-deciding
    "unrecognised" on its own, which is exactly the second-notion-of-validity bug
    this check was rewritten to avoid."""
    findings = fleet_status.check_templates_version("v9.9.9", ALL_TAGS)
    assert findings == []


def test_stamp_status_helper():
    assert fleet_status._stamp_status("v1.5.0", ALL_TAGS) == "current"
    assert fleet_status._stamp_status("v1.2.0", ALL_TAGS) == "stale"
    assert fleet_status._stamp_status(None, ALL_TAGS) is None
    assert fleet_status._stamp_status("v9.9.9", ALL_TAGS) is None


# --------------------------------------------------------------- security

SECURITY_TEMPLATE = (REPO_ROOT / "templates" / "SECURITY.md").read_text()
UNRELATED_SECURITY_MD = "# Security Policy\n\nEmail security@example.com.\n"


def test_marker_matches_the_shipped_policy():
    """SECURITY_PVR_MARKER anchors on the literal button label templates/SECURITY.md
    tells a researcher to click. If that file gets reworded and stops containing the
    marker verbatim, check_security_reporting() returns [] for every repo -- silently.
    Nothing else would go red. This is the tripwire: reword the policy without updating
    SECURITY_PVR_MARKER to match, and this test catches it instead of the check quietly
    turning itself off."""
    assert fleet_status.SECURITY_PVR_MARKER in SECURITY_TEMPLATE


def test_public_repo_with_disabled_reporting_is_flagged():
    """The exact failure mode this check exists for: the policy promises a button
    the repo doesn't have."""
    findings = fleet_status.check_security_reporting(SECURITY_TEMPLATE, "disabled")
    assert severities(findings, "security") == ["warn"]
    assert messages(findings, "security") == (
        "SECURITY.md documents private vulnerability reporting but it is "
        "disabled on this repo; enable it in Settings or re-run onboard.py"
    )


def test_public_repo_with_enabled_reporting_is_clean():
    assert fleet_status.check_security_reporting(SECURITY_TEMPLATE, "enabled") == []


def test_private_repo_is_never_flagged_regardless_of_policy_text():
    """Private repos can't have the feature at all -- not-applicable, not a finding,
    even though the text and the "disabled"-shaped API state would otherwise match."""
    assert fleet_status.check_security_reporting(SECURITY_TEMPLATE, "not-applicable") == []


def test_repo_with_no_security_md_is_never_flagged():
    assert fleet_status.check_security_reporting(None, "disabled") == []


def test_security_md_that_doesnt_promise_the_button_is_never_flagged():
    """A different SECURITY.md (e.g. an email-based policy) makes no promise about
    the Security tab, so a disabled setting there isn't a broken promise."""
    assert fleet_status.check_security_reporting(UNRELATED_SECURITY_MD, "disabled") == []


def test_ambiguous_api_response_is_never_read_as_disabled():
    """A 404 covers private/no-access/unavailable alike -- guessing "disabled" from
    it is exactly the failure mode this whole check exists to avoid."""
    findings = fleet_status.check_security_reporting(SECURITY_TEMPLATE, "unknown")
    assert severities(findings, "security") == ["info"]
    assert "warn" not in [f.severity for f in findings]
    assert "could not determine" in messages(findings, "security")


# ------------------------------------------------------------- roll-up logic


def test_a_healthy_repo_has_no_actionable_findings():
    """A genuinely healthy repo: workflow_call-able CI, composite-action stub,
    the three required contexts, and an unmodified changeset.py.

    No manifest/stamp is passed -- that's the "onboarded before provenance
    tracking existed" state, which is `info`, not actionable.
    """
    ci = CI_LINT_OFF.replace("  pull_request:\n", "  pull_request:\n  workflow_call:\n")
    findings = fleet_status.evaluate(ci, GOOD_STUB, HEALTHY_CONTEXTS)
    actionable = [f for f in findings if f.severity != "info"]
    assert actionable == [], [f"{f.check}: {f.message}" for f in actionable]


def test_evaluate_wires_templates_and_stamp_checks_together():
    """A repo with a current stamp and a hand-edited managed template: the
    `templates` finding reads as info (a choice), and there's no `stamp`
    finding at all (nothing to flag when the stamp is current)."""
    ci = CI_LINT_OFF.replace("  pull_request:\n", "  pull_request:\n  workflow_call:\n")
    findings = fleet_status.evaluate(
        ci,
        GOOD_STUB,
        HEALTHY_CONTEXTS,
        manifest=[MANAGED_ENTRY],
        dest_texts={"scripts/changeset.py": "# customised\n"},
        stamp="v1.5.0",
        tags=ALL_TAGS,
    )
    assert severities(findings, "templates") == ["info"]
    assert severities(findings, "stamp") == []


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
