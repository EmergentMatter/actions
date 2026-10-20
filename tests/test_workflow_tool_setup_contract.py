"""A job that runs `uv` must set uv up first.

Text-level, like the other workflow contract tests: a job is the block under
a 2-space-indented key beneath `jobs:`, a step is a `- ` item at 6 spaces.
A runner image does not ship uv, so a `run:` step calling it in a job with no
earlier `astral-sh/setup-uv` step exits 127 -- which no other test here can
see, since no workflow is ever run by this suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml"))
UV_COMMAND = re.compile(
    r"(?:^|[\s(;|&])uvx?\s+(?:build|version|lock|publish|pip|venv|sync|run|add|tool|python|"
    r"towncrier|[a-z][\w-]*==)"
)
SETUP_UV = "astral-sh/setup-uv@"


def _jobs(text: str) -> dict[str, str]:
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):\n", body, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def _steps(job: str) -> list[str]:
    return re.split(r"^      - ", job, flags=re.MULTILINE)[1:]


def _runs_uv(step: str) -> list[str]:
    """The lines of a step's `run:` that invoke uv as a command."""
    match = re.search(r"^        run: (.*(?:\n(?: {10,}.*|\s*))*)", step, flags=re.MULTILINE)
    if match is None:
        return []
    found = []
    for line in match.group(1).splitlines():
        stripped = line.strip().removeprefix("|").strip()
        if not stripped or stripped.startswith(("#", "echo", "printf")):
            continue
        if UV_COMMAND.search(stripped):
            found.append(stripped)
    return found


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_job_runs_uv_before_setting_it_up(path):
    text = path.read_text(encoding="utf-8")
    if "\njobs:\n" not in text:
        pytest.skip("no jobs")
    offenders = []
    for job_name, job in _jobs(text).items():
        ready = False
        for step in _steps(job):
            if SETUP_UV in step:
                ready = True
            if not ready:
                offenders += [f"{job_name}: {line}" for line in _runs_uv(step)]
    assert not offenders, (
        f"{path.name} runs uv in a job before any {SETUP_UV} step:\n  " + "\n  ".join(offenders)
    )


def test_the_check_sees_a_job_that_forgets_uv():
    job = "  release:\n    steps:\n      - name: Build\n        run: uv build\n"
    (job_text,) = _jobs("x\njobs:\n" + job).values()
    assert _runs_uv(_steps(job_text)[0]) == ["uv build"]


def test_the_check_sees_the_workflows_that_do_run_uv():
    """Guards against the scan matching nothing at all."""
    seen = 0
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        if "\njobs:\n" not in text:
            continue
        for job in _jobs(text).values():
            seen += sum(len(_runs_uv(step)) for step in _steps(job))
    assert seen >= 4
