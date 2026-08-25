"""Tests for scripts/compute_bump.py.

Each test drives the CLI as a subprocess against a tmp_path fixture, since
that's exactly how the GitHub Actions workflow will invoke it.
"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "compute_bump.py"


def write_pyproject(tmp_path: Path, version: str) -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(f'[project]\nname = "demo"\nversion = "{version}"\n')
    return path


def write_note(notes_dir: Path, name: str, body: str = "something changed") -> None:
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / name).write_text(body + "\n")


def run_compute_bump(tmp_path: Path, *extra_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--format", "json", *extra_args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


class TestBumpMatrix:
    """Each level against a known current version: standard semver, no
    special-casing below 1.0 except the major transition (covered separately).
    """

    def test_patch_bump(self, tmp_path: Path) -> None:
        write_pyproject(tmp_path, "1.2.3")
        write_note(tmp_path / "changelog.d", "+aaaa1111.patch.md")
        result = run_compute_bump(tmp_path)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload == {"level": "patch", "current": "1.2.3", "next": "1.2.4", "count": 1}

    def test_minor_bump(self, tmp_path: Path) -> None:
        write_pyproject(tmp_path, "1.2.3")
        write_note(tmp_path / "changelog.d", "+aaaa1111.minor.md")
        result = run_compute_bump(tmp_path)
        payload = json.loads(result.stdout)
        assert payload["level"] == "minor"
        assert payload["next"] == "1.3.0"

    def test_major_bump(self, tmp_path: Path) -> None:
        write_pyproject(tmp_path, "1.2.3")
        write_note(tmp_path / "changelog.d", "+aaaa1111.major.md")
        result = run_compute_bump(tmp_path)
        payload = json.loads(result.stdout)
        assert payload["level"] == "major"
        assert payload["next"] == "2.0.0"


def test_max_wins_across_mixed_pending_notes(tmp_path: Path) -> None:
    """V1: one major among twenty patches still yields major."""
    write_pyproject(tmp_path, "3.0.0")
    notes_dir = tmp_path / "changelog.d"
    for i in range(20):
        write_note(notes_dir, f"+patch{i:04x}.patch.md")
    write_note(notes_dir, "+onemajor.major.md")
    result = run_compute_bump(tmp_path)
    payload = json.loads(result.stdout)
    assert payload["level"] == "major"
    assert payload["next"] == "4.0.0"
    assert payload["count"] == 21


def test_minor_beats_patch_but_not_major(tmp_path: Path) -> None:
    write_pyproject(tmp_path, "1.0.0")
    notes_dir = tmp_path / "changelog.d"
    write_note(notes_dir, "+a.patch.md")
    write_note(notes_dir, "+b.minor.md")
    result = run_compute_bump(tmp_path)
    payload = json.loads(result.stdout)
    assert payload["level"] == "minor"


def test_below_1_0_major_transition(tmp_path: Path) -> None:
    """V2: no special handling below 1.0, so major on 0.4.2 gives 1.0.0."""
    write_pyproject(tmp_path, "0.4.2")
    write_note(tmp_path / "changelog.d", "+x.major.md")
    result = run_compute_bump(tmp_path)
    payload = json.loads(result.stdout)
    assert payload["current"] == "0.4.2"
    assert payload["next"] == "1.0.0"


def test_below_1_0_minor_and_patch_behave_normally(tmp_path: Path) -> None:
    write_pyproject(tmp_path, "0.4.2")
    write_note(tmp_path / "changelog.d", "+x.minor.md")
    result = run_compute_bump(tmp_path)
    payload = json.loads(result.stdout)
    assert payload["next"] == "0.5.0"


def test_unknown_fragment_type_exits_nonzero(tmp_path: Path) -> None:
    """V3: unknown/unparseable fragment type must error loudly, never guess."""
    write_pyproject(tmp_path, "1.0.0")
    write_note(tmp_path / "changelog.d", "+weird.banana.md")
    result = run_compute_bump(tmp_path)
    assert result.returncode == 1
    assert "banana" in result.stderr


def test_towncrier_issue_numbered_fragment_also_parses(tmp_path: Path) -> None:
    """Non-orphan towncrier fragment names (e.g. 123.minor.md) must parse too."""
    write_pyproject(tmp_path, "1.0.0")
    write_note(tmp_path / "changelog.d", "123.minor.md")
    result = run_compute_bump(tmp_path)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["level"] == "minor"


def test_dotfile_md_fragment_is_rejected_loudly(tmp_path: Path) -> None:
    """A leading-dot ".md" fragment (e.g. ".foo.major.md") must never be
    silently skipped alongside true non-fragment dotfiles like .gitkeep.
    towncrier's own fragment discovery might pick it up even though
    compute_bump.py's count didn't. That mismatch is a silent-wrong-version
    path.
    """
    write_pyproject(tmp_path, "1.0.0")
    notes_dir = tmp_path / "changelog.d"
    write_note(notes_dir, "+valid1234.patch.md")
    write_note(notes_dir, ".foo.major.md")
    result = run_compute_bump(tmp_path)
    assert result.returncode == 1
    assert ".foo.major.md" in result.stderr


def test_non_md_dotfile_is_silently_ignored(tmp_path: Path) -> None:
    """.gitkeep and similar non-.md dotfiles are not fragments and stay
    silently ignored -- only a dotfile ending in .md is rejected."""
    write_pyproject(tmp_path, "1.0.0")
    notes_dir = tmp_path / "changelog.d"
    notes_dir.mkdir()
    (notes_dir / ".gitkeep").write_text("")
    write_note(notes_dir, "+valid1234.patch.md")
    result = run_compute_bump(tmp_path)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["level"] == "patch"
    assert payload["count"] == 1


def test_empty_notes_dir_yields_level_none(tmp_path: Path) -> None:
    write_pyproject(tmp_path, "1.0.0")
    (tmp_path / "changelog.d").mkdir()
    result = run_compute_bump(tmp_path)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload == {"level": "none", "current": "1.0.0", "next": "1.0.0", "count": 0}


def test_missing_notes_dir_yields_level_none(tmp_path: Path) -> None:
    """The caller stops when level=none; this is not an error even if the
    notes directory doesn't exist at all yet."""
    write_pyproject(tmp_path, "1.0.0")
    result = run_compute_bump(tmp_path)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["level"] == "none"


def test_github_format_appends_to_github_output(tmp_path: Path, monkeypatch) -> None:
    write_pyproject(tmp_path, "1.0.0")
    write_note(tmp_path / "changelog.d", "+a.patch.md")
    output_file = tmp_path / "gh_output.txt"
    output_file.write_text("")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--format", "github"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "GITHUB_OUTPUT": str(output_file)},
    )
    assert result.returncode == 0
    contents = output_file.read_text()
    assert "level=patch" in contents
    assert "current=1.0.0" in contents
    assert "next=1.0.1" in contents
    assert "count=1" in contents
