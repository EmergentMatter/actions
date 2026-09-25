"""Tests for the publish-target / licence-tier contract in
`.github/workflows/version.yml` and `.github/workflows/build-release.yml`.

Text-level, not a real YAML parse or a live workflow run -- the same level
`test_version_workflow_contract.py` and `test_build_release_workflow_contract.py`
use, for the same reason (see those files' own module docstrings): no
workflow_call in this repo is ever actually driven by a runner in this
test suite, so this is the level available to catch a regression in the
input names, defaults, and guard steps CONTRACT.md documents.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "version.yml"
BUILD_RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-release.yml"
STUB_VERSION = REPO_ROOT / "templates" / "stub-version.yml"
STUB_BUILD_RELEASE = REPO_ROOT / "templates" / "stub-build-release.yml"
CONTRACT = REPO_ROOT / "CONTRACT.md"

WORKFLOWS = [VERSION_WORKFLOW, BUILD_RELEASE_WORKFLOW]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _string_input_default(text: str, name: str) -> str | None:
    """Same text-level matcher test_version_workflow_contract.py uses:
    `      <name>:` followed, within the next few lines, by a quoted or
    bare `default:` value. Bare (unquoted) is needed here for
    `publish-target: pypi` and similar string defaults that aren't
    numeric-looking enough to need quotes in YAML.
    """
    pattern = rf'^ {{6}}{re.escape(name)}:\n(?:.*\n)*? {{8}}default: "?([^"\n]*)"?'
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1) if match else None


# --------------------------------------------------------------- both workflows


def test_both_workflows_declare_the_new_inputs_with_backward_compatible_defaults():
    expected_defaults = {
        "publish-target": "pypi",  # preserves exactly what publish: true did before
        "aws-role-arn": "",
        "aws-region": "us-east-2",
        "static-index-bucket": "",
        "static-index-distribution-id": "",
        "codeartifact-domain": "em",
        "codeartifact-domain-owner": "967228661072",
        "codeartifact-repository": "em-platform",
    }
    for path in WORKFLOWS:
        text = _text(path)
        for name, default in expected_defaults.items():
            assert _string_input_default(text, name) == default, (
                f"{path.name}: {name} must default to {default!r}"
            )


def test_both_workflows_gate_pypi_publish_on_the_target():
    for path in WORKFLOWS:
        text = _text(path)
        idx = text.index("- name: Publish to PyPI")
        following = text[idx : idx + 200]
        assert "if: inputs.publish-target == 'pypi'" in following


def test_both_workflows_gate_aws_credentials_on_the_aws_targets():
    for path in WORKFLOWS:
        text = _text(path)
        idx = text.index("- name: Configure AWS credentials")
        following = text[idx : idx + 400]
        for target in ("static-index", "codeartifact", "both"):
            assert f"inputs.publish-target == '{target}'" in following


def test_both_workflows_run_the_tier_check_before_any_upload():
    for path in WORKFLOWS:
        text = _text(path)
        tier_idx = text.index("check_publish_tier.py")
        aws_idx = text.index("- name: Configure AWS credentials")
        pypi_idx = text.index("- name: Publish to PyPI")
        assert tier_idx < aws_idx
        assert tier_idx < pypi_idx


def test_both_workflows_validate_required_inputs_per_target():
    for path in WORKFLOWS:
        text = _text(path)
        assert "aws-role-arn is required for publish-target" in text
        assert "static-index-bucket is required for publish-target" in text


def test_both_workflows_mask_the_codeartifact_token():
    for path in WORKFLOWS:
        text = _text(path)
        idx = text.index("get-authorization-token")
        following = text[idx : idx + 400]
        assert "::add-mask::" in following


def test_both_workflows_publish_jobs_declare_no_wider_permission_than_needed():
    """version.yml's publish job keeps declaring NO permissions: block at
    all (ADR 0003); build-release.yml's keeps declaring exactly
    id-token: write plus the new contents: read for its two new checkout
    steps -- neither gets WIDER than what a publish-enabled consumer's
    stub already grants."""
    version_text = _text(VERSION_WORKFLOW)
    publish_job = version_text[version_text.index("\n  publish:") :]
    # A real job-level `permissions:` key, not the word inside a comment
    # line explaining why it's absent.
    assert not re.search(r"^ {4}permissions:", publish_job, re.MULTILINE)

    build_release_text = _text(BUILD_RELEASE_WORKFLOW)
    publish_job = build_release_text[build_release_text.index("\n  publish:") :]
    perms_block = publish_job[publish_job.index("permissions:") : publish_job.index("steps:")]
    assert "contents: read" in perms_block
    assert "id-token: write" in perms_block


# --------------------------------------------------------------------- version.yml


def test_version_job_exposes_tag_and_actions_ref_for_the_publish_job():
    text = _text(VERSION_WORKFLOW)
    outputs_idx = text.index("outputs:")
    following = text[outputs_idx : outputs_idx + 400]
    assert "tag: ${{ steps.tag.outputs.tag }}" in following
    assert "actions-ref: ${{ steps.shared.outputs.ref }}" in following


def test_publish_job_checks_out_the_consumer_at_the_release_tag():
    text = _text(VERSION_WORKFLOW)
    publish_job = text[text.index("\n  publish:") :]
    assert "ref: ${{ needs.version.outputs.tag }}" in publish_job


# ---------------------------------------------------------------- build-release.yml


def test_build_release_gained_an_actions_ref_input():
    """New as of this change -- see the file header comment for why: the
    publish job now needs check_publish_tier.py / publish_static_index.py
    from this repo, which build-release.yml never had to load before."""
    text = _text(BUILD_RELEASE_WORKFLOW)
    assert _string_input_default(text, "actions-ref") == "v1"


def test_build_release_job_exposes_actions_ref_for_the_publish_job():
    text = _text(BUILD_RELEASE_WORKFLOW)
    release_job = text[text.index("\n  release:") : text.index("\n  publish:")]
    assert "actions-ref: ${{ steps.shared.outputs.ref }}" in release_job


def test_stub_build_release_sets_actions_ref_matching_its_pin():
    text = _text(STUB_BUILD_RELEASE)
    assert "uses: EmergentMatter/actions/.github/workflows/build-release.yml@v1" in text
    assert "actions-ref: v1" in text


# -------------------------------------------------------------- consumer stubs


def test_stub_version_documents_publish_target_as_a_commented_example():
    text = _text(STUB_VERSION)
    assert "# publish-target:" in text
    assert "# aws-role-arn:" in text
    assert "# static-index-bucket:" in text


def test_stub_build_release_documents_publish_target_as_a_commented_example():
    text = _text(STUB_BUILD_RELEASE)
    assert "# publish-target:" in text
    assert "# aws-role-arn:" in text


# -------------------------------------------------------------------- CONTRACT.md


def test_contract_documents_publish_target_values():
    text = _text(CONTRACT)
    assert "publish-target" in text
    for value in ("pypi", "static-index", "codeartifact", "both"):
        assert value in text


def test_contract_documents_the_tier_guard():
    text = _text(CONTRACT)
    assert "tier" in text.lower()
    assert "closed" in text
