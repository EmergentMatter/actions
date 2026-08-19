#!/usr/bin/env python3
"""Compute the next semver release from pending changelog.d/ notes.

Reads note files named `+<hex>.<level>.md` (or any towncrier fragment name
`<anything>.<level>.md`) and reports the maximum bump level across them,
plus the resulting next version. See CONTRACT.md for the full spec.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

LEVELS = ("major", "minor", "patch")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes-dir", default="changelog.d")
    parser.add_argument("--pyproject", default="pyproject.toml")
    parser.add_argument("--format", choices=["github", "json"], default="github")
    return parser.parse_args(argv)


def read_current_version(pyproject_path: Path) -> str:
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)
    try:
        return data["project"]["version"]
    except KeyError:
        print(f"error: [project] version not found in {pyproject_path}", file=sys.stderr)
        sys.exit(1)


def max_level(notes_dir: Path) -> tuple[str | None, int]:
    """Return (max level or None if no notes, count of note files)."""
    if not notes_dir.is_dir():
        return None, 0

    found_levels: list[str] = []
    count = 0
    for path in sorted(notes_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            # Dotfiles (.gitkeep, etc.) are conventionally not fragments and are
            # silently ignored -- EXCEPT a dotfile ending in .md, which is never
            # legitimate (changeset.py always writes "+<hex>.<level>.md", no
            # leading dot) and must not be silently skipped: towncrier's own
            # fragment discovery may or may not treat it the same way, and a
            # mismatch there is a silent-wrong-version path, not a crash. Reject
            # it loudly instead of guessing. Do not "simplify" this back to a
            # plain dotfile skip.
            if path.suffix == ".md":
                print(
                    f"error: {path.name!r} in {notes_dir} looks like a changelog fragment "
                    "but starts with '.', which is not a valid fragment name. Rename it to "
                    "'+<hex>.<level>.md'.",
                    file=sys.stderr,
                )
                sys.exit(1)
            continue
        if path.suffix != ".md":
            continue
        count += 1
        # Fragment grammar: <name>.<level>.md — take the part after the
        # last "." before ".md".
        stem = path.stem  # strips ".md"
        level = stem.rsplit(".", 1)[-1]
        if level not in LEVELS:
            print(
                f"error: unrecognized bump level {level!r} in note file {path.name} "
                f"(expected one of {LEVELS})",
                file=sys.stderr,
            )
            sys.exit(1)
        found_levels.append(level)

    if count == 0:
        return None, 0

    for level in LEVELS:  # major, minor, patch — highest first
        if level in found_levels:
            return level, count

    # Unreachable: every found level was validated against LEVELS above.
    raise AssertionError("unreachable")


def bump_version(current: str, level: str) -> str:
    parts = current.split(".")
    if len(parts) != 3:
        print(f"error: current version {current!r} is not X.Y.Z semver", file=sys.stderr)
        sys.exit(1)
    try:
        major, minor, patch = (int(p) for p in parts)
    except ValueError:
        print(f"error: current version {current!r} is not X.Y.Z semver", file=sys.stderr)
        sys.exit(1)

    if level == "major":
        major, minor, patch = major + 1, 0, 0
    elif level == "minor":
        minor, patch = minor + 1, 0
    else:  # patch
        patch += 1

    return f"{major}.{minor}.{patch}"


def emit(result: dict[str, str | int], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(result))
        return

    github_output = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{key}={value}" for key, value in result.items()]
    if github_output:
        with Path(github_output).open("a") as f:
            for line in lines:
                f.write(line + "\n")
    else:
        for line in lines:
            print(line)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    current = read_current_version(Path(args.pyproject))
    level, count = max_level(Path(args.notes_dir))

    if level is None:
        emit({"level": "none", "current": current, "next": current, "count": 0}, args.format)
        return 0

    next_version = bump_version(current, level)
    emit(
        {"level": level, "current": current, "next": next_version, "count": count},
        args.format,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
