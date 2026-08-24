#!/usr/bin/env python3
"""Create a changelog.d/ note for this change.

Copied verbatim into consuming repos at `scripts/changeset.py` — stdlib
only, no dependencies, invoked directly by path (`uv run scripts/changeset.py`)
from the repo root, with no `[project.scripts]` entry point. It must never
live inside the package (`src/<pkg>/...`): this is a contributor-only
authoring tool, and anything under the package root ships in the built
wheel — see CONTRACT.md and docs/onboarding.md for why this file's location
is load-bearing, not cosmetic.

Run it, pick a bump level, write a one-line user-facing summary. `q` or
`Esc` backs out of the level picker without writing anything.
"""

from __future__ import annotations

import os
import secrets
import select
import sys
from pathlib import Path

LEVELS = [
    ("major", "Existing callers have to change something"),
    ("minor", "New capability, nothing breaks"),
    ("patch", "Fixes, documentation, anything else user-visible"),
]

NOTES_DIR = Path("changelog.d")

ESC = "\x1b"
KEY_UP = "\x1b[A"
KEY_DOWN = "\x1b[B"
KEY_INTERRUPT = "\x03"  # Ctrl-C
# Ctrl-D. Raw mode hands this over as a plain byte instead of closing stdin,
# so it has to be recognised explicitly to mean what it already means at the
# numbered prompt, where the line discipline turns it into an EOFError.
KEY_EOT = "\x04"

# How long to wait for the rest of an escape sequence before concluding the
# user pressed a bare Esc. Arrow keys arrive as one burst, so anything that
# hasn't landed within this is not part of the sequence.
ESC_SEQUENCE_TIMEOUT = 0.05


def handle_key(ch: str, selected: int) -> tuple[int, str]:
    """Pure state machine for the interactive picker.

    Takes the key just read (an arrow key arrives as its whole escape
    sequence; "" means EOF) and the current selection, and returns the new
    selection plus one of "move", "select", "cancel", "interrupt", "ignore".
    Kept free of terminal I/O so it can be tested without a pty.
    """
    if ch == KEY_UP:
        return (selected - 1) % len(LEVELS), "move"
    if ch == KEY_DOWN:
        return (selected + 1) % len(LEVELS), "move"
    if ch in ("\r", "\n"):
        return selected, "select"
    if ch in ("q", "Q", ESC, KEY_EOT, ""):
        # A bare Esc is a deliberate back-out; "" is EOF (stdin closing under
        # us) and must not be looped on.
        return selected, "cancel"
    if ch == KEY_INTERRUPT:
        return selected, "interrupt"
    return selected, "ignore"


def read_key(fd: int) -> str:
    r"""Read one keypress from `fd`, resolving arrow-key escape sequences.

    Reads with `os.read` rather than `sys.stdin.read` so the `select` peek
    below is accurate: stdin's text layer pulls the whole burst ("\x1b[A")
    into its own buffer on the first read, after which `select` would report
    nothing pending and a real arrow key would look like a bare Esc.

    Returns "" at EOF, ESC for a lone Esc, KEY_UP/KEY_DOWN for the arrows,
    and the raw bytes read for anything else.
    """

    def read_one() -> str:
        return os.read(fd, 1).decode("utf-8", "replace")

    def pending() -> bool:
        return bool(select.select([fd], [], [], ESC_SEQUENCE_TIMEOUT)[0])

    ch = read_one()
    if ch != ESC or not pending():
        # Nothing followed the Esc, so it is a keypress in its own right and
        # reading further would block — the hang this guards against.
        return ch
    ch2 = read_one()
    if ch2 != "[" or not pending():
        return ESC + ch2
    return ESC + ch2 + read_one()


def prompt_level_interactive() -> str | None:
    """Arrow-key selectable list, using raw terminal mode.

    Returns the chosen level, or None if the user cancelled with q/Esc/EOF.
    """
    import termios
    import tty

    selected = 0
    action = "cancel"
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    def render() -> None:
        # Raw mode turns off ONLCR, so "\n" is a bare line feed and leaves the
        # cursor in the current column. Every line must end "\r\n" or the list
        # walks diagonally across the screen on each redraw.
        lines = ["Bump level (↑↓ then Enter, q to cancel)"]
        for i, (level, desc) in enumerate(LEVELS):
            marker = "❯" if i == selected else " "
            lines.append(f"  {marker} {level:<8} {desc}")
        sys.stdout.write("".join(f"{line}\r\n" for line in lines))
        sys.stdout.flush()

    def clear(n: int) -> None:
        sys.stdout.write("\r")
        for _ in range(n):
            sys.stdout.write("\x1b[1A\x1b[2K")
        sys.stdout.flush()

    try:
        tty.setraw(fd)
        render()
        while True:
            selected, action = handle_key(read_key(fd), selected)
            if action == "move":
                clear(len(LEVELS) + 1)
                render()
            elif action != "ignore":
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    if action == "interrupt":
        raise KeyboardInterrupt
    if action == "cancel":
        return None
    print()
    return LEVELS[selected][0]


def prompt_level_numbered() -> str | None:
    """Fallback for non-TTY stdin/stdout: plain numbered prompt.

    Returns the chosen level, or None if the user cancelled with q/EOF.
    """
    print("Bump level:")
    for i, (level, desc) in enumerate(LEVELS, start=1):
        print(f"  {i}. {level} - {desc}")
    while True:
        try:
            choice = input(f"Select 1-{len(LEVELS)} (or q to cancel): ").strip()
        except EOFError:
            print()
            return None
        if choice.lower() == "q":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(LEVELS):
            return LEVELS[int(choice) - 1][0]
        print("Invalid choice, try again.")


def prompt_level() -> str | None:
    """Returns the chosen level, or None if the user cancelled.

    Cancelling is a return value rather than a `sys.exit` so the broad
    `except` below can't be tempted to swallow it and so it stays testable.
    """
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if interactive:
        try:
            return prompt_level_interactive()
        except Exception as exc:  # noqa: BLE001 - degrade to the numbered prompt on any
            # platform quirk (e.g. `import termios` failing on Windows) rather than
            # taking the script down. A cancel is None, not an exception, so it
            # returns above instead of falling through into a second prompt.
            print(f"interactive prompt unavailable ({exc}); using numbered prompt", file=sys.stderr)
    return prompt_level_numbered()


def prompt_summary() -> str | None:
    """Ask for the one-line summary. Returns None if the user cancelled.

    An empty summary re-prompts rather than cancelling: "q" is a perfectly
    good summary, so no printable string can mean "back out" here. Ctrl-C
    and Ctrl-D (EOFError) are the ways out, and the re-prompt says so.
    """
    prompt = "Summary (user-facing, one line): "
    while True:
        try:
            summary = input(prompt).strip()
        except EOFError:
            print()
            return None
        if summary:
            return summary
        prompt = "Summary can't be empty (Ctrl-C to cancel): "


def main() -> int:
    level = prompt_level()
    if level is None:
        print("Cancelled.")
        return 0

    summary = prompt_summary()
    if summary is None:
        print("Cancelled.")
        return 0

    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    note_path = NOTES_DIR / f"+{secrets.token_hex(4)}.{level}.md"
    note_path.write_text(summary + "\n")

    print(f"Created {note_path}")
    print("Commit it with your change.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # Ctrl-C at any prompt: no traceback, and 130 is the conventional
        # exit code for dying to SIGINT.
        print("Cancelled.")
        sys.exit(130)
