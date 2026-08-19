#!/usr/bin/env python3
"""Write a version string into every location a repo declares.

Reads `[tool.em-release] version_files = ["path:symbol", ...]` from
pyproject.toml. For each entry, finds the assignment `symbol = "..."` in
`path` and rewrites the quoted value to the given version. Every declared
location is written, and nothing else is touched. See CONTRACT.md for the
full spec.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

# Matches an assignment like:  symbol = "1.2.3"   or   symbol = '1.2.3'
# Captures: (1) everything up to and including the opening quote,
#           (2) the quote character, (3) the old value.
ASSIGNMENT_TEMPLATE = r'^(\s*{symbol}\s*=\s*)([\'"])(.*?)\2(.*)$'


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--pyproject", default="pyproject.toml")
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def read_version_files(pyproject_path: Path) -> list[str]:
    if not pyproject_path.is_file():
        print(f"error: pyproject file not found: {pyproject_path}", file=sys.stderr)
        sys.exit(1)
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)
    return data.get("tool", {}).get("em-release", {}).get("version_files", [])


def parse_entry(entry: str) -> tuple[str, str]:
    if ":" not in entry:
        print(
            f"error: malformed version_files entry {entry!r}, expected 'path:symbol'",
            file=sys.stderr,
        )
        sys.exit(1)
    path_str, symbol = entry.rsplit(":", 1)
    return path_str, symbol


def find_assignment(text: str, symbol: str) -> re.Match[str] | None:
    pattern = re.compile(ASSIGNMENT_TEMPLATE.format(symbol=re.escape(symbol)), re.MULTILINE)
    return pattern.search(text)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    pyproject_path = Path(args.pyproject)
    entries = read_version_files(pyproject_path)

    drift = False
    for entry in entries:
        path_str, symbol = parse_entry(entry)
        target = Path(path_str)

        if not target.is_file():
            print(f"error: declared version file not found: {target}", file=sys.stderr)
            return 1

        text = target.read_text()
        match = find_assignment(text, symbol)
        if match is None:
            print(
                f"error: assignment for symbol {symbol!r} not found in {target}",
                file=sys.stderr,
            )
            return 1

        current_value = match.group(3)

        if args.check:
            if current_value != args.version:
                print(
                    f"drift: {target}:{symbol} is {current_value!r}, expected {args.version!r}",
                    file=sys.stderr,
                )
                drift = True
            continue

        prefix, quote, _old_value, suffix = match.groups()
        replacement = f"{prefix}{quote}{args.version}{quote}{suffix}"
        new_text = text[: match.start()] + replacement + text[match.end() :]
        target.write_text(new_text)

    if args.check and drift:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
