#!/usr/bin/env python3
"""Fail when the next release this repo would cut has a major other than the expected one.

This repo's consumers pin the moving `v1` tag, so a `major` changelog note
here is a breaking change that must go through the manual `v2` process in
.github/RELEASING.md, never through the automated release. Run before the
release workflow, against the same notes it will read: no notes means the
next version is the current one, which passes while the current major is
the expected one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import compute_bump


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--major", type=int, default=1, help="the only major allowed to release")
    parser.add_argument("--notes-dir", default="changelog.d")
    parser.add_argument("--pyproject", default="pyproject.toml")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    current = compute_bump.read_current_version(Path(args.pyproject))
    level, _ = compute_bump.max_level(Path(args.notes_dir))
    next_version = current if level is None else compute_bump.bump_version(current, level)

    next_major = int(next_version.split(".")[0])
    if next_major != args.major:
        print(
            f"::error::The next release would be {next_version}, but only major {args.major} "
            "may be released automatically. A breaking change needs a new major tag by hand: "
            "see .github/RELEASING.md. Remove or retype the `major` note to continue.",
            file=sys.stderr,
        )
        return 1

    print(f"OK  next release is {next_version} (major {args.major})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
