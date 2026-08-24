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
import os
import re
import sys
import termios
import tty
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


@pytest.mark.parametrize("key,expected", [("k", 0), ("j", 2)])
def test_handle_key_accepts_vim_keys(key, expected):
    assert changeset.handle_key(key, 1) == (expected, "move")


@pytest.mark.parametrize("digit,index", [("1", 0), ("2", 1), ("3", 2)])
def test_handle_key_digit_picks_that_level(digit, index):
    # The numbered fallback trains 1-3; the picker honours it too.
    assert changeset.handle_key(digit, 0) == (index, "select")


def test_handle_key_ignores_a_digit_with_no_level(capsys):
    assert changeset.handle_key("9", 1) == (1, "ignore")


def test_handle_key_bare_esc_cancels():
    # A bare Esc must cancel outright. Waiting on it as the start of an
    # arrow sequence that never continues would hang the prompt.
    assert changeset.handle_key(changeset.ESC, 1) == (1, "cancel")


def test_handle_key_ctrl_d_cancels():
    # Raw mode delivers Ctrl-D as a byte instead of closing stdin, so it has
    # to be recognised explicitly to match the numbered prompt's EOFError.
    assert changeset.handle_key(changeset.KEY_EOT, 2) == (2, "cancel")


def test_handle_key_eof_cancels():
    # "" is EOF; anything but cancel here would spin the read loop forever
    # once stdin closes.
    assert changeset.handle_key("", 0) == (0, "cancel")


def test_handle_key_ctrl_c_interrupts():
    assert changeset.handle_key(changeset.KEY_INTERRUPT, 0) == (0, "interrupt")


@pytest.mark.parametrize("key", ["x", "\x1b[C", "\x1bO"])
def test_handle_key_ignores_everything_else(key):
    assert changeset.handle_key(key, 1) == (1, "ignore")


def test_handle_key_ignores_a_paste_marker():
    assert changeset.handle_key(changeset.PASTE_START, 1) == (1, "ignore")


# --------------------------------------------------------------------- read_key
#
# read_key is the one function here that cannot be tested without real
# file-descriptor I/O -- its whole job is resolving escape sequences by
# peeking at what is actually queued on the fd. A pty in raw mode is the
# cheapest thing that behaves like a terminal.


@pytest.fixture
def keyboard():
    """A raw-mode pty. Write keystrokes to it, read them back with read_key."""
    master, slave = os.openpty()
    tty.setraw(slave, termios.TCSANOW)

    def send(data: str) -> int:
        os.write(master, data.encode())
        return slave

    try:
        yield send
    finally:
        os.close(master)
        os.close(slave)


def test_read_key_resolves_an_arrow_from_a_single_burst(keyboard):
    # The regression guard that matters most: a terminal sends "\x1b[A" as
    # one write. Reading through sys.stdin's text layer would buffer the
    # whole burst, so select() would report nothing pending and every arrow
    # key would look like a bare Esc, which cancels.
    fd = keyboard(changeset.KEY_UP)
    assert changeset.read_key(fd) == changeset.KEY_UP


def test_read_key_returns_a_lone_esc_without_blocking(keyboard):
    fd = keyboard(changeset.ESC)
    assert changeset.read_key(fd) == changeset.ESC


def test_read_key_returns_a_plain_character(keyboard):
    fd = keyboard("q")
    assert changeset.read_key(fd) == "q"


def test_read_key_consumes_a_long_sequence_whole(keyboard):
    # Home is "\x1b[1~" -- longer than an arrow key. Stopping at a fixed
    # three bytes would leave "~" queued to be read as its own keypress.
    fd = keyboard("\x1b[1~x")
    assert changeset.read_key(fd) == "\x1b[1~"
    assert changeset.read_key(fd) == "x"


def test_read_key_consumes_an_ss3_sequence_whole(keyboard):
    fd = keyboard("\x1bOPx")
    assert changeset.read_key(fd) == "\x1bOP"
    assert changeset.read_key(fd) == "x"


def test_read_key_swallows_a_bracketed_paste(keyboard):
    # Without this, the "q" inside pasted text cancels the picker and the
    # "\r" selects a level -- a paste silently throws away the prompt.
    fd = keyboard(f"{changeset.PASTE_START}hello q world\r{changeset.PASTE_END}x")
    assert changeset.read_key(fd) == changeset.PASTE_START
    assert changeset.read_key(fd) == "x"


def test_an_unterminated_paste_does_not_hang(keyboard):
    # The bound on the swallow is what keeps a malformed or truncated paste
    # from freezing the prompt: it gives up once nothing more is queued.
    fd = keyboard(f"{changeset.PASTE_START}text that never ends")
    assert changeset.read_key(fd) == changeset.PASTE_START


def test_paste_content_never_reaches_the_state_machine(keyboard):
    fd = keyboard(f"{changeset.PASTE_START}q\r{changeset.PASTE_END}")
    _, action = changeset.handle_key(changeset.read_key(fd), 0)
    assert action == "ignore"


# --------------------------------------------------------- prompt_level_numbered


def _scripted_input(monkeypatch, answers):
    """Feed `answers` to input(); an exception instance is raised instead.

    Keyed off BaseException, not Exception, because KeyboardInterrupt is one
    of the answers these tests need to script.
    """
    seen = []

    def fake_input(prompt=""):
        seen.append(prompt)
        answer = answers.pop(0)
        if isinstance(answer, BaseException):
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


# ---------------------------------------------------------------------- ask


def test_ask_returns_none_at_eof(monkeypatch):
    _scripted_input(monkeypatch, [EOFError()])
    assert changeset.ask("prompt: ") is None


def test_ask_breaks_the_line_before_reraising_ctrl_c(monkeypatch, capsys):
    # Cooked mode echoes "^C" with no newline, so without this the top-level
    # handler's "Cancelled." lands glued to the prompt.
    _scripted_input(monkeypatch, [KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        changeset.ask("Summary (user-facing, one line): ")
    assert capsys.readouterr().out == "\n"


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
