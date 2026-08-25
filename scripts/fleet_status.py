#!/usr/bin/env python3
"""Report the release-control state of every repo pinned to this one.

Onboarding happens once per repo. Drift accumulates forever. `templates/`
is COPIED at onboarding and never re-synced, so every consumer's copy
diverges from the day it lands. Nothing else watches that.

`templates_version` in a repo's `[tool.em-release]` block turns "this
repo's copy differs" from a guess into a fact. onboard.py writes it and
sync.py updates it. A stale stamp means an old copy. A current stamp plus
a diff means a deliberate local edit. No stamp at all means the repo
predates provenance tracking. See `templates` and `stamp` below.

Checks, per repo:

  gate            the lint gate's two halves agree (see lint_gate.py)
  format_gate     same, for the `format` job
  typecheck_gate  same, for the `typecheck` job
  stub        the changelog stub uses the composite action, not the
              `workflow_call` path removed in v1.1.0. A repo still on
              that path is BROKEN right now, not merely stale.
  pins        no action pinned to a version targeting Node 20
  contexts    required status checks look like a recognised configuration
  naming      ci.yml declares `name:`, so checks read `CI / lint` rather
              than `.github/workflows/ci.yml / lint`
  templates   every `managed` file in templates/manifest.toml matches its
              live copy. (`seed-once` files, e.g. ci.yml and ruff.toml,
              are skipped: repos legitimately customise those.)
  stamp       the `templates_version` provenance stamp against this repo's
              newest release tag
  security    if SECURITY.md documents private vulnerability reporting,
              the repo actually has it turned on. It's a per-repo setting
              nothing inherits, so a public repo can carry a policy
              promising a route it doesn't have. (Private repos: N/A.)
  tooling     pyproject.toml declares [tool.mypy] and
              [tool.pytest.ini_options] (existence only)
  ruff_config ruff-base.toml present with no ruff.toml to `extend` it, or
              pyproject.toml still has an inline [tool.ruff] section
  ts_job      a `ui/bun.lock` exists but the `ts` job in ci.yml is still
              commented out

Reads everything over the API. No clones. Stdlib only; GitHub access
shells out to `gh`.

    python3 fleet_status.py                    # discover consumers
    python3 fleet_status.py --repo owner/name  # just these
    python3 fleet_status.py --json

Exits non-zero if any repo has a finding, so it works in CI.
"""

from __future__ import annotations

import argparse
import base64
import functools
import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Never runs; gives mypy the real modules for attribute checks below.
    import lint_gate
    import onboard
    import sync
else:
    _spec = importlib.util.spec_from_file_location(
        "lint_gate", Path(__file__).resolve().parent / "lint_gate.py"
    )
    assert _spec is not None and _spec.loader is not None  # always true for a real file path
    lint_gate = importlib.util.module_from_spec(_spec)
    # Must be registered before exec: @dataclass resolves cls.__module__
    # through sys.modules, and a module loaded this way isn't there by
    # default.
    sys.modules["lint_gate"] = lint_gate
    _spec.loader.exec_module(lint_gate)

    # Import templates/manifest.toml's one parser (onboard.py's) rather than
    # redefining TemplateEntry/load_manifest here, the same way sync.py does.
    # A redefinition here could drift from the other two tools' notion of the
    # manifest -- for example, silently defaulting a missing `policy` key to
    # "managed" instead of raising, which would under-report drift on exactly
    # the seed-once files that default is supposed to protect.
    _spec = importlib.util.spec_from_file_location(
        "onboard", Path(__file__).resolve().parent / "onboard.py"
    )
    assert _spec is not None and _spec.loader is not None
    onboard = importlib.util.module_from_spec(_spec)
    sys.modules["onboard"] = onboard
    _spec.loader.exec_module(onboard)

    # Same reasoning for what counts as a *valid* templates_version stamp:
    # sync.py's usable_stamp() is the one place that already knows the two
    # shapes onboard.py can ever write it in (a vX.Y.Z point release, or the
    # short commit SHA current_templates_version() falls back to when HEAD
    # isn't tagged -- every repo synced off an unmerged branch gets this
    # one). Redefining that notion here risks the same drift: a stamp
    # validity check that disagrees with onboard.py would warn "not a
    # recognised release tag" on a SHA stamp onboard.py wrote correctly,
    # permanently, on every repo synced off an unmerged branch.
    _spec = importlib.util.spec_from_file_location(
        "sync", Path(__file__).resolve().parent / "sync.py"
    )
    assert _spec is not None and _spec.loader is not None
    sync = importlib.util.module_from_spec(_spec)
    sys.modules["sync"] = sync
    _spec.loader.exec_module(sync)

TemplateEntry = onboard.TemplateEntry
load_manifest = onboard.load_manifest
usable_stamp = sync.usable_stamp

ACTIONS_REPO = "EmergentMatter/actions"
REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "templates"
CI_FILE = ".github/workflows/ci.yml"
CHANGELOG_STUB = ".github/workflows/changelog-check.yml"
SECURITY_FILE = "SECURITY.md"
BUN_LOCK_FILE = "ui/bun.lock"  # the conventional path; a package elsewhere isn't detected
RUFF_BASE_FILE = "ruff-base.toml"
RUFF_TOML_FILE = "ruff.toml"

# The literal button label templates/SECURITY.md tells a researcher to click.
# Its presence is how we know the policy is *promising* the private-reporting
# route, as opposed to a repo with some other SECURITY.md that never does.
SECURITY_PVR_MARKER = "Report a vulnerability"

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
    """One thing a `check_*` function noticed about a repo."""

    check: str
    severity: str  # broken | warn | info
    message: str


@dataclass
class RepoReport:
    """Every finding for one repo, plus the roll-up severity `render()` and
    `main()` sort and exit on."""

    repo: str
    findings: list[Finding] = field(default_factory=list)
    error: str | None = None

    @property
    def worst(self) -> str | None:
        """The most severe finding's severity, or None if the repo is clean."""
        if self.error:
            return "broken"
        if not self.findings:
            return None
        return min((f.severity for f in self.findings), key=lambda s: SEVERITY_ORDER[s])


# ---------------------------------------------------------------- pure logic


def check_stub(text: str | None) -> list[Finding]:
    """The changelog-check stub uses the composite action, not the
    `workflow_call` path removed in v1.1.0."""
    if text is None:
        return [Finding("stub", "warn", f"no {CHANGELOG_STUB} -- changelog gate not installed")]
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
    """No action pinned to a version whose action.yml targets Node 20."""
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
    """ci.yml declares a top-level `name:`, so checks read `CI / lint`
    rather than falling back to the file path."""
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


def check_security_reporting(security_text: str | None, pvr_status: str) -> list[Finding]:
    """SECURITY.md's promised route only exists if the repo has it turned on.

    templates/SECURITY.md tells a researcher to open the Security tab and
    click "Report a vulnerability". That button only appears when private
    vulnerability reporting is enabled. It's a per-repo setting that
    nothing inherits. Verified live: on for this repo, off for every
    other public repo in the org, with no org default enabling it for new
    ones. A repo can carry a policy promising a route it doesn't have. A
    researcher who follows it and finds no button either gives up or
    discloses publicly.

    `pvr_status` is one of:
      "enabled"         -- the button exists; nothing to report.
      "disabled"        -- the promise is broken; warn.
      "not-applicable"  -- private repo. The feature can't exist there,
                           so this is never a finding, regardless of the
                           text.
      "unknown"         -- the API response was ambiguous. Private repo,
                           no access, and feature-unavailable all look
                           like a 404. Never read this as "disabled":
                           that would be exactly the guess this check
                           exists to replace.
    """
    if security_text is None or SECURITY_PVR_MARKER not in security_text:
        return []
    if pvr_status in ("enabled", "not-applicable"):
        return []
    if pvr_status == "unknown":
        return [
            Finding(
                "security",
                "info",
                "could not determine whether private vulnerability reporting is "
                "enabled (ambiguous response from the API) -- SECURITY.md documents it",
            )
        ]
    return [
        Finding(
            "security",
            "warn",
            "SECURITY.md documents private vulnerability reporting but it is "
            "disabled on this repo; enable it in Settings or re-run onboard.py",
        )
    ]


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


_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _parse_version(tag: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.match(tag)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def _sorted_versions(tags: list[str]) -> list[str]:
    """Point-release tags only, oldest first. Drops the moving `v1` alias."""
    parsed = [(t, _parse_version(t)) for t in tags]
    valid = [(t, v) for t, v in parsed if v is not None]
    valid.sort(key=lambda tv: tv[1])
    return [t for t, _ in valid]


def _stamp_status(stamp: str | None, tags: list[str]) -> str | None:
    """'current', 'stale', or None (missing, a SHA, or a tag we don't recognise).

    Deliberately returns None for a SHA-shaped stamp, not just an
    unrecognised one: a SHA has no position in the tag sequence, so
    "current" vs "stale" isn't knowable from it, and check_templates()
    falls back to a neutral drift message rather than asserting a cause it
    doesn't have evidence for. See check_templates_version() for the fuller
    account of the two shapes a stamp can validly take.
    """
    versions = _sorted_versions(tags)
    if stamp is None or stamp not in versions:
        return None
    return "current" if stamp == versions[-1] else "stale"


def check_templates(
    entries: list[TemplateEntry],
    dest_texts: dict[str, str | None],
    stamp_status: str | None,
) -> list[Finding]:
    """Every `managed` template, compared to its live copy in the target repo.

    `seed-once` templates are skipped entirely -- repos legitimately
    customise their own CI, so a diff there is not a finding.

    Whether a diff means a stale copy or a deliberate edit is known from
    the `stamp` check's verdict: a current stamp plus a diff is a choice
    to surface, not a mistake, so it's reported as info, not warn. A
    stale or unrecognised stamp doesn't establish a cause here -- see the
    `stamp` finding for that.
    """
    out = []
    for entry in entries:
        if entry.policy != "managed":
            continue
        try:
            template_text = (TEMPLATES / entry.source).read_text()
        except OSError:
            out.append(
                Finding(
                    "templates",
                    "broken",
                    f"templates/{entry.source} is declared in manifest.toml but missing "
                    "from this repo -- onboard.py and sync.py will fail on it too",
                )
            )
            continue
        dest_text = dest_texts.get(entry.dest)
        if dest_text is None:
            out.append(Finding("templates", "warn", f"no {entry.dest}; not installed"))
            continue
        if dest_text == template_text:
            continue
        if stamp_status == "current":
            out.append(
                Finding(
                    "templates",
                    "info",
                    f"{entry.dest} differs from templates/{entry.source} -- templates_version "
                    "is current, so this is a deliberate local edit",
                )
            )
        else:
            out.append(
                Finding("templates", "warn", f"{entry.dest} differs from templates/{entry.source}")
            )
    return out


def check_templates_version(stamp: str | None, tags: list[str]) -> list[Finding]:
    """The `templates_version` provenance stamp against this repo's newest tag.

    A stamp onboard.py wrote is one of two shapes (see usable_stamp()): a
    vX.Y.Z point release, or a commit SHA -- the fallback when HEAD wasn't
    tagged at onboarding/sync time. Only the tag form has a position in the
    release sequence to report "N versions behind" against. A SHA is a
    valid, immutable stamp with no such position -- it is NOT unrecognised,
    there is just nothing to say about it, so it's silent rather than
    warned on. Anything that's neither a known tag nor a valid SHA-or-tag
    ref (a moving alias like `v1`, or garbage) still warns -- that's the
    case this check exists for.

    Three distinct states for "no info to report", not two -- collapsing
    "no stamp" into either "up to date" or "stale" would be reporting
    something that isn't known.
    """
    if stamp is None:
        return [
            Finding(
                "stamp",
                "info",
                "no templates_version stamp in pyproject.toml -- onboarded before "
                "provenance tracking existed; staleness can't be checked",
            )
        ]
    versions = _sorted_versions(tags)
    if stamp in versions:
        newest = versions[-1]
        if stamp == newest:
            return []
        behind = len(versions) - 1 - versions.index(stamp)
        plural = "s" if behind != 1 else ""
        message = f"{behind} version{plural} behind ({stamp} -> {newest}); run sync.py"
        return [Finding("stamp", "warn", message)]
    if usable_stamp(stamp) is not None:
        # a valid SHA (or a point release not in our local tag list) -- no position to report
        return []
    return [
        Finding("stamp", "warn", f"templates_version {stamp!r} is not a recognised release tag")
    ]


def check_gate(ci_text: str | None, contexts: list[str] | None) -> list[Finding]:
    """The `lint` job's two staged-rollout halves (see lint_gate.py) agree."""
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


def _check_staged_job_gate(
    job: lint_gate.JobName, check_name: str, ci_text: str | None, contexts: list[str] | None
) -> list[Finding]:
    """Shared body behind check_format_gate and check_typecheck_gate."""
    if ci_text is None or contexts is None:
        return []
    try:
        has_line = lint_gate.has_continue_on_error(ci_text, job)
    except lint_gate.LintGateError:
        # Expected for a repo that hasn't adopted this job into ci.yml yet.
        return [Finding(check_name, "info", f"no `{job}:` job; not yet adopted")]
    state = lint_gate.State(has_line, job in contexts, job)
    if state.name == "INCONSISTENT":
        first = state.detail.strip().split("\n")[0]
        return [Finding(check_name, "broken", f"INCONSISTENT -- {first}")]
    return [Finding(check_name, "info", state.name)]


def check_format_gate(ci_text: str | None, contexts: list[str] | None) -> list[Finding]:
    """The `format` job's two staged-rollout halves agree."""
    return _check_staged_job_gate("format", "format_gate", ci_text, contexts)


def check_typecheck_gate(ci_text: str | None, contexts: list[str] | None) -> list[Finding]:
    """The `typecheck` job's two staged-rollout halves agree."""
    return _check_staged_job_gate("typecheck", "typecheck_gate", ci_text, contexts)


def check_pyproject_tooling(pyproject_text: str | None) -> list[Finding]:
    """Check that pyproject.toml has [tool.mypy] and [tool.pytest.ini_options].

    Existence only, not exact content -- neither tool has ruff's `extend`.
    """
    if pyproject_text is None:
        return [
            Finding("tooling", "warn", "no pyproject.toml -- cannot check for mypy/pytest config")
        ]
    try:
        data = tomllib.loads(pyproject_text)
    except tomllib.TOMLDecodeError:
        return [Finding("tooling", "warn", "pyproject.toml does not parse as TOML")]
    tool = data.get("tool", {})
    out = []
    if "mypy" not in tool:
        out.append(Finding("tooling", "warn", "pyproject.toml has no [tool.mypy] block"))
    if "ini_options" not in tool.get("pytest", {}):
        out.append(
            Finding("tooling", "warn", "pyproject.toml has no [tool.pytest.ini_options] block")
        )
    return out


def check_ruff_config_adoption(
    ruff_base_present: bool, ruff_toml_present: bool, pyproject_text: str | None
) -> list[Finding]:
    """ruff-base.toml is inert without a ruff.toml that `extend`s it -- ruff
    never auto-discovers ruff-base.toml on its own, and sync.py can only
    install the managed ruff-base.toml; it can never create the seed-once
    ruff.toml for a repo onboarded before this system existed. See
    docs/onboarding.md's migration note for the fix."""
    if not ruff_base_present:
        return []
    out = []
    if not ruff_toml_present:
        out.append(
            Finding(
                "ruff_config",
                "warn",
                "ruff-base.toml present but no ruff.toml -- ruff never reads it as-is",
            )
        )
    if pyproject_text is not None:
        try:
            data = tomllib.loads(pyproject_text)
        except tomllib.TOMLDecodeError:
            data = {}
        if "ruff" in data.get("tool", {}):
            out.append(
                Finding(
                    "ruff_config",
                    "warn",
                    "ruff-base.toml present but pyproject.toml still has an inline "
                    "[tool.ruff] section -- move it into ruff.toml",
                )
            )
    return out


def check_ts_job(bun_lock_present: bool, ci_text: str | None) -> list[Finding]:
    """A repo with a Bun package at the conventional `ui/bun.lock` path
    should have uncommented the `ts` job in templates/ci.yml."""
    if not bun_lock_present:
        return []
    if ci_text is not None and re.search(r"^\s*ts:\s*(#.*)?$", ci_text, re.MULTILINE):
        return []
    return [
        Finding(
            "ts_job",
            "info",
            f"{BUN_LOCK_FILE} present but no `ts:` job in ci.yml -- see templates/ci.yml",
        )
    ]


def check_contexts(contexts: list[str] | None) -> list[Finding]:
    """Required status checks look like a recognised configuration."""
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
    *,
    manifest: list[TemplateEntry] | None = None,
    dest_texts: dict[str, str | None] | None = None,
    stamp: str | None = None,
    tags: list[str] | None = None,
    security_text: str | None = None,
    pvr_status: str = "unknown",
    pyproject_text: str | None = None,
    bun_lock_present: bool = False,
    ruff_toml_present: bool = False,
) -> list[Finding]:
    """Run every `check_*` function against one repo's fetched state.

    See the module docstring's "Checks, per repo" list for what each one
    covers; the keyword args here are just those checks' own inputs,
    threaded through in one call so `inspect()` has a single entry point.
    """
    manifest = manifest or []
    dest_texts = dest_texts or {}
    tags = tags or []
    return [
        *check_stub(stub_text),
        *check_workflow_call(ci_text),
        *check_gate(ci_text, contexts),
        *check_format_gate(ci_text, contexts),
        *check_typecheck_gate(ci_text, contexts),
        *check_contexts(contexts),
        *check_verify_wheel(ci_text),
        *check_templates(manifest, dest_texts, _stamp_status(stamp, tags)),
        *check_templates_version(stamp, tags),
        *check_security_reporting(security_text, pvr_status),
        *check_pyproject_tooling(pyproject_text),
        *check_ruff_config_adoption(
            dest_texts.get(RUFF_BASE_FILE) is not None, ruff_toml_present, pyproject_text
        ),
        *check_ts_job(bun_lock_present, ci_text),
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


def fetch_stamp(repo: str) -> str | None:
    text = fetch_file(repo, "pyproject.toml")
    if text is None:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    return data.get("tool", {}).get("em-release", {}).get("templates_version")


def fetch_local_tags() -> list[str]:
    """This repo's own release tags -- local, read-only, no `gh` call.

    Called once in main() and shared across every repo in the sweep, not
    once per repo -- it's the same answer every time.
    """
    p = subprocess.run(
        ["git", "tag", "--list", "v*"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if p.returncode != 0:
        return []
    return [line.strip() for line in p.stdout.splitlines() if line.strip()]


def fetch_private_vuln_reporting(repo: str) -> str:
    """ "enabled" | "disabled" | "not-applicable" (private repo) | "unknown".

    Visibility is checked FIRST, deliberately: the reporting endpoint 404s
    for a private repo, for no access, and for "feature unavailable" alike,
    so a bare 404 from it can't be told apart from "disabled". Reading any
    of those as "disabled" would emit a warn accusing a repo that either
    can't have the feature or that we simply couldn't inspect.
    """
    code, out, _ = gh(["api", f"repos/{repo}", "-q", ".private"])
    if code != 0:
        return "unknown"
    private = out.strip()
    if private == "true":
        return "not-applicable"
    if private != "false":
        return "unknown"
    code, out, _ = gh(["api", f"repos/{repo}/private-vulnerability-reporting", "-q", ".enabled"])
    if code != 0:
        return "unknown"
    enabled = out.strip()
    if enabled == "true":
        return "enabled"
    if enabled == "false":
        return "disabled"
    return "unknown"


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


def inspect(repo: str, manifest: list[TemplateEntry], tags: list[str]) -> RepoReport:
    """Fetch one repo's state over the API and evaluate() it. Never raises:
    any failure becomes the report's own `error`, so one bad repo can't
    sink the sweep."""
    try:
        ci = fetch_file(repo, CI_FILE)
        stub = fetch_file(repo, CHANGELOG_STUB)
        dest_texts = {
            entry.dest: fetch_file(repo, entry.dest)
            for entry in manifest
            if entry.policy == "managed"
        }
        stamp = fetch_stamp(repo)
        contexts = fetch_contexts(repo)
        security = fetch_file(repo, SECURITY_FILE)
        pyproject = fetch_file(repo, "pyproject.toml")
        bun_lock_present = fetch_file(repo, BUN_LOCK_FILE) is not None
        ruff_toml_present = fetch_file(repo, RUFF_TOML_FILE) is not None
        # Only spend the two extra `gh api` calls when there's actually a
        # promise to verify -- most repos won't have SECURITY.md yet.
        pvr_status = (
            fetch_private_vuln_reporting(repo)
            if security is not None and SECURITY_PVR_MARKER in security
            else "unknown"
        )
        return RepoReport(
            repo,
            evaluate(
                ci,
                stub,
                contexts,
                manifest=manifest,
                dest_texts=dest_texts,
                stamp=stamp,
                tags=tags,
                security_text=security,
                pvr_status=pvr_status,
                pyproject_text=pyproject,
                bun_lock_present=bun_lock_present,
                ruff_toml_present=ruff_toml_present,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - one bad repo must not sink the sweep
        return RepoReport(repo, [], error=str(exc))


# -------------------------------------------------------------------- output


MARK = {"broken": "BROKEN", "warn": "warn", "info": "ok", None: "ok"}


def render(reports: list[RepoReport], *, show_info: bool) -> str:
    lines = []
    for r in sorted(reports, key=lambda r: (SEVERITY_ORDER.get(r.worst or "", 3), r.repo)):
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
        manifest = load_manifest()
    except (OSError, tomllib.TOMLDecodeError, KeyError, onboard.OnboardError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        repos = args.repo or discover(args.org)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not repos:
        print("No consuming repos found.")
        return 0

    tags = fetch_local_tags()
    inspect_repo = functools.partial(inspect, manifest=manifest, tags=tags)
    with ThreadPoolExecutor(max_workers=8) as pool:
        reports = list(pool.map(inspect_repo, repos))

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
