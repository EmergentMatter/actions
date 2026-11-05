#!/usr/bin/env python3
"""Print the package indexes a project declares, one `name=url` per line.

`verify-wheel` installs the built wheel into a brand-new environment, away
from the checkout, so uv never sees the project's `[[tool.uv.index]]`
tables there. A wheel whose dependencies live on one of those indexes (an
`explicit = true` index is used only for names a source pins to it) then
cannot be installed. This reads the declarations so the caller can pass each
one as `--index name=url`, which is ordinary, outranks the default index,
and keeps credentials keyed on the index name working.

Stdlib only; reads `pyproject.toml` in the path given (default: the current
directory) and prints nothing when it declares no index.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path


def declared_indexes(pyproject: Path) -> list[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    specs: list[str] = []
    for entry in data.get("tool", {}).get("uv", {}).get("index", []):
        url = entry.get("url")
        if not url:
            continue
        name = entry.get("name")
        specs.append(f"{name}={url}" if name else url)
    return specs


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else Path("pyproject.toml")
    if path.is_file():
        for spec in declared_indexes(path):
            print(spec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
