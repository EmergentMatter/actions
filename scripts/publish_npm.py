#!/usr/bin/env python3
"""Publish one npm package to the CodeArtifact repository's npm endpoint.

Called from the publish job in version.yml / build-release.yml, after
`aws-actions/configure-aws-credentials` has assumed the consumer's own
per-repo role (see CONTRACT.md, "Publishing npm packages") -- so this is a
"called from a workflow" script (README.md's "The scripts"): stdlib only,
reads its command-line arguments, `package.json` and one environment
variable, and shells out to `npm` (already on the runner image).

What it enforces that a bare `npm publish` would not:

1. **The token is only ever an environment variable.** `CODEARTIFACT_AUTH_TOKEN`
   is read by npm itself through a `${CODEARTIFACT_AUTH_TOKEN}` reference in
   an `.npmrc` that is written to a fresh directory under the runner's temp
   dir and removed afterwards. The token never appears in a file, an
   argument or this script's output, and no `.npmrc` is ever written into
   the repository.
2. **The `.npmrc` names one scope and nothing else.** `@scope:registry=` and
   the auth line for that endpoint, so npm asks CodeArtifact for nothing
   outside the package's own scope and the default registry is untouched. An
   unscoped package name is refused, because it could only be published to
   the default registry, and so is a `publishConfig.registry` that would send
   it somewhere this script was not told about.
3. **The version comes from the release, not the file.** The version in
   `package.json` is overwritten with `--version`, the one the release
   computed, so an npm package and its Python siblings always carry the same
   number. It must be plain `MAJOR.MINOR.PATCH`.
4. **A version that is already published fails the run.** CodeArtifact
   refuses a second publish of a version; this script does not catch that
   and does not retry, for the same reason `uv publish` is not retried.

A package that needs files built or copied in before it is packed does that
in its own `prepack` script, which npm runs for `publish`. Lifecycle scripts
that npm runs on the consumer's machine at install time (`preinstall`,
`install`, `postinstall`, `prepare`) are refused: a contract package is
data, and nothing it ships may execute when it is installed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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


def inside(package_dir: Path, root: Path) -> bool:
    return package_dir.resolve().is_relative_to(root.resolve())


def publish(package_dir: Path, version: str, endpoint: str, temp_root: Path) -> None:
    if not os.environ.get(TOKEN_VAR):
        raise PublishError(f"{TOKEN_VAR} is not set")
    if not inside(package_dir, Path.cwd()):
        raise PublishError(f"{package_dir} is outside the workspace")
    manifest = read_manifest(package_dir)
    scope = check_manifest(manifest, version)
    endpoint = check_endpoint(endpoint)
    manifest["version"] = version
    (package_dir / "package.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    workdir = Path(tempfile.mkdtemp(prefix="em-npm-", dir=temp_root))
    try:
        npmrc = workdir / "npmrc"
        npmrc.touch(mode=0o600)
        npmrc.write_text(npmrc_text(scope, endpoint), encoding="utf-8")
        print(f"Publishing {manifest['name']}@{version} to {endpoint}")
        result = subprocess.run(
            ["npm", "publish", "--userconfig", str(npmrc), "--registry", endpoint],
            cwd=package_dir,
            check=False,
        )
        if result.returncode != 0:
            raise PublishError(
                f"npm publish failed for {manifest['name']}@{version} (exit {result.returncode})"
            )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--package-dir", required=True, type=Path)
    parser.add_argument("--version", required=True, help="the release version, no leading v")
    parser.add_argument(
        "--endpoint", required=True, help="the CodeArtifact npm repository endpoint"
    )
    parser.add_argument("--temp-dir", type=Path, default=Path(tempfile.gettempdir()))
    args = parser.parse_args(argv)
    try:
        publish(args.package_dir, args.version, args.endpoint, args.temp_dir)
    except PublishError as err:
        print(f"::error::{err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
