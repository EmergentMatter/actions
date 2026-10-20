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

    python verify_wheel.py <distribution-name> [<distribution-name> ...]

    # Not inside the clean environment: read each wheel's own METADATA and
    # print "<name> <version>" per wheel. With --lockstep, also fail unless
    # every wheel carries the same version. Used by the multi-package form
    # of the verify-wheel action to learn which names to check.
    python verify_wheel.py --identify [--lockstep] <wheel> [<wheel> ...]
"""

from __future__ import annotations

import email.parser
import importlib
import importlib.metadata as md
import sys
import zipfile
from pathlib import Path


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


def wheel_identity(path: Path) -> tuple[str, str]:
    """`(Name, Version)` from the wheel's own `*.dist-info/METADATA`.

    The wheel is the authority on what it is called, not its filename and
    not the repo's root `pyproject.toml`: a workspace builds several wheels
    and only one of them is named by the root.
    """
    with zipfile.ZipFile(path) as zf:
        members = [
            n for n in zf.namelist() if n.count("/") == 1 and n.endswith(".dist-info/METADATA")
        ]
        if len(members) != 1:
            raise ValueError(f"{path.name}: expected one dist-info/METADATA, found {len(members)}")
        message = email.parser.Parser().parsestr(
            zf.read(members[0]).decode("utf-8"), headersonly=True
        )
    name, version = message.get("Name"), message.get("Version")
    if not name or not version:
        raise ValueError(f"{path.name}: METADATA has no Name/Version")
    return name, version


def identify(paths: list[Path], *, lockstep: bool) -> tuple[bool, str]:
    """One `name version` line per wheel; with `lockstep`, ok only if the
    wheels share a single version and no name appears twice."""
    try:
        identities = [wheel_identity(p) for p in paths]
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        return False, f"cannot read wheel metadata: {exc}"
    lines = "\n".join(f"{name} {version}" for name, version in identities)
    if lockstep:
        names = [name.lower().replace("_", "-") for name, _ in identities]
        if len(set(names)) != len(names):
            return False, f"the same distribution appears in more than one wheel:\n{lines}"
        versions = {version for _, version in identities}
        if len(versions) > 1:
            return False, (
                "the wheels do not share one version, but this release publishes its "
                f"packages in lockstep:\n{lines}"
            )
    return True, lines


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--identify":
        rest = argv[1:]
        lockstep = bool(rest) and rest[0] == "--lockstep"
        paths = [Path(a) for a in (rest[1:] if lockstep else rest)]
        if not paths:
            print(f"usage: {sys.argv[0]} --identify [--lockstep] <wheel>...", file=sys.stderr)
            return 2
        ok, message = identify(paths, lockstep=lockstep)
        if not ok:
            print(f"::error::{message}", file=sys.stderr)
            return 1
        print(message)
        return 0
    if not argv:
        print(f"usage: {sys.argv[0]} <distribution-name>...", file=sys.stderr)
        return 2
    failed = False
    for name in argv:
        ok, message = verify(name)
        if not ok:
            print(f"::error::{message}", file=sys.stderr)
            failed = True
        else:
            print(message)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
