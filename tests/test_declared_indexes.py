"""scripts/declared_indexes.py: the indexes verify-wheel must pass on.

verify-wheel installs the built wheel away from the checkout, where uv
cannot see the project's `[[tool.uv.index]]` tables. These pin what is read
from them and that the action uses the result.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "declared_indexes.py"
spec = importlib.util.spec_from_file_location("declared_indexes", SCRIPT)
declared_indexes = importlib.util.module_from_spec(spec)
sys.modules["declared_indexes"] = declared_indexes
spec.loader.exec_module(declared_indexes)


def test_named_and_unnamed_indexes_are_read_in_order(tmp_path, capsys):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[[tool.uv.index]]\nname = "a"\nurl = "https://a.test/simple"\nexplicit = true\n'
        '[[tool.uv.index]]\nurl = "https://b.test/simple"\n'
        '[[tool.uv.index]]\nname = "no-url"\n'
    )
    assert declared_indexes.declared_indexes(pyproject) == [
        "a=https://a.test/simple",
        "https://b.test/simple",
    ]
    assert declared_indexes.main([str(pyproject)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "a=https://a.test/simple",
        "https://b.test/simple",
    ]


def test_no_indexes_and_no_file_print_nothing(tmp_path, capsys):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\n')
    assert declared_indexes.main([str(pyproject)]) == 0
    assert declared_indexes.main([str(tmp_path / "absent.toml")]) == 0
    assert capsys.readouterr().out == ""


def test_verify_wheel_installs_with_the_declared_indexes():
    text = (ROOT / "verify-wheel" / "action.yml").read_text()
    assert "declared_indexes.py" in text
    install = text[text.index("uv pip install") :]
    assert install.index("index_args") < install.index("install_targets")
