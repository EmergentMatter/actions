#!/usr/bin/env python3
"""Hold every Python file to a code-line limit, with a baseline that may not grow.

`ruff-base.toml` limits complexity and function size; ruff has no rule for file
length, so this script covers it. A file over the limit is allowed only while it
is listed in `lint-baseline.json` at the repo root, at or below the count listed
there. Code lines are lines holding code: blank lines, comments and docstrings
do not count.

    python3 scripts/lint-limits.py                    fail if the baseline and the code differ
    python3 scripts/lint-limits.py --base origin/main also fail if the baseline grew vs a ref
    python3 scripts/lint-limits.py --update           rewrite the baseline (shrinks only)
    python3 scripts/lint-limits.py --update --allow-growth
                                                      the one way to add to it, on purpose

A repo with no `lint-baseline.json` has an empty baseline. Stdlib only. Keep this
file at `scripts/lint-limits.py`; it is managed, so change it in
EmergentMatter/actions, not here.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import subprocess
import sys
import tokenize
from pathlib import Path

MAX_LINES = 400
BASELINE_NAME = "lint-baseline.json"
ROOT = Path(__file__).resolve().parent.parent
SKIPPED_DIRS = {".venv", "venv", "node_modules", "build", "dist", ".git", "__pycache__"}
_NON_CODE = {
    tokenize.COMMENT,
    tokenize.NL,
    tokenize.NEWLINE,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.ENCODING,
    tokenize.ENDMARKER,
}


def docstring_lines(tree: ast.AST) -> set[int]:
    """Line numbers covered by module, class and function docstrings."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def code_lines(source: str) -> int:
    """How many lines of `source` hold code. An unparseable file counts non-blank lines."""
    try:
        skip = docstring_lines(ast.parse(source))
        held: set[int] = set()
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in _NON_CODE:
                continue
            held.update(range(tok.start[0], tok.end[0] + 1))
        return len(held - skip)
    except (SyntaxError, tokenize.TokenError, ValueError):
        return sum(1 for line in source.splitlines() if line.strip())


def python_files(root: Path) -> list[Path]:
    """Tracked `.py` files under `root`; every `.py` file outside skipped folders without git."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.py"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return [root / name for name in out.split("\0") if name and (root / name).is_file()]
    except (OSError, subprocess.CalledProcessError):
        return [
            path
            for path in root.rglob("*.py")
            if not SKIPPED_DIRS.intersection(path.relative_to(root).parts)
        ]


def measure(root: Path, limit: int) -> dict[str, int]:
    """`{relative path: code lines}` for every file over `limit`."""
    over: dict[str, int] = {}
    for path in python_files(root):
        n = code_lines(path.read_text(encoding="utf-8", errors="replace"))
        if n > limit:
            over[path.relative_to(root).as_posix()] = n
    return over


def compare(baseline: dict[str, int], actual: dict[str, int], limit: int) -> list[str]:
    """The problems with `baseline` as a description of `actual`; empty means they agree."""
    problems = []
    for file, n in sorted(actual.items()):
        allowed = baseline.get(file)
        if allowed is None:
            problems.append(f"{file}: {n} code lines (limit {limit}). Split it into modules.")
        elif n > allowed:
            problems.append(f"{file}: {n} code lines (baseline {allowed}). It may not grow.")
    for file, allowed in sorted(baseline.items()):
        n = actual.get(file)
        if n is None:
            problems.append(f"{file}: no longer over {limit}. Run `scripts/lint-limits.py --update`.")
        elif n < allowed:
            problems.append(
                f"{file}: now {n}, baseline says {allowed}. Run `scripts/lint-limits.py --update`."
            )
    return problems


def growth(prior: dict[str, int], new: dict[str, int]) -> list[str]:
    """Where `new` allows more than `prior` did, or allows a file `prior` did not list."""
    return [
        f"{file}: {prior.get(file, 0)} -> {n}"
        for file, n in sorted(new.items())
        if n > prior.get(file, 0)
    ]


def read_baseline(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def baseline_at(root: Path, ref: str) -> dict[str, int] | None:
    """The baseline committed at `ref`, or None when that ref has no baseline yet."""
    try:
        out = subprocess.run(
            ["git", "show", f"{ref}:{BASELINE_NAME}"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return json.loads(out)


def serialise(counts: dict[str, int]) -> str:
    return json.dumps(dict(sorted(counts.items())), indent=2) + "\n"


def update(root: Path, actual: dict[str, int], allow_growth: bool) -> int:
    path = root / BASELINE_NAME
    grew = growth(read_baseline(path), actual)
    if grew and not allow_growth:
        print("The baseline may not grow:\n  " + "\n  ".join(grew), file=sys.stderr)
        return 1
    path.write_text(serialise(actual), encoding="utf-8")
    print(f"{BASELINE_NAME}: {len(actual)} entries")
    return 0


def check(root: Path, actual: dict[str, int], limit: int, base: str | None) -> int:
    baseline = read_baseline(root / BASELINE_NAME)
    problems = compare(baseline, actual, limit)
    prior = baseline_at(root, base) if base else None
    if prior is not None:
        problems += [f"baseline grew vs {base}: {g}" for g in growth(prior, baseline)]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"lint-limits: ok ({len(baseline)} baselined files, limit {limit})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repo root (default: this repo)")
    parser.add_argument("--max-lines", type=int, default=MAX_LINES)
    parser.add_argument("--base", help="a git ref whose baseline the new one may not exceed")
    parser.add_argument("--update", action="store_true", help="rewrite the baseline")
    parser.add_argument("--allow-growth", action="store_true", help="with --update: allow adding")
    args = parser.parse_args(argv)
    actual = measure(args.root, args.max_lines)
    if args.update:
        return update(args.root, actual, args.allow_growth)
    return check(args.root, actual, args.max_lines, args.base)


if __name__ == "__main__":
    sys.exit(main())
