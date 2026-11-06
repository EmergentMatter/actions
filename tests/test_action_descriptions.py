"""A composite action's metadata is parsed for expressions before anything runs.

An input or action `description` is documentation, but GitHub evaluates `${{ ... }}` in it
and rejects the whole action when the context (`vars`, `secrets`) does not exist there, so
every job that uses the action fails at set-up. Write the name of a variable, not an
expression, in a description.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = sorted(ROOT.glob("*/action.yml"))
DESCRIPTION = re.compile(
    r"^(?P<indent>\s*)description:(?P<first>.*)\n(?P<rest>(?:(?P=indent)[ ]+.*\n|\n)*)", re.M
)


def test_there_are_composite_actions_to_check():
    assert len(ACTIONS) >= 4


@pytest.mark.parametrize("path", ACTIONS, ids=lambda p: p.parent.name)
def test_no_description_holds_an_expression(path):
    text = path.read_text(encoding="utf-8")
    bad = [
        m.group(0).strip().splitlines()[0]
        for m in DESCRIPTION.finditer(text)
        if "${{" in m.group("first") + m.group("rest")
    ]
    assert not bad, f"{path.parent.name}: a description contains ${{{{ }}}}: {bad}"
