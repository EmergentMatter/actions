"""Tests for a repo with a package.json and no pyproject.toml (CONTRACT.md, "A repo with only a
package.json"): the version scripts, the tier guard, and the workflows' text-level contract."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
VERSION_WF = (ROOT / ".github/workflows/version.yml").read_text(encoding="utf-8")
BUILD_RELEASE_WF = (ROOT / ".github/workflows/build-release.yml").read_text(encoding="utf-8")

spec = importlib.util.spec_from_file_location(
    "check_publish_tier", SCRIPTS / "check_publish_tier.py"
)
tier = importlib.util.module_from_spec(spec)
sys.modules["check_publish_tier"] = tier
spec.loader.exec_module(tier)

PACKAGE_JSON = """{
  "name": "@scope/pkg",
  "version": "0.4.1",
  "files": [
    "dist"
  ],
  "dependencies": {
    "version": "1.0.0"
  }
}
"""


def run(script: str, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args], cwd=cwd, capture_output=True, text=True
    )


def note(tmp_path: Path, name: str) -> None:
    (tmp_path / "changelog.d").mkdir(exist_ok=True)
    (tmp_path / "changelog.d" / name).write_text("changed\n")


# ---------------------------------------------------------------- compute_bump


def test_compute_bump_reads_package_json_when_there_is_no_pyproject(tmp_path):
    (tmp_path / "package.json").write_text(PACKAGE_JSON)
    note(tmp_path, "+aaaa1111.minor.md")
    result = run("compute_bump.py", tmp_path, "--format", "json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "level": "minor",
        "current": "0.4.1",
        "next": "0.5.0",
        "count": 1,
    }


def test_compute_bump_prefers_pyproject_when_both_exist(tmp_path):
    (tmp_path / "package.json").write_text(PACKAGE_JSON)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "d"\nversion = "2.0.0"\n')
    note(tmp_path, "+aaaa1111.patch.md")
    result = run("compute_bump.py", tmp_path, "--format", "json")
    assert json.loads(result.stdout)["current"] == "2.0.0"


def test_compute_bump_fails_when_package_json_has_no_version(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "@scope/pkg"}')
    result = run("compute_bump.py", tmp_path, "--format", "json")
    assert result.returncode == 1
    assert "version not found" in result.stderr


def test_compute_bump_fails_when_package_json_is_not_json(tmp_path):
    (tmp_path / "package.json").write_text("{")
    result = run("compute_bump.py", tmp_path, "--format", "json")
    assert result.returncode == 1
    assert "not valid JSON" in result.stderr


# ----------------------------------------------------------- set_package_version


def test_set_package_version_changes_only_the_top_level_version_line(tmp_path):
    path = tmp_path / "package.json"
    path.write_text(PACKAGE_JSON)
    result = run("set_package_version.py", tmp_path, "--version", "0.5.0")
    assert result.returncode == 0, result.stderr
    assert path.read_text() == PACKAGE_JSON.replace('"version": "0.4.1"', '"version": "0.5.0"')


def test_set_package_version_check_writes_nothing(tmp_path):
    path = tmp_path / "package.json"
    path.write_text(PACKAGE_JSON)
    assert run("set_package_version.py", tmp_path, "--version", "0.5.0", "--check").returncode == 1
    assert run("set_package_version.py", tmp_path, "--version", "0.4.1", "--check").returncode == 0
    assert path.read_text() == PACKAGE_JSON


@pytest.mark.parametrize(
    "text", ['{"name": "x"}\n', '{\n  "version": "1.0.0",\n  "version": "2.0.0"\n}\n']
)
def test_set_package_version_refuses_zero_or_several_version_lines(tmp_path, text):
    (tmp_path / "package.json").write_text(text)
    result = run("set_package_version.py", tmp_path, "--version", "1.1.0")
    assert result.returncode == 1
    assert "exactly one" in result.stderr


def test_set_package_version_refuses_a_version_that_is_not_plain_semver(tmp_path):
    (tmp_path / "package.json").write_text(PACKAGE_JSON)
    assert run("set_package_version.py", tmp_path, "--version", "1.0").returncode == 1
    assert run("set_package_version.py", tmp_path, "--version", "1.0.0-rc.1").returncode == 1


# ------------------------------------------------------------ check_publish_tier


def write_tier(tmp_path: Path, value: object | None) -> Path:
    path = tmp_path / "package.json"
    data: dict[str, object] = {"name": "@scope/pkg", "version": "0.0.0"}
    if value is not None:
        data["em-release"] = {"tier": value}
    path.write_text(json.dumps(data))
    return path


def test_package_json_tier_is_read(tmp_path):
    assert tier.resolve_tier_from_package_json(write_tier(tmp_path, "closed")) == "closed"
    assert tier.resolve_tier_from_package_json(write_tier(tmp_path, None)) is None


def test_package_json_tier_rejects_an_unknown_value(tmp_path):
    with pytest.raises(tier.PublishTierError, match="premium"):
        tier.resolve_tier_from_package_json(write_tier(tmp_path, "premium"))


def test_main_applies_the_closed_table_to_an_npm_only_repo(tmp_path):
    pkg = write_tier(tmp_path, "closed")
    missing = tmp_path / "pyproject.toml"
    base = ["--pyproject", str(missing), "--package-json", str(pkg), "--target"]
    assert tier.main([*base, "codeartifact"]) == 0
    for target in ("pypi", "static-index", "both"):
        assert tier.main([*base, target]) == 1


def test_main_refuses_an_npm_only_repo_with_no_tier_on_an_aws_target(tmp_path):
    pkg = write_tier(tmp_path, None)
    missing = tmp_path / "pyproject.toml"
    assert (
        tier.main(
            ["--pyproject", str(missing), "--package-json", str(pkg), "--target", "codeartifact"]
        )
        == 1
    )


def test_main_prefers_pyproject_when_both_exist(tmp_path):
    pkg = write_tier(tmp_path, "open")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.em-release]\ntier = "closed"\n')
    args = ["--pyproject", str(pyproject), "--package-json", str(pkg), "--target", "pypi"]
    assert tier.main(args) == 1


# --------------------------------------------------------------------- workflows


@pytest.mark.parametrize("text", [VERSION_WF, BUILD_RELEASE_WF], ids=["version", "build-release"])
def test_publish_job_detects_npm_only_and_skips_the_wheel_steps(text):
    publish = text[text.index("\n  publish:") :]
    assert "id: kind" in publish
    assert "[ ! -f pyproject.toml ] && [ -f package.json ]" in publish
    for step in ("Download build artifact", "Publish to CodeArtifact"):
        block = publish[publish.index(f"- name: {step}") :][:600]
        assert "inputs.has-wheel" in block.split("uses:")[0].split("run:")[0], step
    read_name = publish[publish.index("- name: Read the package name") :][:500]
    assert "steps.kind.outputs.npm-only != 'true'" in read_name
    assert "needs publish-target 'codeartifact' and a non-empty npm-packages" in publish


@pytest.mark.parametrize("text", [VERSION_WF, BUILD_RELEASE_WF], ids=["version", "build-release"])
def test_publishing_without_a_wheel_is_allowed_only_with_npm_packages(text):
    idx = text.index("- name: Refuse publish without a wheel to publish")
    assert (
        "inputs.publish && !inputs.has-wheel && inputs.npm-packages == ''" in text[idx : idx + 300]
    )


def test_version_job_bumps_package_json_instead_of_running_uv():
    npm = VERSION_WF[VERSION_WF.index("- name: Bump package.json version (npm-only repo)") :][:700]
    assert "steps.kind.outputs.npm-only == 'true'" in npm
    assert "set_package_version.py" in npm
    for step in (
        "Bump [project] version and refresh the lockfile",
        "Sync declared version strings",
    ):
        block = VERSION_WF[VERSION_WF.index(f"- name: {step}") :][:600]
        assert "steps.kind.outputs.npm-only != 'true'" in block, step


def test_detection_refuses_has_wheel_true_and_all_packages():
    block = VERSION_WF[VERSION_WF.index("- name: Detect an npm-only repo") :][:1500]
    assert "Set has-wheel: false" in block
    assert "all-packages is for a uv workspace" in block


def test_released_version_is_read_from_package_json_for_npm_only():
    block = VERSION_WF[VERSION_WF.index("- name: Read released version") :][:800]
    assert re.search(r"json\.load\(open\('package\.json'\)\)\['version'\]", block)
