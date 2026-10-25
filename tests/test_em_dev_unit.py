"""Unit tests for templates/scripts/em-dev.py that never invoke `uv`.

Covers the bundled TOML reader, lock parsing, name/folder discovery,
`--only` matching, config/env precedence, freshness state, the installed-
environment introspection em-dev shares with `status` and tool mode, and
the "nothing to make local" no-op path. Everything that actually runs `uv`
(the two-pass install, tool mode) is in test_em_dev_integration.py and
test_em_dev_tool_mode.py instead, both marked `integration`.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

# ------------------------------------------------------------------- TOML


def test_toml_reads_basic_and_literal_strings(em_dev):
    data = em_dev.toml_loads("a = \"hi\\n\"\nb = 'raw\\nnot-escaped'\n")
    assert data["a"] == "hi\n"
    assert data["b"] == "raw\\nnot-escaped"


def test_toml_reads_triple_quoted_strings_trimming_leading_newline(em_dev):
    data = em_dev.toml_loads('a = """\nline one\nline two"""\n')
    assert data["a"] == "line one\nline two"


def test_toml_reads_numbers_and_booleans(em_dev):
    data = em_dev.toml_loads("i = 42\nf = 3.5\nt = true\nfa = false\nneg = -7\n")
    assert data == {"i": 42, "f": 3.5, "t": True, "fa": False, "neg": -7}


def test_toml_reads_arrays_multiline_with_trailing_comma(em_dev):
    data = em_dev.toml_loads("a = [\n  1,\n  2,\n  3,\n]\n")
    assert data["a"] == [1, 2, 3]


def test_toml_reads_inline_tables_in_arrays(em_dev):
    data = em_dev.toml_loads('deps = [{ name = "a" }, { name = "b", extra = ["x"] }]\n')
    assert data["deps"] == [{"name": "a"}, {"name": "b", "extra": ["x"]}]


def test_toml_reads_nested_tables_and_array_of_tables(em_dev):
    text = '[[package]]\nname = "a"\n[package.metadata]\nx = 1\n[[package]]\nname = "b"\n'
    data = em_dev.toml_loads(text)
    assert [p["name"] for p in data["package"]] == ["a", "b"]
    assert data["package"][0]["metadata"] == {"x": 1}


def test_toml_reads_dotted_keys(em_dev):
    data = em_dev.toml_loads("tool.uv.package = false\n")
    assert data["tool"]["uv"]["package"] is False


def test_toml_ignores_comments(em_dev):
    data = em_dev.toml_loads("# a comment\na = 1  # trailing\n")
    assert data == {"a": 1}


def test_toml_unrecognized_token_falls_back_to_raw_string(em_dev):
    # A date literal: never parsed as a date, but must not blow up the rest
    # of the document -- nothing here ever reads a date's value.
    data = em_dev.toml_loads("d = 1979-05-27T07:32:00Z\nafter = 1\n")
    assert data["after"] == 1
    assert isinstance(data["d"], str)


def test_toml_load_wraps_errors_with_the_path(em_dev, tmp_path: Path):
    bad = tmp_path / "bad.toml"
    bad.write_text("a = [1, 2\n")  # unterminated array
    with pytest.raises(em_dev.EmDevError, match=str(bad)):
        em_dev.toml_load(bad)


# ------------------------------------------------------------- normalize_name


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Emergent_Matter.SDM-Core", "emergent-matter-sdm-core"),
        ("foo__bar", "foo-bar"),
        ("already-normal", "already-normal"),
    ],
)
def test_normalize_name(em_dev, raw, expected):
    assert em_dev.normalize_name(raw) == expected


# ------------------------------------------------------------- lock parsing


def _lock(packages: list[dict]) -> dict:
    return {"package": packages}


def test_discover_ours_includes_transitive_and_excludes_own(em_dev):
    lock = _lock(
        [
            {"name": "emergent-matter-sdm-core"},
            {"name": "emergent-matter-sdm-materials"},  # only arrives via core
            {"name": "emergent-matter-example-tool"},  # this repo's own package
            {"name": "some-third-party-lib"},
        ]
    )
    assert em_dev.discover_ours(lock, "emergent-matter-example-tool") == [
        "emergent-matter-sdm-core",
        "emergent-matter-sdm-materials",
    ]


def test_discover_ours_empty_lock_is_nothing_to_do(em_dev):
    assert em_dev.discover_ours(_lock([]), "some-repo") == []


def test_locked_version_normalizes_before_matching(em_dev):
    lock = _lock([{"name": "emergent-matter-sdm-core", "version": "1.2.3"}])
    assert em_dev.locked_version(lock, "Emergent_Matter_SDM_Core") == "1.2.3"
    assert em_dev.locked_version(lock, "nope") is None


# -------------------------------------------------------- pyproject.toml reads


def test_own_project_name_requires_project_name(em_dev):
    with pytest.raises(em_dev.EmDevError, match="no \\[project\\] name"):
        em_dev.own_project_name({})


def test_own_project_version_requires_project_version(em_dev):
    with pytest.raises(em_dev.EmDevError, match="no \\[project\\] version"):
        em_dev.own_project_version({"project": {"name": "x"}})


def test_is_real_package_defaults_true(em_dev):
    assert em_dev.is_real_package({}) is True
    assert em_dev.is_real_package({"tool": {"uv": {"package": False}}}) is False


def test_dependency_group_names(em_dev):
    data = {"dependency-groups": {"dev": ["a"], "docs": ["b"]}}
    assert em_dev.dependency_group_names(data) == {"dev", "docs"}
    assert em_dev.dependency_group_names({}) == set()


def test_em_dev_tool_config_requires_tool_true(em_dev):
    assert em_dev.em_dev_tool_config({}) is None
    assert em_dev.em_dev_tool_config({"tool": {"em-dev": {"tool": False}}}) is None
    cfg = {"tool": {"em-dev": {"tool": True, "tool-extras": {"win32": ["terminal-windows"]}}}}
    assert em_dev.em_dev_tool_config(cfg) == {
        "tool": True,
        "tool-extras": {"win32": ["terminal-windows"]},
    }


def test_platform_extras_keyed_by_platform(em_dev):
    tool_cfg = {"tool-extras": {"win32": ["terminal-windows"]}}
    assert em_dev.platform_extras(tool_cfg, platform="win32") == ["terminal-windows"]
    assert em_dev.platform_extras(tool_cfg, platform="darwin") == []
    assert em_dev.platform_extras({}, platform="win32") == []


# ------------------------------------------------------------- folder resolution


def _make_sibling(root: Path, folder_name: str, declared_name: str) -> None:
    d = root / folder_name
    d.mkdir(parents=True)
    (d / "pyproject.toml").write_text(f'[project]\nname = "{declared_name}"\nversion = "1.0.0"\n')


def test_resolve_sibling_local_when_folder_and_name_match(em_dev, tmp_path: Path):
    _make_sibling(tmp_path, "emergent-matter-sdm-core", "emergent-matter-sdm-core")
    r = em_dev.resolve_sibling("emergent-matter-sdm-core", tmp_path)
    assert r.status == "local"
    assert r.folder == tmp_path / "emergent-matter-sdm-core"


def test_resolve_sibling_missing_when_no_folder(em_dev, tmp_path: Path):
    r = em_dev.resolve_sibling("emergent-matter-sdm-core", tmp_path)
    assert r.status == "missing"


def test_resolve_sibling_missing_when_folder_has_no_pyproject(em_dev, tmp_path: Path):
    (tmp_path / "emergent-matter-sdm-core").mkdir()
    r = em_dev.resolve_sibling("emergent-matter-sdm-core", tmp_path)
    assert r.status == "missing"


def test_resolve_sibling_name_mismatch_when_folder_declares_other_name(em_dev, tmp_path: Path):
    _make_sibling(tmp_path, "emergent-matter-sdm-core", "something-else")
    r = em_dev.resolve_sibling("emergent-matter-sdm-core", tmp_path)
    assert r.status == "name-mismatch"
    assert r.found_name == "something-else"


# ------------------------------------------------------- workspace-member resolution

ENGINE = "emergent-matter-sdm-engine"
TOOLS = "emergent-matter-sdm-tools"


def _make_engine(root: Path, *, tools_name: str = TOOLS) -> Path:
    """A sibling that is a uv workspace root with one member under packages/."""
    engine = root / ENGINE
    engine.mkdir(parents=True)
    (engine / "pyproject.toml").write_text(
        f'[project]\nname = "{ENGINE}"\nversion = "1.0.0"\n'
        '[tool.uv.workspace]\nmembers = ["packages/*"]\nexclude = ["packages/skipped"]\n'
    )
    member = engine / "packages" / "sdm-tools"
    member.mkdir(parents=True)
    (member / "pyproject.toml").write_text(f'[project]\nname = "{tools_name}"\nversion = "1.0.0"\n')
    return member


def test_workspace_members_indexes_member_by_declared_name(em_dev, tmp_path: Path):
    member = _make_engine(tmp_path)
    assert em_dev.workspace_members(tmp_path) == {TOOLS: (member, tmp_path / ENGINE)}


def test_workspace_members_honours_exclude_and_ignores_non_workspaces(em_dev, tmp_path: Path):
    _make_engine(tmp_path)
    skipped = tmp_path / ENGINE / "packages" / "skipped"
    skipped.mkdir()
    (skipped / "pyproject.toml").write_text('[project]\nname = "emergent-matter-skipped"\n')
    _make_sibling(tmp_path, "plain", "emergent-matter-plain")
    assert list(em_dev.workspace_members(tmp_path)) == [TOOLS]


def test_workspace_members_missing_root_is_empty(em_dev, tmp_path: Path):
    assert em_dev.workspace_members(tmp_path / "nope") == {}


def test_resolve_sibling_finds_package_as_member_of_sibling_workspace(em_dev, tmp_path: Path):
    member = _make_engine(tmp_path)
    (r,) = em_dev.resolve_siblings([TOOLS], tmp_path)
    assert r.status == "local"
    assert r.folder == member
    assert r.via == tmp_path / ENGINE


def test_resolve_sibling_own_name_folder_wins_over_member(em_dev, tmp_path: Path):
    _make_engine(tmp_path)
    _make_sibling(tmp_path, TOOLS, TOOLS)
    (r,) = em_dev.resolve_siblings([TOOLS], tmp_path)
    assert r.status == "local"
    assert r.folder == tmp_path / TOOLS
    assert r.via is None


def test_resolve_sibling_absent_member_stays_missing(em_dev, tmp_path: Path):
    _make_engine(tmp_path, tools_name="emergent-matter-something-else")
    (r,) = em_dev.resolve_siblings([TOOLS], tmp_path)
    assert r.status == "missing"
    assert r.via is None


def test_workspace_root_package_still_resolves_by_its_own_folder(em_dev, tmp_path: Path):
    _make_engine(tmp_path)
    (r,) = em_dev.resolve_siblings([ENGINE], tmp_path)
    assert r.status == "local"
    assert r.folder == tmp_path / ENGINE
    assert r.via is None


def test_filter_only_selects_a_workspace_member(em_dev, tmp_path: Path):
    member = _make_engine(tmp_path)
    resolutions = em_dev.resolve_siblings([ENGINE, TOOLS], tmp_path)
    (r,) = em_dev.filter_only(resolutions, ["sdm-tools"])
    assert r.folder == member


def test_status_reports_where_a_member_came_from(em_dev, tmp_path: Path, monkeypatch, capsys):
    root = tmp_path / "root"
    member = _make_engine(root)
    repo = root / "consumer"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "consumer"\nversion = "1.0.0"\n')
    (repo / "uv.lock").write_text(f'[[package]]\nname = "{TOOLS}"\nversion = "1.2.3"\n')
    monkeypatch.delenv("EM_DEV_ROOT", raising=False)
    assert em_dev.status(repo) == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines() if TOOLS in ln)
    assert "local" in line
    assert str(member) in line
    assert f"workspace member of {root / ENGINE}" in line


def test_resolve_root_env_wins_over_config_and_default(em_dev, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    elsewhere = tmp_path / "elsewhere"
    assert em_dev.resolve_root(repo, {}, env={}) == tmp_path
    assert em_dev.resolve_root(repo, {"root": str(elsewhere)}, env={}) == elsewhere
    override = tmp_path / "env-override"
    got = em_dev.resolve_root(repo, {"root": str(elsewhere)}, env={"EM_DEV_ROOT": str(override)})
    assert got == override.resolve()


def test_load_config_absent_file_is_empty(em_dev, tmp_path: Path):
    assert em_dev.load_config(tmp_path) == {}


def test_load_config_reads_em_dev_toml(em_dev, tmp_path: Path):
    (tmp_path / ".em-dev.toml").write_text('root = "../elsewhere"\n')
    assert em_dev.load_config(tmp_path) == {"root": "../elsewhere"}


# -------------------------------------------------------------------- --only


def _resolutions(em_dev, names: list[str]) -> list:
    return [em_dev.SiblingResolution(n, Path(f"/tmp/{n}"), "local") for n in names]


def test_filter_only_none_returns_everything(em_dev):
    res = _resolutions(em_dev, ["emergent-matter-sdm-core", "emergent-matter-sdm-materials"])
    assert em_dev.filter_only(res, None) == res


def test_filter_only_matches_by_suffix(em_dev):
    res = _resolutions(em_dev, ["emergent-matter-sdm-core", "emergent-matter-sdm-materials"])
    got = em_dev.filter_only(res, ["core"])
    assert [r.name for r in got] == ["emergent-matter-sdm-core"]


def test_filter_only_matches_full_name(em_dev):
    res = _resolutions(em_dev, ["emergent-matter-sdm-core"])
    got = em_dev.filter_only(res, ["emergent-matter-sdm-core"])
    assert [r.name for r in got] == ["emergent-matter-sdm-core"]


def test_filter_only_unknown_token_raises(em_dev):
    res = _resolutions(em_dev, ["emergent-matter-sdm-core"])
    with pytest.raises(em_dev.EmDevError, match="matches no package"):
        em_dev.filter_only(res, ["materials"])


def test_filter_only_ambiguous_token_raises(em_dev):
    res = _resolutions(em_dev, ["emergent-matter-sdm-core", "emergent-matter-other-core"])
    with pytest.raises(em_dev.EmDevError, match="ambiguous"):
        em_dev.filter_only(res, ["core"])


# --------------------------------------------------------------- freshness


def test_needs_rebuild_true_when_venv_missing(em_dev, tmp_path: Path):
    assert em_dev.needs_rebuild(tmp_path / "nope", "hash", []) is True


def test_needs_rebuild_false_when_state_matches(em_dev, tmp_path: Path):
    venv = tmp_path / ".venv-local"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("")
    locals_used = _resolutions(em_dev, ["emergent-matter-sdm-core"])
    em_dev.write_state(venv, em_dev.build_state("abc123", locals_used))
    assert em_dev.needs_rebuild(venv, "abc123", locals_used) is False


def test_needs_rebuild_true_when_lock_hash_changed(em_dev, tmp_path: Path):
    venv = tmp_path / ".venv-local"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("")
    locals_used = _resolutions(em_dev, ["emergent-matter-sdm-core"])
    em_dev.write_state(venv, em_dev.build_state("abc123", locals_used))
    assert em_dev.needs_rebuild(venv, "different-hash", locals_used) is True


def test_needs_rebuild_true_when_sibling_list_changed(em_dev, tmp_path: Path):
    venv = tmp_path / ".venv-local"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("")
    em_dev.write_state(
        venv, em_dev.build_state("abc123", _resolutions(em_dev, ["emergent-matter-sdm-core"]))
    )
    changed = _resolutions(em_dev, ["emergent-matter-sdm-core", "emergent-matter-sdm-materials"])
    assert em_dev.needs_rebuild(venv, "abc123", changed) is True


def test_read_state_missing_or_corrupt_returns_none(em_dev, tmp_path: Path):
    assert em_dev.read_state(tmp_path) is None
    em_dev.state_path(tmp_path).write_text("not json")
    assert em_dev.read_state(tmp_path) is None


def test_lock_hash_changes_with_content(em_dev, tmp_path: Path):
    a = tmp_path / "a.lock"
    b = tmp_path / "b.lock"
    a.write_text("one")
    b.write_text("two")
    assert em_dev.lock_hash(a) != em_dev.lock_hash(b)
    a2 = tmp_path / "a2.lock"
    a2.write_text("one")
    assert em_dev.lock_hash(a) == em_dev.lock_hash(a2)


# ---------------------------------------------------- installed-env introspection


def test_site_packages_dir_posix_layout(em_dev, tmp_path: Path):
    sp = tmp_path / "lib" / "python3.13" / "site-packages"
    sp.mkdir(parents=True)
    assert em_dev.site_packages_dir(tmp_path) == sp


def test_site_packages_dir_windows_layout(em_dev, tmp_path: Path):
    sp = tmp_path / "Lib" / "site-packages"
    sp.mkdir(parents=True)
    assert em_dev.site_packages_dir(tmp_path) == sp


def test_site_packages_dir_missing_venv_returns_none(em_dev, tmp_path: Path):
    assert em_dev.site_packages_dir(tmp_path / "nope") is None


def test_find_dist_info_matches_regardless_of_dash_underscore(em_dev, tmp_path: Path):
    (tmp_path / "emergent_matter_sdm_core-1.0.0.dist-info").mkdir()
    found = em_dev.find_dist_info(tmp_path, "emergent-matter-sdm-core")
    assert found is not None
    assert found.name == "emergent_matter_sdm_core-1.0.0.dist-info"


def test_find_dist_info_no_match_returns_none(em_dev, tmp_path: Path):
    assert em_dev.find_dist_info(tmp_path, "emergent-matter-sdm-core") is None


def _write_direct_url(dist_info: Path, *, editable: bool | None) -> None:
    dist_info.mkdir()
    if editable is None:
        return
    (dist_info / "direct_url.json").write_text(
        json.dumps({"url": "file:///x", "dir_info": {"editable": editable}})
    )


def test_is_editable_direct_url_true(em_dev, tmp_path: Path):
    d = tmp_path / "pkg.dist-info"
    _write_direct_url(d, editable=True)
    assert em_dev.is_editable_direct_url(d) is True


def test_is_editable_direct_url_false(em_dev, tmp_path: Path):
    d = tmp_path / "pkg.dist-info"
    _write_direct_url(d, editable=False)
    assert em_dev.is_editable_direct_url(d) is False


def test_is_editable_direct_url_missing_file_is_none(em_dev, tmp_path: Path):
    d = tmp_path / "pkg.dist-info"
    _write_direct_url(d, editable=None)
    assert em_dev.is_editable_direct_url(d) is None


def test_is_editable_direct_url_malformed_json_is_none(em_dev, tmp_path: Path):
    d = tmp_path / "pkg.dist-info"
    d.mkdir()
    (d / "direct_url.json").write_text("{not json")
    assert em_dev.is_editable_direct_url(d) is None


# ------------------------------------------------------------------- tool mode


def test_console_script_name_none_when_no_scripts(em_dev):
    assert em_dev.console_script_name({"project": {}}) is None


def test_console_script_name_returns_declared_script(em_dev):
    data = {"project": {"scripts": {"example-tool": "example_tool.cli:main"}}}
    assert em_dev.console_script_name(data) == "example-tool"


def test_local_checkout_defines_script_true_and_false(em_dev, tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion="1.0.0"\n[project.scripts]\nfoo = "x:main"\n'
    )
    assert em_dev.local_checkout_defines_script(tmp_path, "foo") is True
    assert em_dev.local_checkout_defines_script(tmp_path, "bar") is False


def test_local_checkout_defines_script_false_when_no_pyproject(em_dev, tmp_path: Path):
    assert em_dev.local_checkout_defines_script(tmp_path, "foo") is False


def test_tool_server_running_detects_a_real_listener(em_dev):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert em_dev.tool_server_running(port=port) is True

    # After the context manager closes the socket, the same port should
    # (almost always) no longer accept connections.
    assert em_dev.tool_server_running(port=port) is False


# ---------------------------------------------------------- git-ignore management


def _init_git(repo: Path) -> None:
    (repo / ".git").mkdir()


def test_ensure_local_gitignore_creates_info_exclude(em_dev, tmp_path: Path):
    _init_git(tmp_path)
    em_dev.ensure_local_gitignore(tmp_path)
    exclude = tmp_path / ".git" / "info" / "exclude"
    text = exclude.read_text()
    assert ".venv-local/" in text
    assert ".em-dev.toml" in text


def test_ensure_local_gitignore_is_idempotent(em_dev, tmp_path: Path):
    _init_git(tmp_path)
    em_dev.ensure_local_gitignore(tmp_path)
    first = (tmp_path / ".git" / "info" / "exclude").read_text()
    em_dev.ensure_local_gitignore(tmp_path)
    second = (tmp_path / ".git" / "info" / "exclude").read_text()
    assert first == second


def test_ensure_local_gitignore_noop_without_git_dir(em_dev, tmp_path: Path):
    em_dev.ensure_local_gitignore(tmp_path)  # must not raise
    assert not (tmp_path / ".git").exists()


def test_ensure_local_gitignore_preserves_existing_entries(em_dev, tmp_path: Path):
    _init_git(tmp_path)
    exclude = tmp_path / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True)
    exclude.write_text("some-other-thing\n")
    em_dev.ensure_local_gitignore(tmp_path)
    text = exclude.read_text()
    assert "some-other-thing" in text
    assert ".venv-local/" in text


# ----------------------------------------------------------- nothing-to-do path


def test_make_local_nothing_to_make_local(em_dev, tmp_path: Path, capsys):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "solo-repo"\nversion = "1.0.0"\n')
    (tmp_path / "uv.lock").write_text('version = 1\nrevision = 3\nrequires-python = ">=3.11"\n')
    exit_code = em_dev.make_local(tmp_path)
    assert exit_code == 0
    assert not (tmp_path / em_dev.VENV_LOCAL).exists()
    assert "nothing here to make local" in capsys.readouterr().out


def test_make_local_raises_without_uv_lock(em_dev, tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "solo-repo"\nversion = "1.0.0"\n')
    with pytest.raises(em_dev.EmDevError, match="no uv.lock"):
        em_dev.make_local(tmp_path)
