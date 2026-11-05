"""scripts/check_duplicate_modules.py: a companion adds to the public package, never copies it."""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_duplicate_modules", ROOT / "scripts" / "check_duplicate_modules.py"
)
check = importlib.util.module_from_spec(spec)
sys.modules["check_duplicate_modules"] = check
spec.loader.exec_module(check)


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


@pytest.fixture()
def packages(tmp_path):
    public = tmp_path / "pubpkg"
    write(public / "__init__.py")
    write(public / "sdf" / "__init__.py")
    write(public / "sdf" / "lipschitz.py", "def bound(): ...\nclass Estimator: ...\n")
    write(public / "assembly" / "model.py", "class Assembly: ...\ndef _hidden(): ...\n")
    companion = tmp_path / "proj" / "src" / "pubpkg_private"
    write(companion / "__init__.py")
    return public, companion


def test_a_companion_that_only_adds_passes(packages):
    public, companion = packages
    write(companion / "assembly" / "__init__.py")  # a subpackage the public one also has
    write(companion / "assembly" / "process.py", "class AssemblyProcess: ...\n")
    write(companion / "bodies.py", "def register_all(): ...\ndef main(): ...\ndef _helper(): ...\n")
    assert check.duplicates(companion, public, set()) == []


def test_a_module_at_the_same_path_is_a_copy(packages):
    public, companion = packages
    write(companion / "sdf" / "lipschitz.py", "x = 1\n")
    assert check.duplicates(companion, public, set()) == [
        "sdf/lipschitz.py: the public package has a module at the same path"
    ]


def test_a_class_or_function_defined_in_both_is_a_copy_wherever_it_lives(packages):
    public, companion = packages
    write(companion / "elsewhere.py", "class Assembly: ...\nasync def bound(): ...\n")
    found = check.duplicates(companion, public, set())
    assert "elsewhere.py: Assembly is also defined in public assembly/model.py" in found
    assert "elsewhere.py: bound is also defined in public sdf/lipschitz.py" in found


def test_allow_excuses_a_path_or_a_name(packages):
    public, companion = packages
    write(companion / "sdf" / "lipschitz.py", "def bound(): ...\n")
    write(companion / "shim.py", "class Assembly: ...\n")
    allow = {"path:sdf/lipschitz.py", "name:Assembly"}
    assert check.duplicates(companion, public, allow) == []


def test_main_does_nothing_without_the_table(tmp_path, capsys):
    write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')
    assert check.main(["--project", str(tmp_path)]) == 0
    assert "nothing to check" in capsys.readouterr().out


def test_main_reports_and_fails(tmp_path, monkeypatch, capsys):
    site = tmp_path / "site"
    write(site / "pubpkg" / "__init__.py")
    write(site / "pubpkg" / "thing.py", "class Thing: ...\n")
    monkeypatch.syspath_prepend(str(site))
    project = tmp_path / "proj"
    write(project / "pyproject.toml", '[tool.em-companion]\npublic = "pubpkg"\n')
    write(project / "src" / "pubpkg_private" / "__init__.py")
    write(project / "src" / "pubpkg_private" / "thing.py", "class Thing: ...\n")
    assert check.main(["--project", str(project)]) == 1
    err = capsys.readouterr().err
    assert "thing.py: the public package has a module at the same path" in err
    assert "Thing is also defined in public thing.py" in err


def test_a_missing_public_package_is_an_error(tmp_path):
    project = tmp_path / "proj"
    write(project / "pyproject.toml", '[tool.em-companion]\npublic = "no_such_pkg_anywhere"\n')
    write(project / "src" / "x_private" / "__init__.py")
    with pytest.raises(SystemExit, match="not installed"):
        check.main(["--project", str(project)])


def test_the_action_runs_the_script_with_the_projects_interpreter():
    text = (ROOT / "check-duplicate-modules" / "action.yml").read_text(encoding="utf-8")
    assert "uv run --no-sync python" in text and "check_duplicate_modules.py" in text
