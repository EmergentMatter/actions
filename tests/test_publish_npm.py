"""Tests for scripts/publish_npm.py and its wiring in both release workflows.

`prepare` runs the real `npm pack` (no network); `publish` runs against a stub `npm` on PATH that
hands `pack` to the real one and answers `view` and `publish` itself, recording the argument
list, the `.npmrc` it was given and the token it saw.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
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
REAL_NPM = shutil.which("npm")

STUB = """#!/bin/sh
cmd="$1"
if [ "$cmd" = pack ]; then exec "$REAL_NPM" "$@"; fi
{
  echo "cmd: $*"
  echo "token: $CODEARTIFACT_AUTH_TOKEN"
  rc=""
  for a in "$@"; do [ "$prev" = "--userconfig" ] && rc="$a"; prev="$a"; done
  echo "npmrc-mode: $(stat -c %a "$rc" 2>/dev/null || stat -f %Lp "$rc")"
  echo "---npmrc"; cat "$rc"
} >> "$STUB_LOG"
if [ "$cmd" = view ]; then
  case "$STUB_VIEW" in
    404) echo "npm error code E404" >&2; exit 1 ;;
    net) echo "npm error code ETIMEDOUT" >&2; exit 1 ;;
    *) echo "\\"$STUB_VIEW\\""; exit 0 ;;
  esac
fi
exit "${STUB_EXIT:-0}"
"""


@pytest.fixture(autouse=True)
def needs_npm() -> None:
    if REAL_NPM is None:
        if os.environ.get("CI"):
            pytest.fail("npm is required in CI for these tests")
        pytest.skip("npm is not installed")


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
    monkeypatch.setenv("REAL_NPM", str(REAL_NPM))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "log.txt"))
    monkeypatch.setenv("STUB_VIEW", "404")
    monkeypatch.setenv(pn.TOKEN_VAR, TOKEN)
    monkeypatch.chdir(work)
    return {"work": work, "temp": temp, "log": tmp_path / "log.txt", "out": work / "packed"}


def package(work: Path, content: str = "one", **over: object) -> Path:
    manifest: dict[str, object] = {
        "name": "@emergentmatter/contract",
        "version": "0.0.0",
        "files": ["data.json"],
        **over,
    }
    pkg = work / "pkg"
    pkg.mkdir(exist_ok=True)
    (pkg / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pkg / "data.json").write_text(json.dumps({"v": content}), encoding="utf-8")
    return pkg


def prepare(
    env: dict[str, Path], pkg: Path, version: str = "1.4.0", wheels: Path | None = None
) -> int:
    argv = [
        "prepare",
        "--package-dir",
        str(pkg),
        "--version",
        version,
        "--out-dir",
        str(env["out"]),
    ]
    return pn.main(argv + ["--wheel-dir", str(wheels or env["work"] / "dist")])


def tarball(env: dict[str, Path]) -> Path:
    (found,) = env["out"].glob("*.tgz")
    return found


def publish(env: dict[str, Path], tgz: Path, endpoint: str = ENDPOINT) -> int:
    return pn.main(
        ["publish", "--tarball", str(tgz), "--endpoint", endpoint, "--temp-dir", str(env["temp"])]
    )


def wheel(dest: Path, files: dict[str, str]) -> Path:
    dest.mkdir(exist_ok=True)
    path = dest / "x-1.4.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return path


# -------------------------------------------------------------------- prepare


def test_prepare_stamps_the_version_and_packs_the_tarball(env):
    pkg = package(env["work"])
    assert prepare(env, pkg) == 0
    assert json.loads((pkg / "package.json").read_text())["version"] == "1.4.0"
    files = pn.tarball_files(tarball(env))
    assert json.loads(files["package.json"])["version"] == "1.4.0"
    assert set(files) == {"package.json", "data.json"}


def test_prepare_runs_the_packages_prepack(env):
    rewrite = "require('fs').writeFileSync('data.json', JSON.stringify({v: 'packed'}))"
    pkg = package(env["work"], scripts={"prepack": f'node -e "{rewrite}"'})
    assert prepare(env, pkg) == 0
    assert json.loads(pn.tarball_files(tarball(env))["data.json"]) == {"v": "packed"}


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
def test_prepare_refuses_a_package_that_must_not_be_published(env, capsys, over, version, why):
    assert prepare(env, package(env["work"], **over), version) == 1
    assert why in capsys.readouterr().err
    assert not env["out"].exists() or not list(env["out"].glob("*.tgz"))


def test_prepare_refuses_a_package_directory_outside_the_workspace(env, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    pkg = package(outside)
    assert prepare(env, pkg) == 1


def test_prepare_refuses_a_missing_package_json(env):
    (env["work"] / "empty").mkdir()
    assert prepare(env, env["work"] / "empty") == 1


MIRROR = {"em-release": {"mirrors-wheel-dir": "pkg_chat"}}


def test_a_mirrored_package_must_match_the_wheel_byte_for_byte(env):
    pkg = package(env["work"], **MIRROR)
    good = wheel(
        env["work"] / "dist", {"pkg_chat/data.json": '{"v": "one"}', "pkg_chat/extra.json": "x"}
    )
    assert prepare(env, pkg, wheels=good.parent) == 0


def test_a_mirrored_package_whose_file_differs_from_the_wheel_is_refused(env, capsys):
    pkg = package(env["work"], **MIRROR)
    wheel(env["work"] / "dist", {"pkg_chat/data.json": '{"v": "other"}'})
    assert prepare(env, pkg) == 1
    assert "differs: data.json" in capsys.readouterr().err


def test_a_mirrored_package_with_a_file_the_wheel_lacks_is_refused(env, capsys):
    pkg = package(env["work"], **MIRROR)
    wheel(env["work"] / "dist", {"pkg_chat/other.json": "{}"})
    assert prepare(env, pkg) == 1
    assert "not in the wheel: data.json" in capsys.readouterr().err


def test_a_mirrored_package_with_no_wheel_or_no_such_directory_is_refused(env, capsys):
    pkg = package(env["work"], **MIRROR)
    assert prepare(env, pkg) == 1  # no dist/ at all
    wheel(env["work"] / "dist", {"elsewhere/data.json": "{}"})
    assert prepare(env, pkg) == 1
    assert "no built wheel holds pkg_chat/" in capsys.readouterr().err


# -------------------------------------------------------------------- publish


def packed(env, **kw):
    assert prepare(env, package(env["work"], **kw)) == 0
    return tarball(env)


def test_publish_uses_a_scope_only_npmrc_and_the_token_only_through_the_environment(env, capsys):
    tgz = packed(env)
    assert publish(env, tgz) == 0
    log = env["log"].read_text()
    assert "cmd: publish " in log and f"--registry {ENDPOINT}" in log and str(tgz.resolve()) in log
    assert "npmrc-mode: 600" in log
    npmrc = log.split("---npmrc\n")[1].split("cmd:")[0]
    assert npmrc == (
        f"@emergentmatter:registry={ENDPOINT}\n"
        "//em-111.d.codeartifact.us-east-2.amazonaws.com/npm/em-platform/"
        ":_authToken=${CODEARTIFACT_AUTH_TOKEN}\n"
    )
    assert TOKEN not in npmrc and "token: " + TOKEN in log
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
    assert list(env["temp"].iterdir()) == []
    assert not (env["work"] / ".npmrc").exists()


def test_publish_skips_a_version_already_there_with_the_same_content(env, monkeypatch):
    tgz = packed(env)
    monkeypatch.setenv("STUB_VIEW", pn.integrity_of(tgz))
    assert publish(env, tgz) == 0
    assert "cmd: publish" not in env["log"].read_text()


def test_publish_fails_for_the_same_version_with_different_content(env, monkeypatch, capsys):
    tgz = packed(env)
    monkeypatch.setenv("STUB_VIEW", "sha512-AAAA")
    assert publish(env, tgz) == 1
    assert "already exists with different content" in capsys.readouterr().err
    assert "cmd: publish" not in env["log"].read_text()


def test_publish_fails_when_it_cannot_ask_the_registry(env, monkeypatch, capsys):
    tgz = packed(env)
    monkeypatch.setenv("STUB_VIEW", "net")
    assert publish(env, tgz) == 1
    assert "could not ask the registry" in capsys.readouterr().err
    assert "cmd: publish" not in env["log"].read_text()


def test_a_failed_npm_publish_fails_the_run_and_still_cleans_up(env, monkeypatch, capsys):
    tgz = packed(env)
    monkeypatch.setenv("STUB_EXIT", "1")
    assert publish(env, tgz) == 1
    assert "npm publish failed" in capsys.readouterr().err
    assert list(env["temp"].iterdir()) == []


@pytest.mark.parametrize("endpoint", ["http://x.example/npm/r/", "ftp://x/", "https://x/?a=1", "x"])
def test_publish_refuses_an_endpoint_that_is_not_plain_https(env, endpoint):
    assert publish(env, packed(env), endpoint) == 1
    assert not env["log"].exists()


def test_publish_refuses_without_the_token(env, monkeypatch):
    tgz = packed(env)
    monkeypatch.delenv(pn.TOKEN_VAR)
    assert publish(env, tgz) == 1
    assert not env["log"].exists()


def test_publish_rechecks_the_packed_manifest(env, tmp_path, capsys):
    # A tarball someone packed by hand with an install script is refused at publish too.
    pkg = package(env["work"], scripts={"postinstall": "x"})
    env["out"].mkdir()
    subprocess.run(
        [
            REAL_NPM,
            "pack",
            "--pack-destination",
            str(env["out"]),
            "--ignore-scripts",
            "--loglevel",
            "error",
        ],
        cwd=pkg,
        check=True,
        capture_output=True,
    )
    assert publish(env, tarball(env)) == 1
    assert "install-time" in capsys.readouterr().err
    assert not env["log"].exists()


# ---------------------------------------------------------------- the workflows

WORKFLOWS = [ROOT / ".github/workflows/version.yml", ROOT / ".github/workflows/build-release.yml"]


def step(text: str, name: str) -> str:
    return text.split(f"- name: {name}")[1].split("\n      - name:")[0]


def test_both_workflows_take_npm_packages_and_a_pinned_node_version():
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        for name, default in (("npm-packages", '""'), ("node-version", '"24.18.0"')):
            block = text.split(f"      {name}:\n")[1].split("\n\n")[0]
            assert "type: string" in block and f"default: {default}" in block, (path.name, name)


def test_both_workflows_refuse_npm_packages_for_any_other_target():
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        validate = step(text, "Validate inputs for this publish target")
        assert "npm-packages needs publish-target 'codeartifact'" in validate


def test_every_npm_package_is_checked_before_anything_is_uploaded():
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        prepare = text.index("- name: Pack and check npm packages")
        assert text.index("- name: Download build artifact") < prepare
        for later in ("- name: Publish to PyPI", "- name: Configure AWS credentials"):
            assert prepare < text.index(later), (path.name, later)
        body = step(text, "Pack and check npm packages")
        assert " prepare " in body and "--wheel-dir dist" in body
        assert "aws " not in body and "CODEARTIFACT_AUTH_TOKEN" not in body


def test_the_networked_npm_step_is_codeartifact_only_and_before_the_wheel_upload():
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        body = step(text, "Publish npm packages to CodeArtifact")
        assert "if: inputs.publish-target == 'codeartifact' && inputs.npm-packages != ''" in body
        assert "--format npm" in body and " publish " in body
        assert "${{ inputs.npm-packages }}" not in body.split("run: |")[1], "input in shell"
        assert text.index("- name: Publish npm packages to CodeArtifact") < text.index(
            "- name: Publish to CodeArtifact"
        )


def test_node_is_pinned_by_commit_and_version_in_both_workflows():
    for path in WORKFLOWS:
        body = step(path.read_text(encoding="utf-8"), "Set up Node")
        assert "actions/setup-node@" in body and "# v" in body
        assert "node-version: ${{ inputs.node-version }}" in body
