#!/usr/bin/env python3
"""Check that an installed distribution actually ships importable modules.

Run INSIDE the clean environment the wheel was installed into, and from a
directory that is not the repo checkout. That second part is load-bearing:
from the repo root, `import yourpackage` resolves against the working tree
and succeeds no matter what the wheel contains, which is exactly the
failure this exists to catch.

`uv build` exiting 0 does not mean the wheel is usable. Verified against
hatchling: a wheel target naming a package directory that does not exist,
and `exclude = ["*"]`, both build successfully and produce an artifact with
nothing importable inside.

    python verify_wheel.py <distribution-name>
"""

from __future__ import annotations

import importlib
import importlib.metadata as md
import sys


def top_level_modules(files: list[str]) -> set[str]:
    """Importable top-level names among a distribution's installed files.

    Skips metadata directories (`*.dist-info`, `*.data`), dotfiles, and
    private modules -- none of those are the package's public entry point,
    and failing to import `_vendor` would be a false alarm.
    """
    tops: set[str] = set()
    for f in files:
        head = f.replace("\\", "/").split("/")[0]
        if head.endswith((".dist-info", ".data", ".pth")):
            continue
        if head.startswith((".", "_")):
            continue
        tops.add(head[:-3] if head.endswith(".py") else head)
    return tops


def verify(name: str) -> tuple[bool, str]:
    try:
        dist = md.distribution(name)
    except md.PackageNotFoundError:
        return False, (
            f"{name} is not installed in this environment. The wheel did not "
            "install, or it declares a different distribution name."
        )

    files = [str(f) for f in (dist.files or [])]
    tops = top_level_modules(files)
    if not tops:
        return False, (
            f"The wheel for {name} installs no importable module. It built "
            "successfully and contains nothing usable -- check the build "
            "backend's package/include configuration."
        )

    failures = []
    for module in sorted(tops):
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - report every failure, not the first
            failures.append(f"  {module}: {type(exc).__name__}: {exc}")

    if failures:
        return False, (
            f"The wheel for {name} installs modules that cannot be imported:\n"
            + "\n".join(failures)
        )

    return True, f"{name} {dist.version}: imported {', '.join(sorted(tops))}"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(f"usage: {sys.argv[0]} <distribution-name>", file=sys.stderr)
        return 2
    ok, message = verify(argv[0])
    if not ok:
        print(f"::error::{message}", file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
