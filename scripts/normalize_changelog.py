#!/usr/bin/env python3
"""Make a generated CHANGELOG.md pass the usual Markdown formatters.

`towncrier build` leaves two blank lines between the section it writes and the release
below it. Prettier and mdformat both collapse that to one, so a release PR that carries the
raw file fails its repo's own `format` check. This collapses every run of blank lines to a
single one, outside fenced code blocks, and ends the file with exactly one newline. It
changes nothing else, so it is safe for a repo that runs no formatter at all.

    python3 normalize_changelog.py [CHANGELOG.md]

Stdlib only. Run after `towncrier build`, before the release commit.
"""

from __future__ import annotations

import sys
from pathlib import Path


def normalize(text: str) -> str:
    """`text` with blank-line runs collapsed outside code fences and one final newline."""
    out: list[str] = []
    fence = ""
    for line in text.split("\n"):
        stripped = line.lstrip()
        if not fence and stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
        elif fence and stripped.startswith(fence):
            fence = ""
        blank = not line.strip()
        if blank and not fence and out and not out[-1].strip():
            continue
        out.append(line.rstrip() if blank else line)
    return "\n".join(out).rstrip("\n") + "\n"


def main(argv: list[str]) -> int:
    path = Path(argv[0] if argv else "CHANGELOG.md")
    text = path.read_text(encoding="utf-8")
    fixed = normalize(text)
    if fixed != text:
        path.write_text(fixed, encoding="utf-8")
        print(f"{path}: collapsed extra blank lines")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
