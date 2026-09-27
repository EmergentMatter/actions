#!/usr/bin/env python3
"""Publish a release's wheel/sdist to the static index bucket.

Called from the publish job in version.yml / build-release.yml, after
`aws-actions/configure-aws-credentials` has assumed the consumer's own
per-repo role (see CONTRACT.md's publish-target section) -- so this is a
"called from a workflow" script (README.md's "The scripts"): stdlib only,
reads only its command-line arguments and the checked-out dist/, and talks
to AWS by shelling out to the `aws` CLI (already on the runner image), the
same way the maintenance scripts shell out to `gh`. No boto3, no
third-party dependency.

Two things this enforces that a plain `aws s3 cp` loop would not:

1. **Immutable uploads.** A wheel/sdist filename already in the bucket
   with DIFFERENT content is refused outright, before anything else
   uploads -- see sync_dist_files(). A version can't be silently
   re-published out from under anyone who already installed it. The SAME
   content already being there (a retried run) is fine and uploads
   nothing again.
2. **A `simple/<package>/index.html` that lists every release, not just
   this one.** The per-repo role can `ListBucket` only its own prefixes
   (`downloads/<package>/*` and `simple/<package>/*` -- not any other
   package's, and not the bucket root), so this script rebuilds the page
   from the `downloads/<package>/` listing every run, merging this run's
   new files with whatever generate_index_page.py's sha256 metadata says
   is already there -- never from a record of past runs, which nothing
   here keeps.
3. **A `downloads/<package>/<version>/metadata.json` per release,
   written from the wheel's own METADATA** (see write_metadata_file() and
   CONTRACT.md's "metadata.json" section) -- always before the
   `simple/<package>/index.html` write, because that write is what
   triggers the downstream root-index Lambda, and the metadata has to
   already exist by then.
4. **A `downloads/<package>/<version>/notes.md` per release, extracted
   from `CHANGELOG.md` at the release tag** (see write_notes_file() and
   CONTRACT.md's "notes.md" section), also before the index-page write.
   No matching section in the changelog means no file, never an error.

The root `simple/index.html` (the index of indexes) is deliberately never
touched by this publish flow: that page aggregates across every package's
prefix, which no single per-repo role can list (each role's `ListBucket`
condition is scoped to its own `downloads/<package>/*` and
`simple/<package>/*`, not the bucket root or any other package's). This
script's `--root` mode rebuilds that page instead -- an operator-run
command with admin S3 + CloudFront credentials, never invoked from a
publish job. See regenerate_root_index_page() and docs/onboarding.md.

    # The per-repo publish job's own invocation:
    publish_static_index.py --bucket downloads-em-prod-us-east-2 \\
        --package-name emergent-matter-sdm-core --dist-dir dist

    # The admin-run root-index rebuild: the manual fallback for an empty
    # bucket or a failed automatic rebuild (see CONTRACT.md):
    publish_static_index.py --root --bucket downloads-em-prod-us-east-2 \\
        --distribution-id <distribution-id>

    # The admin-run, one-off metadata.json backfill for versions published
    # before that file existed (see CONTRACT.md and backfill_metadata()):
    publish_static_index.py --backfill-metadata --dry-run \\
        --bucket downloads-em-prod-us-east-2

    # The admin-run, one-off notes.md backfill -- reads each version's
    # already-published metadata.json for its GitHub repository, and
    # fetches CHANGELOG.md there via `gh api` (see backfill_notes()):
    publish_static_index.py --backfill-notes --dry-run \\
        --bucket downloads-em-prod-us-east-2
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Never runs; gives mypy the real module for attribute checks below.
    import generate_index_page as gip
else:
    _spec = importlib.util.spec_from_file_location(
        "generate_index_page", Path(__file__).resolve().parent / "generate_index_page.py"
    )
    assert _spec is not None and _spec.loader is not None  # always true for a real file path
    gip = importlib.util.module_from_spec(_spec)
    sys.modules["generate_index_page"] = gip
    _spec.loader.exec_module(gip)

__all__ = [
    "PublishStaticIndexError",
    "ExistingObject",
    "Runner",
    "sync_dist_files",
    "write_metadata_file",
    "write_notes_file",
    "regenerate_index_page",
    "list_package_prefixes",
    "regenerate_root_index_page",
    "invalidate_cloudfront",
    "download_file",
    "backfill_metadata",
    "parse_github_repo",
    "fetch_changelog_at_tag",
    "backfill_notes",
]

# One HTTP-ish call to the `aws` CLI. Tests inject a fake in place of
# run_aws so the immutable-upload and page-merge logic is exercised
# without a real bucket or network access.
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


class PublishStaticIndexError(RuntimeError):
    """Anything that should stop the publish with a readable message."""


def run_aws(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["aws", *argv], capture_output=True, text=True)


def run_gh(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Only `fetch_changelog_at_tag()` (via `--backfill-notes`) uses this.
    Auth comes from whatever the operator running that admin command
    already has -- their own `gh auth login`, or the `GH_TOKEN`/
    `GITHUB_TOKEN` environment variable `gh` already honors -- never a
    token this script reads or handles itself. The same category of tool
    as the maintenance scripts (onboard.py, sync.py, fleet_status.py),
    which also shell out to `gh`; see this file's own module docstring
    for why the ordinary publish flow never does."""
    return subprocess.run(["gh", *argv], capture_output=True, text=True)


@dataclass(frozen=True)
class ExistingObject:
    """What head_object() found already in the bucket for one key."""

    sha256: str | None
    requires_python: str | None


def head_object(run: Runner, bucket: str, key: str) -> ExistingObject | None:
    """The object's recorded metadata, or None if it isn't there.

    A non-zero exit is read as "absent" -- the common case for a new
    release's files -- rather than distinguishing "not found" from a real
    AWS error. That's deliberate, not a shortcut: a genuine outage or
    permissions problem fails LOUDLY at the very next `aws` call this
    script makes (the upload, or the list-objects-v2 in
    regenerate_index_page), so nothing here can mistake an outage for a
    clean "nothing uploaded yet" and silently proceed to overwrite
    anything.
    """
    p = run(["s3api", "head-object", "--bucket", bucket, "--key", key])
    if p.returncode != 0:
        return None
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        raise PublishStaticIndexError(
            f"aws s3api head-object --bucket {bucket} --key {key} returned unparseable JSON"
        ) from exc
    meta = data.get("Metadata", {})
    return ExistingObject(sha256=meta.get("sha256"), requires_python=meta.get("requires-python"))


def list_existing_keys(run: Runner, bucket: str, prefix: str) -> list[str]:
    p = run(["s3api", "list-objects-v2", "--bucket", bucket, "--prefix", prefix])
    if p.returncode != 0:
        raise PublishStaticIndexError(
            f"aws s3api list-objects-v2 --bucket {bucket} --prefix {prefix} failed: "
            f"{p.stderr.strip()}"
        )
    try:
        data = json.loads(p.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise PublishStaticIndexError(
            f"aws s3api list-objects-v2 --bucket {bucket} --prefix {prefix} "
            "returned unparseable JSON"
        ) from exc
    return [obj["Key"] for obj in data.get("Contents", [])]


def _metadata_dict(sha256: str, requires_python: str | None) -> dict[str, str]:
    metadata = {"sha256": sha256}
    if requires_python:
        metadata["requires-python"] = requires_python
    return metadata


def _s3_cp_argv(
    bucket: str, key: str, path: Path, *, content_type: str, metadata: dict[str, str] | None
) -> list[str]:
    """The argv for one `aws s3 cp`, split out of upload_file() so a test
    can hand this exact list to the real `aws` CLI (with `--dryrun`
    appended) and confirm it's accepted, without going through the Runner
    seam at all.

    `--metadata` takes JSON (`aws s3 cp`'s `--metadata` accepts either the
    comma-separated `key=value` shorthand or a JSON object), never the
    shorthand: a `requires-python` value like `>=3.10,<4` contains a comma,
    which the shorthand parser reads as a second `key=value` pair and
    fails on (`Expected: '=', received: 'EOF'`). JSON has no such
    ambiguity. `metadata=None` (the index pages carry none) omits the flag
    entirely rather than passing an empty value, which the CLI rejects the
    same way.
    """
    argv = ["s3", "cp", str(path), f"s3://{bucket}/{key}", "--content-type", content_type]
    if metadata:
        argv += ["--metadata", json.dumps(metadata)]
    return argv


def upload_file(
    run: Runner,
    bucket: str,
    key: str,
    path: Path,
    *,
    content_type: str,
    metadata: dict[str, str] | None,
) -> None:
    """Upload `path` to `s3://{bucket}/{key}`. See _s3_cp_argv() for the
    argv this builds and why."""
    p = run(_s3_cp_argv(bucket, key, path, content_type=content_type, metadata=metadata))
    if p.returncode != 0:
        raise PublishStaticIndexError(f"upload of s3://{bucket}/{key} failed: {p.stderr.strip()}")


def sync_dist_files(
    run: Runner, bucket: str, package_name: str, dist_dir: Path
) -> list[gip.DistFile]:
    """Upload every wheel/sdist under `dist_dir` that isn't already present
    with identical, VERIFIED content. Refuses (rather than overwrites or
    silently trusts) a same-name key whose recorded sha256 disagrees with
    this build's, or whose recorded sha256 is missing entirely -- an
    object this script never uploaded is not a "safe retry" just because a
    key with that name exists."""
    normalized = gip.normalize_name(package_name)
    prefix = f"downloads/{normalized}/"
    uploaded: list[gip.DistFile] = []
    for dist_file in gip.dist_files_in(dist_dir):
        key = prefix + dist_file.filename
        existing = head_object(run, bucket, key)
        if existing is not None:
            if existing.sha256 is None:
                # An object at this key that this script never uploaded (no
                # sha256 metadata) -- e.g. put there by hand, or from before
                # this metadata scheme existed. There's nothing to compare
                # against, so this can't be trusted as "same content,
                # retried run": treat it exactly like a hash mismatch,
                # refuse, and never write this build's hash into the index
                # page for content that was never verified against it.
                raise PublishStaticIndexError(
                    f"s3://{bucket}/{key} already exists but has no recorded sha256 metadata -- "
                    "it was not uploaded by this script, so its content can't be verified "
                    f"against this build's {dist_file.filename} (sha256={dist_file.sha256}). "
                    "Refusing to treat it as a safe retry or to publish a hash for content "
                    "that was never checked."
                )
            if existing.sha256 != dist_file.sha256:
                raise PublishStaticIndexError(
                    f"s3://{bucket}/{key} already exists with sha256={existing.sha256}, but "
                    f"this build's {dist_file.filename} hashes to {dist_file.sha256}. A "
                    "published wheel/sdist is immutable -- refusing to overwrite it. If this "
                    "content is genuinely different, it needs a new version."
                )
            # Same content already there: a retried run. Nothing to upload.
        else:
            upload_file(
                run,
                bucket,
                key,
                dist_dir / dist_file.filename,
                content_type="application/octet-stream",
                metadata=_metadata_dict(dist_file.sha256, dist_file.requires_python),
            )
        uploaded.append(dist_file)
    return uploaded


def _read_wheel_metadata(wheel_path: Path, *, source: str) -> dict[str, Any]:
    """`gip.wheel_metadata_json()`, wrapped so a corrupt/unreadable wheel
    fails through `PublishStaticIndexError` (the one exception type
    `main()` catches and reports with `::error::`) instead of leaking a
    raw zipfile/`ValueError` traceback. `source` is what the error names
    -- see `write_metadata_file()`'s own `source` param for why this
    differs between the ordinary publish flow and a backfill mode's
    tempfile. Shared by `write_metadata_file()` (which needs the whole
    dict) and callers that only need one field, like `main()`'s and
    `backfill_metadata()`'s own use of `["version"]` below, so a wheel is
    never read as anything other than through this one, consistently
    error-wrapped path."""
    try:
        return gip.wheel_metadata_json(wheel_path)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise PublishStaticIndexError(f"{source}: can't read METADATA: {exc}") from exc


def write_metadata_file(
    run: Runner,
    bucket: str,
    package_name: str,
    wheel_path: Path,
    *,
    source: str | None = None,
    dry_run: bool = False,
) -> str:
    """Upload `downloads/<package>/<version>/metadata.json` for one
    release, from `wheel_path`'s own METADATA -- see
    `gip.wheel_metadata_json()` for the fields, and CONTRACT.md's
    "metadata.json" section for the contract this fulfils. `version`
    comes from the METADATA itself, never a filename parse.

    `source` is what error messages name as the origin of this METADATA --
    defaults to `wheel_path` itself, which is exactly right for the
    ordinary publish flow (`main()` below), where `wheel_path` is the real
    `dist/<wheel>` being published. `backfill_metadata()` below passes the
    ORIGINAL `downloads/<package>/<wheel>` S3 key instead, since there
    `wheel_path` is a throwaway local tempfile a download landed in --
    naming that in an error would point an operator at a path that no
    longer exists by the time they read the message.

    Same immutability rule as `sync_dist_files()`: a key already there
    with different content is refused, never silently overwritten;
    identical content (a retried run, or a version this backfill already
    covered) uploads nothing again and is reported as such.

    Callers in the ordinary publish flow (`main()` below) must run this
    BEFORE `regenerate_index_page()`: that call's
    `simple/<package>/index.html` write is what triggers the root-index
    Lambda downstream, and this file has to already exist by the time
    that fires.

    Returns "uploaded", "skipped-identical", or ("would-upload" only with
    `dry_run=True`, used by `backfill_metadata()` below) -- never prints
    or invalidates CloudFront itself, the same division of concerns as
    `upload_file()`.
    """
    source = source if source is not None else str(wheel_path)
    normalized = gip.normalize_name(package_name)
    metadata = _read_wheel_metadata(wheel_path, source=source)
    version = metadata["version"]
    content = json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    sha256 = hashlib.sha256(content).hexdigest()
    key = f"downloads/{normalized}/{version}/metadata.json"

    existing = head_object(run, bucket, key)
    if existing is not None:
        if existing.sha256 is None or existing.sha256 != sha256:
            raise PublishStaticIndexError(
                f"s3://{bucket}/{key} already exists but its content does not match "
                f"{source}'s metadata. A published version's metadata is immutable "
                "-- refusing to overwrite it."
            )
        return "skipped-identical"

    if dry_run:
        return "would-upload"

    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as f:
        f.write(content)
        tmp_path = Path(f.name)
    try:
        upload_file(
            run,
            bucket,
            key,
            tmp_path,
            content_type="application/json",
            metadata=_metadata_dict(sha256, None),
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    return "uploaded"


def write_notes_file(
    run: Runner,
    bucket: str,
    package_name: str,
    version: str,
    changelog_text: str | None,
    *,
    source: str | None = None,
    dry_run: bool = False,
) -> str:
    """Upload `downloads/<package>/<version>/notes.md` for one release,
    extracted from `changelog_text` (the release tag's own `CHANGELOG.md`)
    via `gip.release_notes_for_version()` -- the one pure function shared
    by the ordinary publish flow (`main()` below, given the tag
    checkout's own file) and `backfill_notes()` below (given one fetched
    from GitHub at the release tag).

    `changelog_text` is `None` when there's no `CHANGELOG.md` to read at
    all (an optional file; a repo without one, or a fetch that came back
    empty) -- treated exactly like "no matching section", never an
    error: CONTRACT.md's rule is "no matching section means no file", and
    a wholly absent changelog is the same absence one level up.

    `source` names the origin in an error message, the same convention as
    `write_metadata_file()`'s own `source` param.

    Same immutability rule as `write_metadata_file()`: a key already
    there with different content is refused, never silently overwritten.

    Returns "uploaded", "skipped-identical", "no-section" (nothing to
    upload -- not an error), or ("would-upload" only with `dry_run=True`).
    Callers in the ordinary publish flow must run this BEFORE
    `regenerate_index_page()`, the same requirement `write_metadata_file()`
    has and for the same reason (the index-page write triggers the
    downstream root-index Lambda).
    """
    source = source if source is not None else f"{package_name}=={version}"
    notes = gip.release_notes_for_version(changelog_text, version) if changelog_text else None
    if notes is None:
        return "no-section"
    content = notes.encode("utf-8")
    sha256 = hashlib.sha256(content).hexdigest()
    normalized = gip.normalize_name(package_name)
    key = f"downloads/{normalized}/{version}/notes.md"

    existing = head_object(run, bucket, key)
    if existing is not None:
        if existing.sha256 is None or existing.sha256 != sha256:
            raise PublishStaticIndexError(
                f"s3://{bucket}/{key} already exists but its content does not match "
                f"{source}'s CHANGELOG.md section. A published version's release notes "
                "are immutable -- refusing to overwrite it."
            )
        return "skipped-identical"

    if dry_run:
        return "would-upload"

    with tempfile.NamedTemporaryFile("wb", suffix=".md", delete=False) as f:
        f.write(content)
        tmp_path = Path(f.name)
    try:
        upload_file(
            run,
            bucket,
            key,
            tmp_path,
            content_type="text/markdown; charset=utf-8",
            metadata=_metadata_dict(sha256, None),
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    return "uploaded"


def regenerate_index_page(
    run: Runner, bucket: str, package_name: str, new_files: list[gip.DistFile]
) -> str:
    """Rebuild and upload `simple/<package>/index.html` from the bucket's
    own listing plus `new_files`. Returns the CloudFront invalidation path
    for the caller to invalidate (this script never calls CloudFront
    itself -- see the workflow step that calls this)."""
    normalized = gip.normalize_name(package_name)
    prefix = f"downloads/{normalized}/"
    by_name = {f.filename: f for f in new_files}
    for key in list_existing_keys(run, bucket, prefix):
        filename = key[len(prefix) :]
        if not filename or filename in by_name:
            continue
        existing = head_object(run, bucket, key)
        if existing is None or existing.sha256 is None:
            raise PublishStaticIndexError(
                f"s3://{bucket}/{key} has no recorded sha256 metadata -- it was not uploaded "
                "by this script. Refusing to list it on the index page with a fabricated hash."
            )
        by_name[filename] = gip.DistFile(filename, existing.sha256, existing.requires_python)

    page = gip.render_index(package_name, list(by_name.values()))
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(page)
        page_path = Path(f.name)
    try:
        upload_file(
            run,
            bucket,
            f"simple/{normalized}/index.html",
            page_path,
            content_type="text/html",
            metadata=None,
        )
    finally:
        page_path.unlink(missing_ok=True)
    return f"/simple/{normalized}/*"


def list_package_prefixes(run: Runner, bucket: str) -> list[str]:
    """Every package this bucket's static index currently serves: the
    top-level directory names under `simple/`, sorted.

    `--delimiter /` is what makes `list-objects-v2` return one
    `CommonPrefixes` entry per package directory (`simple/<name>/`)
    instead of every object beneath every one of them, and it's the
    listing that needs `s3:ListBucket` on the `simple/` prefix UNSCOPED by
    package -- no per-repo publish role has that (see the module
    docstring), which is why this, and regenerate_root_index_page() below,
    are admin-run operations, never called from a publish job.
    """
    p = run(
        ["s3api", "list-objects-v2", "--bucket", bucket, "--prefix", "simple/", "--delimiter", "/"]
    )
    if p.returncode != 0:
        raise PublishStaticIndexError(
            f"aws s3api list-objects-v2 --bucket {bucket} --prefix simple/ --delimiter / "
            f"failed: {p.stderr.strip()}"
        )
    try:
        data = json.loads(p.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise PublishStaticIndexError(
            f"aws s3api list-objects-v2 --bucket {bucket} --prefix simple/ --delimiter / "
            "returned unparseable JSON"
        ) from exc
    names = []
    for entry in data.get("CommonPrefixes", []):
        prefix = entry.get("Prefix", "")
        name = prefix.removeprefix("simple/").rstrip("/")
        if name:
            names.append(name)
    return sorted(names)


def download_file(run: Runner, bucket: str, key: str, dest: Path) -> None:
    """Download `s3://{bucket}/{key}` to a local path -- the mirror of
    `upload_file()`, used only by `backfill_metadata()` below. The
    ordinary publish flow never reads a dist file back."""
    p = run(["s3", "cp", f"s3://{bucket}/{key}", str(dest)])
    if p.returncode != 0:
        raise PublishStaticIndexError(f"download of s3://{bucket}/{key} failed: {p.stderr.strip()}")


def backfill_metadata(run: Runner, bucket: str, *, dry_run: bool) -> list[tuple[str, str]]:
    """For every package this bucket's static index currently serves,
    download each wheel under `downloads/<package>/` and write its
    `metadata.json` if it's missing -- a one-off catch-up for every
    version published before that file existed (CONTRACT.md's "metadata.json
    backfill"). Admin-run, the same category as `--root`: it needs to list
    every package's `downloads/` prefix, which no per-repo publish role can
    do (see `list_package_prefixes()`).

    Reuses `write_metadata_file()`'s own immutability rule for every
    wheel it finds: a version whose `metadata.json` is already there with
    identical content is reported "skipped-identical", not re-uploaded;
    one that's there with DIFFERENT content still raises -- that's a real
    anomaly a backfill must surface, not paper over.

    Returns `(destination key, status)` for every wheel considered -- the
    `downloads/<package>/<version>/metadata.json` key this call wrote or
    would write, NOT the source wheel key (a previous version of this
    function reported the wheel key, which read as "this wheel was
    uploaded", the wrong file entirely). `status` is one of "uploaded",
    "skipped-identical", or ("would-upload" only with `dry_run=True`).
    Nothing here talks to a terminal directly, so `main()` below is what
    prints this.
    """
    results: list[tuple[str, str]] = []
    for package_name in list_package_prefixes(run, bucket):
        prefix = f"downloads/{package_name}/"
        wheel_keys = [k for k in list_existing_keys(run, bucket, prefix) if k.endswith(".whl")]
        for key in wheel_keys:
            with tempfile.NamedTemporaryFile(suffix=".whl", delete=False) as f:
                tmp_path = Path(f.name)
            try:
                download_file(run, bucket, key, tmp_path)
                version = _read_wheel_metadata(tmp_path, source=key)["version"]
                dest_key = f"downloads/{gip.normalize_name(package_name)}/{version}/metadata.json"
                status = write_metadata_file(
                    run, bucket, package_name, tmp_path, source=key, dry_run=dry_run
                )
            finally:
                tmp_path.unlink(missing_ok=True)
            results.append((dest_key, status))
    return results


_GITHUB_REPO_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


def parse_github_repo(url: str) -> tuple[str, str] | None:
    """`(owner, repo)` from a `https://github.com/<owner>/<repo>` URL --
    the shape `project_urls.Repository` carries in this org's own
    packages -- or `None` if `url` doesn't match that shape at all.
    Tolerates a trailing `.git` or `/`; nothing else is a GitHub project
    URL this function knows how to read."""
    match = _GITHUB_REPO_URL_RE.match(url.strip())
    return (match.group("owner"), match.group("repo")) if match else None


def fetch_changelog_at_tag(gh_run: Runner, owner: str, repo: str, version: str) -> str | None:
    """`CHANGELOG.md`'s raw content in `owner/repo` at tag `v<version>`,
    via `gh api` -- see `run_gh()` for what supplies its credentials.
    `-H "Accept: application/vnd.github.raw+json"` is what makes the API
    return the file's raw bytes directly, instead of a JSON envelope with
    the content base64-encoded inside it.

    Returns `None` if the tag or the file doesn't exist there (a 404) --
    `backfill_notes()` below reports and skips that, per CONTRACT.md's
    rule, rather than treating it as a hard failure that stops the whole
    run. Any other failure (auth, rate limit, network) raises.
    """
    p = gh_run(
        [
            "api",
            "-H",
            "Accept: application/vnd.github.raw+json",
            f"repos/{owner}/{repo}/contents/CHANGELOG.md",
            "-f",
            f"ref=v{version}",
        ]
    )
    if p.returncode != 0:
        if "404" in p.stderr or "Not Found" in p.stderr:
            return None
        raise PublishStaticIndexError(
            f"gh api repos/{owner}/{repo}/contents/CHANGELOG.md?ref=v{version} failed: "
            f"{p.stderr.strip()}"
        )
    return p.stdout


def backfill_notes(
    run: Runner, gh_run: Runner, bucket: str, *, dry_run: bool
) -> list[tuple[str, str]]:
    """For every version already backfilled or published with a
    `metadata.json` (CONTRACT.md's "notes.md backfill"), read
    `project_urls.Repository` from it, fetch `CHANGELOG.md` at tag
    `v<version>` from that GitHub repository, and write
    `downloads/<package>/<version>/notes.md` if it's missing.

    Keyed off `metadata.json` -- never a filename parse, never a second
    wheel download -- because that file is exactly where
    `project_urls.Repository` and the authoritative `version` already
    live (see `write_metadata_file()`); a version with no `metadata.json`
    yet needs `--backfill-metadata` run first, not a second, independent
    way to rediscover the same facts from the wheel again.

    Every precondition failure -- no `metadata.json`, no `Repository` in
    its `project_urls`, an unparseable one, a missing tag, or no
    `CHANGELOG.md` there -- is reported and skipped, never a hard failure
    for the whole run: one bad version must not block every other one.

    Returns `(destination key, status)` for every version considered.
    `status` is one of `write_notes_file()`'s own ("uploaded",
    "skipped-identical", "no-section", "would-upload"), or a
    `"skipped: <reason>"` string for a precondition failure above.
    """
    results: list[tuple[str, str]] = []
    for package_name in list_package_prefixes(run, bucket):
        prefix = f"downloads/{package_name}/"
        metadata_keys = [
            k for k in list_existing_keys(run, bucket, prefix) if k.endswith("/metadata.json")
        ]
        for metadata_key in metadata_keys:
            version = metadata_key.split("/")[-2]
            dest_key = f"downloads/{package_name}/{version}/notes.md"
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
                tmp_path = Path(f.name)
            try:
                download_file(run, bucket, metadata_key, tmp_path)
                metadata = json.loads(tmp_path.read_text(encoding="utf-8"))
            finally:
                tmp_path.unlink(missing_ok=True)

            repository = metadata.get("project_urls", {}).get("Repository")
            if not repository:
                results.append((dest_key, "skipped: no Repository project URL in metadata.json"))
                continue
            parsed = parse_github_repo(repository)
            if parsed is None:
                results.append((dest_key, f"skipped: unrecognized Repository URL {repository!r}"))
                continue
            owner, repo = parsed

            changelog_text = fetch_changelog_at_tag(gh_run, owner, repo, version)
            if changelog_text is None:
                results.append(
                    (dest_key, f"skipped: no CHANGELOG.md at tag v{version} in {owner}/{repo}")
                )
                continue

            status = write_notes_file(
                run,
                bucket,
                package_name,
                version,
                changelog_text,
                source=f"{owner}/{repo}@v{version}",
                dry_run=dry_run,
            )
            results.append((dest_key, status))
    return results


def regenerate_root_index_page(run: Runner, bucket: str) -> str:
    """Rebuild and upload the root `simple/index.html` -- the PEP 503
    index-of-indexes, one link per package this bucket currently serves.

    Admin-run only, by hand, with credentials that can `ListBucket` the
    whole `simple/` prefix: see list_package_prefixes() for why no
    per-repo publish role can do this itself. Returns the CloudFront
    invalidation path for the caller (this function never calls
    CloudFront itself; see invalidate_cloudfront()).
    """
    names = list_package_prefixes(run, bucket)
    page = gip.render_root_index(names)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(page)
        page_path = Path(f.name)
    try:
        upload_file(
            run, bucket, "simple/index.html", page_path, content_type="text/html", metadata=None
        )
    finally:
        page_path.unlink(missing_ok=True)
    return "/simple/*"


def invalidate_cloudfront(run: Runner, distribution_id: str, path: str) -> None:
    p = run(
        ["cloudfront", "create-invalidation", "--distribution-id", distribution_id, "--paths", path]
    )
    if p.returncode != 0:
        raise PublishStaticIndexError(
            f"aws cloudfront create-invalidation --distribution-id {distribution_id} "
            f"--paths {path} failed: {p.stderr.strip()}"
        )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--package-name")
    ap.add_argument("--dist-dir", default="dist", type=Path)
    ap.add_argument(
        "--changelog",
        default="CHANGELOG.md",
        type=Path,
        help=(
            "CHANGELOG.md in the release tag checkout, read for the notes.md section -- "
            "never fetched over the network here (that's --backfill-notes' job). Missing "
            "is not an error: treated exactly like no matching version section, so a repo "
            "with no changelog yet simply publishes no notes.md."
        ),
    )
    ap.add_argument(
        "--root",
        action="store_true",
        help=(
            "Rebuild simple/index.html, the root index of every package this bucket "
            "serves, instead of publishing one package's release. Operator-run by hand "
            "with admin S3 + CloudFront credentials -- no per-repo publish role can do "
            "this (see list_package_prefixes()); never invoked from a publish job. "
            "--package-name and --dist-dir are ignored with this flag."
        ),
    )
    ap.add_argument(
        "--distribution-id",
        default="",
        help=(
            "CloudFront distribution to invalidate after rebuilding the root index. "
            "Only consulted with --root; the per-package flow's own invalidation is "
            "done by the calling workflow step, not this script."
        ),
    )
    ap.add_argument(
        "--backfill-metadata",
        action="store_true",
        help=(
            "One-off: for every package already on this bucket's static index, download "
            "each existing wheel and write downloads/<package>/<version>/metadata.json for "
            "any version published before that file existed. Admin-run by hand, like --root "
            "-- needs to list every package's downloads/ prefix, which no per-repo publish "
            "role can do. --package-name and --dist-dir are ignored with this flag."
        ),
    )
    ap.add_argument(
        "--backfill-notes",
        action="store_true",
        help=(
            "One-off: for every version already on this bucket with a metadata.json, read "
            "its project_urls.Repository, fetch CHANGELOG.md at tag v<version> from that "
            "GitHub repository over `gh api`, and write downloads/<package>/<version>/"
            "notes.md for any version missing one. Admin-run by hand, like "
            "--backfill-metadata (which must run first for a version with no "
            "metadata.json yet) -- --package-name, --dist-dir and --changelog are ignored "
            "with this flag."
        ),
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="With --backfill-metadata or --backfill-notes: report what would be written, "
        "upload nothing.",
    )
    args = ap.parse_args(argv)

    if args.dry_run and not (args.backfill_metadata or args.backfill_notes):
        print(
            "error: --dry-run only means something with --backfill-metadata or --backfill-notes",
            file=sys.stderr,
        )
        return 1

    if args.root:
        try:
            invalidation_path = regenerate_root_index_page(run_aws, args.bucket)
            if args.distribution_id:
                invalidate_cloudfront(run_aws, args.distribution_id, invalidation_path)
        except PublishStaticIndexError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 1
        if args.distribution_id:
            print(f"wrote simple/index.html and invalidated {invalidation_path}")
        else:
            print("wrote simple/index.html (no --distribution-id given, nothing invalidated)")
        return 0

    if args.backfill_metadata:
        try:
            results = backfill_metadata(run_aws, args.bucket, dry_run=args.dry_run)
        except PublishStaticIndexError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 1
        for key, status in results:
            print(f"{status}: {key}")
        uploaded = sum(1 for _, status in results if status in ("uploaded", "would-upload"))
        skipped = sum(1 for _, status in results if status == "skipped-identical")
        verb = "would write" if args.dry_run else "wrote"
        print(f"{verb} {uploaded} metadata.json ({skipped} already present, {len(results)} total)")
        return 0

    if args.backfill_notes:
        try:
            results = backfill_notes(run_aws, run_gh, args.bucket, dry_run=args.dry_run)
        except PublishStaticIndexError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 1
        for key, status in results:
            print(f"{status}: {key}")
        uploaded = sum(1 for _, status in results if status in ("uploaded", "would-upload"))
        verb = "would write" if args.dry_run else "wrote"
        print(f"{verb} {uploaded} notes.md ({len(results)} version(s) considered)")
        return 0

    if not args.package_name:
        print(
            "error: --package-name is required unless --root, --backfill-metadata or "
            "--backfill-notes is given",
            file=sys.stderr,
        )
        return 1
    if not args.dist_dir.is_dir():
        print(f"::error::no such directory: {args.dist_dir}", file=sys.stderr)
        return 1

    try:
        new_files = sync_dist_files(run_aws, args.bucket, args.package_name, args.dist_dir)
        if not new_files:
            print(f"::error::no wheel/sdist in {args.dist_dir}/", file=sys.stderr)
            return 1
        wheel_file = next((f for f in new_files if f.filename.endswith(".whl")), None)
        if wheel_file is None:
            raise PublishStaticIndexError(
                f"no wheel in {args.dist_dir}/ -- can't write metadata.json without one"
            )
        wheel_path = args.dist_dir / wheel_file.filename
        # Both must run before regenerate_index_page(): its simple/<package>/index.html
        # write triggers the root-index Lambda, and both files have to already exist by
        # then.
        write_metadata_file(run_aws, args.bucket, args.package_name, wheel_path)
        version = _read_wheel_metadata(wheel_path, source=str(wheel_path))["version"]
        try:
            changelog_text = args.changelog.read_text(encoding="utf-8")
        except OSError:
            changelog_text = None
        write_notes_file(run_aws, args.bucket, args.package_name, version, changelog_text)
        invalidation_path = regenerate_index_page(
            run_aws, args.bucket, args.package_name, new_files
        )
    except PublishStaticIndexError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    print(f"invalidation-path={invalidation_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
