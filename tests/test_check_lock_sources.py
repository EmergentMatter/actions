"""scripts/check_lock_sources.py: a transitive org package must not come
from an index the project does not declare."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_lock_sources", ROOT / "scripts" / "check_lock_sources.py"
)
check_lock_sources = importlib.util.module_from_spec(spec)
sys.modules["check_lock_sources"] = check_lock_sources
spec.loader.exec_module(check_lock_sources)

PYPROJECT = '[[tool.uv.index]]\nname = "em"\nurl = "https://em.test/simple/"\nexplicit = true\n'


def _lock(*entries: tuple[str, str]) -> str:
    return "version = 1\n" + "".join(
        f'\n[[package]]\nname = "{name}"\nversion = "1.0.0"\nsource = {source}\n'
        for name, source in entries
    )


def _run(tmp_path, lock_text: str, capsys) -> tuple[int, str]:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    (tmp_path / "uv.lock").write_text(lock_text)
    code = check_lock_sources.main(
        ["--lock", str(tmp_path / "uv.lock"), "--pyproject", str(tmp_path / "pyproject.toml")]
    )
    return code, capsys.readouterr().err


def test_packages_from_a_declared_index_pass_whatever_the_trailing_slash(tmp_path, capsys):
    lock = _lock(
        ("emergent-matter-a", '{ registry = "https://em.test/simple" }'),
        ("requests", '{ registry = "https://pypi.org/simple" }'),
    )
    assert _run(tmp_path, lock, capsys)[0] == 0


def test_an_org_package_from_another_index_fails_and_is_named(tmp_path, capsys):
    lock = _lock(("emergent-matter-b", '{ registry = "https://pypi.org/simple" }'))
    code, err = _run(tmp_path, lock, capsys)
    assert code == 1
    assert "emergent-matter-b 1.0.0 came from https://pypi.org/simple" in err


def test_editable_and_virtual_entries_are_not_judged(tmp_path, capsys):
    lock = _lock(
        ("emergent-matter-c", '{ editable = "." }'),
        ("emergent-matter-d", '{ virtual = "." }'),
    )
    assert _run(tmp_path, lock, capsys)[0] == 0
