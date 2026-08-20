#!/usr/bin/env python3
"""Turn a consuming repo's lint gate on or off, or report its state.

The gate has TWO halves that must agree, and they live in different places:

  1. `continue-on-error: true` on ci.yml's lint job   -- a file in the repo
  2. `lint` in the branch's required status checks    -- a GitHub setting

Flipping one without the other is not a half-measure, it is a broken state,
and the two broken states fail in opposite directions:

  line removed, context absent  -> lint gates nothing on PRs, but a lint
                                   failure now fails the run and can stall
                                   version.yml's `needs: ci` mid-release
  context added, line present   -> lint gates PRs, but a lint failure still
                                   sails through the release path

`continue-on-error` does NOT stop a lint failure from blocking a PR -- only
whether `lint` is a required context decides that. Verified on a real run:
the job's conclusion is `failure`, the workflow RUN's conclusion is
`success`, and branch protection reads the CHECK RUN, which is `failure`.
See docs/onboarding.md, "Staging the lint rollout".

Stdlib only, like the other scripts here. GitHub access shells out to `gh`,
reusing its auth rather than handling tokens.

Usage, from the target repo's root (or pass --repo-path):

    python3 lint_gate.py status
    python3 lint_gate.py on
    python3 lint_gate.py off
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CI_FILE = ".github/workflows/ci.yml"
LINT_CONTEXT = "lint"

# Marks the comment block that explains the staged rollout. Used to keep the
# comment honest when the line it describes is added or removed.
# Phrases that identify a staged-rollout comment as OURS to replace. The
# second is the pre-v1.1.0 template's wording: repos onboarded before that
# release carry it, and leaving it behind next to "Lint is ENFORCED"
# produces a file that contradicts itself. Only ever add to this list --
# removing an entry orphans the comment in every repo still carrying it.
OFF_COMMENT_SENTINELS = (
    "# STAGED ROLLOUT.",
    "# Staged rollout: non-blocking until the existing lint backlog is",
)
ON_COMMENT = (
    "    # Lint is ENFORCED: this job has no continue-on-error, and `lint` is\n"
    "    # a required status check. To stage it back off, run lint_gate.py off\n"
    "    # -- which restores both halves together. See docs/onboarding.md.\n"
)


class LintGateError(RuntimeError):
    """Anything that should stop the command with a readable message."""


# ---------------------------------------------------------------- pure logic


def find_lint_job_block(text: str) -> tuple[int, int]:
    """Return (start, end) line indices of the `lint:` job block in ci.yml.

    Text-based on purpose: ci.yml carries long explanatory comments that a
    YAML round-trip would silently drop, and this repo is stdlib-only so
    there is no ruamel available to preserve them.
    """
    lines = text.split("\n")
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^  lint:\s*(#.*)?$", line):
            start = i
            break
    if start is None:
        raise LintGateError(
            f"No `lint:` job found in {CI_FILE}. If this repo's CI uses a "
            "different job name, the gate has to be managed by hand -- see "
            "docs/onboarding.md."
        )
    for j in range(start + 1, len(lines)):
        # Next sibling job: exactly two spaces of indent, then a key.
        if re.match(r"^  \S", lines[j]):
            return start, j
    return start, len(lines)


def has_continue_on_error(text: str) -> bool:
    """True if the lint job carries an active `continue-on-error: true`."""
    start, end = find_lint_job_block(text)
    for line in text.split("\n")[start:end]:
        if re.match(r"^\s*continue-on-error:\s*true\s*(#.*)?$", line):
            return True
    return False


def strip_continue_on_error(text: str) -> str:
    """Remove the lint job's continue-on-error line and its rollout comment."""
    lines = text.split("\n")
    start, end = find_lint_job_block(text)

    target = None
    for i in range(start, end):
        if re.match(r"^\s*continue-on-error:\s*true\s*(#.*)?$", lines[i]):
            target = i
            break
    if target is None:
        return text

    # Walk back over the contiguous comment block directly above the line,
    # but only if it is the staged-rollout explanation -- an unrelated
    # comment someone added is theirs, not ours to delete.
    first = target
    while first - 1 >= start and lines[first - 1].lstrip().startswith("#"):
        first -= 1
    owns_comment = any(
        sentinel in lines[i] for i in range(first, target) for sentinel in OFF_COMMENT_SENTINELS
    )
    if not owns_comment:
        first = target

    return "\n".join(lines[:first] + ON_COMMENT.rstrip("\n").split("\n") + lines[target + 1 :])


def add_continue_on_error(text: str, off_comment: str) -> str:
    """Put the continue-on-error line (and its explanation) back."""
    if has_continue_on_error(text):
        return text
    lines = text.split("\n")
    start, end = find_lint_job_block(text)

    # Drop the "lint is enforced" note if present, then insert before the
    # job's `steps:` key so the flag lands among the job's other settings.
    kept = [
        i
        for i in range(start, end)
        if "# Lint is ENFORCED:" not in lines[i]
        and "# a required status check." not in lines[i]
        and "# -- which restores both halves together." not in lines[i]
    ]
    block = [lines[i] for i in kept]

    insert_at = None
    for offset, line in enumerate(block):
        if re.match(r"^\s*steps:\s*(#.*)?$", line):
            insert_at = offset
            break
    if insert_at is None:
        raise LintGateError(f"No `steps:` key in the lint job of {CI_FILE}.")

    new_block = block[:insert_at] + off_comment.rstrip("\n").split("\n") + block[insert_at:]
    return "\n".join(lines[:start] + new_block + lines[end:])


@dataclass(frozen=True)
class State:
    has_line: bool
    lint_required: bool

    @property
    def name(self) -> str:
        if self.has_line and not self.lint_required:
            return "OFF"
        if not self.has_line and self.lint_required:
            return "ON"
        return "INCONSISTENT"

    @property
    def detail(self) -> str:
        if self.name == "OFF":
            return "lint runs and is visible, but gates nothing. This is the onboarding default."
        if self.name == "ON":
            return "lint blocks pull requests and the release path."
        if not self.has_line and not self.lint_required:
            return (
                "continue-on-error is gone but `lint` is NOT a required check.\n"
                "  Lint gates nothing on pull requests, yet a lint failure now fails the\n"
                "  whole run and can stall version.yml's `needs: ci` mid-release.\n"
                "  Fix: run `lint_gate.py on` (enforce both) or `off` (stage both back)."
            )
        return (
            "`lint` is a required check while continue-on-error is still set.\n"
            "  Lint blocks pull requests, but a lint failure still sails through the\n"
            "  release path because it does not fail the run.\n"
            "  Fix: run `lint_gate.py on` to remove the line and make both halves agree."
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


def lint_backlog_is_clean(repo_path: Path) -> tuple[bool, str]:
    """Run the repo's own ruff. Turning the gate on over a dirty backlog
    blocks every open PR with findings unrelated to their changes."""
    proc = subprocess.run(
        ["uv", "run", "ruff", "check", "."],
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
    state = State(has_continue_on_error(text), LINT_CONTEXT in required_contexts(repo, branch))

    print(f"repo:              {repo} ({branch})")
    print(f"continue-on-error: {'present' if state.has_line else 'absent'}")
    print(f"`lint` required:   {'yes' if state.lint_required else 'no'}")
    print(f"state:             {state.name}")
    print(f"  {state.detail}")
    return 0 if state.name != "INCONSISTENT" else 1


def cmd_toggle(args: argparse.Namespace, *, turn_on: bool) -> int:
    repo_path = Path(args.repo_path).resolve()
    path, text = read_ci(repo_path)
    repo = args.repo or detect_repo(repo_path)
    branch = args.branch or default_branch(repo)
    contexts = required_contexts(repo, branch)
    before = State(has_continue_on_error(text), LINT_CONTEXT in contexts)

    if before.name == ("ON" if turn_on else "OFF"):
        print(f"Already {before.name}. Nothing to do.")
        return 0

    if turn_on and not args.skip_backlog_check:
        clean, output = lint_backlog_is_clean(repo_path)
        if not clean:
            print(
                "Refusing to enforce lint: this repo still has findings.\n"
                "\n"
                "Turning the gate on now would block every open pull request with errors\n"
                "unrelated to its changes -- the exact problem the staged rollout exists to\n"
                "avoid. Clear them in a dedicated lint-only PR first, then run this again.\n"
                "\n"
                f"{output}\n",
                file=sys.stderr,
            )
            return 1

    if turn_on:
        new_text = strip_continue_on_error(text)
        new_contexts = contexts + [LINT_CONTEXT] if LINT_CONTEXT not in contexts else contexts
    else:
        new_text = add_continue_on_error(text, args.off_comment)
        new_contexts = [c for c in contexts if c != LINT_CONTEXT]

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


DEFAULT_OFF_COMMENT = """\
    # STAGED ROLLOUT. `continue-on-error` does NOT make lint non-blocking
    # on pull requests -- only leaving `lint` out of the required status
    # checks does that. What this line buys is that a lint failure doesn't
    # fail the whole run, so it can't stall version.yml's `needs: ci`
    # mid-rollout. Managed by scripts/lint_gate.py; see docs/onboarding.md.
    continue-on-error: true"""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo-path", default=".", help="Path to the target repo (default: cwd)")
    parser.add_argument("--repo", help="owner/name (default: parsed from origin)")
    parser.add_argument("--branch", help="Protected branch (default: the repo's default branch)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Report both halves and the derived state")

    for name, help_text in (("on", "Enforce lint"), ("off", "Stage lint back to advisory")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--dry-run", action="store_true", help="Show changes without making them")
        p.add_argument("--off-comment", default=DEFAULT_OFF_COMMENT, help=argparse.SUPPRESS)
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
