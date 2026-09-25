"""Tests for scripts/publish_static_index.py.

No real bucket, no network, no boto3: every `aws` call goes through a fake
Runner that answers from an in-memory dict standing in for bucket state.
That's the seam the script itself is built around (see its Runner type
alias), specifically so the immutable-upload and index-merge logic can be
exercised without AWS credentials.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish_static_index.py"
spec = importlib.util.spec_from_file_location("publish_static_index", SCRIPT)
psi = importlib.util.module_from_spec(spec)
sys.modules["publish_static_index"] = psi
spec.loader.exec_module(psi)


class FakeBucket:
    """A tiny in-memory stand-in for the subset of S3 this script calls,
    driven the same way the real `aws` CLI would be: one shelled-out
    argv list per call, JSON on stdout."""

    def __init__(self):
        # key -> {"metadata": {...}}
        self.objects: dict[str, dict] = {}
        self.calls: list[list[str]] = []

    def put(self, key: str, *, sha256: str, requires_python: str | None = None) -> None:
        meta = {"sha256": sha256}
        if requires_python:
            meta["requires-python"] = requires_python
        self.objects[key] = {"metadata": meta}

    def run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[:2] == ["s3api", "head-object"]:
            key = argv[argv.index("--key") + 1]
            obj = self.objects.get(key)
            if obj is None:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Not Found")
            return subprocess.CompletedProcess(
                argv, 0, stdout=json.dumps({"Metadata": obj["metadata"]}), stderr=""
            )
        if argv[:2] == ["s3api", "list-objects-v2"]:
            prefix = argv[argv.index("--prefix") + 1]
            contents = [{"Key": k} for k in self.objects if k.startswith(prefix)]
            return subprocess.CompletedProcess(
                argv, 0, stdout=json.dumps({"Contents": contents}), stderr=""
            )
        if argv[:2] == ["s3", "cp"]:
            # argv: s3 cp <local> s3://bucket/<key> --content-type T --metadata M
            local_path = Path(argv[2])
            dest = argv[3]
            key = dest.split("/", 3)[-1]
            metadata_arg = argv[argv.index("--metadata") + 1]
            meta = dict(pair.split("=", 1) for pair in metadata_arg.split(",") if pair)
            # Read NOW, at call time: publish_static_index.py deletes its
            # own temp file (the index page) right after this call returns.
            self.objects[key] = {"metadata": meta, "content": local_path.read_bytes()}
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        raise AssertionError(f"FakeBucket got an unexpected aws call: {argv}")


@pytest.fixture
def dist_dir(tmp_path):
    d = tmp_path / "dist"
    d.mkdir()
    (d / "pkg-1.0.0-py3-none-any.whl").write_bytes(b"wheel bytes")
    (d / "pkg-1.0.0.tar.gz").write_bytes(b"sdist bytes")
    return d


# ------------------------------------------------------------- sync_dist_files


def test_sync_dist_files_uploads_new_files(dist_dir):
    bucket = FakeBucket()
    uploaded = psi.sync_dist_files(bucket.run, "my-bucket", "pkg", dist_dir)
    assert {f.filename for f in uploaded} == {
        "pkg-1.0.0-py3-none-any.whl",
        "pkg-1.0.0.tar.gz",
    }
    assert "downloads/pkg/pkg-1.0.0-py3-none-any.whl" in bucket.objects
    assert "downloads/pkg/pkg-1.0.0.tar.gz" in bucket.objects


def test_sync_dist_files_skips_a_key_already_uploaded_with_identical_content(dist_dir):
    bucket = FakeBucket()
    first = psi.sync_dist_files(bucket.run, "my-bucket", "pkg", dist_dir)
    calls_after_first = len(bucket.calls)

    second = psi.sync_dist_files(bucket.run, "my-bucket", "pkg", dist_dir)
    assert second == first
    # Every call this time was a head-object check -- no re-upload.
    assert all(c[:2] == ["s3api", "head-object"] for c in bucket.calls[calls_after_first:])


def test_sync_dist_files_refuses_to_overwrite_different_content(dist_dir):
    bucket = FakeBucket()
    bucket.put("downloads/pkg/pkg-1.0.0-py3-none-any.whl", sha256="deadbeef" * 8)
    with pytest.raises(psi.PublishStaticIndexError, match="immutable"):
        psi.sync_dist_files(bucket.run, "my-bucket", "pkg", dist_dir)


def test_sync_dist_files_normalizes_the_package_name_in_the_prefix(dist_dir):
    bucket = FakeBucket()
    psi.sync_dist_files(bucket.run, "my-bucket", "Emergent_Matter.SDM-Core", dist_dir)
    assert any(k.startswith("downloads/emergent-matter-sdm-core/") for k in bucket.objects)


# --------------------------------------------------------- regenerate_index_page


def test_regenerate_index_page_includes_new_and_previously_published_files(dist_dir):
    bucket = FakeBucket()
    # A file from an earlier release, already in the bucket with recorded
    # metadata -- must survive into the regenerated page even though this
    # run never touches it directly.
    bucket.put("downloads/pkg/pkg-0.9.0-py3-none-any.whl", sha256="c" * 64)

    new_files = psi.sync_dist_files(bucket.run, "my-bucket", "pkg", dist_dir)
    path = psi.regenerate_index_page(bucket.run, "my-bucket", "pkg", new_files)

    assert path == "/simple/pkg/*"
    page = bucket.objects["simple/pkg/index.html"]
    uploaded_page_text = page["content"].decode()
    assert "pkg-0.9.0-py3-none-any.whl" in uploaded_page_text
    assert "pkg-1.0.0-py3-none-any.whl" in uploaded_page_text
    assert "pkg-1.0.0.tar.gz" in uploaded_page_text
    assert page["metadata"] == {}  # the page itself carries no sha256/requires-python metadata


def test_regenerate_index_page_refuses_an_object_with_no_recorded_hash(dist_dir):
    """A key in the bucket that this script never uploaded (no sha256
    metadata) must not be silently listed with a fabricated hash."""
    bucket = FakeBucket()
    bucket.objects["downloads/pkg/mystery-1.0.0.whl"] = {"metadata": {}}
    new_files = psi.sync_dist_files(bucket.run, "my-bucket", "pkg", dist_dir)
    with pytest.raises(psi.PublishStaticIndexError, match="no recorded sha256"):
        psi.regenerate_index_page(bucket.run, "my-bucket", "pkg", new_files)


# --------------------------------------------------------------------------- CLI


def test_main_end_to_end_against_the_fake_bucket(dist_dir, monkeypatch):
    bucket = FakeBucket()
    monkeypatch.setattr(psi, "run_aws", bucket.run)
    rc = psi.main(["--bucket", "my-bucket", "--package-name", "pkg", "--dist-dir", str(dist_dir)])
    assert rc == 0
    assert "downloads/pkg/pkg-1.0.0-py3-none-any.whl" in bucket.objects
    assert "simple/pkg/index.html" in bucket.objects


def test_main_fails_loudly_on_an_empty_dist_dir(tmp_path, monkeypatch, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(psi, "run_aws", FakeBucket().run)
    rc = psi.main(["--bucket", "my-bucket", "--package-name", "pkg", "--dist-dir", str(empty)])
    assert rc == 1
    assert "no wheel/sdist" in capsys.readouterr().err
