"""Tests for scripts/generate_index_page.py.

The Done-when checklist names this test explicitly: "wheel in, valid PEP
503 page out, hash correct." That's render_index() + sha256_of() +
requires_python_of(), exercised against a real (tiny, hand-built) wheel --
a zip file with a METADATA member -- not a mock.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_index_page.py"
spec = importlib.util.spec_from_file_location("generate_index_page", SCRIPT)
gip = importlib.util.module_from_spec(spec)
sys.modules["generate_index_page"] = gip
spec.loader.exec_module(gip)


def _make_wheel(path: Path, *, requires_python: str | None = None) -> None:
    """A minimal, real wheel: a zip with one module, a dist-info/METADATA
    member, and the WHEEL/RECORD members a real installer expects -- close
    enough to what `uv build` produces that `uv pip install --require-hashes`
    accepts it (RECORD's absence is a hard error there, unlike a plain
    install, which tolerates it by rewriting one)."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pkg/__init__.py", "__version__ = '1.0.0'\n")
        metadata = "Metadata-Version: 2.1\nName: pkg\nVersion: 1.0.0\n"
        if requires_python is not None:
            metadata += f"Requires-Python: {requires_python}\n"
        zf.writestr("pkg-1.0.0.dist-info/METADATA", metadata)
        zf.writestr(
            "pkg-1.0.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        zf.writestr("pkg-1.0.0.dist-info/RECORD", "")


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


# --------------------------------------------------------------- wheel_metadata


def _write_wheel_metadata(path: Path, metadata: str) -> None:
    """A minimal wheel carrying exactly the given METADATA text, for
    tests that need control over fields _make_wheel() doesn't set."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pkg/__init__.py", "")
        zf.writestr("pkg-1.0.0.dist-info/METADATA", metadata)


def test_wheel_metadata_raises_when_theres_no_metadata_member(tmp_path):
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("pkg/__init__.py", "")
    with pytest.raises(ValueError, match="no .*METADATA member"):
        gip.wheel_metadata(wheel)


# ---------------------------------------------------------- wheel_metadata_json


def test_wheel_metadata_json_reads_every_contract_field(tmp_path):
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    _write_wheel_metadata(
        wheel,
        "Metadata-Version: 2.4\n"
        "Name: pkg\n"
        "Version: 1.0.0\n"
        "Summary: A test package.\n"
        "License-Expression: Apache-2.0\n"
        "Requires-Python: >=3.13\n"
        "Project-URL: Homepage, https://example.com\n"
        "Project-URL: Issues, https://example.com/issues\n",
    )
    metadata = gip.wheel_metadata_json(wheel)
    assert metadata == {
        "name": "pkg",
        "version": "1.0.0",
        "summary": "A test package.",
        "license": "Apache-2.0",
        "requires_python": ">=3.13",
        "project_urls": {
            "Homepage": "https://example.com",
            "Issues": "https://example.com/issues",
        },
    }


def test_wheel_metadata_json_prefers_license_expression_over_classic_license(tmp_path):
    """PEP 639: a wheel could in principle carry both during a transition;
    License-Expression is the one every package in this org's fleet
    actually emits, so it wins."""
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    _write_wheel_metadata(
        wheel,
        "Metadata-Version: 2.4\nName: pkg\nVersion: 1.0.0\n"
        "License: Apache Software License\nLicense-Expression: Apache-2.0\n",
    )
    assert gip.wheel_metadata_json(wheel)["license"] == "Apache-2.0"


def test_wheel_metadata_json_falls_back_to_classic_license(tmp_path):
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    _write_wheel_metadata(wheel, "Metadata-Version: 2.1\nName: pkg\nVersion: 1.0.0\nLicense: MIT\n")
    assert gip.wheel_metadata_json(wheel)["license"] == "MIT"


def test_wheel_metadata_json_nulls_every_absent_optional_field(tmp_path):
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    _write_wheel_metadata(wheel, "Metadata-Version: 2.1\nName: pkg\nVersion: 1.0.0\n")
    metadata = gip.wheel_metadata_json(wheel)
    assert metadata["summary"] is None
    assert metadata["license"] is None
    assert metadata["requires_python"] is None
    assert metadata["project_urls"] == {}


def test_wheel_metadata_json_raises_without_a_name(tmp_path):
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    _write_wheel_metadata(wheel, "Metadata-Version: 2.1\nVersion: 1.0.0\n")
    with pytest.raises(ValueError, match="Name and/or Version"):
        gip.wheel_metadata_json(wheel)


def test_wheel_metadata_json_raises_without_a_version(tmp_path):
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    _write_wheel_metadata(wheel, "Metadata-Version: 2.1\nName: pkg\n")
    with pytest.raises(ValueError, match="Name and/or Version"):
        gip.wheel_metadata_json(wheel)


def test_wheel_metadata_json_collapses_a_folded_summary(tmp_path):
    """A long Summary can be an RFC 822 folded header -- a continuation
    line, which email.parser keeps verbatim (embedded newline and leading
    whitespace intact). That raw folding must never leak into the JSON
    contract's summary field."""
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    _write_wheel_metadata(
        wheel,
        "Metadata-Version: 2.1\nName: pkg\nVersion: 1.0.0\n"
        "Summary: A really long summary\n that wraps across\n multiple lines\n",
    )
    assert (
        gip.wheel_metadata_json(wheel)["summary"]
        == "A really long summary that wraps across multiple lines"
    )


def test_wheel_metadata_json_raises_on_a_project_url_with_no_comma(tmp_path):
    """The "Label, URL" shape is the only one a Project-URL header has -- one with
    no comma can't be split into a label and a URL at all, so it must
    raise rather than be stored as e.g. {"https://example.com": ""}."""
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    _write_wheel_metadata(
        wheel,
        "Metadata-Version: 2.1\nName: pkg\nVersion: 1.0.0\nProject-URL: https://example.com\n",
    )
    with pytest.raises(ValueError, match="malformed Project-URL"):
        gip.wheel_metadata_json(wheel)


# A real excerpt (emergent-matter-sdm-core's own CHANGELOG.md, its first
# two released sections, copied verbatim) -- not a hand-built
# approximation of towncrier's shape.
SDM_CORE_CHANGELOG_EXCERPT = (
    Path(__file__).resolve().parent / "fixtures" / "sdm-core-changelog-excerpt.md"
).read_text(encoding="utf-8")


# ------------------------------------------------------ release_notes_for_version


def test_release_notes_for_version_is_deterministic_across_two_runs():
    changelog = "## 1.0.0 (2026-01-01)\n\n- one\n- two\n"
    first = gip.release_notes_for_version(changelog, "1.0.0")
    second = gip.release_notes_for_version(changelog, "1.0.0")
    assert first == second


def test_release_notes_for_version_crlf_matches_lf_byte_for_byte():
    lf = "## 1.0.0 (2026-01-01)\n\n- one\n- two\n\n## 0.9.0 (2025-12-01)\n\n- old\n"
    crlf = lf.replace("\n", "\r\n")
    assert gip.release_notes_for_version(lf, "1.0.0") == gip.release_notes_for_version(
        crlf, "1.0.0"
    )


def test_release_notes_for_version_strips_trailing_whitespace_and_blank_edges():
    changelog = (
        "## 1.0.0 (2026-01-01)\n"
        "\n"
        "\n"
        "- has trailing spaces   \n"
        "- has a trailing tab\t\n"
        "\n"
        "\n"
        "## 0.9.0 (2025-12-01)\n"
    )
    notes = gip.release_notes_for_version(changelog, "1.0.0")
    assert notes == "- has trailing spaces\n- has a trailing tab\n"


def test_release_notes_for_version_does_not_match_a_longer_version_it_prefixes():
    """`## 2.0.0` must not match a `## 2.0.01` or `## 2.0.0rc1` heading --
    neither has whitespace, `(`, or end-of-line right after `2.0.0`."""
    changelog = (
        "## 2.0.01 (2026-01-03)\n\n- newer patch, must not match 2.0.0\n\n"
        "## 2.0.0rc1 (2026-01-02)\n\n- prerelease, must not match 2.0.0\n\n"
        "## 2.0.0 (2026-01-01)\n\n- the real one\n"
    )
    assert gip.release_notes_for_version(changelog, "2.0.0") == "- the real one\n"


def test_release_notes_for_version_a_literal_dot_does_not_match_any_character():
    """`2.0.0`'s dots are regex-escaped -- a changelog heading like
    `## 2x0x0` (dot replaced by some other single character) must not
    match, the way an unescaped `re` pattern would let it."""
    changelog = "## 2x0x0 (2026-01-01)\n\n- must not match\n"
    assert gip.release_notes_for_version(changelog, "2.0.0") is None


def test_release_notes_for_version_the_last_section_in_the_file():
    changelog = "## 2.0.0 (2026-01-01)\n\n- newer\n\n## 1.0.0 (2025-12-01)\n\n- last one\n"
    assert gip.release_notes_for_version(changelog, "1.0.0") == "- last one\n"


def test_release_notes_for_version_returns_none_when_theres_no_matching_heading():
    changelog = "## 1.0.0 (2026-01-01)\n\n- one\n"
    assert gip.release_notes_for_version(changelog, "9.9.9") is None


def test_release_notes_for_version_matches_towncriers_own_heading_shape():
    """towncrier's title_format is `## {version} ({project_date})` -- the
    space right after the version is what the `\\s` alternative matches."""
    changelog = "## 1.2.3 (2026-03-04)\n\nsomething happened\n"
    assert gip.release_notes_for_version(changelog, "1.2.3") == "something happened\n"


def test_release_notes_for_version_against_a_real_towncrier_changelog():
    """A real excerpt (emergent-matter-sdm-core's own CHANGELOG.md, its
    first two released sections), not a hand-built approximation of
    towncrier's shape."""
    notes_2_0_0 = gip.release_notes_for_version(SDM_CORE_CHANGELOG_EXCERPT, "2.0.0")
    assert notes_2_0_0 is not None
    assert notes_2_0_0.startswith("### Major\n")
    assert "Move `pyvista` from a required dependency" in notes_2_0_0
    assert "## 1.0.0" not in notes_2_0_0
    assert not notes_2_0_0.endswith("\n\n")

    notes_1_0_0 = gip.release_notes_for_version(SDM_CORE_CHANGELOG_EXCERPT, "1.0.0")
    assert notes_1_0_0 is not None
    assert notes_1_0_0.startswith("### Major\n")
    assert "Now depends on `emergent-matter-sdm-materials`" in notes_1_0_0
    assert notes_1_0_0.endswith("with its license.\n")
    assert not notes_1_0_0.endswith("\n\n")


# ------------------------------------------------------------------ render_index


def test_render_index_produces_one_link_per_file_with_a_sha256_fragment():
    """The href is relative to the actual upload location
    (downloads/<package>/<filename>), not a bare filename -- a bare
    filename resolves against this page's own directory
    (simple/<package>/), which is empty, and 404s."""
    files = [
        gip.DistFile("pkg-1.0.0-py3-none-any.whl", "aaaa"),
        gip.DistFile("pkg-1.0.0.tar.gz", "bbbb"),
    ]
    page = gip.render_index("pkg", files)
    assert '<a href="../../downloads/pkg/pkg-1.0.0-py3-none-any.whl#sha256=aaaa">' in page
    assert '<a href="../../downloads/pkg/pkg-1.0.0.tar.gz#sha256=bbbb">' in page


def test_render_index_href_uses_the_normalized_package_name():
    """The `downloads/<package>/` segment in the href must match the
    normalized name publish_static_index.py actually uploads to -- the
    two can never be allowed to drift apart."""
    files = [gip.DistFile("pkg-1.0.0-py3-none-any.whl", "aaaa")]
    page = gip.render_index("Emergent_Matter.SDM-Core", files)
    assert "../../downloads/emergent-matter-sdm-core/pkg-1.0.0-py3-none-any.whl" in page


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


# ------------------------------------------------------------- render_root_index


def test_render_root_index_links_every_package():
    page = gip.render_root_index(["pkg-a", "pkg-b"])
    assert '<a href="pkg-a/">pkg-a</a><br/>' in page
    assert '<a href="pkg-b/">pkg-b</a><br/>' in page


def test_render_root_index_normalizes_and_deduplicates_names():
    page = gip.render_root_index(["Emergent_Matter.SDM-Core", "emergent-matter-sdm-core"])
    assert page.count('<a href="emergent-matter-sdm-core/">') == 1


def test_render_root_index_carries_no_sha256_fragments():
    """Hashes belong on each package's own page (render_index()), not the
    root index of indexes."""
    page = gip.render_root_index(["pkg"])
    assert "#sha256=" not in page


def test_render_root_index_orders_links_deterministically():
    page = gip.render_root_index(["z-pkg", "a-pkg"])
    assert page.index("a-pkg") < page.index("z-pkg")


def test_render_root_index_is_empty_but_valid_for_no_packages():
    page = gip.render_root_index([])
    assert page.startswith("<!DOCTYPE html>")
    assert "<title>Simple index</title>" in page


def test_root_index_links_resolve_to_the_package_page_and_on_to_the_wheel():
    """The root page's href (`<pkg>/`) is relative to `simple/index.html`
    itself, one level up from a package page's own hrefs
    (`../../downloads/<pkg>/<file>`, relative to `simple/<pkg>/index.html`).
    Resolved with the real URL-resolution algorithm (RFC 3986, the same
    one a browser or `uv`/`pip` uses against the page's own URL), not by
    inspection, the full chain -- root page to package page to wheel --
    must land on the actual upload location."""
    root_page_url = "https://get.example.com/simple/index.html"
    root_page = gip.render_root_index(["pkg-a"])
    href = re.search(r'href="([^"]+)"', root_page).group(1)
    package_page_url = urljoin(root_page_url, href + "index.html")
    assert package_page_url == "https://get.example.com/simple/pkg-a/index.html"

    package_page = gip.render_index("pkg-a", [gip.DistFile("pkg-a-1.0.0.whl", "aaaa")])
    wheel_href = re.search(r'href="([^"]+)"', package_page).group(1)
    wheel_url = urljoin(package_page_url, wheel_href)
    assert wheel_url == "https://get.example.com/downloads/pkg-a/pkg-a-1.0.0.whl#sha256=aaaa"


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
    assert re.search(
        r'href="\.\./\.\./downloads/pkg/pkg-1\.0\.0-py3-none-any\.whl#sha256=[0-9a-f]{64}"', page
    )


def test_main_fails_loudly_on_an_empty_dist_dir(tmp_path, capsys):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    rc = gip.main(
        ["--package-name", "pkg", "--dist-dir", str(dist_dir), "--out", str(tmp_path / "out.html")]
    )
    assert rc == 1
    assert "no wheel/sdist" in capsys.readouterr().err


# --------------------------------------------------------- real install, end to end


def _uv_pip_install(index_dir: Path, target: Path, reqs: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--index-url",
            index_dir.as_uri(),
            "--target",
            str(target),
            "--no-deps",
            "--require-hashes",
            "-r",
            str(reqs),
        ],
        capture_output=True,
        text=True,
        env=dict(os.environ, UV_NO_CACHE="1"),
        timeout=60,
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_page_generator_output_installs_and_verifies_the_hash(tmp_path):
    """End to end: a real wheel, the real render_index() page, laid out
    exactly like the bucket (simple/<pkg>/index.html and
    downloads/<pkg>/<wheel>), installed by the real `uv` from a file://
    index -- not the fake runner or a hand-written page. This is what
    actually caught the wrong-href regression: a bare filename href
    resolves against simple/<pkg>/, which has no wheel in it, and 404s;
    the fixed relative href resolves to downloads/<pkg>/, where the wheel
    actually lives.

    `--require-hashes` against a requirements.txt hash pin is what makes
    `uv` verify the wheel's content against a hash at all -- a plain
    `uv pip install pkg==1.0.0` does not check the page's own #sha256=
    fragment. The pin here is read out of DistFile.sha256, the same value
    render_index() put in the page, so a real content/hash mismatch would
    fail both halves of this test identically.
    """
    bucket = tmp_path / "bucket"
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    wheel = dist_dir / "pkg-1.0.0-py3-none-any.whl"
    _make_wheel(wheel)

    files = gip.dist_files_in(dist_dir)
    page = gip.render_index("pkg", files)
    (bucket / "simple" / "pkg").mkdir(parents=True)
    (bucket / "simple" / "pkg" / "index.html").write_text(page)
    (bucket / "downloads" / "pkg").mkdir(parents=True)
    shutil.copy(wheel, bucket / "downloads" / "pkg" / wheel.name)

    sha256 = files[0].sha256
    reqs = tmp_path / "requirements.txt"
    reqs.write_text(f"pkg==1.0.0 --hash=sha256:{sha256}\n")

    result = _uv_pip_install(bucket / "simple", tmp_path / "out", reqs)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "pkg" / "__init__.py").is_file()

    # And the hash really is checked: a requirements pin that disagrees
    # with the page's (correct) content must fail, not install anyway.
    bad_reqs = tmp_path / "requirements-bad.txt"
    bad_reqs.write_text(f"pkg==1.0.0 --hash=sha256:{'0' * 64}\n")
    bad_result = _uv_pip_install(bucket / "simple", tmp_path / "out-bad", bad_reqs)
    assert bad_result.returncode != 0
    assert "hash" in bad_result.stderr.lower()
