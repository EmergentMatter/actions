#!/usr/bin/env python3
"""Install release control into a consuming repo.

Does the mechanical half of docs/onboarding.md: copies the stubs and
templates, adds the config block, creates `changelog.d/`, and creates the
`skip-changelog` label. Everything it does is idempotent -- run it twice and
the second run reports "already correct" rather than duplicating anything.

It deliberately does NOT do three things:

  version_files    proposed, never guessed. The doc's worked example exists
                   specifically to warn against declaring a version string
                   just because you found one: the question is "should this
                   move every time the package version moves?", and only a
                   human knows. Pass --version-file to declare them, or run
                   without and get candidates to choose from.

  your CI          never overwritten. If .github/workflows/ci.yml exists it
                   is left alone; the most this does is tell you whether it
                   needs `workflow_call` added.

  branch protection  the required contexts have to be read off a real pull
                   request run, not predicted -- see docs/onboarding.md,
                   "The name you see is not the name you type". Printed as a
                   next step instead.

What gets copied and under what policy is declared once, in
templates/manifest.toml (see load_manifest below) -- shared with sync.py,
which is what keeps a "managed" template current after onboarding. A
"seed-once" template (currently only ci.yml) is written here if absent and
never touched again by either script.

Stdlib only. GitHub access shells out to `gh`.

    python3 onboard.py --repo-path ../some-repo                 # propose
    python3 onboard.py --repo-path ../some-repo \
        --version-file src/pkg/__init__.py:__version__          # apply
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
CI_DEST = ".github/workflows/ci.yml"
LABEL = "skip-changelog"
LABEL_DESC = "Exempts this PR from the changelog note requirement"
LABEL_COLOR = "cfd3d7"
PVR_PATH = "private-vulnerability-reporting"
PVR_NAME = "private vulnerability reporting"

_VALID_POLICIES = {"managed", "seed-once"}


class OnboardError(RuntimeError):
    pass


@dataclass(frozen=True)
class TemplateEntry:
    source: str  # relative to templates/
    dest: str  # relative to the target repo root
    policy: str  # "managed" | "seed-once"


def load_manifest(path: Path | None = None) -> list[TemplateEntry]:
    """Read templates/manifest.toml -- the one declaration onboard.py and
    sync.py both consume. `path` is resolved against TEMPLATES at call time
    (not at import time), so tests can point it at a fixture directory by
    monkeypatching the module-level TEMPLATES constant."""
    manifest_path = path if path is not None else TEMPLATES / "manifest.toml"
    with manifest_path.open("rb") as f:
        data = tomllib.load(f)
    entries = [
        TemplateEntry(source=raw["source"], dest=raw["dest"], policy=raw["policy"])
        for raw in data.get("template", [])
    ]
    bad = {e.policy for e in entries} - _VALID_POLICIES
    if bad:
        word = "policy" if len(bad) == 1 else "policies"
        raise OnboardError(f"manifest.toml: unknown {word} {sorted(bad)!r}")
    return entries


POINT_RELEASE_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def parse_point_release(tag: str) -> tuple[int, int, int] | None:
    """A `vX.Y.Z` point release's numbers, or None. Deliberately excludes
    the moving `v1` alias (force-moved to the newest release on every cut,
    per .github/RELEASING.md) -- only a point release is an immutable ref."""
    m = POINT_RELEASE_RE.match(tag)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def current_templates_version(actions_repo: Path | None = None) -> str:
    """The actions repo's own version, right now: the highest vX.Y.Z point
    release tagged exactly at HEAD, or the short commit SHA if none is.

    Never the moving `v1` alias, even when it also points at HEAD --a
    stamp naming it would silently stop meaning anything the moment the
    next release force-moves it, so `git show v1:templates/<path>` in
    sync.py would drift to resolve a different, newer commit than the one
    this repo actually synced from.

    `git describe --tags --exact-match`'s tie-break when several tags point
    at the same commit is unspecified -- it depends on ref packing, not on
    anything this code controls, so it must not be trusted to prefer the
    point release over the alias. `git tag --points-at HEAD` enumerates
    every tag there instead, so the choice is made explicitly.
    """
    repo = actions_repo if actions_repo is not None else Path(__file__).resolve().parent.parent
    p = subprocess.run(
        ["git", "tag", "--points-at", "HEAD"], cwd=repo, capture_output=True, text=True
    )
    if p.returncode != 0:
        raise OnboardError(f"could not list tags at HEAD for {repo}: {p.stderr.strip()}")
    candidates = []
    for tag in p.stdout.split():
        version = parse_point_release(tag)
        if version is not None:
            candidates.append((version, tag))
    if candidates:
        return max(candidates)[1]

    p = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True
    )
    if p.returncode != 0:
        raise OnboardError(f"could not resolve a git ref for {repo}: {p.stderr.strip()}")
    return p.stdout.strip()


@dataclass
class Action:
    kind: str  # create | skip | manual
    target: str
    note: str = ""


@dataclass
class Plan:
    repo_path: Path
    package_name: str
    version: str
    actions: list[Action] = field(default_factory=list)
    candidates: list[tuple[str, str]] = field(default_factory=list)
    manifest: list[TemplateEntry] = field(default_factory=list)
    ci_needs_workflow_call: bool = False
    ci_job_names: list[str] = field(default_factory=list)
    ci_is_ours: bool = False


# ------------------------------------------------------------------ detection


def read_pyproject(repo: Path) -> dict:
    p = repo / "pyproject.toml"
    if not p.is_file():
        raise OnboardError(
            f"No pyproject.toml in {repo}. Release control assumes a Python project."
        )
    with p.open("rb") as f:
        return tomllib.load(f)


def parse_ci(text: str) -> tuple[bool, list[str]]:
    """Return (has_workflow_call, job_names) from a CI workflow's text."""
    has_call = re.search(r"^\s{2,4}workflow_call:", text, re.MULTILINE) is not None
    jobs: list[str] = []
    in_jobs = False
    for line in text.split("\n"):
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if in_jobs:
            if re.match(r"^\S", line):  # dedented out of jobs:
                break
            m = re.match(r"^  ([A-Za-z0-9_-]+):\s*(#.*)?$", line)
            if m:
                jobs.append(m.group(1))
    return has_call, jobs


def propose_version_files(repo: Path, version: str) -> list[tuple[str, str]]:
    """Find `SYMBOL = "<current version>"` assignments outside the venv.

    Candidates only. Whether a match SHOULD track the package version is a
    judgement about that repo's data, not something to infer from the fact
    that a version-shaped string exists.
    """
    out: list[tuple[str, str]] = []
    pattern = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*["\']' + re.escape(version) + r'["\']')
    for path in sorted(repo.rglob("*.py")):
        rel = path.relative_to(repo)
        if any(part in {".venv", "venv", "build", "dist", ".git"} for part in rel.parts):
            continue
        try:
            lines = path.read_text(errors="replace").split("\n")
        except OSError:
            continue
        for line in lines:
            m = pattern.match(line.strip())
            if m:
                out.append((str(rel), m.group(1)))
    return out


TOWNCRIER_MARKER = "<!-- towncrier release notes start -->"


def add_workflow_call(text: str) -> str | None:
    """Add `workflow_call:` to an existing CI workflow's triggers.

    Purely additive -- every existing trigger is preserved. Done rather than
    left as a manual step because forgetting it doesn't degrade gracefully:
    `version.yml` reaches this file with `uses: ./.github/workflows/ci.yml`,
    and a workflow that isn't `workflow_call`-able fails at PARSE time, so
    every push to main errors before a single job starts.

    Returns None if it is already there.
    """
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if re.match(r"^on:\s*$", ln)), None)
    if start is None:
        # `on: [push, pull_request]` inline form — too many shapes to rewrite
        # safely, so leave it for a human.
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i].startswith((" ", "\t")):
            end = i
            break
    block = lines[start:end]
    if any(re.match(r"^\s+workflow_call:", ln) for ln in block):
        return None
    while block and not block[-1].strip():
        block.pop()
    block.append("  workflow_call:      # required: version.yml calls this file by reference")
    return "\n".join(lines[:start] + block + [""] + lines[end:])


def insert_marker(text: str) -> str | None:
    """Put the towncrier marker above the existing history.

    Returns None if it is already there. `towncrier build` writes each new
    release directly BELOW this marker and never touches anything beneath,
    so it has to sit above the newest existing entry -- placing it at the
    bottom would bury every future release under the hand-written history.
    """
    if TOWNCRIER_MARKER in text:
        return None
    lines = text.split("\n")
    # First `## ` heading is the newest existing release section; the marker
    # belongs immediately above it. Fall back to just after the H1 for a
    # changelog with no entries yet.
    anchor = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), None)
    if anchor is None:
        anchor = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), -1) + 1
        while anchor < len(lines) and lines[anchor].strip():
            anchor += 1
    block = [TOWNCRIER_MARKER, ""]
    return "\n".join(lines[:anchor] + block + lines[anchor:])


def build_plan(repo: Path, declared: list[str]) -> Plan:
    data = read_pyproject(repo)
    project = data.get("project", {})
    name = project.get("name")
    version = project.get("version")
    if not name:
        raise OnboardError("pyproject.toml has no [project] name.")
    if not version:
        raise OnboardError(
            "pyproject.toml has no [project] version. Release control writes that field, "
            "so it must exist (set it to your current version)."
        )

    plan = Plan(repo, name, version)
    manifest = load_manifest()
    plan.manifest = manifest
    try:
        ci_entry = next(e for e in manifest if e.dest == CI_DEST)
    except StopIteration:
        raise OnboardError(f"manifest.toml has no entry for {CI_DEST}") from None

    for entry in manifest:
        if entry.dest == CI_DEST:
            continue  # handled below -- it gets bespoke treatment, not a plain copy
        target = repo / entry.dest
        if target.exists():
            same = target.read_bytes() == (TEMPLATES / entry.source).read_bytes()
            note = (
                "already present and identical"
                if same
                else "already present, DIFFERS from template -- left alone"
            )
            plan.actions.append(Action("skip", entry.dest, note))
        else:
            plan.actions.append(Action("create", entry.dest, f"from templates/{entry.source}"))

    ci = repo / CI_DEST
    if ci.exists():
        text = ci.read_text(errors="replace")
        has_call, jobs = parse_ci(text)
        plan.ci_needs_workflow_call = not has_call
        plan.ci_job_names = jobs
        if not has_call:
            if add_workflow_call(text) is not None:
                plan.actions.append(
                    Action("create", CI_DEST, "add `workflow_call:` to your triggers (additive)")
                )
            else:
                plan.actions.append(Action("skip", CI_DEST, "your own CI -- left alone"))
                plan.actions.append(
                    Action(
                        "manual",
                        CI_DEST,
                        "add `workflow_call:` under `on:` by hand (inline `on: [...]` form) "
                        "-- version.yml fails at parse time without it",
                    )
                )
        else:
            plan.actions.append(Action("skip", CI_DEST, "your own CI -- left alone"))
    else:
        plan.ci_is_ours = True
        plan.ci_job_names = ["lint", "test", "build"]
        plan.actions.append(
            Action("create", CI_DEST, f"from templates/{ci_entry.source} (repo has no CI)")
        )

    if not (repo / "changelog.d" / ".gitkeep").exists():
        plan.actions.append(Action("create", "changelog.d/.gitkeep", "holds pending notes"))
    else:
        plan.actions.append(Action("skip", "changelog.d/.gitkeep", "already present"))

    changelog = repo / "CHANGELOG.md"
    if not changelog.exists():
        plan.actions.append(Action("create", "CHANGELOG.md", "with the towncrier marker"))
    elif TOWNCRIER_MARKER in changelog.read_text(errors="replace"):
        plan.actions.append(Action("skip", "CHANGELOG.md", "towncrier marker already present"))
    else:
        plan.actions.append(
            Action("create", "CHANGELOG.md", "insert the towncrier marker above existing history")
        )

    has_towncrier = "towncrier" in data.get("tool", {})
    has_emrelease = "em-release" in data.get("tool", {})
    if has_towncrier and has_emrelease:
        plan.actions.append(Action("skip", "pyproject.toml", "config block already present"))
    else:
        plan.actions.append(Action("create", "pyproject.toml", "append the config block"))

    if not declared:
        plan.candidates = propose_version_files(repo, version)

    return plan


# -------------------------------------------------------------------- writing


def render_config_block(version_files: list[str], templates_version: str) -> str:
    snippet = (TEMPLATES / "pyproject-snippet.toml").read_text()
    entries = "\n".join(f'  "{v}",' for v in version_files)
    block = re.sub(
        r"(?ms)^\[tool\.em-release\].*?^version_files\s*=\s*\[.*?^\]",
        f'[tool.em-release]\ntemplates_version = "{templates_version}"\n'
        "version_files = [\n" + entries + "\n]",
        snippet,
    )
    if "[tool.em-release]" not in block:
        block += (
            f'\n[tool.em-release]\ntemplates_version = "{templates_version}"\n'
            "version_files = [\n" + entries + "\n]\n"
        )
    return block


def apply_plan(plan: Plan, version_files: list[str], templates_version: str) -> list[str]:
    done = []
    for action in plan.actions:
        if action.kind != "create":
            continue
        dest = plan.repo_path / action.target
        dest.parent.mkdir(parents=True, exist_ok=True)

        if action.target == "changelog.d/.gitkeep":
            dest.write_text("")
        elif action.target == "CHANGELOG.md":
            if dest.exists():
                updated = insert_marker(dest.read_text())
                if updated is not None:
                    dest.write_text(updated)
            else:
                dest.write_text(f"# Changelog\n\n{TOWNCRIER_MARKER}\n")
        elif action.target == "pyproject.toml":
            existing = dest.read_text()
            sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
            dest.write_text(existing + sep + render_config_block(version_files, templates_version))
        elif action.target == CI_DEST:
            if dest.exists():
                updated = add_workflow_call(dest.read_text())
                if updated is not None:
                    dest.write_text(updated)
            else:
                ci_entry = next(e for e in plan.manifest if e.dest == CI_DEST)
                # copy, not copyfile: copyfile drops permission bits, and
                # templates/changeset.py is 100755 for its shebang (see below).
                shutil.copy(TEMPLATES / ci_entry.source, dest)
        else:
            entry = next(e for e in plan.manifest if e.dest == action.target)
            # `shutil.copy` preserves the mode; `copyfile` does not. changeset.py
            # carries a shebang and is 100755 in templates/, and a copy that lands
            # non-executable trips ruff's EXE001 in every repo onboarded -- which
            # is exactly how it reached both consumers before this was fixed.
            shutil.copy(TEMPLATES / entry.source, dest)
        done.append(action.target)
    return done


def ensure_label(repo_slug: str, *, dry_run: bool) -> str:
    code, _, _ = _gh(["label", "list", "--repo", repo_slug, "--search", LABEL, "--json", "name"])
    if code != 0:
        return f"could not check labels on {repo_slug}"
    if dry_run:
        return f"would ensure label `{LABEL}` exists"
    code, _, err = _gh(
        [
            "label", "create", LABEL,
            "--repo", repo_slug,
            "--description", LABEL_DESC,
            "--color", LABEL_COLOR,
            "--force",
        ]
    )
    return f"label `{LABEL}` ready" if code == 0 else f"label create failed: {err.strip()}"


def repo_visibility(repo_slug: str) -> str | None:
    """"public" / "private" / "internal", or None if it couldn't be read."""
    code, out, _ = _gh(["api", f"repos/{repo_slug}", "-q", ".visibility"])
    return out.strip() if code == 0 else None


def ensure_pvr(repo_slug: str, visibility: str | None, *, dry_run: bool) -> str:
    """Enable GitHub's private vulnerability reporting.

    templates/SECURITY.md tells a researcher to use it on whatever repo
    they found the bug in, but it's a per-repo setting nothing turns on by
    default -- checked live: on for EmergentMatter/actions, off on every
    other repo in the org, and there's no org-level default enabling it
    for new repos. Without this, onboarding installs a doc pointing at a
    button that doesn't exist.

    Public-repo-only: the endpoint 404s on a private repo, and PVR is a
    public-repo feature outright. Most repos are onboarded private and go
    public later, so "skipped, repo is private" is the expected path here,
    not a failure -- see print_next_steps for the reminder to flip it on
    when that happens.
    """
    if visibility != "public":
        if visibility is None:
            return f"could not check visibility of {repo_slug}"
        return f"{PVR_NAME} skipped -- repo is private (enable once it's public)"

    code, out, _ = _gh(["api", f"repos/{repo_slug}/{PVR_PATH}", "-q", ".enabled"])
    if code != 0:
        return f"could not check {PVR_NAME} on {repo_slug}"
    if out.strip() == "true":
        return f"{PVR_NAME} already enabled"
    if dry_run:
        return f"would enable {PVR_NAME}"

    code, _, err = _gh(["api", "-X", "PUT", f"repos/{repo_slug}/{PVR_PATH}"])
    return f"{PVR_NAME} enabled" if code == 0 else f"{PVR_NAME} enable failed: {err.strip()}"


def _gh(args: list[str]) -> tuple[int, str, str]:
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def detect_slug(repo: Path) -> str | None:
    p = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=repo, capture_output=True, text=True
    )
    if p.returncode != 0:
        return None
    m = re.search(r"[/:]([^/:]+/[^/]+?)(?:\.git)?$", p.stdout.strip())
    return m.group(1) if m else None


# --------------------------------------------------------------------- output


def print_plan(plan: Plan) -> None:
    print(f"repo:     {plan.repo_path}")
    print(f"package:  {plan.package_name} {plan.version}")
    print()
    for a in plan.actions:
        mark = {"create": "create", "skip": "  ok ", "manual": " TODO"}[a.kind]
        print(f"  {mark}  {a.target:<42} {a.note}")


def print_next_steps(plan: Plan, version_files: list[str], *, private_repo: bool = False) -> None:
    contexts = [*plan.ci_job_names, "changelog"]
    if plan.ci_is_ours:
        contexts = ["test", "build", "changelog"]  # lint is opt-in later
    print()
    print("Next, in order:")
    n = 1
    if plan.ci_needs_workflow_call:
        print(f"  {n}. Add `workflow_call:` under `on:` in {CI_DEST}.")
        n += 1
    print(f"  {n}. Review the config block, especially version_files:")
    for v in version_files:
        print(f"       {v}")
    print("     Each entry moves on every release. Anything with its own")
    print("     independent schedule must NOT be listed.")
    n += 1
    print(f"  {n}. Commit and open the onboarding PR.")
    n += 1
    print(f"  {n}. PROVE THE GATE: the PR must first go RED on the changelog check")
    print("     with 'No changelog note was added'. A green check here means the")
    print("     gate is not wired up, not that you got away with it. Then add a")
    print("     note and confirm green.")
    n += 1
    print(f"  {n}. Set required status checks from that PR's run — `gh pr checks <N>`")
    print(f"     prints contexts. Expected here: {', '.join(contexts)}")
    if plan.ci_is_ours:
        print("     (`lint` is deliberately absent; enable later with lint_gate.py)")
    n += 1
    print(f"  {n}. Verify from outside: fleet_status.py --repo <owner>/<name>")
    if private_repo:
        n += 1
        print(f"  {n}. This repo is private, so {PVR_NAME} was NOT enabled -- SECURITY.md")
        print("     points researchers at a button that doesn't exist yet. Turn it on the")
        print("     day this repo goes public: Settings -> Code security -> Private")
        print(f"     vulnerability reporting, or `gh api -X PUT repos/<owner>/<name>/{PVR_PATH}`.")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-path", required=True)
    ap.add_argument(
        "--version-file",
        action="append",
        default=[],
        metavar="PATH:SYMBOL",
        help="A location whose version must move on every release. Repeatable.",
    )
    ap.add_argument("--no-version-files", action="store_true",
                    help="This repo has no version string outside pyproject.toml")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    repo = Path(args.repo_path).resolve()
    if not (repo / ".git").exists():
        print(f"error: {repo} is not a git repository", file=sys.stderr)
        return 1

    try:
        plan = build_plan(repo, args.version_file)
    except OnboardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print_plan(plan)

    for v in args.version_file:
        if ":" not in v:
            print(f"\nerror: --version-file needs PATH:SYMBOL, got {v!r}", file=sys.stderr)
            return 1
        path, symbol = v.rsplit(":", 1)
        if not (repo / path).is_file():
            print(f"\nerror: --version-file path not found: {path}", file=sys.stderr)
            return 1
        text = (repo / path).read_text(errors="replace")
        if not re.search(rf'^{re.escape(symbol)}\s*=', text, re.MULTILINE):
            print(f"\nerror: no `{symbol} = ...` assignment in {path}", file=sys.stderr)
            return 1

    needs_config = any(a.kind == "create" and a.target == "pyproject.toml" for a in plan.actions)
    if needs_config and not args.version_file and not args.no_version_files:
        print()
        print("Stopping: version_files not declared.")
        print()
        if plan.candidates:
            print("Candidates found (assignments matching the current version):")
            for path, symbol in plan.candidates:
                print(f"    --version-file {path}:{symbol}")
            print()
            print("Do NOT pass all of them reflexively. For each, ask: should this")
            print("move every time the package version moves? A schema or data")
            print("version on its own release schedule must be left off -- the")
            print("release workflow only ever writes what is declared.")
        else:
            print("No candidates found. If the version really only lives in")
            print("pyproject.toml, re-run with --no-version-files.")
        return 2

    if args.dry_run:
        print("\n[dry-run] nothing written")
        print_next_steps(plan, args.version_file)
        return 0

    try:
        templates_version = current_templates_version()
    except OnboardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    written = apply_plan(plan, args.version_file, templates_version)
    print()
    for w in written:
        print(f"  wrote {w}")

    slug = detect_slug(repo)
    visibility = None
    if slug:
        visibility = repo_visibility(slug)
        print(f"  {ensure_label(slug, dry_run=False)}")
        print(f"  {ensure_pvr(slug, visibility, dry_run=False)}")
    else:
        print(f"  could not detect owner/name -- create the `{LABEL}` label by hand")
        print(f"  could not detect owner/name -- enable {PVR_NAME} by hand")

    print_next_steps(plan, args.version_file, private_repo=(visibility == "private"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
