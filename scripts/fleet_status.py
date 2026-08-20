#!/usr/bin/env python3
"""Report the release-control state of every repo pinned to this one.

Onboarding happens once per repo. Drift accumulates forever -- `templates/`
is COPIED at onboarding and never re-synced, so every consumer's copy
diverges from the day it lands. Nothing else watches that.

Checks, per repo:

  gate        the lint gate's two halves agree (see lint_gate.py)
  stub        the changelog stub uses the composite action, not the
              `workflow_call` path removed in v1.1.0 -- a repo still on
              that path is BROKEN right now, not merely stale
  pins        no action pinned to a version targeting Node 20
  contexts    required status checks look like a recognised configuration
  naming      ci.yml declares `name:`, so checks read `CI / lint` rather
              than `.github/workflows/ci.yml / lint`

Reads everything over the API -- no clones. Stdlib only; GitHub access
shells out to `gh`, reusing its auth.

    python3 fleet_status.py                    # discover consumers
    python3 fleet_status.py --repo owner/name  # just these
    python3 fleet_status.py --json

Exits non-zero if any repo has a finding, so it works in CI.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "lint_gate", Path(__file__).resolve().parent / "lint_gate.py"
)
lint_gate = importlib.util.module_from_spec(_spec)
# Must be registered before exec: @dataclass resolves cls.__module__ through
# sys.modules, and a module loaded this way isn't there by default.
sys.modules["lint_gate"] = lint_gate
_spec.loader.exec_module(lint_gate)

ACTIONS_REPO = "EmergentMatter/actions"
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
CI_FILE = ".github/workflows/ci.yml"
CHANGELOG_STUB = ".github/workflows/changelog-check.yml"
CHANGESET = "scripts/changeset.py"

REMOVED_WORKFLOW_PATH = "actions/.github/workflows/changelog-check.yml@"
COMPOSITE_ACTION_PATH = "actions/changelog-check@"
VERIFY_WHEEL_PATH = "actions/verify-wheel@"

# Pinned action versions whose action.yml declares `runs.using: node20`.
# GitHub force-runs these on Node 24 and warns on every job. Add entries as
# upstream releases move; the comment is the version, the key is the SHA.
NODE20_PINS = {
    "330a01c490aca151604b8cf639adc76d48f6c5d4": "actions/upload-artifact@v5.0.0",
}

SEVERITY_ORDER = {"broken": 0, "warn": 1, "info": 2}


@dataclass
class Finding:
    check: str
    severity: str  # broken | warn | info
    message: str


@dataclass
class RepoReport:
    repo: str
    findings: list[Finding] = field(default_factory=list)
    error: str | None = None

    @property
    def worst(self) -> str | None:
        if self.error:
            return "broken"
        if not self.findings:
            return None
        return min((f.severity for f in self.findings), key=lambda s: SEVERITY_ORDER[s])


# ---------------------------------------------------------------- pure logic


def check_stub(text: str | None) -> list[Finding]:
    if text is None:
        return [
            Finding("stub", "warn", f"no {CHANGELOG_STUB} -- changelog gate not installed")
        ]
    if REMOVED_WORKFLOW_PATH in text:
        return [
            Finding(
                "stub",
                "broken",
                "calls the workflow_call path removed in v1.1.0; this 404s on every PR. "
                "Migrate to `uses: EmergentMatter/actions/changelog-check@v1`.",
            )
        ]
    if COMPOSITE_ACTION_PATH not in text:
        return [
            Finding("stub", "warn", "does not reference EmergentMatter/actions/changelog-check")
        ]
    return []


def check_pins(text: str | None) -> list[Finding]:
    if text is None:
        return []
    out = []
    for sha, label in NODE20_PINS.items():
        if sha in text:
            out.append(
                Finding(
                    "pins",
                    "warn",
                    f"{label} targets Node 20; deprecation warning on every run",
                )
            )
    return out


def check_naming(text: str | None) -> list[Finding]:
    if text is None:
        return [Finding("naming", "warn", f"no {CI_FILE}")]
    if not re.search(r"^name:\s*\S", text, re.MULTILINE):
        return [
            Finding(
                "naming",
                "info",
                "ci.yml has no top-level `name:`; checks display as "
                "`.github/workflows/ci.yml / lint`",
            )
        ]
    return []


def check_workflow_call(text: str | None) -> list[Finding]:
    """ci.yml must be callable by reference or the release path never starts.

    `version.yml` reaches it with `uses: ./.github/workflows/ci.yml`. A
    workflow that isn't `workflow_call`-able fails at PARSE time, so every
    push to main errors before a job runs -- and nothing about the repo
    looks wrong until someone pushes.
    """
    if text is None:
        return []
    if re.search(r"^\s{2,4}workflow_call:", text, re.MULTILINE):
        return []
    return [
        Finding(
            "workflow_call",
            "broken",
            "ci.yml is not workflow_call-able; version.yml fails at parse time "
            "on every push to main",
        )
    ]


def check_verify_wheel(text: str | None) -> list[Finding]:
    """A build job that only runs `uv build` proves less than it looks.

    `uv build` reports success for a wheel containing nothing importable, so
    without the verify step the build stage catches only builds that fail
    outright -- not the "green tests, broken package" case it exists for.
    """
    if text is None or "uv build" not in text:
        return []
    if VERIFY_WHEEL_PATH in text:
        return []
    return [
        Finding(
            "verify",
            "warn",
            "build job runs `uv build` without verify-wheel; a wheel that builds "
            "but installs nothing would pass. Add "
            "`- uses: EmergentMatter/actions/verify-wheel@v1` after `uv build`.",
        )
    ]


def check_changeset(text: str | None) -> list[Finding]:
    """changeset.py is copied, and divergence is always a mistake.

    Unlike ci.yml -- which repos legitimately customise -- every consumer's
    copy of this contributor tool should be identical to the template. A
    difference means a repo is running an old version, silently.
    """
    try:
        template = (TEMPLATES / "changeset.py").read_text()
    except OSError:
        return []
    if text is None:
        return [Finding("changeset", "warn", f"no {CHANGESET}; contributors cannot write notes")]
    if text != template:
        return [
            Finding(
                "changeset",
                "warn",
                f"{CHANGESET} differs from templates/changeset.py -- it is copied at "
                "onboarding and never re-synced, so this repo is on an older version",
            )
        ]
    return []


def check_gate(ci_text: str | None, contexts: list[str] | None) -> list[Finding]:
    if ci_text is None or contexts is None:
        return []
    try:
        has_line = lint_gate.has_continue_on_error(ci_text)
    except lint_gate.LintGateError:
        return [Finding("gate", "info", "no `lint:` job; gate managed by hand")]
    state = lint_gate.State(has_line, lint_gate.LINT_CONTEXT in contexts)
    if state.name == "INCONSISTENT":
        first = state.detail.strip().split("\n")[0]
        return [Finding("gate", "broken", f"INCONSISTENT -- {first}")]
    return [Finding("gate", "info", state.name)]


def check_contexts(contexts: list[str] | None) -> list[Finding]:
    if contexts is None:
        return [Finding("contexts", "warn", "no required status checks on the default branch")]
    expected = {"test", "build", "changelog"}
    missing = expected - set(contexts)
    if missing:
        return [
            Finding("contexts", "warn", f"missing required check(s): {', '.join(sorted(missing))}")
        ]
    return []


def evaluate(
    ci_text: str | None,
    stub_text: str | None,
    contexts: list[str] | None,
    changeset_text: str | None = None,
) -> list[Finding]:
    return [
        *check_stub(stub_text),
        *check_workflow_call(ci_text),
        *check_gate(ci_text, contexts),
        *check_contexts(contexts),
        *check_verify_wheel(ci_text),
        *check_changeset(changeset_text),
        *check_pins(ci_text),
        *check_naming(ci_text),
    ]


# ------------------------------------------------------------------ gh calls


def gh(args: list[str]) -> tuple[int, str, str]:
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def fetch_file(repo: str, path: str) -> str | None:
    code, out, _ = gh(["api", f"repos/{repo}/contents/{path}", "-q", ".content"])
    if code != 0 or not out.strip():
        return None
    try:
        return base64.b64decode(out.strip()).decode("utf-8", errors="replace")
    except Exception:
        return None


def fetch_contexts(repo: str) -> list[str] | None:
    code, out, _ = gh(["api", f"repos/{repo}", "-q", ".default_branch"])
    if code != 0:
        return None
    branch = out.strip()
    code, out, _ = gh(["api", f"repos/{repo}/branches/{branch}/protection/required_status_checks"])
    if code != 0:
        return None
    try:
        return list(json.loads(out).get("contexts", []))
    except json.JSONDecodeError:
        return None


def discover(org: str) -> list[str]:
    code, out, err = gh(
        ["search", "code", "--owner", org, ACTIONS_REPO, "--limit", "100", "--json", "repository"]
    )
    if code != 0:
        raise RuntimeError(f"code search failed: {err.strip()}")
    repos = {r["repository"]["nameWithOwner"] for r in json.loads(out or "[]")}
    repos.discard(ACTIONS_REPO)
    return sorted(repos)


def inspect(repo: str) -> RepoReport:
    try:
        ci = fetch_file(repo, CI_FILE)
        stub = fetch_file(repo, CHANGELOG_STUB)
        changeset = fetch_file(repo, CHANGESET)
        contexts = fetch_contexts(repo)
        return RepoReport(repo, evaluate(ci, stub, contexts, changeset))
    except Exception as exc:  # noqa: BLE001 - one bad repo must not sink the sweep
        return RepoReport(repo, [], error=str(exc))


# -------------------------------------------------------------------- output


MARK = {"broken": "BROKEN", "warn": "warn", "info": "ok", None: "ok"}


def render(reports: list[RepoReport], *, show_info: bool) -> str:
    lines = []
    for r in sorted(reports, key=lambda r: (SEVERITY_ORDER.get(r.worst, 3), r.repo)):
        shown = [f for f in r.findings if show_info or f.severity != "info"]
        lines.append(f"{MARK[r.worst]:>6}  {r.repo}")
        if r.error:
            lines.append(f"          error: {r.error}")
        for f in sorted(shown, key=lambda f: SEVERITY_ORDER[f.severity]):
            lines.append(f"          {f.check:<9} {f.message}")
    broken = sum(1 for r in reports if r.worst == "broken")
    warn = sum(1 for r in reports if r.worst == "warn")
    lines.append("")
    lines.append(f"{len(reports)} repo(s): {broken} broken, {warn} with warnings")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", action="append", help="owner/name; repeatable. Skips discovery.")
    ap.add_argument("--org", default=ACTIONS_REPO.split("/")[0], help="Org to search")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--quiet", action="store_true", help="Hide informational findings")
    args = ap.parse_args(argv)

    try:
        repos = args.repo or discover(args.org)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not repos:
        print("No consuming repos found.")
        return 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        reports = list(pool.map(inspect, repos))

    if args.as_json:
        print(
            json.dumps(
                [
                    {
                        "repo": r.repo,
                        "state": r.worst or "ok",
                        "error": r.error,
                        "findings": [vars(f) for f in r.findings],
                    }
                    for r in reports
                ],
                indent=2,
            )
        )
    else:
        print(render(reports, show_info=not args.quiet))

    return 1 if any(r.worst in ("broken", "warn") for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
