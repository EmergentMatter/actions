#!/usr/bin/env python3
"""Fail if a companion package copies a module of the public package it extends.

A companion adds to a public package and never copies from it. A copy goes stale the
moment the public one changes, and nothing ties the two together. This compares the
companion's modules with the installed public package, from the source text alone (nothing
is imported):

* **Paths.** A module at the same relative path in both (``sdf/lipschitz.py`` in each),
  not counting ``__init__.py``: a companion may add to a subpackage the public package
  also has, but may not shadow one of its modules.
* **Names.** A public class or function (no leading underscore) that the companion also
  defines at the top level of a module, anywhere in the public package.

A project opts in with ``[tool.em-companion]`` in its ``pyproject.toml``::

    [tool.em-companion]
    public = "software_defined_matter"      # the public package's import name
    allow = ["path:assembly/old.py", "name:OldThing"]   # intentional, e.g. a shim

``allow`` holds the exceptions a quarterly move needs (a re-export shim for a module that
just went public), each removed with the shim. Without the table the check warns and
does nothing, so a repo that is not a companion can run it unchanged; with ``--require`` a
missing table is a failure, which is how a repo that IS a companion (its name ends in
``-private``) cannot lose the check by misspelling the table. A table spelled with an
underscore (``[tool.em_companion]``) is refused either way.

Run it with the project's own interpreter (``uv run python``): the public package is found
the way the project imports it.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import sys
import tomllib
from pathlib import Path

#: Names every module may define without being a copy.
COMMON_NAMES = frozenset({"main", "register_all"})


def top_level_names(path: Path) -> set[str]:
    """Public classes and functions defined at the top level of a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
        and node.name not in COMMON_NAMES
    }


def modules(root: Path) -> dict[str, Path]:
    """Relative POSIX path -> file, for every module under a package directory."""
    return {
        p.relative_to(root).as_posix(): p
        for p in sorted(root.rglob("*.py"))
        if "__pycache__" not in p.parts
    }


def public_root(import_name: str) -> Path:
    spec = importlib.util.find_spec(import_name)
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit(f"error: the public package {import_name!r} is not installed")
    return Path(next(iter(spec.submodule_search_locations)))


def companion_root(project: Path) -> Path:
    packages = [p for p in (project / "src").iterdir() if (p / "__init__.py").is_file()]
    if len(packages) != 1:
        raise SystemExit(f"error: expected one package under src/, found {len(packages)}")
    return packages[0]


def duplicates(companion: Path, public: Path, allow: set[str]) -> list[str]:
    ours, theirs = modules(companion), modules(public)
    found: list[str] = []
    for rel in sorted(ours):
        if rel.endswith("__init__.py") or f"path:{rel}" in allow:
            continue
        if rel in theirs:
            found.append(f"{rel}: the public package has a module at the same path")
    public_names: dict[str, list[str]] = {}
    for rel, path in theirs.items():
        for name in top_level_names(path):
            public_names.setdefault(name, []).append(rel)
    for rel, path in sorted(ours.items()):
        for name in sorted(top_level_names(path)):
            if name in public_names and f"name:{name}" not in allow and f"path:{rel}" not in allow:
                found.append(f"{rel}: {name} is also defined in public {public_names[name][0]}")
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", default=".", help="the project directory (default: .)")
    ap.add_argument(
        "--require",
        action="store_true",
        help="a missing [tool.em-companion] table is a failure, not a skip",
    )
    args = ap.parse_args(argv)
    project = Path(args.project).resolve()
    tool = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8")).get("tool", {})
    for key in tool:
        if key != "em-companion" and key.replace("_", "-").lower() == "em-companion":
            print(
                f"::error::[tool.{key}] is not the table this check reads: it is "
                "[tool.em-companion]",
                file=sys.stderr,
            )
            return 1
    config = tool.get("em-companion")
    if config is None:
        if args.require:
            print(
                "::error::this project must declare the public package it extends: add "
                '[tool.em-companion] public = "<import name>" to pyproject.toml',
                file=sys.stderr,
            )
            return 1
        print(
            "::warning::[tool.em-companion] is absent, so no duplicate-module check ran. If "
            "this package extends a public one, declare it.",
            file=sys.stderr,
        )
        print("not a companion ([tool.em-companion] absent): nothing to check")
        return 0
    public = public_root(config["public"])
    found = duplicates(companion_root(project), public, set(config.get("allow", [])))
    if found:
        print(
            f"::error::{len(found)} module(s) duplicate the public package {config['public']}:",
            file=sys.stderr,
        )
        for line in found:
            print(f"  {line}", file=sys.stderr)
        print(
            "Delete the copy and import the public one; for a shim during a move, list it in "
            "[tool.em-companion] allow.",
            file=sys.stderr,
        )
        return 1
    print(f"ok: no module of this package duplicates {config['public']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
