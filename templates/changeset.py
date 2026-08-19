#!/usr/bin/env python3
"""Create a changelog.d/ note for this change.

Copied verbatim into consuming repos at `scripts/changeset.py` — stdlib
only, no dependencies, invoked directly by path (`uv run scripts/changeset.py`)
from the repo root, with no `[project.scripts]` entry point. It must never
live inside the package (`src/<pkg>/...`): this is a contributor-only
authoring tool, and anything under the package root ships in the built
wheel — see CONTRACT.md and docs/onboarding.md for why this file's location
is load-bearing, not cosmetic.

Run it, pick a bump level, write a one-line user-facing summary.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

LEVELS = [
    ("major", "Existing callers have to change something"),
    ("minor", "New capability, nothing breaks"),
    ("patch", "Fixes, documentation, anything else user-visible"),
]

NOTES_DIR = Path("changelog.d")


def prompt_level_interactive() -> str:
    """Arrow-key selectable list, using raw terminal mode. Returns a level."""
    import termios
    import tty

    selected = 0
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    def render() -> None:
        # Raw mode turns off ONLCR, so "\n" is a bare line feed and leaves the
        # cursor in the current column. Every line must end "\r\n" or the list
        # walks diagonally across the screen on each redraw.
        lines = ["Bump level (↑↓ then Enter)"]
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
            ch = sys.stdin.read(1)
            if ch == "\x1b":  # escape sequence, e.g. arrow key
                ch2 = sys.stdin.read(1)
                ch3 = sys.stdin.read(1) if ch2 == "[" else ""
                if ch3 == "A":  # up
                    selected = (selected - 1) % len(LEVELS)
                elif ch3 == "B":  # down
                    selected = (selected + 1) % len(LEVELS)
                else:
                    continue
            elif ch in ("\r", "\n"):
                break
            elif ch == "\x03":  # Ctrl-C
                raise KeyboardInterrupt
            else:
                continue
            clear(len(LEVELS) + 1)
            render()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    print()
    return LEVELS[selected][0]


def prompt_level_numbered() -> str:
    """Fallback for non-TTY stdin/stdout: plain numbered prompt."""
    print("Bump level:")
    for i, (level, desc) in enumerate(LEVELS, start=1):
        print(f"  {i}. {level} - {desc}")
    while True:
        choice = input(f"Select 1-{len(LEVELS)}: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(LEVELS):
            return LEVELS[int(choice) - 1][0]
        print("Invalid choice, try again.")


def prompt_level() -> str:
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if interactive:
        try:
            return prompt_level_interactive()
        except Exception:
            pass  # fall through to the numbered prompt on any platform quirk
    return prompt_level_numbered()


def main() -> int:
    level = prompt_level()
    summary = input("Summary (user-facing, one line): ").strip()
    while not summary:
        summary = input("Summary can't be empty: ").strip()

    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    note_path = NOTES_DIR / f"+{secrets.token_hex(4)}.{level}.md"
    note_path.write_text(summary + "\n")

    print(f"Created {note_path}")
    print("Commit it with your change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
