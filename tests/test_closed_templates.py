"""The closed-tier CI template and the sign-in action.

Text-level, like the other workflow contract tests: what a runner does with
them is only proven by a live run. These pin the rules a closed repo's CI
breaks on if they are missed.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = (ROOT / "templates" / "ci-closed.yml").read_text(encoding="utf-8")
ACTION = (ROOT / "codeartifact-read" / "action.yml").read_text(encoding="utf-8")
GUARD = "if: github.event_name != 'pull_request' || github.event.pull_request.draft == false"


def _jobs(text: str) -> dict[str, str]:
    """Each top-level job's body, from the first real `  name:` after `jobs:`."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"(?m)^  ([a-z]+):\s*$", body)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def test_every_job_that_syncs_signs_in_and_may_request_a_token():
    jobs = _jobs(CI)
    assert {"lint", "format", "typecheck", "test", "floor", "build"} <= set(jobs)
    for name, body in jobs.items():
        assert "codeartifact-read@v1" in body, name
        assert "id-token: write" in body, name
        assert body.index("checkout") < body.index("codeartifact-read") < body.index("setup-uv")


def test_every_job_keeps_the_draft_guard():
    for name, body in _jobs(CI).items():
        assert GUARD in body, name


def test_the_workflow_itself_grants_no_token():
    head = CI.split("\njobs:\n", 1)[0]
    live = [ln for ln in head.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("id-token" in ln for ln in live)


def test_test_job_checks_lock_sources_and_floor_leaves_the_lock_alone():
    jobs = _jobs(CI)
    assert "check-lock-sources@v1" in jobs["test"]
    floor = jobs["floor"]
    assert "uv sync --resolution lowest-direct" in floor
    # the resolution happens in a copy: the real lock file is never rewritten
    assert floor.index("cp -R . ") < floor.index("uv sync --resolution lowest-direct")


def test_sign_in_does_nothing_without_the_index_and_requires_a_role_with_it():
    assert "declared_indexes.py" in ACTION
    assert "needed == 'true' && inputs.role-arn == ''" in ACTION
    for step in ("Assume the read-only role", "Authenticate uv to the index"):
        block = ACTION.split(f"name: {step}", 1)[1].split("- name:", 1)[0]
        assert "steps.needed.outputs.needed == 'true'" in block


def test_sign_in_defaults_match_the_release_workflow():
    version = (ROOT / ".github" / "workflows" / "version.yml").read_text(encoding="utf-8")
    for key, default in (
        ("domain", "em"),
        ("domain-owner", '"967228661072"'),
        ("region", "us-east-2"),
    ):
        assert re.search(
            rf"(?m)^  {key}:\n(?:    .*\n)*?    default: {re.escape(default)}\n", ACTION
        )
    assert "codeartifact-domain-owner" in version and "967228661072" in version
