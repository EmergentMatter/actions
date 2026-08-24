"""Tests for templates/changeset.py.

That template is copied verbatim into consuming repos as
`scripts/changeset.py` (and `fleet_status.py` byte-compares their copies
against it), so the template is the thing under test.

The interactive picker's key handling lives in `handle_key`, a pure
function, precisely so it can be exercised here without a pty; the
termios wrapper around it is a thin loop.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "templates" / "changeset.py"
spec = importlib.util.spec_from_file_location("changeset", SCRIPT)
changeset = importlib.util.module_from_spec(spec)
sys.modules["changeset"] = changeset
spec.loader.exec_module(changeset)


# ------------------------------------------------------------------ handle_key


def test_handle_key_down_moves_and_wraps():
    assert changeset.handle_key(changeset.KEY_DOWN, 0) == (1, "move")
    last = len(changeset.LEVELS) - 1
    assert changeset.handle_key(changeset.KEY_DOWN, last) == (0, "move")


def test_handle_key_up_moves_and_wraps():
    assert changeset.handle_key(changeset.KEY_UP, 1) == (0, "move")
    last = len(changeset.LEVELS) - 1
    assert changeset.handle_key(changeset.KEY_UP, 0) == (last, "move")


@pytest.mark.parametrize("key", ["\r", "\n"])
def test_handle_key_enter_selects(key):
    assert changeset.handle_key(key, 2) == (2, "select")


@pytest.mark.parametrize("key", ["q", "Q"])
def test_handle_key_q_cancels(key):
    assert changeset.handle_key(key, 1) == (1, "cancel")


def test_handle_key_bare_esc_cancels():
    # A lone Esc used to be read as the start of an arrow sequence and then
    # block on two more reads -- "the script froze".
    assert changeset.handle_key(changeset.ESC, 1) == (1, "cancel")


def test_handle_key_ctrl_d_cancels():
    # Raw mode delivers Ctrl-D as a byte instead of closing stdin, so it has
    # to be recognised explicitly to match the numbered prompt's EOFError.
    assert changeset.handle_key(changeset.KEY_EOT, 2) == (2, "cancel")


def test_handle_key_eof_cancels():
    # "" is EOF. It used to fall into `else: continue` and spin forever.
    assert changeset.handle_key("", 0) == (0, "cancel")


def test_handle_key_ctrl_c_interrupts():
    assert changeset.handle_key(changeset.KEY_INTERRUPT, 0) == (0, "interrupt")


@pytest.mark.parametrize("key", ["x", "\x1b[C", "\x1bO"])
def test_handle_key_ignores_everything_else(key):
    assert changeset.handle_key(key, 1) == (1, "ignore")


# --------------------------------------------------------- prompt_level_numbered


def _scripted_input(monkeypatch, answers):
    """Feed `answers` to input(); an Exception instance is raised instead."""
    seen = []

    def fake_input(prompt=""):
        seen.append(prompt)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr("builtins.input", fake_input)
    return seen


def test_numbered_valid_choice(monkeypatch):
    _scripted_input(monkeypatch, ["2"])
    assert changeset.prompt_level_numbered() == "minor"


def test_numbered_invalid_then_valid(monkeypatch, capsys):
    _scripted_input(monkeypatch, ["9", "1"])
    assert changeset.prompt_level_numbered() == "major"
    assert "Invalid choice" in capsys.readouterr().out


def test_numbered_prompt_advertises_cancel(monkeypatch):
    seen = _scripted_input(monkeypatch, ["3"])
    changeset.prompt_level_numbered()
    assert seen == [f"Select 1-{len(changeset.LEVELS)} (or q to cancel): "]


@pytest.mark.parametrize("answer", ["q", "Q", " q "])
def test_numbered_q_cancels(monkeypatch, answer):
    _scripted_input(monkeypatch, [answer])
    assert changeset.prompt_level_numbered() is None


def test_numbered_eof_cancels(monkeypatch):
    _scripted_input(monkeypatch, [EOFError()])
    assert changeset.prompt_level_numbered() is None


# ------------------------------------------------------------- prompt_summary


def test_summary_reprompts_on_empty_and_names_the_way_out(monkeypatch):
    seen = _scripted_input(monkeypatch, ["", "  ", "made it faster"])
    assert changeset.prompt_summary() == "made it faster"
    assert "Ctrl-C to cancel" in seen[-1]


def test_summary_accepts_q_as_a_summary(monkeypatch):
    # "q" cannot mean cancel here -- it is a legitimate summary.
    _scripted_input(monkeypatch, ["q"])
    assert changeset.prompt_summary() == "q"


def test_summary_eof_cancels(monkeypatch):
    _scripted_input(monkeypatch, [EOFError()])
    assert changeset.prompt_summary() is None


# --------------------------------------------------------------------- main


def test_main_writes_nothing_when_level_cancelled(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # NOTES_DIR is a module-level relative Path
    monkeypatch.setattr(changeset, "prompt_level", lambda: None)
    monkeypatch.setattr(changeset, "prompt_summary", lambda: pytest.fail("not reached"))

    assert changeset.main() == 0
    assert capsys.readouterr().out.strip() == "Cancelled."
    assert not (tmp_path / "changelog.d").exists()


def test_main_writes_nothing_when_summary_cancelled(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(changeset, "prompt_level", lambda: "patch")
    monkeypatch.setattr(changeset, "prompt_summary", lambda: None)

    assert changeset.main() == 0
    assert "Cancelled." in capsys.readouterr().out
    assert list(tmp_path.glob("changelog.d/*")) == []


def test_main_happy_path_writes_named_note(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(changeset, "prompt_level", lambda: "minor")
    monkeypatch.setattr(changeset, "prompt_summary", lambda: "Added a thing")

    assert changeset.main() == 0
    notes = list((tmp_path / "changelog.d").iterdir())
    assert len(notes) == 1
    assert re.fullmatch(r"\+[0-9a-f]{8}\.minor\.md", notes[0].name)
    assert notes[0].read_text() == "Added a thing\n"
