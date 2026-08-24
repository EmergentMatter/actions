#!/usr/bin/env python3
"""Report the release-control state of every repo pinned to this one.

Onboarding happens once per repo. Drift accumulates forever -- `templates/`
is COPIED at onboarding and never re-synced, so every consumer's copy
diverges from the day it lands. Nothing else watches that.

`templates_version` in a repo's `[tool.em-release]` block (written by
onboard.py, updated by sync.py) turns "this repo's copy differs" from a
guess into a fact: a stale stamp means an old copy, a current stamp plus a
diff means a deliberate local edit, and no stamp at all means the repo
predates provenance tracking. See `templates` and `stamp` below.

Checks, per repo:

  gate        the lint gate's two halves agree (see lint_gate.py)
  stub        the changelog stub uses the composite action, not the
              `workflow_call` path removed in v1.1.0 -- a repo still on
              that path is BROKEN right now, not merely stale
  pins        no action pinned to a version targeting Node 20
  contexts    required status checks look like a recognised configuration
  naming      ci.yml declares `name:`, so checks read `CI / lint` rather
              than `.github/workflows/ci.yml / lint`
  templates   every `managed` file in templates/manifest.toml matches its
              live copy (`seed-once` files, e.g. ci.yml, are skipped --
              repos legitimately customise those)
  stamp       the `templates_version` provenance stamp against this repo's
              newest release tag

Reads everything over the API -- no clones. Stdlib only; GitHub access
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

_spec = importlib.util.spec_from_file_location(
    "lint_gate", Path(__file__).resolve().parent / "lint_gate.py"
)
lint_gate = importlib.util.module_from_spec(_spec)
# Must be registered before exec: @dataclass resolves cls.__module__ through
# sys.modules, and a module loaded this way isn't there by default.
sys.modules["lint_gate"] = lint_gate
_spec.loader.exec_module(lint_gate)

ACTIONS_REPO = "EmergentMatter/actions"
REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "templates"
MANIFEST_PATH = TEMPLATES / "manifest.toml"
CI_FILE = ".github/workflows/ci.yml"
CHANGELOG_STUB = ".github/workflows/changelog-check.yml"

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


@dataclass
class TemplateEntry:
    source: str  # relative to templates/, may be nested
    dest: str  # relative to the target repo root
    policy: str  # "managed" | "seed-once"


def load_manifest(path: Path = MANIFEST_PATH) -> list[TemplateEntry]:
    """Raises if the manifest is missing, unreadable, or malformed.

    templates/manifest.toml is a tracked file, and this script only ever
    runs from a clone of this repo -- its absence means something is
    broken, not "zero templates". Treating a missing/malformed manifest as
    empty would make every `templates` and `stamp` finding silently vanish
    while the sweep still exits 0, which is exactly the failure mode this
    system exists to catch.
    """
    if not path.is_file():
        raise FileNotFoundError(f"manifest not found: {path}")
    data = tomllib.loads(path.read_text())
    return [
        TemplateEntry(t["source"], t["dest"], t.get("policy", "managed"))
        for t in data.get("template", [])
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
    """'current', 'stale', or None (missing, or not a tag we recognise)."""
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

    `seed-once` templates (just ci.yml today) are skipped entirely -- repos
    legitimately customise CI, so a diff there is not a finding.

    Whether a diff means "old copy" or "deliberate edit" used to be a
    guess (see the old changeset-only check this replaced). Now it's known
    from the `stamp` check's verdict: a current stamp plus a diff is a
    choice to surface, not a mistake, so it's reported as info, not warn.
    A stale or unrecognised stamp doesn't establish a cause here -- see the
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

    Three distinct states, not two -- collapsing "no stamp" into either
    "up to date" or "stale" would be reporting something that isn't known.
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
    if not versions:
        return []
    if stamp not in versions:
        return [
            Finding("stamp", "warn", f"templates_version {stamp!r} is not a recognised release tag")
        ]
    newest = versions[-1]
    if stamp == newest:
        return []
    behind = len(versions) - 1 - versions.index(stamp)
    plural = "s" if behind != 1 else ""
    message = f"{behind} version{plural} behind ({stamp} -> {newest}); run sync.py"
    return [Finding("stamp", "warn", message)]


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
    *,
    manifest: list[TemplateEntry] | None = None,
    dest_texts: dict[str, str | None] | None = None,
    stamp: str | None = None,
    tags: list[str] | None = None,
) -> list[Finding]:
    manifest = manifest or []
    dest_texts = dest_texts or {}
    tags = tags or []
    return [
        *check_stub(stub_text),
        *check_workflow_call(ci_text),
        *check_gate(ci_text, contexts),
        *check_contexts(contexts),
        *check_verify_wheel(ci_text),
        *check_templates(manifest, dest_texts, _stamp_status(stamp, tags)),
        *check_templates_version(stamp, tags),
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
            ),
        )
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
        manifest = load_manifest()
    except (OSError, tomllib.TOMLDecodeError) as exc:
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
