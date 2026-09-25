#!/usr/bin/env python3
"""Refuse a publish target this repo's licence tier does not allow.

Called from the publish job in version.yml / build-release.yml, BEFORE
any AWS credentials are assumed or any upload runs (see those workflows'
"Validate publish target" step). Reads only the consumer's own checked-out
pyproject.toml, so it's a "called from a workflow" script (README.md's
"The scripts"): stdlib only, no `gh`.

`[tool.em-release] tier` ("open" | "closed") decides what `publish-target`
values are allowed:

  - A "closed" repo may never reach a PUBLIC index. `pypi`, `static-index`,
    and `both` (static-index + codeartifact) are all refused outright.
    Only `codeartifact` (sign-in required to even read it) is allowed.
  - A repo with NO declared tier is still allowed `pypi` -- that's the one
    publish path that existed before licence tiers did, so a repo that
    hasn't declared a tier yet doesn't lose it -- but is refused every
    AWS-backed target (`static-index`, `codeartifact`, `both`). Those need
    a per-repo IAM role scoped to a specific tier, and there is no correct
    default to assume between "open" and "closed" on a repo's behalf.

Exit 1 with a clear message on a disallowed combination, an unrecognized
`--target`, or a `tier` that is neither "open" nor "closed". Exit 0
(silently) when the target is allowed -- nothing to say, same convention
as sync_version.py's `--check`.

    check_publish_tier.py --target pypi|static-index|codeartifact|both
                           [--pyproject pyproject.toml]
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

__all__ = ["PublishTierError", "VALID_TARGETS", "resolve_tier", "validate"]

VALID_TARGETS = {"pypi", "static-index", "codeartifact", "both"}
_VALID_TIERS = {"open", "closed"}

# Targets that would put the artifact somewhere reachable with no
# credentials at all: PyPI itself, and/or our own static index.
_PUBLIC_TARGETS = {"pypi", "static-index", "both"}
# Targets that need an AWS role assumed, and therefore a tier to pick
# which per-repo role is even correct to assume.
_AWS_TARGETS = {"static-index", "codeartifact", "both"}


class PublishTierError(RuntimeError):
    """Anything that should stop the publish with a readable message."""


def resolve_tier(pyproject_path: Path) -> str | None:
    """`[tool.em-release] tier`, or None if this repo hasn't declared one."""
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)
    tier = data.get("tool", {}).get("em-release", {}).get("tier")
    if tier is not None and tier not in _VALID_TIERS:
        raise PublishTierError(
            f"[tool.em-release] tier is {tier!r} in {pyproject_path}, not 'open' or 'closed'"
        )
    return tier


def validate(target: str, tier: str | None) -> None:
    """Raise PublishTierError if `target` is not allowed for `tier`."""
    if target not in VALID_TARGETS:
        raise PublishTierError(
            f"unrecognized publish-target {target!r}; must be one of {sorted(VALID_TARGETS)}"
        )
    if tier == "closed" and target in _PUBLIC_TARGETS:
        raise PublishTierError(
            f"publish-target {target!r} reaches a public index (PyPI and/or the static "
            "index), but this repo's [tool.em-release] tier is 'closed'. A closed-tier repo "
            "may only publish with publish-target: codeartifact."
        )
    if tier is None and target in _AWS_TARGETS:
        raise PublishTierError(
            f"publish-target {target!r} needs an AWS role scoped to a licence tier, but this "
            'repo has no [tool.em-release] tier declared. Add tier = "open" or tier = '
            '"closed" before enabling this publish target.'
        )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", required=True)
    ap.add_argument("--pyproject", default=Path("pyproject.toml"), type=Path)
    args = ap.parse_args(argv)

    if not args.pyproject.is_file():
        print(f"error: no such file: {args.pyproject}", file=sys.stderr)
        return 1

    try:
        tier = resolve_tier(args.pyproject)
        validate(args.target, tier)
    except PublishTierError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
