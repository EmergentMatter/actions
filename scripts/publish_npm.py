#!/usr/bin/env python3
"""Pack, check and publish one npm package for a release, in two steps.

Called from the publish job in version.yml / build-release.yml (see CONTRACT.md,
"Publishing npm packages") -- a "called from a workflow" script (README.md's "The scripts"):
stdlib only, shelling out to `npm`.

    publish_npm.py prepare --package-dir DIR --version X.Y.Z --out-dir DIR [--wheel-dir dist]
    publish_npm.py publish --tarball FILE --endpoint URL [--temp-dir DIR]

`prepare` needs no network and no credential, and runs for every package BEFORE any wheel
is uploaded, so everything that can be wrong with a package is found while nothing is
published yet:

1. The manifest is checked: a scoped name, not private, no `publishConfig.registry`, no
   install-time script (`preinstall`, `install`, `postinstall`, `prepare`), a plain
   `MAJOR.MINOR.PATCH` version. A `prepack` script is allowed; it is how a package copies in
   files that live elsewhere in its repository, and it is not run by a consumer.
2. The release version is stamped into `package.json` (the committed one is a placeholder),
   so an npm package and its Python siblings carry the same number.
3. `npm pack` makes the tarball that will be published, byte for byte.
4. If the manifest declares `"em-release": {"mirrors-wheel-dir": "<dir in the wheel>"}`, every
   file in the tarball other than `package.json` must exist, byte-identical, under that
   directory in a built wheel in `--wheel-dir`. A package that is a second publication of
   files a wheel already carries is held to that here, not just in the repository's own tests.

`publish` is the only step that touches the network. The token is only ever an environment
variable (`CODEARTIFACT_AUTH_TOKEN`), read by npm through a `${CODEARTIFACT_AUTH_TOKEN}`
reference in an `.npmrc` written to a fresh directory under the runner's temp dir and removed
afterwards; it names one scope and the endpoint's auth line, so npm asks CodeArtifact for
nothing outside the package's own scope, and no `.npmrc` is ever written into the repository.

`publish` is safe to run again. If the package's version is already in the repository with
the same content (the tarball's integrity matches), it is skipped; if the version exists with
different content the run fails, because that is the one case a re-run must never paper over.
A version that does not exist yet is published; any other failure to ask (network, auth)
fails the run.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

TOKEN_VAR = "CODEARTIFACT_AUTH_TOKEN"
SCOPED_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._-]*)/[a-z0-9][a-z0-9._-]*$")
RELEASE_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
INSTALL_SCRIPTS = ("preinstall", "install", "postinstall", "prepare")


class PublishError(Exception):
    """Raised for any reason this package must not be published."""


def read_manifest(package_dir: Path) -> dict[str, object]:
    path = package_dir / "package.json"
    if not path.is_file():
        raise PublishError(f"{path} does not exist")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise PublishError(f"{path} is not a JSON object")
    return manifest


def check_manifest(manifest: dict[str, object], version: str) -> str:
    """Refuse a package that must not be published; return its scope (`@scope`)."""
    name = manifest.get("name")
    match = SCOPED_NAME.match(name) if isinstance(name, str) else None
    if match is None:
        raise PublishError(
            f"package name {name!r} is not scoped (@scope/name); an unscoped package would go to "
            "the default registry, not CodeArtifact"
        )
    if manifest.get("private") is True:
        raise PublishError(f"{name} is marked private; npm would refuse to publish it")
    publish_config = manifest.get("publishConfig")
    if isinstance(publish_config, dict) and "registry" in publish_config:
        raise PublishError(
            f"{name} sets publishConfig.registry; the endpoint is not the package's to pick"
        )
    scripts = manifest.get("scripts")
    if isinstance(scripts, dict):
        found = [s for s in INSTALL_SCRIPTS if s in scripts]
        if found:
            raise PublishError(f"{name} declares install-time scripts: {', '.join(found)}")
    if RELEASE_VERSION.match(version) is None:
        raise PublishError(f"version {version!r} is not MAJOR.MINOR.PATCH")
    return match.group(1)


def check_endpoint(endpoint: str) -> str:
    """The endpoint, with a trailing slash; only https."""
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
        raise PublishError(f"endpoint {endpoint!r} is not a plain https URL")
    return endpoint if endpoint.endswith("/") else endpoint + "/"


def npmrc_text(scope: str, endpoint: str) -> str:
    """The scope-only registry line and its auth line; the token is a reference, not a value."""
    parts = urlsplit(endpoint)
    return (
        f"{scope}:registry={endpoint}\n//{parts.netloc}{parts.path}:_authToken=${{{TOKEN_VAR}}}\n"
    )


def inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def integrity_of(tarball: Path) -> str:
    """npm's `dist.integrity` for these bytes."""
    return "sha512-" + base64.b64encode(hashlib.sha512(tarball.read_bytes()).digest()).decode(
        "ascii"
    )


def tarball_files(tarball: Path) -> dict[str, bytes]:
    """The tarball's regular files by path, without npm's leading `package/`."""
    files: dict[str, bytes] = {}
    with tarfile.open(tarball) as archive:
        for member in archive.getmembers():
            if member.isfile():
                extracted = archive.extractfile(member)
                assert extracted is not None
                files[member.name.removeprefix("package/")] = extracted.read()
    return files


def check_mirrors_wheel(files: dict[str, bytes], mirror: str, wheel_dir: Path) -> None:
    """Every tarball file but package.json is byte-identical under `mirror/` in a built wheel."""
    wheels = sorted(wheel_dir.glob("*.whl"))
    if not wheels:
        raise PublishError(f"mirrors-wheel-dir is declared but {wheel_dir} holds no wheel")
    prefix = mirror.strip("/") + "/"
    in_wheels: dict[str, bytes] = {}
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                if name.startswith(prefix) and not name.endswith("/"):
                    in_wheels.setdefault(name[len(prefix) :], archive.read(name))
    if not in_wheels:
        raise PublishError(f"no built wheel holds {prefix}")
    contract = {n: b for n, b in files.items() if n != "package.json"}
    if not contract:
        raise PublishError("the tarball holds nothing but package.json")
    missing = sorted(n for n in contract if n not in in_wheels)
    differing = sorted(n for n in contract if n in in_wheels and in_wheels[n] != contract[n])
    if missing or differing:
        raise PublishError(
            "the tarball does not match the wheel's "
            + prefix
            + (f"; not in the wheel: {', '.join(missing)}" if missing else "")
            + (f"; differs: {', '.join(differing)}" if differing else "")
        )


def prepare(package_dir: Path, version: str, out_dir: Path, wheel_dir: Path) -> Path:
    if not inside(package_dir, Path.cwd()):
        raise PublishError(f"{package_dir} is outside the workspace")
    manifest = read_manifest(package_dir)
    check_manifest(manifest, version)
    manifest["version"] = version
    (package_dir / "package.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "NPM_CONFIG_USERCONFIG": os.devnull}  # no registry, no credential
    result = subprocess.run(
        [
            "npm",
            "pack",
            "--pack-destination",
            str(out_dir.resolve()),
            "--json",
            "--loglevel",
            "error",
        ],
        cwd=package_dir,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise PublishError(f"npm pack failed for {manifest['name']} (exit {result.returncode})")
    filename = json.loads(result.stdout)[0]["filename"]
    tarball = out_dir / filename
    release_config = manifest.get("em-release")
    mirror = release_config.get("mirrors-wheel-dir") if isinstance(release_config, dict) else None
    if mirror is not None:
        check_mirrors_wheel(tarball_files(tarball), str(mirror), wheel_dir)
    print(f"Packed {manifest['name']}@{version}: {tarball}")
    return tarball


def existing_integrity(npm_view: subprocess.CompletedProcess[str]) -> str | None:
    """The published version's integrity, None if it is not there; anything else is an error."""
    if npm_view.returncode == 0 and npm_view.stdout.strip():
        value = json.loads(npm_view.stdout)
        if isinstance(value, str):
            return value
        raise PublishError(f"unexpected answer from npm view: {npm_view.stdout.strip()[:200]}")
    if "E404" in npm_view.stderr:
        return None
    raise PublishError(
        f"could not ask the registry whether the version exists: {npm_view.stderr.strip()[:300]}"
    )


def publish(tarball: Path, endpoint: str, temp_root: Path) -> None:
    if not os.environ.get(TOKEN_VAR):
        raise PublishError(f"{TOKEN_VAR} is not set")
    files = tarball_files(tarball)
    if "package.json" not in files:
        raise PublishError(f"{tarball} has no package.json")
    manifest = json.loads(files["package.json"])
    version = str(manifest.get("version"))
    scope = check_manifest(manifest, version)
    endpoint = check_endpoint(endpoint)
    name = manifest["name"]

    workdir = Path(tempfile.mkdtemp(prefix="em-npm-", dir=temp_root))
    try:
        npmrc = workdir / "npmrc"
        npmrc.touch(mode=0o600)
        npmrc.write_text(npmrc_text(scope, endpoint), encoding="utf-8")
        config = ["--userconfig", str(npmrc), "--registry", endpoint]
        view = subprocess.run(
            ["npm", "view", f"{name}@{version}", "dist.integrity", "--json", *config],
            check=False,
            capture_output=True,
            text=True,
        )
        existing = existing_integrity(view)
        if existing is not None:
            if existing == integrity_of(tarball):
                print(f"{name}@{version} is already published with the same content; skipping.")
                return
            raise PublishError(f"{name}@{version} already exists with different content")
        print(f"Publishing {name}@{version} to {endpoint}")
        result = subprocess.run(["npm", "publish", str(tarball.resolve()), *config], check=False)
        if result.returncode != 0:
            raise PublishError(
                f"npm publish failed for {name}@{version} (exit {result.returncode})"
            )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="check, stamp and pack; no network")
    prep.add_argument("--package-dir", required=True, type=Path)
    prep.add_argument("--version", required=True, help="the release version, no leading v")
    prep.add_argument("--out-dir", required=True, type=Path)
    prep.add_argument("--wheel-dir", type=Path, default=Path("dist"))
    pub = sub.add_parser("publish", help="publish a packed tarball; the only networked step")
    pub.add_argument("--tarball", required=True, type=Path)
    pub.add_argument("--endpoint", required=True, help="the CodeArtifact npm repository endpoint")
    pub.add_argument("--temp-dir", type=Path, default=Path(tempfile.gettempdir()))
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            prepare(args.package_dir, args.version, args.out_dir, args.wheel_dir)
        else:
            publish(args.tarball, args.endpoint, args.temp_dir)
    except PublishError as err:
        print(f"::error::{err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
