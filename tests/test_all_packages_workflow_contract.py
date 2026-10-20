"""Tests for the `all-packages` contract across version.yml, build-release.yml
and the verify-wheel action.

Text-level for the shape, like test_version_workflow_contract.py, plus one
integration test that runs the real bump step's shell, extracted from
version.yml, against a throwaway uv workspace: a workflow's `run:` block is
bash, and bash can be driven without a runner. What it cannot show is that
GitHub accepts the workflow, which only a live run proves.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION = REPO_ROOT / ".github" / "workflows" / "version.yml"
BUILD_RELEASE = REPO_ROOT / ".github" / "workflows" / "build-release.yml"
VERIFY_ACTION = REPO_ROOT / "verify-wheel" / "action.yml"
STUBS = [
    REPO_ROOT / "templates" / "stub-version.yml",
    REPO_ROOT / "templates" / "stub-build-release.yml",
]
CONTRACT = REPO_ROOT / "CONTRACT.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _step_run(text: str, step_name: str) -> str:
    """The dedented `run: |` body of the step with this exact `name:`."""
    match = re.search(
        rf"^( *)- name: {re.escape(step_name)}\n(?:.*\n)*?\1  run: \|\n((?:\1    .*\n|\n)+)",
        text,
        re.MULTILINE,
    )
    assert match, f"no run block for step {step_name!r}"
    indent = len(match.group(1)) + 4
    return "\n".join(line[indent:] for line in match.group(2).splitlines()) + "\n"


@pytest.mark.parametrize("path", [VERSION, BUILD_RELEASE])
def test_all_packages_is_an_off_by_default_boolean_input(path):
    text = _text(path)
    match = re.search(
        r"^ {6}all-packages:\n(?:.*\n)*? {8}type: boolean\n {8}default: (\w+)", text, re.MULTILINE
    )
    assert match is not None, f"{path.name} lacks the all-packages input"
    assert match.group(1) == "false"


def test_verify_wheel_input_is_off_by_default():
    text = _text(VERIFY_ACTION)
    assert re.search(r'^  all-packages:\n(?:.*\n)*?    default: "false"', text, re.MULTILINE)


@pytest.mark.parametrize("path", [VERSION, BUILD_RELEASE])
def test_both_build_steps_choose_all_packages_the_same_way(path):
    body = _step_run(_text(path), "Build wheel + sdist")
    assert (
        'if [ "${ALL_PACKAGES}" = "true" ]; then\n  uv build --all-packages\nelse\n  uv build\nfi'
        in body
    )


def test_the_two_build_steps_are_identical():
    assert _step_run(_text(VERSION), "Build wheel + sdist") == _step_run(
        _text(BUILD_RELEASE), "Build wheel + sdist"
    )


@pytest.mark.parametrize("path", [VERSION, BUILD_RELEASE])
def test_static_index_with_all_packages_is_refused_in_input_validation(path):
    body = _step_run(_text(path), "Validate inputs for this publish target")
    assert 'if [ "${ALL_PACKAGES}" = "true" ]; then' in body
    assert "static-index|both)" in body.split("static-index-bucket is required")[0]
    assert "all-packages cannot publish to" in body


def test_the_bump_step_keeps_the_single_package_path_verbatim():
    body = _step_run(_text(VERSION), "Bump [project] version and refresh the lockfile")
    assert '  uv version --bump "${LEVEL}"\n  uv lock\n  actual=$(' in body
    assert "Semver bump semantics have diverged" in body


def test_the_bump_step_locks_once_after_bumping_every_package():
    body = _step_run(_text(VERSION), "Bump [project] version and refresh the lockfile")
    multi = body.split('if [ "${ALL_PACKAGES}" = "true" ]; then')[1].split("\nelse\n  uv version")[
        0
    ]
    assert multi.count("uv lock") == 1
    assert (
        multi.index("--package")
        < multi.index("uv add")
        < multi.index("uv lock")
        < multi.index("check --version")
    )


def test_version_workflow_still_has_no_permissions_key_after_the_change():
    assert not re.search(r"^permissions:", _text(VERSION), re.MULTILINE)


@pytest.mark.parametrize("stub", STUBS)
def test_stubs_document_all_packages_as_a_commented_example(stub):
    assert "# all-packages: true" in _text(stub)
    assert not re.search(r"^\s+all-packages:", _text(stub), re.MULTILINE)


def test_contract_documents_all_packages():
    text = _text(CONTRACT)
    assert "### Releasing several packages together (`all-packages`)" in text
    assert "`all-packages`=`false`" in text


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_the_bump_step_bumps_every_member_and_fails_when_one_is_out_of_step(tmp_path):
    _run_bump_cases(tmp_path)


def _run_bump_cases(tmp_path):
    step = _step_run(_text(VERSION), "Bump [project] version and refresh the lockfile")

    def make(
        tools_version: str,
        dependency: str = "eng-tools>=1",
        extra_dependency: str | None = None,
    ) -> Path:
        root = tmp_path / f"ws-{tools_version}-{abs(hash((dependency, extra_dependency)))}"
        (root / "src" / "eng").mkdir(parents=True)
        (root / "src" / "eng" / "__init__.py").write_text("")
        (root / "packages" / "tools" / "src" / "tools").mkdir(parents=True)
        (root / "packages" / "tools" / "src" / "tools" / "__init__.py").write_text("")
        build = '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
        (root / "pyproject.toml").write_text(
            '[project]\nname = "eng"\nversion = "1.2.3"\nrequires-python = ">=3.11"\n'
            f'dependencies = [\n  "{dependency}",  # keep this comment\n]\n'
            + (
                f'[project.optional-dependencies]\nextra = ["{extra_dependency}"]\n'
                if extra_dependency
                else ""
            )
            + "[tool.uv.sources]\neng-tools = { workspace = true }\n"
            '[tool.uv.workspace]\nmembers = ["packages/*"]\n'
            + build
            + '[tool.hatch.build.targets.wheel]\npackages = ["src/eng"]\n'
        )
        (root / "packages" / "tools" / "pyproject.toml").write_text(
            f'[project]\nname = "eng-tools"\nversion = "{tools_version}"\n'
            'requires-python = ">=3.11"\n'
            + build
            + '[tool.hatch.build.targets.wheel]\npackages = ["src/tools"]\n'
        )
        (tmp_path / "runner-temp").mkdir(exist_ok=True)
        if not (tmp_path / "runner-temp" / "em-actions").exists():
            (tmp_path / "runner-temp" / "em-actions").symlink_to(REPO_ROOT)
        return root

    def run(root: Path) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": __import__("os").environ["PATH"],
            "HOME": __import__("os").environ.get("HOME", ""),
            "LEVEL": "minor",
            "EXPECTED_NEXT": "1.3.0",
            "ALL_PACKAGES": "true",
            "RUNNER_TEMP": str(tmp_path / "runner-temp"),
        }
        return subprocess.run(
            ["bash", "-c", step], cwd=root, env=env, capture_output=True, text=True, check=False
        )

    in_step = make("1.2.3")
    done = run(in_step)
    assert done.returncode == 0, done.stderr
    for pyproject in (
        in_step / "pyproject.toml",
        in_step / "packages" / "tools" / "pyproject.toml",
    ):
        assert 'version = "1.3.0"' in pyproject.read_text()

    out_of_step = make("1.0.0")
    failed = run(out_of_step)
    assert failed.returncode == 1
    assert "not in lockstep" in failed.stderr and "eng-tools is at 1.1.0" in failed.stderr

    # An exact pin between members moves with the bump, a range does not,
    # and the comment beside the pin survives.
    pinned = make("1.2.3", "eng-tools==1.2.3", "eng-tools[x]==1.2.3; python_version >= '3.11'")
    done = run(pinned)
    assert done.returncode == 0, done.stderr
    text = (pinned / "pyproject.toml").read_text()
    assert '"eng-tools==1.3.0",  # keep this comment' in text
    # uv rewrites the entry it touches, normalizing the marker's spelling.
    assert re.search(r"eng-tools\[x\]==1\.3\.0 ?; python_\w+ >= '3\.11'", text)

    ranged = make("1.2.3", "eng-tools>=1,<2")
    assert run(ranged).returncode == 0
    assert '"eng-tools>=1,<2",' in (ranged / "pyproject.toml").read_text()

    # The check names an internal pin left behind.
    (pinned / "pyproject.toml").write_text(
        text.replace('"eng-tools==1.3.0",', '"eng-tools==1.2.3",')
    )
    check = subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "scripts" / "workspace_packages.py"),
            "--root",
            str(pinned),
            "check",
            "--version",
            "1.3.0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 1
    assert "eng pins eng-tools==1.2.3, expected ==1.3.0" in check.stderr


# ------------------------------------------- the shared checkout stays out of builds


@pytest.mark.parametrize("path", [VERSION, BUILD_RELEASE])
def test_every_shared_checkout_is_moved_out_of_the_workspace_right_after(path):
    text = _text(path)
    checkouts = [m.start() for m in re.finditer(r"path: \.em-actions\n", text)]
    assert checkouts
    for start in checkouts:
        following = text[start : start + 700]
        assert "- name: Move the shared scripts out of the workspace" in following
        assert 'run: mv .em-actions "${RUNNER_TEMP}/em-actions"' in following


@pytest.mark.parametrize("path", [VERSION, BUILD_RELEASE])
def test_no_script_is_run_from_inside_the_workspace(path):
    text = _text(path)
    assert not re.search(r'"\.em-actions/', text)
    assert '"${RUNNER_TEMP}/em-actions/scripts/' in text


def test_the_release_pr_has_no_cleanup_step_for_the_shared_checkout():
    assert "rm -rf .em-actions" not in _text(VERSION)


def test_the_two_sdist_checks_are_identical_and_follow_the_build():
    for path in (VERSION, BUILD_RELEASE):
        text = _text(path)
        build = text.index("- name: Build wheel + sdist")
        check = text.index("- name: Refuse an sdist that carries the shared checkout")
        assert (
            build < check < text.index("- name: Create the GitHub Release (with build artifacts)")
        )
    assert _step_run(
        _text(VERSION), "Refuse an sdist that carries the shared checkout"
    ) == _step_run(_text(BUILD_RELEASE), "Refuse an sdist that carries the shared checkout")


def _sdist(path: Path, members: list[str]) -> None:
    import io
    import tarfile

    with tarfile.open(path, "w:gz") as tf:
        for member in members:
            data = b"x"
            info = tarfile.TarInfo(member)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


@pytest.mark.parametrize(
    ("members", "ok"),
    [
        (["pkg-1/pyproject.toml", "pkg-1/src/pkg/__init__.py"], True),
        (["pkg-1/pyproject.toml", "pkg-1/.em-actions/scripts/x.py"], False),
        (["pkg-1/.em-actions"], False),
        (["pkg-1/src/not-em-actions/x.py", "pkg-1/.em-actions-notes.md"], True),
    ],
)
def test_the_sdist_check_refuses_a_shared_checkout_in_any_sdist(tmp_path, members, ok):
    step = _step_run(_text(VERSION), "Refuse an sdist that carries the shared checkout")
    (tmp_path / "dist").mkdir()
    _sdist(tmp_path / "dist" / "clean-1.tar.gz", ["clean-1/pyproject.toml"])
    # The second sdist is the one under test, as an all-packages build has several.
    _sdist(tmp_path / "dist" / "other-1.tar.gz", members)
    done = subprocess.run(
        ["bash", "-c", step], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert (done.returncode == 0) is ok, done.stderr
    if not ok:
        assert "other-1.tar.gz contains .em-actions/" in done.stdout
