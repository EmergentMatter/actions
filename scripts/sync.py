#!/usr/bin/env python3
"""Re-sync a consuming repo's copy of `templates/` after onboarding.

onboard.py copies templates in once. Nothing re-syncs them afterwards, so a
consumer's copy drifts from the day it lands -- that's the gap fleet_status.py
reports and this script closes.

The hard part isn't copying files. It's telling "this repo's copy is just
stale" apart from "this repo deliberately customised this file". A
two-way diff cannot make that distinction: both cases look identical,
since the file simply differs from the current template. This script
uses a three-way compare instead, exactly like git:

    base    = the template as it was at the version this repo last synced
              from. That's `[tool.em-release] templates_version` in its
              pyproject.toml, written by onboard.py and advanced by this
              script, retrieved via `git show <tag>:templates/<path>`.
    ours    = the repo's current file on disk.
    theirs  = the template at HEAD in this actions repo.

    ours vs base | theirs vs base | result
    -------------+----------------+----------------------------------------
    same         | same           | up to date -- no-op
    same         | changed        | clean update -- write silently
    changed      | same           | local edit only -- leave alone, REPORT
    changed      | changed        | real conflict -- three-way merge

Only "managed" templates (see templates/manifest.toml) are touched.
"Seed-once" templates are never re-synced, the same way onboard.py never
overwrites one that already exists.

If the repo has no recorded `templates_version` (onboarded before this
existed), there is no base -- this degrades to a two-way diff and treats
every difference as a decision for a human. It never guesses which side is
right; that guess is exactly the bug this script exists to fix.

Runs entirely against a local clone. Never opens a PR, never pushes,
never talks to GitHub -- this repo is passive (see CONTRACT.md) and so is
this script.

    sync.py --repo-path ../repo --dry-run
    sync.py --repo-path ../repo --theirs          # take upstream on conflict
    sync.py --repo-path ../repo --ours            # keep local on conflict
    sync.py --repo-path ../repo --only .github/CODEOWNERS
    sync.py --repo-path ../repo --json

Exit codes: 0 done, 1 error, 2 decisions pending.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

ACTIONS_REPO = Path(__file__).resolve().parent.parent

if TYPE_CHECKING:
    # This repo ships no installed package (see CLAUDE.md: scripts/ is
    # stdlib-only, run in place, never `pip install`-ed), so a plain
    # `import onboard` has nothing to resolve to at runtime -- hence the
    # importlib.util dynamic load below, which mypy can't see through on
    # its own. This branch is never taken at runtime (TYPE_CHECKING is
    # always False there); it exists only so mypy resolves `onboard.*`
    # against the real module instead of an opaque ModuleType.
    import onboard
else:
    _spec = importlib.util.spec_from_file_location(
        "onboard", Path(__file__).resolve().parent / "onboard.py"
    )
    # Both narrow a real path on disk to a concrete ModuleSpec/loader;
    # spec_from_file_location only returns None for an import kind this
    # isn't (namespace packages, frozen/built-in modules), never a plain
    # file path.
    assert _spec is not None and _spec.loader is not None
    onboard = importlib.util.module_from_spec(_spec)
    sys.modules["onboard"] = onboard
    _spec.loader.exec_module(onboard)


class SyncError(RuntimeError):
    pass


@dataclass
class EntryResult:
    dest: str
    action: str
    detail: str = ""
    pending: bool = False


# ---------------------------------------------------------------- pure-ish IO


def read_stamp(repo: Path) -> str | None:
    """The `templates_version` this repo last synced from, or None if it was
    onboarded before that stamp existed (no base -- see module docstring)."""
    p = repo / "pyproject.toml"
    if not p.is_file():
        raise SyncError(f"No pyproject.toml in {repo}.")
    with p.open("rb") as f:
        data = tomllib.load(f)
    return data.get("tool", {}).get("em-release", {}).get("templates_version")


_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def usable_stamp(stamp: str | None) -> str | None:
    """A stamp is only a trustworthy merge base if it names an immutable
    ref. That means a vX.Y.Z point release, or the short/full commit SHA
    `current_templates_version` falls back to when HEAD isn't tagged; both
    are what onboard.py can ever write (see there).

    Anything else is untrusted entirely, not just deprioritised.
    Concretely, that's the moving `v1` alias (see .github/RELEASING.md),
    or any other tag-shaped stamp that isn't a point release.

    The reason: `git show <ref>:templates/<path>` against a moving ref
    can silently resolve a DIFFERENT, newer commit than the one this repo
    actually synced from. That would make `base` collapse toward `theirs`
    and stop sync.py from ever detecting staleness again -- the exact
    silent-no-op failure this whole tool exists to prevent.

    A wrong base is worse than no base. So an untrusted stamp degrades to
    the no-stamp two-way path (see decide_and_apply) rather than being
    used."""
    if stamp is None:
        return None
    if onboard.parse_point_release(stamp) is not None or _SHA_RE.match(stamp):
        return stamp
    return None


_TEMPLATES_VERSION_RE = re.compile(r'(?m)^templates_version\s*=\s*"[^"]*"\s*$')
_EM_RELEASE_HEADER_RE = re.compile(r"(?m)^\[tool\.em-release\][ \t]*$")


def write_templates_version(repo: Path, version: str) -> None:
    """Advance the `templates_version` stamp, or add it if the repo's
    `[tool.em-release]` block predates that field. That happens when a
    repo was onboarded before this provenance tracking existed. Stamping
    it is still accurate, not a guess: a clean full sync has just
    demonstrably brought the repo to `version`.

    Never creates the `[tool.em-release]` block itself. Its absence means
    the repo was never onboarded at all, and inventing the block would
    fake an onboarding that didn't happen. That case still errors,
    pointing at onboard.py."""
    p = repo / "pyproject.toml"
    text = p.read_text()

    new_text, n = _TEMPLATES_VERSION_RE.subn(f'templates_version = "{version}"', text, count=1)
    if n > 0:
        p.write_text(new_text)
        return

    header = _EM_RELEASE_HEADER_RE.search(text)
    if header is None:
        raise SyncError(
            f"{p} has no [tool.em-release] block -- this repo has never been onboarded "
            "to release control. Run onboard.py first."
        )

    insert_at = header.end()
    new_text = text[:insert_at] + f'\ntemplates_version = "{version}"' + text[insert_at:]
    p.write_text(new_text)


def read_local(repo: Path, dest: str) -> bytes | None:
    p = repo / dest
    return p.read_bytes() if p.is_file() else None


def read_template_at(actions_repo: Path, ref: str, source: str) -> bytes | None:
    """The template's content at `ref` in the actions repo, or None if `ref`
    doesn't resolve or the path didn't exist there -- indistinguishable on
    purpose: either way there is no usable content to compare against."""
    p = subprocess.run(
        ["git", "show", f"{ref}:templates/{source}"], cwd=actions_repo, capture_output=True
    )
    return p.stdout if p.returncode == 0 else None


def three_way_merge(ours: bytes, base: bytes, theirs: bytes) -> tuple[bytes, bool]:
    """`git merge-file --stdout` over three temp files. Returns (content,
    clean). `git merge-file` is plumbing -- it needs no repo of its own."""
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        o, b, t = tdp / "ours", tdp / "base", tdp / "theirs"
        o.write_bytes(ours)
        b.write_bytes(base)
        t.write_bytes(theirs)
        p = subprocess.run(
            ["git", "merge-file", "--stdout", str(o), str(b), str(t)], capture_output=True
        )
        return p.stdout, p.returncode == 0


def _prompt(dest: str, kind: str) -> str:
    """Ask a human. Never called when --dry-run or --ours/--theirs is set --
    those exist specifically so this never has to run in CI."""
    if kind == "Missing":
        msg = f"Missing {dest} -- install from template (t), leave absent (o), or skip (s)? "
    else:
        msg = f"{kind} in {dest} -- keep (o)urs, take (t)heirs, or (s)kip? "
    ans = input(msg).strip().lower()
    return ans[:1] if ans else "s"


# -------------------------------------------------------------------- deciding


def _write(dest_path: Path, content: bytes) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(content)


# kind -> the action reported when the decision is left pending (dry-run, or
# a human answers "skip"). "Missing" is its own kind, not folded into
# "Undecided difference" -- see decide_and_apply: an absent file isn't a
# stale-vs-edited question at all, there's nothing local to compare.
_PENDING_ACTION = {
    "Undecided difference": "diff-pending",
    "Conflict": "conflict-pending",
    "Missing": "absent-pending",
}


def _resolve_undecided(
    dest_path: Path,
    dest: str,
    theirs: bytes,
    detail: str,
    *,
    kind: str,
    dry_run: bool,
    side: str | None,
) -> EntryResult:
    """Shared resolution for a no-base two-way diff, an unresolved
    three-way conflict, and a missing local file: dry-run reports and
    writes nothing; --ours/--theirs decide without prompting; otherwise ask
    a human."""
    pending_action = _PENDING_ACTION[kind]
    # For a missing file there is no "ours" to keep -- side="ours"/choice "o"
    # means "leave it absent", not "keep an existing copy".
    ours_action = "left-absent" if kind == "Missing" else "kept-ours"

    if dry_run:
        return EntryResult(dest, pending_action, detail, pending=True)
    if side == "ours":
        return EntryResult(dest, ours_action, detail)
    if side == "theirs":
        _write(dest_path, theirs)
        return EntryResult(dest, "updated", detail)
    choice = _prompt(dest, kind)
    if choice == "o":
        return EntryResult(dest, ours_action, detail)
    if choice == "t":
        _write(dest_path, theirs)
        return EntryResult(dest, "updated", detail)
    return EntryResult(dest, pending_action, detail, pending=True)


def decide_and_apply(
    repo: Path,
    actions_repo: Path,
    entry: onboard.TemplateEntry,
    *,
    stamp: str | None,
    dry_run: bool,
    side: str | None,
) -> EntryResult:
    dest_path = repo / entry.dest
    ours = read_local(repo, entry.dest)
    theirs = read_template_at(actions_repo, "HEAD", entry.source)
    if theirs is None:
        hint = ""
        if (actions_repo / "templates" / entry.source).exists():
            hint = " (present in the actions repo's working tree but not committed to HEAD)"
        return EntryResult(
            entry.dest, "error", f"template missing at HEAD in the actions repo{hint}"
        )

    if ours is None:
        # Not a stale-vs-edited question -- there's nothing local to compare
        # against a base. Matches fleet_status.py's check_templates wording
        # and severity for the same condition, so the two tools don't
        # describe an incomplete onboarding differently. Never auto-create:
        # that's onboard.py's job, not sync.py's.
        return _resolve_undecided(
            dest_path,
            entry.dest,
            theirs,
            f"no {entry.dest}; not installed",
            kind="Missing",
            dry_run=dry_run,
            side=side,
        )

    base = read_template_at(actions_repo, stamp, entry.source) if stamp else None

    if base is None:
        # No usable base: no recorded stamp, or this entry didn't exist yet
        # at the stamped version. Degrade to a two-way diff -- never guess
        # which side is right.
        if ours == theirs:
            return EntryResult(entry.dest, "up-to-date")
        return _resolve_undecided(
            dest_path,
            entry.dest,
            theirs,
            "no recorded base version -- cannot tell stale copy from local edit",
            kind="Undecided difference",
            dry_run=dry_run,
            side=side,
        )

    ours_changed = ours != base
    theirs_changed = theirs != base

    if not ours_changed and not theirs_changed:
        return EntryResult(entry.dest, "up-to-date")
    if not ours_changed and theirs_changed:
        if not dry_run:
            _write(dest_path, theirs)
        return EntryResult(entry.dest, "would-update" if dry_run else "updated", "clean update")
    if ours_changed and not theirs_changed:
        return EntryResult(entry.dest, "local-edit", "local edit only -- left alone")

    # Both changed: real conflict. Try an automatic three-way merge first --
    # only an actual overlap needs a human.
    merged, clean = three_way_merge(ours, base, theirs)
    if clean:
        if not dry_run:
            _write(dest_path, merged)
        return EntryResult(
            entry.dest, "would-merge" if dry_run else "merged", "clean three-way merge"
        )
    return _resolve_undecided(
        dest_path,
        entry.dest,
        theirs,
        "real conflict -- three-way merge did not resolve cleanly",
        kind="Conflict",
        dry_run=dry_run,
        side=side,
    )


def sync_repo(
    repo: Path, actions_repo: Path, *, dry_run: bool, side: str | None, only: list[str] | None
) -> list[EntryResult]:
    manifest = onboard.load_manifest()
    entries = [e for e in manifest if e.policy == "managed"]
    if only:
        wanted = set(only)
        known = {e.dest for e in entries}
        missing = wanted - known
        if missing:
            raise SyncError(
                f"--only target(s) not found among managed manifest entries: "
                f"{', '.join(sorted(missing))}"
            )
        entries = [e for e in entries if e.dest in wanted]

    stamp = usable_stamp(read_stamp(repo))
    return [
        decide_and_apply(repo, actions_repo, e, stamp=stamp, dry_run=dry_run, side=side)
        for e in entries
    ]


# --------------------------------------------------------------------- output


def print_results(repo: Path, results: list[EntryResult]) -> None:
    print(f"repo: {repo}")
    print()
    for r in results:
        mark = {
            "up-to-date": "  ok ",
            "updated": " sync",
            "would-update": "would",
            "merged": "merge",
            "would-merge": "would",
            "local-edit": " info",
            "kept-ours": " ours",
            "left-absent": " ours",
            "diff-pending": " TODO",
            "conflict-pending": " TODO",
            "absent-pending": " MISS",
            "error": "ERROR",
        }.get(r.action, r.action)
        print(f"  {mark}  {r.dest:<48} {r.action:<17} {r.detail}")
    pending = [r for r in results if r.pending]
    print()
    print(f"{len(results)} managed template(s): {len(pending)} pending decision(s)")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--dry-run", action="store_true")
    side_group = ap.add_mutually_exclusive_group()
    side_group.add_argument("--ours", action="store_true", help="Keep local content on conflict")
    side_group.add_argument("--theirs", action="store_true", help="Take upstream on conflict")
    ap.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="DEST",
        help="Sync only this dest path (relative to the target repo root). Repeatable.",
    )
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    repo = Path(args.repo_path).resolve()
    if not repo.is_dir():
        print(f"error: {repo} is not a directory", file=sys.stderr)
        return 1

    side = "ours" if args.ours else "theirs" if args.theirs else None

    try:
        results = sync_repo(repo, ACTIONS_REPO, dry_run=args.dry_run, side=side, only=args.only)
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    pending = [r for r in results if r.pending]
    errors = [r for r in results if r.action == "error"]

    if args.as_json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print_results(repo, results)

    # A full (non---only), clean or fully-resolved run is what "synced"
    # means -- advance the stamp so the next run's base moves forward too.
    # A partial run (--only) doesn't touch every managed template, so
    # advancing the stamp for the whole repo would claim more than actually
    # happened.
    if not args.dry_run and not pending and not errors and args.only is None and results:
        try:
            version = onboard.current_templates_version(ACTIONS_REPO)
            write_templates_version(repo, version)
            if not args.as_json:
                print(f"\n  templates_version -> {version}")
        except (SyncError, onboard.OnboardError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if errors:
        return 1
    if pending:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
