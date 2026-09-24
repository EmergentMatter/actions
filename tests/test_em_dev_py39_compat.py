"""Pins that templates/scripts/em-dev.py parses under Python 3.9 syntax --
its stated floor (see the module docstring: it runs on macOS's system
`python3`, which is older than this repo's own 3.13 floor). A change that
sneaks in 3.10+-only syntax (a `match` statement, PEP 604 unions outside a
`from __future__ import annotations`-guarded annotation, etc.) is caught
here rather than on a contributor's Mac.
"""

from __future__ import annotations

import ast
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "templates" / "scripts" / "em-dev.py"


def test_em_dev_parses_as_python_3_9():
    source = SCRIPT.read_text()
    ast.parse(source, filename=str(SCRIPT), feature_version=(3, 9))


def test_em_dev_cmd_wrapper_delegates_to_the_py_script():
    cmd = SCRIPT.parent / "em-dev.cmd"
    text = cmd.read_text()
    assert "em-dev.py" in text
    assert text.startswith("@python")
