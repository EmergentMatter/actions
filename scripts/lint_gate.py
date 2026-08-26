#!/usr/bin/env python3
"""Turn a consuming repo's staged-rollout gate on or off, or report its state.

Manages `lint`, `format`, or `typecheck` via `--job` (default: `lint`).

The gate has TWO halves that must agree, and they live in different places:

  1. `continue-on-error: true` on ci.yml's job        -- a file in the repo
  2. the job's name in required status checks         -- a GitHub setting

Flipping one without the other creates a broken state, not a half-measure.
The two broken states fail in opposite directions:

  line removed, context absent  -> the job gates nothing on PRs, but its
                                   failure now fails the run and can stall
                                   version.yml's `needs: ci` mid-release
  context added, line present   -> the job gates PRs, but its failure still
                                   sails through the release path

Only whether the job's name is a required context decides whether its
failure blocks a PR. `continue-on-error` does NOT stop that. Verified on a
real run: the job's conclusion is `failure`, the workflow RUN's conclusion
is `success`, and branch protection reads the CHECK RUN, which is `failure`.
See docs/onboarding.md, "Staging the lint rollout" (the same mechanics
apply to format and typecheck).

Stdlib only, like the other scripts here. GitHub access shells out to `gh`,
reusing its auth rather than handling tokens.

Usage, from the target repo's root (or pass --repo-path):

    python3 lint_gate.py status
    python3 lint_gate.py on
    python3 lint_gate.py off
    python3 lint_gate.py --job format status
    python3 lint_gate.py --job typecheck on
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

# fleet_status.py imports this module directly (dynamically -- see its own
# module docstring). __all__ is scoped to that cross-module surface.
__all__ = ["LintGateError", "State", "JobName", "LINT_CONTEXT", "has_continue_on_error"]

CI_FILE = ".github/workflows/ci.yml"
LINT_CONTEXT = "lint"

JobName = Literal["lint", "format", "typecheck"]
JOBS: tuple[JobName, ...] = get_args(JobName)

# The command that proves a job's backlog is clean, keyed by job.
BACKLOG_CHECK_CMD: dict[JobName, list[str]] = {
    "lint": ["uv", "run", "ruff", "check", "."],
    "format": ["uv", "run", "ruff", "format", "--check", "."],
    "typecheck": ["uv", "run", "mypy", "src"],
}

# Text that marks a comment line as OURS to replace, so flipping a job
# never leaves a stale explanation sitting above the new one. Each tuple
# covers one direction. Only ever add an entry; removing one stops this
# script recognizing (and replacing) that wording in a repo still
# carrying it, so the old and new one end up stacked instead of swapped.
#
# STAGED_COMMENT_SENTINELS: the comment `on` looks for and removes.
# "# STAGED ROLLOUT." covers both the current lint job's comment in
# templates/ci.yml and the short one-liner format/typecheck carry.
# "# Staged rollout: non-blocking..." is the pre-v1.1.0 template's wording.
STAGED_COMMENT_SENTINELS = (
    "# STAGED ROLLOUT.",
    "# Staged rollout: non-blocking until the existing lint backlog is",
    "# Staged:",
)

# ENFORCED_COMMENT_SENTINELS: the comment `off` looks for and removes.
# "is ENFORCED:" is the pre-this-fix wording ("Lint is ENFORCED:", etc.).
ENFORCED_COMMENT_SENTINELS = (
    "# Enforced:",
    "is ENFORCED:",
)


def _job_label(job: str) -> str:
    """Display form for a job id: `lint` -> `Lint`, `typecheck` -> `Typecheck`."""
    return job[:1].upper() + job[1:]


def _find_sentinel_line(
    lines: list[str], start: int, end: int, sentinels: tuple[str, ...]
) -> int | None:
    """Index of the first line in `lines[start:end]` naming one of `sentinels`,
    or None. Finding the sentinel's own line, rather than walking backward
    through however many comment lines happen to be contiguous above it,
    is what lets `on`/`off` remove a whole multi-line comment even when a
    blank line splits it into more than one contiguous run."""
    for i in range(start, end):
        if any(sentinel in lines[i] for sentinel in sentinels):
            return i
    return None


def on_comment(job: JobName = "lint") -> str:
    """The comment left behind once `job` is enforced (continue-on-error removed)."""
    return (
        f"    # Enforced: no continue-on-error, and `{job}` is a required status\n"
        f"    # check. Stage it back to advisory with `lint_gate.py --job {job} off`.\n"
    )


# The lint job's comment.
ON_COMMENT = on_comment("lint")


class LintGateError(RuntimeError):
    """Anything that should stop the command with a readable message."""


# ---------------------------------------------------------------- pure logic


def find_lint_job_block(text: str, job: JobName = "lint") -> tuple[int, int]:
    """Return (start, end) line indices of the `<job>:` job block in ci.yml.

    Text-based on purpose: ci.yml carries long explanatory comments that a
    YAML round-trip would silently drop, and this repo is stdlib-only so
    there is no ruamel available to preserve them.
    """
    lines = text.split("\n")
    start = None
    pattern = re.compile(rf"^  {re.escape(job)}:\s*(#.*)?$")
    for i, line in enumerate(lines):
        if pattern.match(line):
            start = i
            break
    if start is None:
        raise LintGateError(
            f"No `{job}:` job found in {CI_FILE}. If this repo's CI uses a "
            "different job name, the gate has to be managed by hand -- see "
            "docs/onboarding.md."
        )
    for j in range(start + 1, len(lines)):
        # Next sibling job: exactly two spaces of indent, then a key.
        if re.match(r"^  \S", lines[j]):
            return start, j
    return start, len(lines)


def has_continue_on_error(text: str, job: JobName = "lint") -> bool:
    """True if `job` carries an active `continue-on-error: true`."""
    start, end = find_lint_job_block(text, job)
    for line in text.split("\n")[start:end]:
        if re.match(r"^\s*continue-on-error:\s*true\s*(#.*)?$", line):
            return True
    return False


def strip_continue_on_error(text: str, job: JobName = "lint") -> str:
    """Remove `job`'s continue-on-error line and its staged-rollout comment,
    replacing both with `on_comment(job)`."""
    lines = text.split("\n")
    start, end = find_lint_job_block(text, job)

    target = None
    for i in range(start, end):
        if re.match(r"^\s*continue-on-error:\s*true\s*(#.*)?$", lines[i]):
            target = i
            break
    if target is None:
        return text

    # Find where OUR comment starts, rather than walking back through
    # however many comment lines are contiguous above the line: a blank
    # line inside a long explanation would stop that walk partway through
    # and leave the rest of the old comment sitting above the new one.
    comment_start = _find_sentinel_line(lines, start, target, STAGED_COMMENT_SENTINELS)
    first = comment_start if comment_start is not None else target

    replacement = on_comment(job).rstrip("\n").split("\n")
    return "\n".join(lines[:first] + replacement + lines[target + 1 :])


def add_continue_on_error(text: str, off_comment: str, job: JobName = "lint") -> str:
    """Put the continue-on-error line (and its staged-rollout comment) back,
    replacing any existing enforced-comment with `off_comment`."""
    if has_continue_on_error(text, job):
        return text
    lines = text.split("\n")
    start, end = find_lint_job_block(text, job)

    insert_at = None
    for i in range(start, end):
        if re.match(r"^\s*steps:\s*(#.*)?$", lines[i]):
            insert_at = i
            break
    if insert_at is None:
        raise LintGateError(f"No `steps:` key in the {job} job of {CI_FILE}.")

    # Same reasoning as strip_continue_on_error: find the enforced comment's
    # own start line rather than filtering by fixed substrings, so it comes
    # out whole even if its exact wording has changed since it was written.
    comment_start = _find_sentinel_line(lines, start, insert_at, ENFORCED_COMMENT_SENTINELS)
    cut_at = comment_start if comment_start is not None else insert_at

    new_block = lines[start:cut_at] + off_comment.rstrip("\n").split("\n") + lines[insert_at:end]
    return "\n".join(lines[:start] + new_block + lines[end:])


@dataclass(frozen=True)
class State:
    """The gate's two halves for one job, and what they imply together."""

    has_line: bool
    lint_required: bool
    job: JobName = "lint"

    @property
    def name(self) -> str:
        """`OFF`, `ON`, or `INCONSISTENT` -- see the module docstring's table."""
        if self.has_line and not self.lint_required:
            return "OFF"
        if not self.has_line and self.lint_required:
            return "ON"
        return "INCONSISTENT"

    @property
    def detail(self) -> str:
        """A human-readable explanation of `name`, with the fix for the
        two INCONSISTENT states (they fail in opposite directions, so the
        message differs)."""
        label = _job_label(self.job)
        on_hint = "on" if self.job == "lint" else f"--job {self.job} on"
        if self.name == "OFF":
            return (
                f"{label.lower()} runs and is visible, but gates nothing. "
                "This is the onboarding default."
            )
        if self.name == "ON":
            return f"{label.lower()} blocks pull requests and the release path."
        if not self.has_line and not self.lint_required:
            return (
                f"continue-on-error is gone but `{self.job}` is NOT a required check.\n"
                f"  {label} gates nothing on pull requests, yet a failure now fails the\n"
                "  whole run and can stall version.yml's `needs: ci` mid-release.\n"
                f"  Fix: run `lint_gate.py {on_hint}` (enforce both) or `off` (stage both back)."
            )
        return (
            f"`{self.job}` is a required check while continue-on-error is still set.\n"
            f"  {label} blocks pull requests, but a failure still sails through the\n"
            "  release path because it does not fail the run.\n"
            f"  Fix: run `lint_gate.py {on_hint}` to remove the line and make both halves agree."
        )


# ------------------------------------------------------------------ gh calls


def run(cmd: list[str], *, cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise LintGateError(f"`{' '.join(cmd[:3])} ...` failed:\n{proc.stderr.strip()}")
    return proc.stdout


def detect_repo(repo_path: Path) -> str:
    url = run(["git", "remote", "get-url", "origin"], cwd=repo_path).strip()
    m = re.search(r"[/:]([^/:]+/[^/]+?)(?:\.git)?$", url)
    if not m:
        raise LintGateError(f"Could not parse an owner/name from origin: {url}")
    return m.group(1)


def default_branch(repo: str) -> str:
    return run(["gh", "api", f"repos/{repo}", "-q", ".default_branch"]).strip()


def required_contexts(repo: str, branch: str) -> list[str]:
    try:
        out = run(
            ["gh", "api", f"repos/{repo}/branches/{branch}/protection/required_status_checks"]
        )
    except LintGateError as exc:
        if "404" in str(exc) or "Not Found" in str(exc):
            raise LintGateError(
                f"{repo} has no required status checks on `{branch}`. Set up branch "
                "protection first -- see docs/onboarding.md."
            ) from exc
        raise
    return list(json.loads(out).get("contexts", []))


def set_required_contexts(repo: str, branch: str, contexts: list[str], *, strict: bool) -> None:
    body = json.dumps({"strict": strict, "contexts": contexts})
    proc = subprocess.run(
        [
            "gh",
            "api",
            "-X",
            "PATCH",
            f"repos/{repo}/branches/{branch}/protection/required_status_checks",
            "--input",
            "-",
        ],
        input=body,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise LintGateError(f"Could not update required checks:\n{proc.stderr.strip()}")


def strict_setting(repo: str, branch: str) -> bool:
    out = run(["gh", "api", f"repos/{repo}/branches/{branch}/protection/required_status_checks"])
    return bool(json.loads(out).get("strict", False))


def lint_backlog_is_clean(repo_path: Path, job: JobName = "lint") -> tuple[bool, str]:
    """Run `job`'s own check command. Turning the gate on over a dirty backlog
    blocks every open PR with findings unrelated to their changes."""
    proc = subprocess.run(
        BACKLOG_CHECK_CMD[job],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


# -------------------------------------------------------------------- driver


def read_ci(repo_path: Path) -> tuple[Path, str]:
    path = repo_path / CI_FILE
    if not path.is_file():
        raise LintGateError(f"{path} not found. Is {repo_path} an onboarded repo?")
    return path, path.read_text()


def cmd_status(args: argparse.Namespace) -> int:
    repo_path = Path(args.repo_path).resolve()
    _, text = read_ci(repo_path)
    repo = args.repo or detect_repo(repo_path)
    branch = args.branch or default_branch(repo)
    state = State(
        has_continue_on_error(text, args.job), args.job in required_contexts(repo, branch), args.job
    )

    print(f"repo:              {repo} ({branch})")
    print(f"job:               {args.job}")
    print(f"continue-on-error: {'present' if state.has_line else 'absent'}")
    print(f"`{args.job}` required:   {'yes' if state.lint_required else 'no'}")
    print(f"state:             {state.name}")
    print(f"  {state.detail}")
    return 0 if state.name != "INCONSISTENT" else 1


def cmd_toggle(args: argparse.Namespace, *, turn_on: bool) -> int:
    repo_path = Path(args.repo_path).resolve()
    path, text = read_ci(repo_path)
    repo = args.repo or detect_repo(repo_path)
    branch = args.branch or default_branch(repo)
    contexts = required_contexts(repo, branch)
    before = State(has_continue_on_error(text, args.job), args.job in contexts, args.job)

    if before.name == ("ON" if turn_on else "OFF"):
        print(f"Already {before.name}. Nothing to do.")
        return 0

    if turn_on and not args.skip_backlog_check:
        clean, output = lint_backlog_is_clean(repo_path, args.job)
        if not clean:
            print(
                f"Refusing to enforce {args.job}: this repo still has findings.\n"
                "\n"
                "Turning the gate on now would block every open pull request with errors\n"
                "unrelated to its changes -- the exact problem the staged rollout exists to\n"
                f"avoid. Clear them in a dedicated {args.job}-only PR first, then run this again.\n"
                "\n"
                f"{output}\n",
                file=sys.stderr,
            )
            return 1

    off_comment = (
        args.off_comment if args.off_comment is not None else default_off_comment(args.job)
    )

    if turn_on:
        new_text = strip_continue_on_error(text, args.job)
        new_contexts = contexts + [args.job] if args.job not in contexts else contexts
    else:
        new_text = add_continue_on_error(text, off_comment, args.job)
        new_contexts = [c for c in contexts if c != args.job]

    if args.dry_run:
        print(f"[dry-run] {path}: would {'remove' if turn_on else 'add'} continue-on-error")
        print(f"[dry-run] required checks: {contexts} -> {new_contexts}")
        return 0

    path.write_text(new_text)
    print(f"{path}: continue-on-error {'removed' if turn_on else 'added'}")

    set_required_contexts(repo, branch, new_contexts, strict=strict_setting(repo, branch))
    print(f"required checks: {contexts} -> {new_contexts}")

    print(
        "\nBoth halves changed. The file edit is UNCOMMITTED -- commit and open a PR,\n"
        "or the repo and its branch protection disagree until you do."
    )
    return 0


def default_off_comment(job: JobName = "lint") -> str:
    """The comment `off` restores for `job`, explaining the staged rollout."""
    return (
        f"    # Staged: continue-on-error keeps a `{job}` failure from failing the\n"
        f"    # run. Enforce it with `lint_gate.py --job {job} on`.\n"
        "    continue-on-error: true"
    )


# The lint job's off-comment.
DEFAULT_OFF_COMMENT = default_off_comment("lint")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo-path", default=".", help="Path to the target repo (default: cwd)")
    parser.add_argument("--repo", help="owner/name (default: parsed from origin)")
    parser.add_argument("--branch", help="Protected branch (default: the repo's default branch)")
    parser.add_argument(
        "--job",
        default="lint",
        choices=JOBS,
        help="Which staged-rollout job to manage (default: lint)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Report both halves and the derived state")

    for name, help_text in (("on", "Enforce the job"), ("off", "Stage the job back to advisory")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--dry-run", action="store_true", help="Show changes without making them")
        p.add_argument("--off-comment", default=None, help=argparse.SUPPRESS)
        if name == "on":
            p.add_argument(
                "--skip-backlog-check",
                action="store_true",
                help="Enforce even with outstanding findings (you will block open PRs)",
            )

    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            return cmd_status(args)
        return cmd_toggle(args, turn_on=args.command == "on")
    except LintGateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
