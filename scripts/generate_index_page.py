#!/usr/bin/env python3
"""Build a PEP 503 "simple" index page for one package.

Called from the publish job in version.yml / build-release.yml (via
publish_static_index.py, which handles the S3 side), so this is one of
the "called from a workflow" scripts (see README.md's "The scripts"):
stdlib only, no `gh`, reads only what it is given on the command line.

The page shape is exactly what `pip` / `uv` expect from a static index
(PEP 503): one `<a href="...">` per distribution file, a `#sha256=`
fragment on every link (so the installer verifies content, not just
trusts the filename), and an optional `data-requires-python` attribute
(PEP 345 environment marker string) that lets an installer skip a
wheel/sdist that can't run on the interpreter it has, without downloading
it first.

The functions here are pure: given a package name and a list of
(filename, sha256, requires-python) tuples, render_index() returns the
page as a string. Nothing here touches a network, a filesystem beyond the
one wheel/sdist it's asked to hash, or a bucket -- that orchestration
(what's already uploaded, what's new this release, immutability) is
publish_static_index.py's job. Keeping this half pure is what makes it
unit-testable without a real bucket: "wheel in, valid PEP 503 page out,
hash correct" is a call to render_index() and sha256_of(), nothing more.

    generate_index_page.py --package-name emergent-matter-sdm-core \\
        --dist-dir dist --out simple/emergent-matter-sdm-core/index.html
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import html
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DistFile",
    "normalize_name",
    "render_index",
    "render_root_index",
    "sha256_of",
    "requires_python_of",
]

_DIST_SUFFIXES = (".whl", ".tar.gz")


@dataclass(frozen=True)
class DistFile:
    """One link on the index page."""

    filename: str
    sha256: str
    requires_python: str | None = None


def normalize_name(name: str) -> str:
    """PEP 503's normalization: runs of `-`, `_`, `.` collapse to one `-`,
    lowercased. `emergent_matter.sdm-Core` and `emergent-matter-sdm-core`
    must resolve to the same index path, or `pip`/`uv` -- which normalize
    the name they're asked for before requesting it -- will 404 against a
    page built under the un-normalized spelling."""
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256_of(path: Path) -> str:
    """The `#sha256=` fragment content: a streaming hash, not `read()`, so
    a wheel too large to hold comfortably in memory is still fine here."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def requires_python_of(wheel_path: Path) -> str | None:
    """The wheel's `Requires-Python` metadata value, or None if it has
    none (an sdist, or a wheel that never declared one).

    A wheel is a zip whose `*.dist-info/METADATA` member is an RFC 822-ish
    document -- the same shape `importlib.metadata` parses, hence reusing
    `email.parser` rather than a bespoke line-scanner.
    """
    if wheel_path.suffix != ".whl":
        return None
    try:
        with zipfile.ZipFile(wheel_path) as zf:
            metadata_name = next(
                (n for n in zf.namelist() if n.endswith(".dist-info/METADATA")), None
            )
            if metadata_name is None:
                return None
            raw = zf.read(metadata_name).decode("utf-8", errors="replace")
    except (OSError, zipfile.BadZipFile):
        return None
    message = email.parser.Parser().parsestr(raw, headersonly=True)
    return message.get("Requires-Python")


def render_index(package_name: str, files: list[DistFile]) -> str:
    """The full `simple/<package>/index.html` page for `files`.

    Deterministic ordering (by filename) so a rebuild from the same
    inputs produces byte-identical output -- useful for the "upload only
    if different" check on the page itself, and for tests.
    """
    normalized = normalize_name(package_name)
    links = []
    for f in sorted(files, key=lambda d: d.filename):
        attr = (
            f' data-requires-python="{html.escape(f.requires_python, quote=True)}"'
            if f.requires_python
            else ""
        )
        href = f"{html.escape(f.filename)}#sha256={f.sha256}"
        links.append(f'    <a href="{href}"{attr}>{html.escape(f.filename)}</a><br/>')
    body = "\n".join(links)
    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "  <head>\n"
        '    <meta name="pypi:repository-version" content="1.0">\n'
        f"    <title>Links for {html.escape(normalized)}</title>\n"
        "  </head>\n"
        "  <body>\n"
        f"    <h1>Links for {html.escape(normalized)}</h1>\n"
        f"{body}\n"
        "  </body>\n"
        "</html>\n"
    )


def render_root_index(package_names: list[str]) -> str:
    """The root `simple/index.html`: PEP 503's index of every package this
    static index serves, one `<a href="<name>/">` per package, no hashes
    (those live one level down, on each package's own render_index() page).

    Normalized and de-duplicated the same way render_index() normalizes a
    single package's name, so two spellings of the same package collapse
    to the one link `pip`/`uv` will actually request. Deterministic
    ordering for the same reason render_index() sorts its links.
    """
    names = sorted({normalize_name(n) for n in package_names})
    links = [f'    <a href="{html.escape(n)}/">{html.escape(n)}</a><br/>' for n in names]
    body = "\n".join(links)
    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "  <head>\n"
        '    <meta name="pypi:repository-version" content="1.0">\n'
        "    <title>Simple index</title>\n"
        "  </head>\n"
        "  <body>\n"
        f"{body}\n"
        "  </body>\n"
        "</html>\n"
    )


def dist_files_in(dist_dir: Path) -> list[DistFile]:
    """Hash every wheel/sdist directly under `dist_dir` (no upload, no
    comparison against anything already published -- see
    publish_static_index.py for that)."""
    out = []
    for path in sorted(dist_dir.iterdir()):
        if not path.name.endswith(_DIST_SUFFIXES):
            continue
        out.append(DistFile(path.name, sha256_of(path), requires_python_of(path)))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--package-name", required=True)
    ap.add_argument("--dist-dir", default="dist", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    if not args.dist_dir.is_dir():
        print(f"error: no such directory: {args.dist_dir}", file=sys.stderr)
        return 1
    files = dist_files_in(args.dist_dir)
    if not files:
        print(f"error: no wheel/sdist in {args.dist_dir}/", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_index(args.package_name, files))
    print(f"wrote {args.out} ({len(files)} file(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
