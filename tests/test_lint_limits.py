"""templates/scripts/lint-limits.py: the file-length limit and its non-growing baseline."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "lint_limits", ROOT / "templates" / "scripts" / "lint-limits.py"
)
limits = importlib.util.module_from_spec(spec)
sys.modules["lint_limits"] = limits
spec.loader.exec_module(limits)


def body(n: int) -> str:
    return "".join(f"x{i} = {i}\n" for i in range(n))


def repo(tmp_path: Path, files: dict[str, int], baseline: dict[str, int] | None = None) -> Path:
    for name, n in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body(n), encoding="utf-8")
    if baseline is not None:
        (tmp_path / "lint-baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
    return tmp_path


def run(root: Path, *args: str) -> int:
    return limits.main(["--root", str(root), *args])


def test_blank_lines_comments_and_docstrings_are_not_code():
    source = (
        '"""Module doc.\n\nOver two lines.\n"""\n\n# a comment\nx = 1\n\n\n'
        'def f():\n    """Doc."""\n    return 2\n'
    )
    assert limits.code_lines(source) == 3


def test_a_multiline_statement_counts_every_line_it_spans():
    assert limits.code_lines("x = [\n    1,\n    2,\n]\n") == 4


def test_an_unparseable_file_still_counts_its_nonblank_lines():
    assert limits.code_lines("def (:\n\nx\n") == 2


def test_a_repo_under_the_limit_passes_with_no_baseline(tmp_path):
    assert run(repo(tmp_path, {"a.py": 400})) == 0


def test_a_new_file_over_the_limit_fails_with_no_baseline(tmp_path, capsys):
    assert run(repo(tmp_path, {"a.py": 401})) == 1
    assert "a.py: 401 code lines (limit 400)" in capsys.readouterr().err


def test_a_baselined_file_may_stay_but_not_grow(tmp_path, capsys):
    root = repo(tmp_path, {"a.py": 450}, {"a.py": 450})
    assert run(root) == 0
    repo(tmp_path, {"a.py": 451})
    assert run(root) == 1
    assert "a.py: 451 code lines (baseline 450)" in capsys.readouterr().err


def test_a_shrunk_file_makes_the_baseline_stale(tmp_path, capsys):
    root = repo(tmp_path, {"a.py": 420}, {"a.py": 450})
    assert run(root) == 1
    assert "now 420, baseline says 450" in capsys.readouterr().err


def test_a_file_that_dropped_under_the_limit_must_leave_the_baseline(tmp_path, capsys):
    root = repo(tmp_path, {"a.py": 10}, {"a.py": 450})
    assert run(root) == 1
    assert "no longer over 400" in capsys.readouterr().err


def test_update_shrinks_but_refuses_to_grow_without_the_flag(tmp_path, capsys):
    root = repo(tmp_path, {"a.py": 420, "b.py": 500}, {"a.py": 450})
    assert run(root, "--update") == 1
    assert "b.py: 0 -> 500" in capsys.readouterr().err
    assert json.loads((root / "lint-baseline.json").read_text()) == {"a.py": 450}
    assert run(root, "--update", "--allow-growth") == 0
    assert json.loads((root / "lint-baseline.json").read_text()) == {"a.py": 420, "b.py": 500}
    assert run(root) == 0


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_base_check_fails_when_the_baseline_grew_against_the_ref(tmp_path, capsys):
    root = repo(tmp_path, {"a.py": 450}, {"a.py": 450})
    git(root, "init", "-q", "-b", "main")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    assert run(root, "--base", "HEAD") == 0
    repo(tmp_path, {"a.py": 460}, {"a.py": 460})
    assert run(root, "--base", "HEAD") == 1
    err = capsys.readouterr().err
    assert "baseline grew vs HEAD: a.py: 450 -> 460" in err


def test_base_check_ignores_a_ref_with_no_baseline(tmp_path):
    root = repo(tmp_path, {"a.py": 10})
    git(root, "init", "-q", "-b", "main")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    repo(tmp_path, {"b.py": 450}, {"b.py": 450})
    git(root, "add", ".")
    assert run(root, "--base", "HEAD") == 0


@pytest.mark.parametrize("skipped", [".venv/lib/x.py", "node_modules/x.py"])
def test_without_git_vendored_folders_are_skipped(tmp_path, skipped):
    assert run(repo(tmp_path, {skipped: 900})) == 0
