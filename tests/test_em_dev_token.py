"""`em-dev token`: prints the assignments that sign uv in to a private index.

The AWS CLI and `gh` are replaced by small fakes on PATH, so no test needs
credentials or a network. What is pinned: the token reaches stdout and
nowhere else, the three places a setting can come from rank in order, a
lapsed session signs in exactly once, and no account, domain or profile is
written into the script.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

TOKEN = "tok-abc123"
SETTING_ENV = (
    "EM_CODEARTIFACT_DOMAIN",
    "EM_CODEARTIFACT_DOMAIN_OWNER",
    "EM_CODEARTIFACT_REGION",
    "EM_CODEARTIFACT_PROFILE",
    "EM_CODEARTIFACT_INDEX_NAME",
)


def _fake(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture()
def fakes(tmp_path, monkeypatch):
    """A PATH holding only fake `aws` and `gh`, and a log of what they saw."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _fake(
        bin_dir,
        "aws",
        f"""echo "aws $*" >> {log}
case "$*" in
  *"sts get-caller-identity"*) [ -f {tmp_path}/lapsed ] && exit 255; echo '{{}}'; exit 0;;
  *"sso login"*) rm -f {tmp_path}/lapsed; echo 'open this browser url'; exit 0;;
  *"get-authorization-token"*) echo {TOKEN}; exit 0;;
esac
exit 1
""",
    )
    _fake(
        bin_dir,
        "gh",
        f"""echo "gh $*" >> {log}
case "$3" in
  EM_CODEARTIFACT_DOMAIN) echo org-domain;;
  EM_CODEARTIFACT_DOMAIN_OWNER) echo 000000000000;;
  EM_CODEARTIFACT_REGION) echo org-region-1;;
  *) exit 1;;
esac
""",
    )
    monkeypatch.setenv("PATH", str(bin_dir))
    for name in SETTING_ENV:
        monkeypatch.delenv(name, raising=False)
    return tmp_path, log


def test_prints_the_assignments_to_stdout_only(em_dev, fakes, monkeypatch, capsys):
    monkeypatch.setenv("EM_CODEARTIFACT_PROFILE", "dev-profile")
    assert em_dev.main(["token"]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "export UV_INDEX_EM_CODEARTIFACT_USERNAME=aws",
        f"export UV_INDEX_EM_CODEARTIFACT_PASSWORD={TOKEN}",
    ]
    assert TOKEN not in err


def test_settings_rank_flag_then_environment_then_org_variable(em_dev, fakes, monkeypatch):
    tmp_path, log = fakes
    monkeypatch.setenv("EM_CODEARTIFACT_DOMAIN", "env-domain")
    settings = em_dev.resolve_token_settings({"domain": "flag-domain", "region": None})
    assert settings["domain"] == "flag-domain"
    settings = em_dev.resolve_token_settings({})
    assert settings["domain"] == "env-domain"
    assert settings["domain_owner"] == "000000000000"  # from the org variable
    assert settings["region"] == "org-region-1"
    assert settings["profile"] is None  # never read from an org variable
    assert settings["index_name"] == "em-codeartifact"
    assert "variable get EM_CODEARTIFACT_PROFILE" not in log.read_text()


def test_a_lapsed_session_signs_in_once_and_keeps_stdout_clean(em_dev, fakes, monkeypatch, capsys):
    tmp_path, log = fakes
    (tmp_path / "lapsed").touch()
    monkeypatch.setenv("EM_CODEARTIFACT_PROFILE", "dev-profile")
    assert em_dev.main(["token"]) == 0
    out, _ = capsys.readouterr()
    assert "browser" not in out
    assert log.read_text().count("sso login") == 1
    assert "--profile dev-profile" in log.read_text()


def test_a_lapsed_session_without_a_profile_says_what_to_set(em_dev, fakes, capsys):
    tmp_path, _ = fakes
    (tmp_path / "lapsed").touch()
    assert em_dev.main(["token"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "EM_CODEARTIFACT_PROFILE" in err


def test_missing_settings_are_named_and_nothing_is_printed(em_dev, tmp_path, monkeypatch, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    for name in SETTING_ENV:
        monkeypatch.delenv(name, raising=False)
    assert em_dev.main(["token"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "EM_CODEARTIFACT_DOMAIN" in err and "EM_CODEARTIFACT_REGION" in err


def test_index_name_sets_the_variable_pair(em_dev, fakes, monkeypatch, capsys):
    monkeypatch.setenv("EM_CODEARTIFACT_PROFILE", "p")
    assert em_dev.main(["token", "--index-name", "my-private", "--format", "json"]) == 0
    out, _ = capsys.readouterr()
    assert json.loads(out) == {
        "UV_INDEX_MY_PRIVATE_USERNAME": "aws",
        "UV_INDEX_MY_PRIVATE_PASSWORD": TOKEN,
    }


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("powershell", "$env:UV_INDEX_X_PASSWORD = 't'"),
        ("cmd", 'set "UV_INDEX_X_PASSWORD=t"'),
        ("sh", "export UV_INDEX_X_PASSWORD=t"),
    ],
)
def test_formats(em_dev, fmt, expected):
    assert expected in em_dev.format_token_lines("x", "t", fmt).splitlines()


def test_the_script_names_no_account_domain_or_profile():
    script = Path(__file__).resolve().parents[1] / "templates" / "scripts" / "em-dev.py"
    text = script.read_text()
    import re

    assert not re.search(r"\b\d{12}\b", text)
    assert "us-east-2" not in text


def test_powershell_doubles_a_single_quote_and_leaves_other_characters_literal(em_dev):
    lines = em_dev.format_token_lines("x", "a'b$c`d&e", "powershell").splitlines()
    assert lines[1] == "$env:UV_INDEX_X_PASSWORD = 'a''b$c`d&e'"


def test_cmd_quotes_the_whole_assignment_so_metacharacters_are_literal(em_dev):
    lines = em_dev.format_token_lines("x", "a&b|c<d>e^f", "cmd").splitlines()
    assert lines[1] == 'set "UV_INDEX_X_PASSWORD=a&b|c<d>e^f"'


@pytest.mark.parametrize("token", ['a"b', "a%PATH%b", "a\nb"])
def test_cmd_refuses_a_token_it_cannot_carry(em_dev, token):
    with pytest.raises(em_dev.EmDevError):
        em_dev.format_token_lines("x", token, "cmd")


def test_a_line_break_is_refused_for_every_shell_format(em_dev):
    for fmt in ("sh", "powershell", "cmd"):
        with pytest.raises(em_dev.EmDevError, match="line break"):
            em_dev.format_token_lines("x", "a\nb", fmt)
