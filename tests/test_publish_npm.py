"""Tests for scripts/publish_npm.py and its wiring in both release workflows.

No registry and no network: `npm` is a stub on PATH that records its argument list, the working
directory, the `.npmrc` it was handed and the token it saw, then exits with a chosen code.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "publish_npm.py"
spec = importlib.util.spec_from_file_location("publish_npm", SCRIPT)
pn = importlib.util.module_from_spec(spec)
sys.modules["publish_npm"] = pn
spec.loader.exec_module(pn)

ENDPOINT = "https://em-111.d.codeartifact.us-east-2.amazonaws.com/npm/em-platform/"
TOKEN = "tok-secret-value"

STUB = """#!/bin/sh
{
  echo "args: $*"
  echo "cwd: $(pwd)"
  echo "token: $CODEARTIFACT_AUTH_TOKEN"
  rc=""
  while [ $# -gt 0 ]; do [ "$1" = "--userconfig" ] && rc="$2"; shift; done
  echo "npmrc-mode: $(stat -f %Lp "$rc" 2>/dev/null || stat -c %a "$rc")"
  echo "---npmrc"; cat "$rc"
} > "$STUB_LOG"
exit "${STUB_EXIT:-0}"
"""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    npm = bindir / "npm"
    npm.write_text(STUB)
    npm.chmod(npm.stat().st_mode | stat.S_IXUSR)
    work = tmp_path / "work"
    work.mkdir()
    temp = tmp_path / "temp"
    temp.mkdir()
    log = tmp_path / "log.txt"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_LOG", str(log))
    monkeypatch.setenv(pn.TOKEN_VAR, TOKEN)
    monkeypatch.chdir(work)
    return {"work": work, "temp": temp, "log": log}


def package(work: Path, **over: object) -> Path:
    manifest: dict[str, object] = {"name": "@emergentmatter/contract", "version": "0.0.0", **over}
    pkg = work / "pkg"
    pkg.mkdir(exist_ok=True)
    (pkg / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pkg


def run(env: dict[str, Path], pkg: Path, version: str = "1.4.0", endpoint: str = ENDPOINT) -> int:
    return pn.main(
        ["--package-dir", str(pkg), "--version", version, "--endpoint", endpoint]
        + ["--temp-dir", str(env["temp"])]
    )


def test_publishes_with_a_scope_only_npmrc_and_the_release_version(env):
    pkg = package(env["work"])
    assert run(env, pkg) == 0
    log = env["log"].read_text()
    assert json.loads((pkg / "package.json").read_text())["version"] == "1.4.0"
    assert f"cwd: {pkg.resolve()}" in log
    assert "args: publish --userconfig" in log and f"--registry {ENDPOINT}" in log
    assert "npmrc-mode: 600" in log
    npmrc = log.split("---npmrc\n")[1]
    assert npmrc == (
        f"@emergentmatter:registry={ENDPOINT}\n"
        "//em-111.d.codeartifact.us-east-2.amazonaws.com/npm/em-platform/"
        ":_authToken=${CODEARTIFACT_AUTH_TOKEN}\n"
    )
    assert TOKEN not in npmrc and "token: " + TOKEN in log  # npm sees it only through the env


def test_the_token_never_reaches_a_file_or_the_output(env, capsys):
    pkg = package(env["work"])
    run(env, pkg)
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
    assert list(env["temp"].iterdir()) == []  # the temp npmrc directory is gone
    assert not (pkg / ".npmrc").exists() and not (env["work"] / ".npmrc").exists()


def test_a_failed_npm_publish_fails_the_run_and_still_cleans_up(env, monkeypatch, capsys):
    monkeypatch.setenv("STUB_EXIT", "1")
    assert run(env, package(env["work"])) == 1
    assert "npm publish failed" in capsys.readouterr().err
    assert list(env["temp"].iterdir()) == []


@pytest.mark.parametrize(
    ("over", "version", "why"),
    [
        ({"name": "contract"}, "1.0.0", "not scoped"),
        ({"private": True}, "1.0.0", "private"),
        ({"publishConfig": {"registry": "https://registry.npmjs.org/"}}, "1.0.0", "publishConfig"),
        ({"scripts": {"postinstall": "node x.js"}}, "1.0.0", "install-time"),
        ({"scripts": {"prepare": "x"}}, "1.0.0", "install-time"),
        ({}, "1.0.0-rc.1", "MAJOR.MINOR.PATCH"),
        ({}, "v1.0.0", "MAJOR.MINOR.PATCH"),
        ({}, "01.0.0", "MAJOR.MINOR.PATCH"),
    ],
)
def test_refuses_a_package_that_must_not_be_published(env, capsys, over, version, why):
    assert run(env, package(env["work"], **over), version) == 1
    assert why in capsys.readouterr().err
    assert not env["log"].exists()  # npm was never run


def test_a_prepack_script_is_allowed(env):
    assert run(env, package(env["work"], scripts={"prepack": "node sync.mjs"})) == 0


@pytest.mark.parametrize("endpoint", ["http://x.example/npm/r/", "ftp://x/", "https://x/?a=1", "x"])
def test_refuses_an_endpoint_that_is_not_plain_https(env, endpoint):
    assert run(env, package(env["work"]), endpoint=endpoint) == 1
    assert not env["log"].exists()


def test_refuses_without_the_token(env, monkeypatch):
    monkeypatch.delenv(pn.TOKEN_VAR)
    assert run(env, package(env["work"])) == 1
    assert not env["log"].exists()


def test_refuses_a_package_directory_outside_the_workspace(env, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    package(outside)
    assert run(env, outside / "pkg") == 1
    assert not env["log"].exists()


def test_a_missing_package_json_is_refused(env):
    (env["work"] / "empty").mkdir()
    assert run(env, env["work"] / "empty") == 1


# ---------------------------------------------------------------- the workflows

WORKFLOWS = [ROOT / ".github/workflows/version.yml", ROOT / ".github/workflows/build-release.yml"]


def test_both_workflows_take_npm_packages_and_default_it_empty():
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        assert "      npm-packages:\n" in text, path.name
        block = text.split("      npm-packages:\n")[1].split("\n\n")[0]
        assert "type: string" in block and 'default: ""' in block, path.name


def test_both_workflows_publish_npm_only_for_codeartifact_and_refuse_other_targets():
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        step = text.split("- name: Publish npm packages to CodeArtifact")[1]
        assert "if: inputs.publish-target == 'codeartifact' && inputs.npm-packages != ''" in step
        assert "--format npm" in step and "publish_npm.py" in step
        assert "${{ inputs.npm-packages }}" not in step.split("run: |")[1], "input in shell"
        validate = text.split("- name: Validate inputs for this publish target")[1][:900]
        assert "npm-packages needs publish-target 'codeartifact'" in validate


def test_the_npm_step_comes_after_the_wheels_in_both_workflows():
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        assert text.index("uv publish --publish-url") < text.index(
            "- name: Publish npm packages to CodeArtifact"
        )
