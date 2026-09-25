"""Tests for scripts/generate_index_page.py.

The Done-when checklist names this test explicitly: "wheel in, valid PEP
503 page out, hash correct." That's render_index() + sha256_of() +
requires_python_of(), exercised against a real (tiny, hand-built) wheel --
a zip file with a METADATA member -- not a mock.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_index_page.py"
spec = importlib.util.spec_from_file_location("generate_index_page", SCRIPT)
gip = importlib.util.module_from_spec(spec)
sys.modules["generate_index_page"] = gip
spec.loader.exec_module(gip)


def _make_wheel(path: Path, *, requires_python: str | None = None) -> None:
    """A minimal, real wheel: a zip with one module and a dist-info/METADATA
    member, the same shape `uv build` produces."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pkg/__init__.py", "__version__ = '1.0.0'\n")
        metadata = "Metadata-Version: 2.1\nName: pkg\nVersion: 1.0.0\n"
        if requires_python is not None:
            metadata += f"Requires-Python: {requires_python}\n"
        zf.writestr("pkg-1.0.0.dist-info/METADATA", metadata)


# ------------------------------------------------------------------ normalize


def test_normalize_name_collapses_separators_and_lowercases():
    assert gip.normalize_name("Emergent_Matter.SDM-Core") == "emergent-matter-sdm-core"


def test_normalize_name_is_idempotent():
    n = gip.normalize_name("emergent-matter-sdm-core")
    assert gip.normalize_name(n) == n


# --------------------------------------------------------------------- sha256


def test_sha256_of_matches_hashlib_directly(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"some wheel bytes, not actually a zip for this test\n" * 1000)
    assert gip.sha256_of(f) == hashlib.sha256(f.read_bytes()).hexdigest()


def test_sha256_of_a_real_wheel(tmp_path):
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    _make_wheel(wheel)
    assert gip.sha256_of(wheel) == hashlib.sha256(wheel.read_bytes()).hexdigest()


# ---------------------------------------------------------- requires_python_of


def test_requires_python_of_reads_the_wheel_metadata(tmp_path):
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    _make_wheel(wheel, requires_python=">=3.11")
    assert gip.requires_python_of(wheel) == ">=3.11"


def test_requires_python_of_is_none_when_absent(tmp_path):
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    _make_wheel(wheel, requires_python=None)
    assert gip.requires_python_of(wheel) is None


def test_requires_python_of_is_none_for_an_sdist(tmp_path):
    sdist = tmp_path / "pkg-1.0.0.tar.gz"
    sdist.write_bytes(b"not actually a tarball, just needs the right suffix")
    assert gip.requires_python_of(sdist) is None


def test_requires_python_of_is_none_for_a_corrupt_wheel(tmp_path):
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    wheel.write_bytes(b"not a zip file at all")
    assert gip.requires_python_of(wheel) is None


# ------------------------------------------------------------------ render_index


def test_render_index_produces_one_link_per_file_with_a_sha256_fragment():
    files = [
        gip.DistFile("pkg-1.0.0-py3-none-any.whl", "aaaa"),
        gip.DistFile("pkg-1.0.0.tar.gz", "bbbb"),
    ]
    page = gip.render_index("pkg", files)
    assert '<a href="pkg-1.0.0-py3-none-any.whl#sha256=aaaa">' in page
    assert '<a href="pkg-1.0.0.tar.gz#sha256=bbbb">' in page


def test_render_index_normalizes_the_package_name_in_the_title():
    page = gip.render_index("Emergent_Matter.SDM-Core", [])
    assert "emergent-matter-sdm-core" in page
    assert "Emergent_Matter.SDM-Core" not in page


def test_render_index_includes_requires_python_when_given():
    # html.escape(quote=True) turns `>=` into `&gt;=` inside the attribute --
    # matching real PyPI simple pages, and undone by any HTML parser that
    # reads it back (pip/uv included).
    files = [gip.DistFile("pkg-1.0.0-py3-none-any.whl", "aaaa", requires_python=">=3.11")]
    page = gip.render_index("pkg", files)
    assert 'data-requires-python="&gt;=3.11"' in page


def test_render_index_omits_the_attribute_when_requires_python_is_none():
    files = [gip.DistFile("pkg-1.0.0.tar.gz", "aaaa")]
    page = gip.render_index("pkg", files)
    assert "data-requires-python" not in page


def test_render_index_escapes_a_hostile_package_name():
    page = gip.render_index("<script>alert(1)</script>", [])
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_render_index_is_valid_looking_html_with_pep503_repository_version():
    page = gip.render_index("pkg", [])
    assert page.startswith("<!DOCTYPE html>")
    assert 'name="pypi:repository-version" content="1.0"' in page


def test_render_index_orders_links_by_filename_deterministically():
    files = [gip.DistFile("z.whl", "1"), gip.DistFile("a.whl", "2")]
    page = gip.render_index("pkg", files)
    assert page.index("a.whl") < page.index("z.whl")


def test_render_index_is_deterministic_for_the_same_input():
    files = [gip.DistFile("a.whl", "1"), gip.DistFile("b.whl", "2")]
    assert gip.render_index("pkg", files) == gip.render_index("pkg", list(reversed(files)))


# --------------------------------------------------------------- dist_files_in


def test_dist_files_in_hashes_every_wheel_and_sdist(tmp_path):
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    _make_wheel(wheel, requires_python=">=3.11")
    sdist = tmp_path / "pkg-1.0.0.tar.gz"
    sdist.write_bytes(b"sdist content")
    (tmp_path / "pkg-1.0.0.tar.gz.sha256").write_text("not a dist file")  # must be ignored

    files = {f.filename: f for f in gip.dist_files_in(tmp_path)}
    assert set(files) == {wheel.name, sdist.name}
    assert files[wheel.name].sha256 == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert files[wheel.name].requires_python == ">=3.11"
    assert files[sdist.name].requires_python is None


# --------------------------------------------------------------------------- CLI


def test_main_writes_a_page_for_the_dist_dir(tmp_path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    _make_wheel(dist_dir / "pkg-1.0.0-py3-none-any.whl", requires_python=">=3.11")
    out = tmp_path / "simple" / "pkg" / "index.html"

    rc = gip.main(["--package-name", "pkg", "--dist-dir", str(dist_dir), "--out", str(out)])
    assert rc == 0
    page = out.read_text()
    assert re.search(r'href="pkg-1\.0\.0-py3-none-any\.whl#sha256=[0-9a-f]{64}"', page)


def test_main_fails_loudly_on_an_empty_dist_dir(tmp_path, capsys):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    rc = gip.main(
        ["--package-name", "pkg", "--dist-dir", str(dist_dir), "--out", str(tmp_path / "out.html")]
    )
    assert rc == 1
    assert "no wheel/sdist" in capsys.readouterr().err
