"""Tests for scripts/check_publish_tier.py.

The publish job's tier guard: a closed-tier repo must never reach a public
index, and a no-tier repo must never reach an AWS-backed target. This is
the Done-when checklist's "the publish job fails a closed repo that asks
for static-index", exercised here at the level that actually matters --
the pure validate() function -- rather than through a live workflow run.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_publish_tier.py"
spec = importlib.util.spec_from_file_location("check_publish_tier", SCRIPT)
check_publish_tier = importlib.util.module_from_spec(spec)
sys.modules["check_publish_tier"] = check_publish_tier
spec.loader.exec_module(check_publish_tier)


# ------------------------------------------------------------------- validate


@pytest.mark.parametrize("target", sorted(check_publish_tier.VALID_TARGETS))
def test_open_tier_allows_every_target(target):
    check_publish_tier.validate(target, "open")  # must not raise


def test_closed_tier_allows_only_codeartifact():
    check_publish_tier.validate("codeartifact", "closed")  # must not raise


@pytest.mark.parametrize("target", ["pypi", "static-index", "both"])
def test_closed_tier_refuses_every_public_target(target):
    with pytest.raises(check_publish_tier.PublishTierError, match="closed"):
        check_publish_tier.validate(target, "closed")


def test_no_tier_still_allows_pypi():
    """Backward compatibility: publish: true with no publish-target set
    (default 'pypi') on a repo that predates licence tiers must keep
    working exactly as it did before this input existed."""
    check_publish_tier.validate("pypi", None)  # must not raise


@pytest.mark.parametrize("target", ["static-index", "codeartifact", "both"])
def test_no_tier_refuses_every_aws_target(target):
    with pytest.raises(check_publish_tier.PublishTierError, match="tier"):
        check_publish_tier.validate(target, None)


def test_unrecognized_target_is_refused_regardless_of_tier():
    with pytest.raises(check_publish_tier.PublishTierError, match="unrecognized"):
        check_publish_tier.validate("npm", "open")


# --------------------------------------------------------------- resolve_tier


def test_resolve_tier_reads_the_declared_value(tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text('[tool.em-release]\ntier = "closed"\n')
    assert check_publish_tier.resolve_tier(p) == "closed"


def test_resolve_tier_returns_none_when_undeclared(tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text('[project]\nname = "p"\n')
    assert check_publish_tier.resolve_tier(p) is None


def test_resolve_tier_rejects_an_unrecognized_value(tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text('[tool.em-release]\ntier = "premium"\n')
    with pytest.raises(check_publish_tier.PublishTierError, match="premium"):
        check_publish_tier.resolve_tier(p)


# ------------------------------------------------------------------------ CLI


def test_main_exits_zero_for_an_allowed_target(tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text('[tool.em-release]\ntier = "open"\n')
    assert check_publish_tier.main(["--target", "static-index", "--pyproject", str(p)]) == 0


def test_main_exits_one_for_a_disallowed_target(tmp_path, capsys):
    p = tmp_path / "pyproject.toml"
    p.write_text('[tool.em-release]\ntier = "closed"\n')
    assert check_publish_tier.main(["--target", "pypi", "--pyproject", str(p)]) == 1
    assert "::error::" in capsys.readouterr().err


def test_main_exits_one_when_pyproject_is_missing(tmp_path, capsys):
    missing = tmp_path / "nope.toml"
    assert check_publish_tier.main(["--target", "pypi", "--pyproject", str(missing)]) == 1
    assert "no such file" in capsys.readouterr().err
