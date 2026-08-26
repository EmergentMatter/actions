"""Tests for the sibling / sibling2 private-checkout contract in
`.github/workflows/version.yml`.

These read the real reusable workflow and its consumer stub as text and
assert on their shape, the same way test_onboard.py's
test_real_manifest_* tests check the real manifest rather than a fixture
copy. No workflow_call in this repo is ever actually driven by a runner
in this test suite -- that only happens live, against
em-release-control-test (see CLAUDE.md) -- so this is the level available
to catch a regression in the input names, defaults, and the guard steps
CONTRACT.md documents.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "version.yml"
STUB_VERSION = REPO_ROOT / "templates" / "stub-version.yml"
CONTRACT = REPO_ROOT / "CONTRACT.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _string_input_default(text: str, name: str) -> str | None:
    """Pull the `default:` value directly under an `inputs.<name>:` block.

    Text-level, not a real YAML parse -- this repo's scripts are stdlib
    only and every other workflow-shape test in this suite (see
    test_onboard.py's parse_ci) works the same way. Matches
    `      <name>:` followed, within the next few lines, by
    `        default: "..."`.
    """
    pattern = rf'^ {{6}}{re.escape(name)}:\n(?:.*\n)*? {{8}}default: "([^"]*)"'
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1) if match else None


# --------------------------------------------------------- workflow inputs


def test_sibling2_inputs_exist_with_empty_string_defaults():
    """Unset (the default), sibling2 must behave exactly like an absent
    sibling-repo -- every step gated on it has to no-op, so the default
    has to be the same empty string sibling-repo uses (CONTRACT.md's
    'omit ... and the sibling steps skip entirely')."""
    text = _text(VERSION_WORKFLOW)
    for name in ("sibling2-repo", "sibling2-ref", "sibling2-path"):
        assert _string_input_default(text, name) == "", (
            f"{name} must default to the empty string, like the sibling-* inputs it mirrors"
        )


def test_sibling_inputs_are_unchanged_by_the_sibling2_addition():
    """Backward compatibility: an existing single-sibling consumer must
    keep working with zero changes -- the four original inputs still
    exist, unchanged in name or default."""
    text = _text(VERSION_WORKFLOW)
    for name in ("sibling-repo", "sibling-ref", "sibling-path"):
        assert _string_input_default(text, name) == ""


def test_sibling2_token_secret_is_declared_optional():
    text = _text(VERSION_WORKFLOW)
    secrets_block = text.split("\n    secrets:\n", 1)[1]
    # secrets: block ends at the next top-level (2-space) key.
    secrets_block = secrets_block.split("\npermissions:", 1)[0]
    assert "sibling2-token:" in secrets_block
    assert "sibling-token:" in secrets_block
    # Both must be optional -- neither shared workflow can require a
    # secret from a consumer that has no private siblings at all.
    for secret_name in ("sibling-token", "sibling2-token"):
        block = secrets_block.split(f"{secret_name}:", 1)[1]
        block = re.split(r"\n {6}\S", block, maxsplit=1)[0]  # up to the next secret key
        assert "required: false" in block, f"{secret_name} must be required: false"


def test_sibling2_requires_sibling_guard_is_present():
    """A nested sibling with no direct sibling is refused, not silently
    accepted -- CONTRACT.md's 'refused as a misconfigured stub'."""
    text = _text(VERSION_WORKFLOW)
    assert "sibling2-repo is set but sibling-repo is not" in text


def test_sibling_path_collision_guard_is_present():
    """sibling-path and sibling2-path land in the same parent directory
    (dirname $GITHUB_WORKSPACE); a collision must be refused before either
    checkout runs, not silently overwrite one with the other."""
    text = _text(VERSION_WORKFLOW)
    assert "is the same as sibling-path" in text


def test_sibling2_checkout_steps_are_gated_on_sibling2_repo():
    """Every sibling2 step must no-op when sibling2-repo is unset, exactly
    like the sibling block -- an existing single-sibling (or no-sibling)
    consumer must see zero behavior change."""
    text = _text(VERSION_WORKFLOW)
    sibling2_step_names = (
        "Validate the sibling2 inputs",
        "Check out the second private sibling",
        "Move the second sibling alongside the consumer checkout",
    )
    for step_name in sibling2_step_names:
        idx = text.index(f"- name: {step_name}")
        following = text[idx : idx + 400]
        assert "if: inputs.sibling2-repo != ''" in following, (
            f"step {step_name!r} must be gated on inputs.sibling2-repo"
        )


def test_sibling2_stages_into_its_own_directory():
    """.em-sibling2/ must be distinct from .em-sibling/ so the two
    in-flight staging checkouts can never collide, even transiently."""
    text = _text(VERSION_WORKFLOW)
    assert ".em-sibling2/" in text
    assert ".em-sibling2" in text  # the rmdir cleanup target


# ------------------------------------------------------------- consumer stub


def test_stub_documents_the_nested_sibling_block():
    text = _text(STUB_VERSION)
    assert "sibling2-repo:" in text
    assert "sibling2-ref:" in text
    assert "sibling2-path:" in text
    assert "sibling2-token" in text
    # The original single-sibling block must still be present, unmodified
    # in shape -- a repo with just one private dependency deletes only the
    # new block.
    assert "sibling-repo: EmergentMatter/emergent-matter-materials" in text


# ---------------------------------------------------------------- CONTRACT.md


def test_contract_documents_sibling2_inputs():
    text = _text(CONTRACT)
    assert "sibling2-repo" in text
    assert "sibling2-ref" in text
    assert "sibling2-path" in text
    assert "sibling2-token" in text
    assert "require `sibling-repo` to also be set" in text
