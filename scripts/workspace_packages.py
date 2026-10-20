#!/usr/bin/env python3
"""List a uv workspace's packages and check they share one version.

Called from version.yml's bump step when `all-packages` is true, so a
workspace that publishes several packages in lockstep gets every one of
them bumped and none left behind. Reads only `pyproject.toml` files; the
bump itself is still `uv version`, which owns how a version is written.

    workspace_packages.py names                  # one distribution name per line
    workspace_packages.py check --version 1.4.0  # exit 1 unless every package is 1.4.0
                                                 # and every internal == pin names it
    workspace_packages.py pins --version 1.4.0   # one "<dependent>|<extra>|<requirement>"
                                                 # line per internal == pin

The packages are the root project (when it has a `[project]` table) plus
every directory matched by `[tool.uv.workspace] members`, minus `exclude`,
the same set `uv build --all-packages` builds. A repo with no
`[tool.uv.workspace]` table is a workspace of one.

An exact pin (`==`) from one workspace package to another is part of the
lockstep: `uv version` does not move it, so it would keep requiring the old
version of its sibling. `pins` lists each one with the requirement it must
become, and `check` fails on any that is not `==` the new version. A range
(`>=1.2,<2`) is left alone: it is a statement about compatibility that a
version bump does not change. Pins in `[project].dependencies` and
`[project.optional-dependencies]` are covered; dependency groups are not.

A package whose version is dynamic, or missing, is an error rather than a
skip: lockstep cannot be asserted about a version this script cannot read.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple


class WorkspaceError(RuntimeError):
    """Anything that should stop the bump with a readable message."""


class Package(NamedTuple):
    name: str
    version: str | None
    path: Path
    # (extra name or "", requirement string) for each declared dependency.
    requirements: tuple[tuple[str, str], ...] = ()


class InternalPin(NamedTuple):
    dependent: str
    extra: str
    member: str
    extras: str  # "[a,b]" or ""
    version: str
    marker: str  # "; python_version >= '3.11'" or ""

    def requirement(self, version: str) -> str:
        return f"{self.member}{self.extras}=={version}{self.marker}"


_REQUIREMENT = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<extras>\[[^\]]*\])?\s*"
    r"(?P<spec>[^;]*?)\s*(?P<marker>;.*)?$"
)
_EXACT = re.compile(r"^==\s*([^\s,*]+)$")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _read(pyproject: Path) -> dict:
    try:
        with pyproject.open("rb") as f:
            return tomllib.load(f)
    except OSError as exc:
        raise WorkspaceError(f"cannot read {pyproject}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise WorkspaceError(f"{pyproject} is not valid TOML: {exc}") from exc


def _package_at(directory: Path) -> Package | None:
    """The package a directory's pyproject.toml declares, or None when it
    has no `[project]` table (a virtual workspace root)."""
    pyproject = directory / "pyproject.toml"
    project = _read(pyproject).get("project")
    if project is None:
        return None
    if "name" not in project:
        raise WorkspaceError(f"{pyproject} has a [project] table with no name")
    if "version" in project.get("dynamic", []):
        raise WorkspaceError(
            f"{project['name']} declares a dynamic version in {pyproject}; "
            "lockstep releases need a static version in every package."
        )
    requirements = [("", r) for r in project.get("dependencies", [])]
    for extra, extra_requirements in project.get("optional-dependencies", {}).items():
        requirements += [(extra, r) for r in extra_requirements]
    return Package(project["name"], project.get("version"), directory, tuple(requirements))


def _expand(root: Path, patterns: list[str]) -> set[Path]:
    found: set[Path] = set()
    for pattern in patterns:
        for match in root.glob(pattern):
            if match.is_dir():
                found.add(match.resolve())
    return found


def workspace_packages(root: Path) -> list[Package]:
    """Root project first, then members in path order."""
    root = root.resolve()
    workspace = _read(root / "pyproject.toml").get("tool", {}).get("uv", {}).get("workspace", {})
    members = _expand(root, workspace.get("members", []))
    members -= _expand(root, workspace.get("exclude", []))
    members.discard(root)

    packages: list[Package] = []
    root_package = _package_at(root)
    if root_package is not None:
        packages.append(root_package)
    for directory in sorted(members):
        if not (directory / "pyproject.toml").is_file():
            raise WorkspaceError(
                f"workspace member {directory.relative_to(root)} has no pyproject.toml"
            )
        package = _package_at(directory)
        if package is None:
            raise WorkspaceError(f"workspace member {directory.relative_to(root)} has no [project]")
        packages.append(package)

    names = [p.name.lower().replace("_", "-") for p in packages]
    if len(set(names)) != len(names):
        raise WorkspaceError("two workspace packages share a distribution name")
    return packages


def check_version(packages: list[Package], expected: str) -> list[str]:
    """One message per package whose version is not `expected`."""
    return [
        f"{p.name} is at {p.version or '(no version)'}, expected {expected}"
        for p in packages
        if p.version != expected
    ]


def internal_pins(packages: list[Package]) -> list[InternalPin]:
    """Every exact pin one workspace package declares on another."""
    members = {_normalize(p.name): p.name for p in packages}
    pins: list[InternalPin] = []
    for package in packages:
        for extra, requirement in package.requirements:
            match = _REQUIREMENT.match(requirement)
            if match is None or _normalize(match["name"]) not in members:
                continue
            exact = _EXACT.match(match["spec"])
            if exact is None:
                continue
            pins.append(
                InternalPin(
                    package.name,
                    extra,
                    members[_normalize(match["name"])],
                    re.sub(r"\s+", "", match["extras"] or ""),
                    exact.group(1),
                    ("; " + match["marker"][1:].strip()) if match["marker"] else "",
                )
            )
    return pins


def check_pins(packages: list[Package], expected: str) -> list[str]:
    """One message per internal exact pin that is not `== expected`."""
    return [
        f"{pin.dependent} pins {pin.member}=={pin.version}, expected =={expected}"
        for pin in internal_pins(packages)
        if pin.version != expected
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=".", type=Path)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("names", help="print each package's distribution name")
    check = sub.add_parser(
        "check", help="fail unless every package and internal == pin is at --version"
    )
    check.add_argument("--version", required=True)
    pins = sub.add_parser("pins", help="list each internal == pin as it must read at --version")
    pins.add_argument("--version", required=True)
    args = ap.parse_args(argv)

    try:
        packages = workspace_packages(args.root)
    except WorkspaceError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    if not packages:
        print("::error::no package found in this workspace", file=sys.stderr)
        return 1

    if args.command == "names":
        for package in packages:
            print(package.name)
        return 0

    if args.command == "pins":
        for pin in internal_pins(packages):
            print(f"{pin.dependent}|{pin.extra}|{pin.requirement(args.version)}")
        return 0

    problems = check_version(packages, args.version) + check_pins(packages, args.version)
    if problems:
        print(
            "::error::the workspace packages are not in lockstep after the bump:\n  "
            + "\n  ".join(problems),
            file=sys.stderr,
        )
        return 1
    print(f"{len(packages)} package(s) at {args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
