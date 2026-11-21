"""scripts/normalize_changelog.py: a towncrier changelog that a Markdown formatter accepts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "normalize_changelog.py"

# What `towncrier build` writes after two releases: two blank lines between sections.
TWO_RELEASES = (
    "# Changelog\n\n<!-- towncrier release notes start -->\n\n"
    "## 0.2.1 (2026-11-21)\n\n### Patch\n\n- More.\n\n\n"
    "## 0.2.0 (2026-11-21)\n\n### Minor\n\n- First.\n\n### Patch\n\n- Fix.\n"
)
THREE_RELEASES = TWO_RELEASES.replace(
    "## 0.2.0", "## 0.2.1b (2026-11-20)\n\n### Patch\n\n- Mid.\n\n\n## 0.2.0"
)


def run(tmp_path: Path, text: str) -> tuple[str, subprocess.CompletedProcess]:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(text, encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True, check=True
    )
    return path.read_text(encoding="utf-8"), done


def test_two_sections_end_up_one_blank_line_apart(tmp_path):
    fixed, _ = run(tmp_path, TWO_RELEASES)
    assert "\n\n\n" not in fixed
    assert "- More.\n\n## 0.2.0" in fixed
    assert fixed.endswith("- Fix.\n")


def test_every_gap_between_three_or_more_sections_is_fixed(tmp_path):
    fixed, _ = run(tmp_path, THREE_RELEASES)
    assert "\n\n\n" not in fixed
    assert fixed.count("\n## ") == 3


def test_a_file_already_clean_is_left_byte_for_byte(tmp_path):
    clean, _ = run(tmp_path, TWO_RELEASES)
    again, done = run(tmp_path, clean)
    assert again == clean and done.stdout == ""


def test_blank_lines_inside_a_code_fence_are_kept(tmp_path):
    text = "## 1.0.0\n\n- Use:\n\n```\na\n\n\nb\n```\n\n\n## 0.9.0\n"
    fixed, _ = run(tmp_path, text)
    assert "a\n\n\nb" in fixed
    assert "```\n\n## 0.9.0" in fixed


def test_trailing_blank_lines_become_one_newline(tmp_path):
    fixed, _ = run(tmp_path, "## 1.0.0\n\n- x\n\n\n\n")
    assert fixed == "## 1.0.0\n\n- x\n"
