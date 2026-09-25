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
   this one.** The per-repo role can `ListBucket` only its own prefix
   (`downloads/<package>/`), so this script rebuilds the page from that
   listing every run, merging this run's new files with whatever
   generate_index_page.py's sha256 metadata says is already there --
   never from a record of past runs, which nothing here keeps.

The root `simple/index.html` (the index of indexes) is deliberately never
touched here: that page aggregates across every package's prefix, which no
single per-repo role can list. See docs/onboarding.md.

    publish_static_index.py --bucket downloads-em-prod-us-east-2 \\
        --package-name emergent-matter-sdm-core --dist-dir dist
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

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
    "regenerate_index_page",
]

# One HTTP-ish call to the `aws` CLI. Tests inject a fake in place of
# run_aws so the immutable-upload and page-merge logic is exercised
# without a real bucket or network access.
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


class PublishStaticIndexError(RuntimeError):
    """Anything that should stop the publish with a readable message."""


def run_aws(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["aws", *argv], capture_output=True, text=True)


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


def _metadata_arg(sha256: str, requires_python: str | None) -> str:
    metadata = f"sha256={sha256}"
    if requires_python:
        metadata += f",requires-python={requires_python}"
    return metadata


def upload_file(
    run: Runner, bucket: str, key: str, path: Path, *, content_type: str, metadata: str
) -> None:
    p = run(
        [
            "s3",
            "cp",
            str(path),
            f"s3://{bucket}/{key}",
            "--content-type",
            content_type,
            "--metadata",
            metadata,
        ]
    )
    if p.returncode != 0:
        raise PublishStaticIndexError(f"upload of s3://{bucket}/{key} failed: {p.stderr.strip()}")


def sync_dist_files(
    run: Runner, bucket: str, package_name: str, dist_dir: Path
) -> list[gip.DistFile]:
    """Upload every wheel/sdist under `dist_dir` that isn't already present
    with identical content. Refuses (rather than overwrites) a same-name
    key whose recorded sha256 disagrees with this build's."""
    normalized = gip.normalize_name(package_name)
    prefix = f"downloads/{normalized}/"
    uploaded: list[gip.DistFile] = []
    for dist_file in gip.dist_files_in(dist_dir):
        key = prefix + dist_file.filename
        existing = head_object(run, bucket, key)
        if existing is not None:
            if existing.sha256 is not None and existing.sha256 != dist_file.sha256:
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
                metadata=_metadata_arg(dist_file.sha256, dist_file.requires_python),
            )
        uploaded.append(dist_file)
    return uploaded


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
            metadata="",
        )
    finally:
        page_path.unlink(missing_ok=True)
    return f"/simple/{normalized}/*"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--package-name", required=True)
    ap.add_argument("--dist-dir", default="dist", type=Path)
    args = ap.parse_args(argv)

    if not args.dist_dir.is_dir():
        print(f"::error::no such directory: {args.dist_dir}", file=sys.stderr)
        return 1

    try:
        new_files = sync_dist_files(run_aws, args.bucket, args.package_name, args.dist_dir)
        if not new_files:
            print(f"::error::no wheel/sdist in {args.dist_dir}/", file=sys.stderr)
            return 1
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
