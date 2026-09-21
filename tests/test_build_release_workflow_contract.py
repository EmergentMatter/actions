"""Tests for the `has-wheel` contract in `.github/workflows/build-release.yml`.

Text-level, not a real YAML parse or a live workflow run -- the same level
`test_version_workflow_contract.py` uses, for the same reason (see that
file's own module docstring).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-release.yml"
STUB_BUILD_RELEASE = REPO_ROOT / "templates" / "stub-build-release.yml"
CONTRACT = REPO_ROOT / "CONTRACT.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_has_wheel_input_defaults_to_true():
    """Backward compatibility: an existing consumer (a real package) must
    keep working with zero changes."""
    text = _text(BUILD_RELEASE_WORKFLOW)
    match = re.search(r"^ {6}has-wheel:\n(?:.*\n)*? {8}default: (\w+)", text, re.MULTILINE)
    assert match is not None, "has-wheel input not found"
    assert match.group(1) == "true"


def test_build_step_is_gated_on_has_wheel():
    text = _text(BUILD_RELEASE_WORKFLOW)
    idx = text.index("- name: Build wheel + sdist")
    following = text[idx : idx + 200]
    assert "if: inputs.has-wheel" in following


def test_both_release_steps_exist_and_are_gated_oppositely():
    text = _text(BUILD_RELEASE_WORKFLOW)
    with_idx = text.index("- name: Create the GitHub Release (with build artifacts)")
    with_block = text[with_idx : with_idx + 700]
    assert "if: inputs.has-wheel" in with_block
    assert "fail_on_unmatched_files: true" in with_block

    without_idx = text.index("- name: Create the GitHub Release (no build artifacts)")
    without_block = text[without_idx : without_idx + 500]
    assert "!inputs.has-wheel" in without_block
    # The no-wheel release must never declare a files: glob -- there is
    # nothing to match, ever, for this repository.
    assert "files: |" not in without_block


def test_publish_without_has_wheel_is_refused():
    """There is nothing to publish without a wheel -- refused before `uv
    build` ever runs, not discovered as an empty publish later."""
    text = _text(BUILD_RELEASE_WORKFLOW)
    assert "publish is true but has-wheel is false" in text
    idx = text.index("- name: Refuse publish without a wheel to publish")
    following = text[idx : idx + 200]
    assert "inputs.publish && !inputs.has-wheel" in following


def test_upload_artifact_step_requires_has_wheel_too():
    text = _text(BUILD_RELEASE_WORKFLOW)
    idx = text.index("- name: Upload build artifact for the publish job")
    following = text[idx : idx + 500]
    assert "inputs.publish && inputs.has-wheel" in following


def test_stub_documents_has_wheel():
    text = _text(STUB_BUILD_RELEASE)
    assert "has-wheel: false" in text


def test_contract_documents_has_wheel():
    text = _text(CONTRACT)
    assert "has-wheel" in text
    assert "package = false" in text
