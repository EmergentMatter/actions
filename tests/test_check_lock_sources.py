"""scripts/check_lock_sources.py: an organization package must come from an index it may."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_lock_sources", ROOT / "scripts" / "check_lock_sources.py"
)
check = importlib.util.module_from_spec(spec)
sys.modules["check_lock_sources"] = check
spec.loader.exec_module(check)

PUBLIC = "https://get.example.test/simple"
PRIVATE = "https://private.d.codeartifact.example.amazonaws.com/pypi/r/simple/"
PYPROJECT = (
    f'[[tool.uv.index]]\nname = "em"\nurl = "{PUBLIC}"\nexplicit = true\n'
    f'[[tool.uv.index]]\nname = "em-codeartifact"\nurl = "{PRIVATE}"\nexplicit = true\n'
)
PYPROJECT_WITH_PYPI = PYPROJECT + (
    '[[tool.uv.index]]\nname = "pypi"\nurl = "https://pypi.org/simple"\n'
)


def lock_text(*entries: tuple[str, str]) -> str:
    return "version = 1\n" + "".join(
        f'\n[[package]]\nname = "{name}"\nversion = "1.0.0"\nsource = {source}\n'
        for name, source in entries
    )


def run(tmp_path, capsys, lock: str, pyproject: str = PYPROJECT, *extra: str) -> tuple[int, str]:
    (tmp_path / "pyproject.toml").write_text(pyproject)
    (tmp_path / "uv.lock").write_text(lock)
    code = check.main(
        [
            "--lock",
            str(tmp_path / "uv.lock"),
            "--pyproject",
            str(tmp_path / "pyproject.toml"),
            *extra,
        ]
    )
    return code, capsys.readouterr().err


def registry(url: str) -> str:
    return f'{{ registry = "{url}" }}'


def test_packages_from_a_declared_index_pass_whatever_the_trailing_slash(tmp_path, capsys):
    lock = lock_text(
        ("emergent-matter-a", registry(PUBLIC + "/")),
        ("requests", registry("https://pypi.org/simple")),
    )
    assert run(tmp_path, capsys, lock)[0] == 0


def test_an_org_package_from_an_undeclared_index_fails_and_is_named(tmp_path, capsys):
    lock = lock_text(("emergent-matter-b", registry("https://elsewhere.test/simple")))
    code, err = run(tmp_path, capsys, lock)
    assert code == 1
    assert "emergent-matter-b 1.0.0 came from https://elsewhere.test/simple" in err


def test_editable_and_virtual_entries_are_not_judged(tmp_path, capsys):
    lock = lock_text(
        ("emergent-matter-c", '{ editable = "." }'), ("emergent-matter-d", '{ virtual = "." }')
    )
    assert run(tmp_path, capsys, lock)[0] == 0


def test_a_closed_package_from_the_public_org_index_fails(tmp_path, capsys):
    lock = lock_text(("emergent-matter-sdm-core-private", registry(PUBLIC)))
    code, err = run(tmp_path, capsys, lock, PYPROJECT, "--private-index", "em-codeartifact")
    assert code == 1 and "closed package" in err and "not the private index" in err


def test_a_closed_package_from_the_private_index_passes(tmp_path, capsys):
    lock = lock_text(
        ("emergent-matter-sdm-core-private", registry(PRIVATE)),
        ("emergent-matter-sdm-cem-kit", registry(PRIVATE)),
        ("emergent-matter-sdm-engine", registry(PRIVATE)),
        ("emergent-matter-sdm-core", registry(PUBLIC)),
    )
    assert run(tmp_path, capsys, lock, PYPROJECT, "--private-index", "em-codeartifact")[0] == 0


def test_a_closed_package_with_no_private_index_declared_fails(tmp_path, capsys):
    only_public = f'[[tool.uv.index]]\nname = "em"\nurl = "{PUBLIC}"\nexplicit = true\n'
    lock = lock_text(("emergent-matter-sdm-sidecar", registry(PUBLIC)))
    code, err = run(tmp_path, capsys, lock, only_public, "--private-index", "em-codeartifact")
    assert code == 1 and "no private index is declared" in err


@pytest.mark.parametrize("name", ["emergent-matter-x", "sdm-view", "em-release-control-test"])
def test_an_org_package_never_comes_from_pypi_even_when_pypi_is_declared(tmp_path, capsys, name):
    lock = lock_text((name, registry("https://pypi.org/simple")))
    code, err = run(tmp_path, capsys, lock, PYPROJECT_WITH_PYPI)
    assert code == 1 and "the public index" in err


def test_names_outside_the_prefix_are_held_to_the_declared_indexes(tmp_path, capsys):
    lock = lock_text(("sdm-view", registry("https://elsewhere.test/simple")))
    code, err = run(tmp_path, capsys, lock)
    assert code == 1 and "sdm-view" in err
    assert run(tmp_path, capsys, lock_text(("sdm-view", registry(PUBLIC))))[0] == 0


def test_name_spellings_are_normalised(tmp_path, capsys):
    lock = lock_text(("Emergent_Matter.SDM.Core_Private", registry(PUBLIC)))
    assert run(tmp_path, capsys, lock, PYPROJECT, "--private-index", "em-codeartifact")[0] == 1


def test_the_org_and_closed_name_lists_can_be_replaced(tmp_path, capsys):
    lock = lock_text(("acme-widget", registry(PUBLIC)))
    args = ("--prefix", "acme-", "--private-names", "acme-*", "--private-index", "em-codeartifact")
    assert run(tmp_path, capsys, lock, PYPROJECT, *args)[0] == 1


ANNOTATED = """\
emergent-matter-toy-core==1.1.0
    # via gw-app
    # from {public}
emergent-matter-toy-ghost==1.0.0
    # via gw-app
    # from {pypi}
requests==2.0.0
    # via gw-app
    # from {pypi}
/some/where/gw_app-0.1.0-py3-none-any.whl
    # via -r req.txt
"""


def test_resolved_output_fails_an_org_name_that_fell_through_to_the_default_index(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    resolved = tmp_path / "resolved.txt"
    resolved.write_text(ANNOTATED.format(public=PUBLIC, pypi="https://pypi.org/simple"))
    code = check.main(
        ["--resolved", str(resolved), "--pyproject", str(tmp_path / "pyproject.toml")]
    )
    err = capsys.readouterr().err
    assert code == 1
    assert "emergent-matter-toy-ghost 1.0.0 came from https://pypi.org/simple" in err
    assert "requests" not in err and "emergent-matter-toy-core" not in err


def test_resolved_output_fails_an_org_name_that_fell_through_to_an_undeclared_default(
    tmp_path, capsys
):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    resolved = tmp_path / "resolved.txt"
    resolved.write_text(ANNOTATED.format(public=PUBLIC, pypi="http://127.0.0.1:8002/simple"))
    code = check.main(
        ["--resolved", str(resolved), "--pyproject", str(tmp_path / "pyproject.toml")]
    )
    assert code == 1 and "does not declare" in capsys.readouterr().err


def test_resolved_output_passes_when_every_org_name_is_from_a_declared_index(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    resolved = tmp_path / "resolved.txt"
    resolved.write_text(ANNOTATED.format(public=PUBLIC, pypi=PUBLIC).replace("toy-ghost", "toy-g"))
    assert (
        check.main(["--resolved", str(resolved), "--pyproject", str(tmp_path / "pyproject.toml")])
        == 0
    )


def test_an_org_package_with_no_recorded_index_cannot_be_placed(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    resolved = tmp_path / "resolved.txt"
    resolved.write_text("emergent-matter-toy-core==1.1.0\n    # via x\n")
    code = check.main(
        ["--resolved", str(resolved), "--pyproject", str(tmp_path / "pyproject.toml")]
    )
    assert code == 1 and "no recorded index" in capsys.readouterr().err


def test_a_mixed_case_public_host_is_still_the_public_index(tmp_path, capsys):
    lock = lock_text(("emergent-matter-x", registry("https://PyPI.org/simple")))
    code, err = run(tmp_path, capsys, lock, PYPROJECT_WITH_PYPI)
    assert code == 1 and "the public index" in err
