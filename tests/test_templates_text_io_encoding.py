"""Every text-mode file I/O call under `templates/` must pin `encoding="utf-8"`.

Modelled on emergent-matter-sdm-ui's `tests/test_text_io_encoding.py`: same
gate, same reasoning, scoped to this repo's own copy of the files it ships.
`templates/scripts/em-dev.py` and `templates/changeset.py` are copied
byte-for-byte into every onboarded repo (see templates/manifest.toml), so
an un-encoded call here reaches every one of them, on Windows and all.

WHY. Python's text-mode default is `locale.getpreferredencoding(False)`,
which is UTF-8 on Linux and macOS but cp1252 (or another Windows code
page) on Windows. `em-dev.cmd` exists specifically because Windows is a
supported platform for `em-dev.py`; an un-encoded `read_text()`/
`write_text()` there reads or writes cp1252 instead of UTF-8 the moment a
sibling's state file, a `.git/info/exclude`, or a changelog note contains
a non-ASCII character.

WHY A STATIC AST WALK AND NOT `ruff check --preview --select PLW1514`.
PLW1514 is preview-only (opt-in, no stability guarantee) and only
recognizes the literal chained shape `pathlib.Path(...).read_text()`. It
does not see a call through a variable, e.g. `state_path(venv_dir)`
assigned on one line and `.write_text()` called on a later one -- see
`test_gate_catches_the_shape_ruff_preview_misses` below.

WHAT THIS DOES NOT COVER, so a call like it is not "safe" just for having
passed:

- `subprocess.run(..., text=True)`: decoded through the same locale
  default, but this walk only looks at `open`/`.open`/`.read_text`/
  `.write_text` call shapes.
- `io.open`: unused in `templates/` today; if introduced, it is a bare
  `open` under a different name and this gate would need a new branch.
- A `.open()` call whose receiver isn't traceable to a plain name (a
  subscript, a comprehension result, ...), or whose receiver's root name
  is in `NON_PATH_OPEN_OWNERS`: no type information here, only the
  attribute name `open`, so a call through one of those names is left
  alone rather than told to add a keyword that would raise `TypeError`.

`templates/scripts/em-dev.py` cannot import a shared `textio`-style helper
(the way emergent-matter-sdm-sidecar's own, stricter gate requires of its
own tree): it has to run stdlib-only, before `uv sync` has put anything on
its importer's path -- see its own module docstring. Pinning
`encoding="utf-8"` at each call site, the way this gate checks for, is
therefore the actual fix here, not a stand-in for routing through a
wrapper module this repo does not have.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Files only -- both of `templates/`'s two Python files, walked
#: individually rather than by directory glob so a new template lands here
#: automatically without anyone remembering to add it.
SCAN_FILES = (
    REPO_ROOT / "templates" / "changeset.py",
    REPO_ROOT / "templates" / "scripts" / "em-dev.py",
)


def _literal_mode(node: ast.AST) -> str | None:
    """The mode string if `node` is a string literal, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_utf8_literal(node: ast.AST) -> bool:
    """True iff `node` is the string literal "utf-8" (case-insensitive,
    "utf8" also allowed).

    Anything else -- a `Name` (a variable, even one called `encoding`), a
    non-UTF-8 literal, or a literal `None` -- fails this and so is still a
    violation below. `encoding=None` means "use the platform default",
    which is the exact bug this gate exists to catch.
    """
    value = _literal_mode(node)
    return value is not None and value.lower() in ("utf-8", "utf8")


def _mode_of_call(call: ast.Call, *, mode_index: int) -> str | None:
    """The call's mode argument as a literal string, or None if unresolved.

    `mode_index` is 0 for `Path.open(mode, ...)` and 1 for builtin
    `open(file, mode, ...)` -- the two forms disagree on where `file`
    sits, so the caller has to say which.
    """
    for kw in call.keywords:
        if kw.arg == "mode":
            return _literal_mode(kw.value)
    if len(call.args) > mode_index:
        return _literal_mode(call.args[mode_index])
    return None


#: Names whose `.open()` is not `pathlib.Path.open` and does not take a
#: text mode at all. See emergent-matter-sdm-ui's own copy of this gate
#: for the fuller account of each one.
NON_PATH_OPEN_OWNERS = frozenset(
    {
        "os",
        "io",
        "webbrowser",
        "gzip",
        "tarfile",
        "zipfile",
        "bz2",
        "lzma",
        "Image",
        "socket",
        "urllib",
    }
)


def _root_name(node: ast.AST) -> str | None:
    """The `Name.id` a receiver expression's chain bottoms out at, if any.

    Walks through attribute access, calls, and the `/` operator `Path`
    uses to join segments. Returns None for anything that doesn't bottom
    out at a plain name: that receiver "cannot be traced to a Path"
    either, so it gets the same treatment as an allowlisted non-Path
    owner below.
    """
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.BinOp):
            node = node.left
        else:
            return None


def _text_io_violations(path: Path) -> list[tuple[int, str]]:
    """(lineno, call shape) for every text-mode call in `path` missing a
    literal `encoding="utf-8"` (or `"utf8"`, case-insensitive).

    A mode string containing "b" is binary and exempt. No mode at all is
    `open`/`Path.open`'s own default, `"r"`, which is text -- not exempt.
    A non-literal (dynamic) mode can't be proven binary, so it is treated
    as text and flagged too.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    violations: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if any(kw.arg == "encoding" and _is_utf8_literal(kw.value) for kw in node.keywords):
            continue

        shape: str | None = None
        mode: str | None = None

        if isinstance(func, ast.Attribute) and func.attr in ("read_text", "write_text"):
            shape = f".{func.attr}()"
        elif isinstance(func, ast.Attribute) and func.attr == "open":
            owner = _root_name(func.value)
            if owner is None or owner in NON_PATH_OPEN_OWNERS:
                continue
            shape = ".open()"
            mode = _mode_of_call(node, mode_index=0)
        elif isinstance(func, ast.Name) and func.id == "open":
            shape = "open()"
            mode = _mode_of_call(node, mode_index=1)

        if shape is None:
            continue
        if mode is not None and "b" in mode:
            continue
        violations.append((node.lineno, shape))

    return violations


# ── negative controls: prove the gate actually fires, on both shapes it
# must catch, before trusting it to police templates/ ──────────────────


def test_gate_catches_a_bare_open_without_encoding(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text('f = open("x.txt")\n', encoding="utf-8")

    violations = _text_io_violations(sample)

    assert violations, "bare open() with no mode and no encoding must be flagged"


def test_gate_catches_the_shape_ruff_preview_misses(tmp_path):
    """A read_text() reached through a variable, not chained off
    `Path(...)` -- the shape `ruff check --preview --select PLW1514` does
    not flag, and the shape `em-dev.py`'s `read_state()`/
    `read_editable_flag()` both used before this fix
    (`json.loads(path.read_text())`, with `path` assigned two lines
    earlier)."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        'from pathlib import Path\nX = Path(__file__).parent / "f"\nX.read_text()\n',
        encoding="utf-8",
    )

    violations = _text_io_violations(sample)

    assert violations, "variable-mediated .read_text() must be flagged, not just the literal chain"


def test_gate_does_not_flag_calls_that_already_pin_encoding(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from pathlib import Path\n"
        'open("x.txt", encoding="utf-8")\n'
        'Path("x.txt").read_text(encoding="utf-8")\n'
        'Path("x.txt").write_text("y", encoding="utf-8")\n'
        'Path("x.txt").open("w", encoding="utf-8")\n',
        encoding="utf-8",
    )

    assert _text_io_violations(sample) == []


def test_gate_does_not_flag_binary_mode(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        'from pathlib import Path\nopen("x.bin", "rb")\nPath("x.bin").open("wb")\n',
        encoding="utf-8",
    )

    assert _text_io_violations(sample) == []


def test_gate_catches_encoding_none(tmp_path):
    """`encoding=None` means "use the platform default" -- the exact bug
    this gate exists to catch -- so the keyword's PRESENCE is not enough;
    only a literal "utf-8" (or "utf8") passes."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        'from pathlib import Path\nPath("x.txt").read_text(encoding=None)\n',
        encoding="utf-8",
    )

    violations = _text_io_violations(sample)

    assert violations, "encoding=None does not pin UTF-8 and must still be flagged"


def test_gate_does_not_flag_a_non_path_dot_open(tmp_path):
    """`webbrowser.open(url)` opens a URL in a browser, not a file --
    treating it as `Path.open` would tell it to add `encoding="utf-8"`,
    which raises `TypeError`."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        'import webbrowser\nwebbrowser.open("https://example.com")\n', encoding="utf-8"
    )

    assert _text_io_violations(sample) == []


# ── the gate ─────────────────────────────────────────────────────────────


def test_every_text_mode_file_io_call_under_templates_pins_utf8():
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in SCAN_FILES:
        assert path.is_file(), f"{path} does not exist -- update SCAN_FILES"
        violations = _text_io_violations(path)
        if violations:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = violations

    assert not offenders, (
        'text-mode file I/O without encoding="utf-8" reads as the platform '
        "default -- cp1252 on Windows, not UTF-8 -- in every repo this template "
        f"is copied into. Pin it explicitly at each call site:\n{offenders}"
    )
