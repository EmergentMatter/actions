#!/usr/bin/env python3
"""Fail if an org-owned package in `uv.lock` came from an index the project
did not declare.

A `[tool.uv.sources]` entry routes only a DIRECT requirement to a named
index. A package that is only ever a dependency of a dependency is resolved
from the default index unless a constraint also names it, and when that
index happens to carry a package of the same name, the lock records it and
nothing else notices. This reads the lock and requires every package whose
name starts with the org prefix to come from one of the project's own
`[[tool.uv.index]]` URLs, so the omission is a red check instead of a
dependency from the wrong place.

Entries that are not registry packages (editable, path, workspace, git) are
not judged here. Stdlib only.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

DEFAULT_PREFIX = "emergent-matter-"


def _norm(url: str) -> str:
    return url.rstrip("/")


def declared_urls(pyproject: dict) -> set[str]:
    entries = pyproject.get("tool", {}).get("uv", {}).get("index", [])
    return {_norm(e["url"]) for e in entries if isinstance(e, dict) and e.get("url")}


def violations(lock: dict, allowed: set[str], prefix: str) -> list[str]:
    found: list[str] = []
    for package in lock.get("package", []):
        name = package.get("name", "")
        registry = package.get("source", {}).get("registry")
        if not name.startswith(prefix) or registry is None:
            continue
        if _norm(registry) not in allowed:
            found.append(f"{name} {package.get('version', '?')} came from {registry}")
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lock", default="uv.lock")
    ap.add_argument("--pyproject", default="pyproject.toml")
    ap.add_argument("--prefix", default=DEFAULT_PREFIX)
    args = ap.parse_args(argv)

    pyproject = tomllib.loads(Path(args.pyproject).read_text(encoding="utf-8"))
    lock = tomllib.loads(Path(args.lock).read_text(encoding="utf-8"))
    allowed = declared_urls(pyproject)
    bad = violations(lock, allowed, args.prefix)
    if bad:
        print(
            f"::error::{len(bad)} package(s) named {args.prefix}* resolved from an index "
            "this project does not declare:",
            file=sys.stderr,
        )
        for line in bad:
            print(f"  {line}", file=sys.stderr)
        print(
            "Name each one in [tool.uv.sources] and in [tool.uv] constraint-dependencies.",
            file=sys.stderr,
        )
        return 1
    print(f"ok: every {args.prefix}* package in {args.lock} comes from a declared index")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
