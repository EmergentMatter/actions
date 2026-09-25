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
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish_static_index.py"
spec = importlib.util.spec_from_file_location("publish_static_index", SCRIPT)
psi = importlib.util.module_from_spec(spec)
sys.modules["publish_static_index"] = psi
spec.loader.exec_module(psi)


def _reject_empty_option_values(argv: list[str]) -> None:
    """Every `--flag` this fake understands takes a value; an empty one is
    exactly what the real `aws` CLI rejects with a ParamValidation error
    (`Expected: '=', received: 'EOF'`, among others) rather than silently
    accepting. Failing loudly here, in the fake, is what makes a
    regression like `upload_file` passing `--metadata ""` show up as a
    test failure instead of a green suite over a broken argv."""
    for i, arg in enumerate(argv):
        if arg.startswith("--") and i + 1 < len(argv) and argv[i + 1] == "":
            raise AssertionError(f"empty value for {arg} in {argv} -- aws would reject this")


class FakeBucket:
    """A tiny in-memory stand-in for the subset of S3 this script calls,
    driven the same way the real `aws` CLI would be: one shelled-out
    argv list per call, JSON on stdout."""

    def __init__(self):
        # key -> {"metadata": {...}}
        self.objects: dict[str, dict] = {}
        self.calls: list[list[str]] = []
        self.invalidations: list[tuple[str, str]] = []  # (distribution_id, path)

    def put(self, key: str, *, sha256: str | None = "", requires_python: str | None = None) -> None:
        meta = {}
        if sha256:
            meta["sha256"] = sha256
        if requires_python:
            meta["requires-python"] = requires_python
        self.objects[key] = {"metadata": meta}

    def run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        _reject_empty_option_values(argv)
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
            delimiter = argv[argv.index("--delimiter") + 1] if "--delimiter" in argv else None
            matching = [k for k in self.objects if k.startswith(prefix)]
            if delimiter:
                # Real list-objects-v2 with a delimiter buckets keys past
                # the first delimiter after the prefix into CommonPrefixes,
                # and only a key with nothing past the prefix into Contents.
                common_prefixes: set[str] = set()
                contents = []
                for key in matching:
                    rest = key[len(prefix) :]
                    if delimiter in rest:
                        common_prefixes.add(prefix + rest.split(delimiter, 1)[0] + delimiter)
                    else:
                        contents.append({"Key": key})
                response = {
                    "Contents": contents,
                    "CommonPrefixes": [{"Prefix": p} for p in sorted(common_prefixes)],
                }
            else:
                response = {"Contents": [{"Key": k} for k in matching]}
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(response), stderr="")
        if argv[:2] == ["s3", "cp"]:
            # argv: s3 cp <local> s3://bucket/<key> --content-type T [--metadata M]
            local_path = Path(argv[2])
            dest = argv[3]
            key = dest.split("/", 3)[-1]
            meta = {}
            if "--metadata" in argv:
                metadata_arg = argv[argv.index("--metadata") + 1]
                # The real `aws s3 cp --metadata` takes JSON here, never the
                # comma-separated key=value shorthand (that shorthand can't
                # represent a value containing a comma, e.g. a
                # requires-python range like ">=3.10,<4"). A non-JSON value
                # reaching this fake is exactly the bug this test guards
                # against, so it fails loudly rather than falling back to
                # the shorthand parser real `aws` would also reject.
                meta = json.loads(metadata_arg)
            # Read NOW, at call time: publish_static_index.py deletes its
            # own temp file (the index page) right after this call returns.
            self.objects[key] = {"metadata": meta, "content": local_path.read_bytes()}
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:2] == ["cloudfront", "create-invalidation"]:
            distribution_id = argv[argv.index("--distribution-id") + 1]
            path = argv[argv.index("--paths") + 1]
            self.invalidations.append((distribution_id, path))
            return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")
        raise AssertionError(f"FakeBucket got an unexpected aws call: {argv}")


@pytest.fixture
def dist_dir(tmp_path):
    d = tmp_path / "dist"
    d.mkdir()
    (d / "pkg-1.0.0-py3-none-any.whl").write_bytes(b"wheel bytes")
    (d / "pkg-1.0.0.tar.gz").write_bytes(b"sdist bytes")
    return d


def _make_wheel(path: Path, *, requires_python: str) -> None:
    """A minimal, real wheel (a zip with a dist-info/METADATA member), the
    same shape gip.requires_python_of() reads."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pkg/__init__.py", "__version__ = '1.0.0'\n")
        metadata = (
            "Metadata-Version: 2.1\nName: pkg\nVersion: 1.0.0\n"
            f"Requires-Python: {requires_python}\n"
        )
        zf.writestr("pkg-1.0.0.dist-info/METADATA", metadata)


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


def test_sync_dist_files_refuses_an_existing_key_with_no_recorded_hash(dist_dir):
    """The bug the reviewer reproduced: a key already at that name in the
    bucket, but with NO sha256 metadata (put there by hand, or from before
    this metadata scheme existed), must never be treated as "same content,
    retried run". There's nothing to verify it against, so this has to
    refuse exactly like a hash mismatch -- silently trusting it would let
    this build's hash reach the index page for content that was never
    checked, and never even uploads/overwrites the mismatched real bytes
    already there."""
    bucket = FakeBucket()
    bucket.put("downloads/pkg/pkg-1.0.0-py3-none-any.whl", sha256=None)
    assert bucket.objects["downloads/pkg/pkg-1.0.0-py3-none-any.whl"]["metadata"] == {}

    with pytest.raises(psi.PublishStaticIndexError, match="no recorded sha256"):
        psi.sync_dist_files(bucket.run, "my-bucket", "pkg", dist_dir)

    # Refusing means refusing outright: no s3 cp call for that key, and the
    # bucket's own (foreign, unverified) content is untouched.
    assert not any(
        c[:2] == ["s3", "cp"] and "pkg-1.0.0-py3-none-any.whl" in c[3] for c in bucket.calls
    )
    assert "content" not in bucket.objects["downloads/pkg/pkg-1.0.0-py3-none-any.whl"]


def test_sync_dist_files_normalizes_the_package_name_in_the_prefix(dist_dir):
    bucket = FakeBucket()
    psi.sync_dist_files(bucket.run, "my-bucket", "Emergent_Matter.SDM-Core", dist_dir)
    assert any(k.startswith("downloads/emergent-matter-sdm-core/") for k in bucket.objects)


def test_sync_dist_files_uploads_a_requires_python_containing_a_comma(tmp_path):
    """The bug this guards against: `--metadata` sent as the comma-joined
    key=value shorthand can't represent a value that itself contains a
    comma, and a real `aws s3 cp` rejects it with a ParamValidation error.
    JSON has no such ambiguity."""
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    _make_wheel(dist_dir / "pkg-1.0.0-py3-none-any.whl", requires_python=">=3.10,<4")
    bucket = FakeBucket()

    psi.sync_dist_files(bucket.run, "my-bucket", "pkg", dist_dir)

    stored = bucket.objects["downloads/pkg/pkg-1.0.0-py3-none-any.whl"]
    assert stored["metadata"]["requires-python"] == ">=3.10,<4"


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


def test_regenerate_index_page_upload_carries_no_metadata_flag(dist_dir):
    """The index page itself has no sha256/requires-python to record, so
    its upload must omit --metadata entirely rather than send it empty --
    an empty value is exactly what a real `aws s3 cp` rejects."""
    bucket = FakeBucket()
    new_files = psi.sync_dist_files(bucket.run, "my-bucket", "pkg", dist_dir)
    psi.regenerate_index_page(bucket.run, "my-bucket", "pkg", new_files)

    page_upload = next(
        c for c in bucket.calls if c[:2] == ["s3", "cp"] and "simple/pkg/index.html" in c[3]
    )
    assert "--metadata" not in page_upload


def test_regenerate_index_page_refuses_an_object_with_no_recorded_hash(dist_dir):
    """A key in the bucket that this script never uploaded (no sha256
    metadata) must not be silently listed with a fabricated hash."""
    bucket = FakeBucket()
    bucket.objects["downloads/pkg/mystery-1.0.0.whl"] = {"metadata": {}}
    new_files = psi.sync_dist_files(bucket.run, "my-bucket", "pkg", dist_dir)
    with pytest.raises(psi.PublishStaticIndexError, match="no recorded sha256"):
        psi.regenerate_index_page(bucket.run, "my-bucket", "pkg", new_files)


# ------------------------------------------------------------------ root index


def test_list_package_prefixes_returns_one_name_per_package():
    bucket = FakeBucket()
    bucket.put("simple/pkg-a/index.html", sha256=None)
    bucket.put("simple/pkg-b/index.html", sha256=None)
    bucket.put("downloads/pkg-a/pkg-a-1.0.0.tar.gz", sha256="aaaa")  # not under simple/
    assert psi.list_package_prefixes(bucket.run, "my-bucket") == ["pkg-a", "pkg-b"]


def test_list_package_prefixes_is_empty_for_a_bucket_with_no_packages():
    assert psi.list_package_prefixes(FakeBucket().run, "my-bucket") == []


def test_regenerate_root_index_page_links_every_known_package():
    bucket = FakeBucket()
    bucket.put("simple/emergent-matter-sdm-core/index.html", sha256=None)
    bucket.put("simple/emergent-matter-sdm-materials/index.html", sha256=None)

    path = psi.regenerate_root_index_page(bucket.run, "my-bucket")

    assert path == "/simple/*"
    page_text = bucket.objects["simple/index.html"]["content"].decode()
    assert '<a href="emergent-matter-sdm-core/">' in page_text
    assert '<a href="emergent-matter-sdm-materials/">' in page_text

    page_upload = next(
        c for c in bucket.calls if c[:2] == ["s3", "cp"] and "simple/index.html" in c[3]
    )
    assert "--metadata" not in page_upload


def test_invalidate_cloudfront_calls_aws_with_the_given_distribution_and_path():
    bucket = FakeBucket()
    psi.invalidate_cloudfront(bucket.run, "EEXAMPLE1234567", "/simple/*")
    assert bucket.invalidations == [("EEXAMPLE1234567", "/simple/*")]


def test_invalidate_cloudfront_raises_on_failure():
    def failing_run(argv):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="access denied")

    with pytest.raises(psi.PublishStaticIndexError, match="access denied"):
        psi.invalidate_cloudfront(failing_run, "EEXAMPLE1234567", "/simple/*")


# --------------------------------------------------------------------------- CLI


def test_main_root_mode_rebuilds_and_invalidates(monkeypatch):
    bucket = FakeBucket()
    bucket.put("simple/pkg-a/index.html", sha256=None)
    monkeypatch.setattr(psi, "run_aws", bucket.run)

    rc = psi.main(["--root", "--bucket", "my-bucket", "--distribution-id", "EEXAMPLE1234567"])

    assert rc == 0
    assert "simple/index.html" in bucket.objects
    assert bucket.invalidations == [("EEXAMPLE1234567", "/simple/*")]


def test_main_root_mode_without_distribution_id_writes_but_does_not_invalidate(monkeypatch):
    bucket = FakeBucket()
    monkeypatch.setattr(psi, "run_aws", bucket.run)

    rc = psi.main(["--root", "--bucket", "my-bucket"])

    assert rc == 0
    assert "simple/index.html" in bucket.objects
    assert bucket.invalidations == []


def test_main_requires_package_name_unless_root(tmp_path, capsys):
    rc = psi.main(["--bucket", "my-bucket", "--dist-dir", str(tmp_path)])
    assert rc == 1
    assert "--package-name is required" in capsys.readouterr().err


# -------------------------------------------------------- real aws CLI argv


@pytest.mark.skipif(shutil.which("aws") is None, reason="aws CLI not on PATH")
def test_s3_cp_argv_is_accepted_by_the_real_aws_cli(tmp_path):
    """The exact argv upload_file() builds, handed to the REAL `aws` CLI
    with `--dryrun` appended -- not the fake Runner. `--dryrun` validates
    and reports what would happen without making the S3 API call, so
    fake, invalid credentials are enough to prove this never reaches AWS:
    a real call would fail authentication instead of succeeding.
    """
    env = dict(os.environ)
    env["AWS_ACCESS_KEY_ID"] = "x"
    env["AWS_SECRET_ACCESS_KEY"] = "x"
    env["AWS_DEFAULT_REGION"] = "us-east-2"

    local_file = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    local_file.write_bytes(b"wheel bytes")

    for metadata in ({"sha256": "a" * 64, "requires-python": ">=3.10,<4"}, None):
        argv = psi._s3_cp_argv(
            "example-bucket",
            "downloads/pkg/pkg-1.0.0-py3-none-any.whl",
            local_file,
            content_type="application/octet-stream",
            metadata=metadata,
        )
        result = subprocess.run(
            ["aws", *argv, "--dryrun"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert result.returncode == 0, (argv, result.stderr)
        assert "dryrun" in result.stdout.lower()
        # Confirms it never reached AWS: fake credentials would fail
        # authentication if a real API call were attempted, and a
        # ParamValidation error (the actual bug) would surface here too --
        # neither did, so this ran entirely offline.
        assert "error" not in result.stderr.lower()


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
