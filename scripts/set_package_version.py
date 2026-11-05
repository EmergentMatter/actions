#!/usr/bin/env python3
"""Write the release version into package.json, for a repo with no pyproject.toml.

Called from the version job in version.yml (see CONTRACT.md, "A repo with only a package.json"):
a "called from a workflow" script (README.md's "The scripts"), stdlib only, no network.

    set_package_version.py --version X.Y.Z [--package-json package.json] [--check]

Replaces the value of the top-level `"version"` line and nothing else, so the rest of the file
keeps its formatting and the release PR's diff is one line. Exactly one top-level `"version"`
line must exist; zero or several is exit 1, because a silent no-op here is the bug. `--check`
writes nothing and exits nonzero if the file disagrees with `--version`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

VERSION_LINE = re.compile(r'^(?P<head> {2}"version"\s*:\s*")(?P<value>[^"]*)(?P<tail>")', re.M)
RELEASE_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--package-json", default="package.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if RELEASE_VERSION.match(args.version) is None:
        print(f"error: {args.version!r} is not MAJOR.MINOR.PATCH", file=sys.stderr)
        return 1
    path = Path(args.package_json)
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8")
    matches = list(VERSION_LINE.finditer(text))
    if len(matches) != 1:
        print(
            f'error: expected exactly one top-level "version" line in {path}, found {len(matches)}',
            file=sys.stderr,
        )
        return 1
    current = matches[0].group("value")
    if args.check:
        if current != args.version:
            print(f"error: {path} has version {current}, expected {args.version}", file=sys.stderr)
            return 1
        return 0
    new_text = VERSION_LINE.sub(
        lambda m: f"{m.group('head')}{args.version}{m.group('tail')}", text, count=1
    )
    path.write_text(new_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
