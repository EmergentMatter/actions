#!/usr/bin/env python3
"""Fail if an organization package was resolved from an index it must not come from.

A `[tool.uv.sources]` entry routes only a DIRECT requirement to a named index. A package that
is only ever a dependency of a dependency is resolved from the default index unless a
constraint also names it, and when that index happens to carry a package of the same name the
lock records it and nothing else notices. This reads what was resolved (a `uv.lock`, or the
annotated output of `uv pip compile --emit-index-annotation`) and holds every organization
package to three rules:

* it never comes from the public index, even when the project declares that index;
* it comes from an index the project declares under `[[tool.uv.index]]`;
* a closed package (`--private-names`) comes from the private index (`--private-index`), and
  from nowhere else: with no private index declared, a closed package is a failure.

A package is the organization's when its name starts with the prefix or is one of the extra
exact names (`--org-names`). Entries that are not registry packages (editable, path,
workspace, git) are not judged. Stdlib only.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_PREFIX = "emergent-matter-"
#: Names outside the prefix that are still the organization's.
DEFAULT_ORG_NAMES = ("sdm-view", "em-release-control-test")
#: Packages that must come from the private index, as fnmatch patterns.
DEFAULT_PRIVATE_NAMES = (
    "*-private",
    "*-cem-kit",
    "emergent-matter-sdm-sidecar",
    "emergent-matter-sdm-engine",
    "emergent-matter-sdm-tools",
)
#: Hosts of the public index and its files. An organization package never comes from these.
PUBLIC_HOSTS = frozenset({"pypi.org", "files.pythonhosted.org", "test.pypi.org"})


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _norm(url: str) -> str:
    return url.rstrip("/")


def declared_indexes(pyproject: dict) -> dict[str, str]:
    """Index name (or the URL when unnamed) -> normalized URL."""
    entries = pyproject.get("tool", {}).get("uv", {}).get("index", [])
    return {
        str(e.get("name") or e["url"]): _norm(e["url"])
        for e in entries
        if isinstance(e, dict) and e.get("url")
    }


@dataclass
class Policy:
    declared: set[str]
    prefix: str = DEFAULT_PREFIX
    org_names: frozenset[str] = frozenset(DEFAULT_ORG_NAMES)
    private_names: tuple[str, ...] = DEFAULT_PRIVATE_NAMES
    private_url: str | None = None
    extra: list[str] = field(default_factory=list)

    def is_org(self, name: str) -> bool:
        n = normalize(name)
        return n.startswith(normalize(self.prefix)) or n in {normalize(x) for x in self.org_names}

    def is_closed(self, name: str) -> bool:
        n = normalize(name)
        return any(fnmatch.fnmatch(n, normalize(p)) for p in self.private_names)

    def problem(self, name: str, version: str, index: str | None) -> str | None:
        if not self.is_org(name) or index is None:
            return None
        url = _norm(index)
        who = f"{name} {version}"
        if urlparse(url).hostname in PUBLIC_HOSTS:
            return f"{who} came from {index}, the public index: an organization package never does"
        if url not in self.declared:
            return f"{who} came from {index}, an index this project does not declare"
        if self.is_closed(name) and url != self.private_url:
            where = "no private index is declared" if self.private_url is None else self.private_url
            return (
                f"{who} is a closed package and came from {index}, not the private index ({where})"
            )
        return None


def lock_violations(lock: dict, policy: Policy) -> list[str]:
    found = []
    for package in lock.get("package", []):
        registry = package.get("source", {}).get("registry")
        problem = policy.problem(package.get("name", ""), package.get("version", "?"), registry)
        if problem:
            found.append(problem)
    return found


_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)")
_FROM = re.compile(r"^\s*#\s*from\s+(\S+)")


def resolved_violations(text: str, policy: Policy) -> list[str]:
    """Judge `uv pip compile --emit-index-annotation` output: a `name==version` line followed
    by `# from <index>`. A pinned organization package with no annotation cannot be placed and
    is reported."""
    found: list[str] = []
    current: tuple[str, str] | None = None
    index: str | None = None

    def close() -> None:
        if current is None or not policy.is_org(current[0]):
            return
        if index is None:
            found.append(f"{current[0]} {current[1]} has no recorded index: cannot be placed")
        else:
            problem = policy.problem(current[0], current[1], index)
            if problem:
                found.append(problem)

    for line in text.splitlines():
        pin = _PIN.match(line)
        if pin:
            close()
            current, index = (pin.group(1), pin.group(2)), None
            continue
        source = _FROM.match(line)
        if source and current is not None:
            index = source.group(1)
    close()
    return found


def split_names(value: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in value.split(",") if p.strip())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--lock", default=None, help="a uv.lock (default: uv.lock)")
    source.add_argument("--resolved", help="`uv pip compile --emit-index-annotation` output")
    ap.add_argument("--pyproject", default="pyproject.toml")
    ap.add_argument("--prefix", default=DEFAULT_PREFIX)
    ap.add_argument("--org-names", default=",".join(DEFAULT_ORG_NAMES), help="extra exact names")
    ap.add_argument("--private-index", default="", help="name of the declared private index")
    ap.add_argument(
        "--private-names",
        default=",".join(DEFAULT_PRIVATE_NAMES),
        help="comma-separated patterns for packages that must come from the private index",
    )
    args = ap.parse_args(argv)

    pyproject = tomllib.loads(Path(args.pyproject).read_text(encoding="utf-8"))
    declared = declared_indexes(pyproject)
    policy = Policy(
        declared=set(declared.values()),
        prefix=args.prefix,
        org_names=frozenset(split_names(args.org_names)),
        private_names=split_names(args.private_names),
        private_url=declared.get(args.private_index) if args.private_index else None,
    )
    if args.resolved:
        what = args.resolved
        bad = resolved_violations(Path(args.resolved).read_text(encoding="utf-8"), policy)
    else:
        what = args.lock or "uv.lock"
        bad = lock_violations(tomllib.loads(Path(what).read_text(encoding="utf-8")), policy)
    if bad:
        print(
            f"::error::{len(bad)} organization package(s) in {what} came from the wrong index:",
            file=sys.stderr,
        )
        for line in bad:
            print(f"  {line}", file=sys.stderr)
        print(
            "Name each one in [tool.uv.sources] and in [tool.uv] constraint-dependencies.",
            file=sys.stderr,
        )
        return 1
    print(f"ok: every organization package in {what} comes from an index it may")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
