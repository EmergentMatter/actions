"""Tests for scripts/sync.py.

The value of this script is entirely in one distinction: "this repo's copy
is stale" vs "this repo deliberately customised it" look identical to a
two-way diff. A three-way compare against a recorded base tells them apart.
ROW2 and ROW3 in `_make_actions_repo` below are the whole point -- both
target files differ from the current template, and the correct behaviour
for each is opposite (auto-update vs leave-alone-and-report). Every test
here uses a real throwaway git repo and the real `git merge-file` binary,
not mocks -- the thing under test is whether the *decision* is right, not
whether Python can shell out.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync.py"
spec = importlib.util.spec_from_file_location("sync", SCRIPT)
sync = importlib.util.module_from_spec(spec)
sys.modules["sync"] = sync
spec.loader.exec_module(sync)


def _git(repo, *args):
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def _make_actions_repo(tmp_path: Path) -> Path:
    """v1.0.0 -> HEAD, touching ROW2 and ROW4 but not ROW1 or ROW3, so one
    fixture covers all four rows of the decision matrix plus a seed-once
    entry that must never be synced."""
    repo = tmp_path / "actions"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    templates = repo / "templates"
    templates.mkdir()
    manifest_entries = "".join(
        f'[[template]]\nsource = "{n}"\ndest = "{n}"\npolicy = "managed"\n\n'
        for n in ["ROW1.md", "ROW2.md", "ROW3.md", "ROW4.md"]
    )
    manifest_entries += (
        '[[template]]\nsource = "SEEDED.md"\ndest = "SEEDED.md"\npolicy = "seed-once"\n'
    )
    (templates / "manifest.toml").write_text(manifest_entries)
    (templates / "ROW1.md").write_text("same\n")
    (templates / "ROW2.md").write_text("a\nb\nc\n")
    (templates / "ROW3.md").write_text("x\ny\nz\n")
    (templates / "ROW4.md").write_text("line1\nline2\nline3\nline4\nline5\n")
    (templates / "SEEDED.md").write_text("seed v1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "v1.0.0")
    _git(repo, "tag", "v1.0.0")
    _git(repo, "tag", "v1")  # the moving alias, pointing at v1.0.0 for now

    (templates / "ROW2.md").write_text("a\nb\nc\nd\n")  # upstream changes this one
    (templates / "ROW4.md").write_text("line1\nline2\nline3-theirs\nline4\nline5\n")  # + this one
    (templates / "SEEDED.md").write_text("seed v2 -- must never reach a consumer\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "HEAD")
    # Mirrors .github/RELEASING.md: `v1` is force-moved to the newest
    # release on every cut. HEAD here is deliberately untagged with a real
    # point release (there's no vX.Y.Z at HEAD in this fixture) -- only the
    # alias follows it, exactly the shape that made the original bug happen.
    _git(repo, "tag", "-f", "v1", "HEAD")

    return repo


def _make_target_repo(
    tmp_path: Path, *, stamp: str | None = "v1.0.0", name: str = "target"
) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    stamp_line = f'templates_version = "{stamp}"\n' if stamp else ""
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "1.0.0"\n\n'
        f"[tool.em-release]\n{stamp_line}version_files = [\n]\n"
    )
    return repo


def _make_never_onboarded_target_repo(tmp_path: Path, *, name: str = "never_onboarded") -> Path:
    """No [tool.em-release] block at all -- distinct from `_make_target_repo(
    stamp=None)`, which is a genuinely onboarded repo that predates the
    templates_version field. This one was never onboarded."""
    repo = tmp_path / name
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "1.0.0"\n')
    return repo


def _seed(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)


def _stamp(repo: Path) -> str | None:
    with (repo / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["tool"]["em-release"].get("templates_version")


@pytest.fixture
def actions_repo(tmp_path, monkeypatch):
    repo = _make_actions_repo(tmp_path)
    monkeypatch.setattr(sync, "ACTIONS_REPO", repo)
    monkeypatch.setattr(sync.onboard, "TEMPLATES", repo / "templates")
    return repo


def _row_target(
    tmp_path, actions_repo, *, row4_ours="line1\nline2\nline3-ours\nline4\nline5\n", stamp="v1.0.0"
):
    """A target repo positioned at every row of the matrix at once."""
    repo = _make_target_repo(tmp_path, stamp=stamp)
    _seed(repo, "ROW1.md", "same\n")  # unchanged both sides -> up to date
    _seed(repo, "ROW2.md", "a\nb\nc\n")  # unchanged locally, upstream changed -> clean update
    _seed(repo, "ROW3.md", "x\ny\nz\nlocal-addition\n")  # local edit, upstream unchanged -> report
    _seed(repo, "ROW4.md", row4_ours)  # both changed, same line -> real conflict
    return repo


def by_dest(results):
    return {r.dest: r for r in results}


def _sync_one(repo, actions_repo, dest, *, dry_run=False, side=None):
    """Run sync_repo for a single dest and return just that EntryResult."""
    results = sync.sync_repo(repo, actions_repo, dry_run=dry_run, side=side, only=[dest])
    return by_dest(results)[dest]


# ------------------------------------------------------------- decision matrix


def test_row1_same_vs_same_is_a_noop(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo)
    result = _sync_one(repo, actions_repo, "ROW1.md")
    assert result.action == "up-to-date"
    assert not result.pending
    assert (repo / "ROW1.md").read_text() == "same\n"


def test_row2_clean_update_writes_silently(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo)
    result = _sync_one(repo, actions_repo, "ROW2.md")
    assert result.action == "updated"
    assert not result.pending
    assert (repo / "ROW2.md").read_text() == "a\nb\nc\nd\n"


def test_row2_dry_run_reports_without_writing(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo)
    result = _sync_one(repo, actions_repo, "ROW2.md", dry_run=True)
    assert result.action == "would-update"
    assert not result.pending, "a clean update is not a decision -- dry-run shouldn't block"
    assert (repo / "ROW2.md").read_text() == "a\nb\nc\n", "dry-run must write nothing"


def test_row3_local_edit_only_is_left_alone_and_reported(actions_repo, tmp_path):
    """The core distinction: ROW2 and ROW3 both 'differ from the template',
    but only ROW2 gets touched. ROW3's local edit survives untouched."""
    repo = _row_target(tmp_path, actions_repo)
    result = _sync_one(repo, actions_repo, "ROW3.md")
    assert result.action == "local-edit"
    assert not result.pending, "nothing upstream changed -- there's no decision to make"
    assert (repo / "ROW3.md").read_text() == "x\ny\nz\nlocal-addition\n"


def test_row4_real_conflict_is_never_silently_resolved_without_a_side(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo)
    result = _sync_one(repo, actions_repo, "ROW4.md", dry_run=True)
    assert result.action == "conflict-pending"
    assert result.pending
    assert (repo / "ROW4.md").read_text() == "line1\nline2\nline3-ours\nline4\nline5\n"


def test_row4_conflicting_edits_resolved_with_theirs(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo)
    result = _sync_one(repo, actions_repo, "ROW4.md", side="theirs")
    assert result.action == "updated"
    assert not result.pending
    assert (repo / "ROW4.md").read_text() == "line1\nline2\nline3-theirs\nline4\nline5\n"


def test_row4_conflicting_edits_resolved_with_ours(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo)
    before = (repo / "ROW4.md").read_text()
    result = _sync_one(repo, actions_repo, "ROW4.md", side="ours")
    assert result.action == "kept-ours"
    assert not result.pending
    assert (repo / "ROW4.md").read_text() == before, "kept-ours must not touch the file"


def test_non_overlapping_changes_merge_cleanly_without_a_side(actions_repo, tmp_path):
    """Both sides changed, but not the same line -- a real three-way merge,
    not a coin flip, resolves it without needing --ours/--theirs."""
    repo = _row_target(tmp_path, actions_repo, row4_ours="line1-ours\nline2\nline3\nline4\nline5\n")
    result = _sync_one(repo, actions_repo, "ROW4.md")
    assert result.action == "merged"
    assert not result.pending
    assert (repo / "ROW4.md").read_text() == "line1-ours\nline2\nline3-theirs\nline4\nline5\n"


# -------------------------------------------------------------- interactivity


def test_conflict_prompts_a_human_rather_than_silently_picking_a_side(
    actions_repo, tmp_path, monkeypatch
):
    repo = _row_target(tmp_path, actions_repo)
    calls = []

    def fake_prompt(dest, kind):
        calls.append((dest, kind))
        return "o"

    monkeypatch.setattr(sync, "_prompt", fake_prompt)
    result = _sync_one(repo, actions_repo, "ROW4.md")
    assert calls == [("ROW4.md", "Conflict")]
    assert result.action == "kept-ours"


def test_prompt_answer_of_theirs_overwrites_the_file(actions_repo, tmp_path, monkeypatch):
    repo = _row_target(tmp_path, actions_repo)
    monkeypatch.setattr(sync, "_prompt", lambda dest, kind: "t")
    result = _sync_one(repo, actions_repo, "ROW4.md")
    assert result.action == "updated"
    assert (repo / "ROW4.md").read_text() == "line1\nline2\nline3-theirs\nline4\nline5\n"


def test_prompt_is_never_called_when_a_side_or_dry_run_is_given(
    actions_repo, tmp_path, monkeypatch
):
    """--dry-run and --ours/--theirs exist precisely so this never hangs in
    CI. Prove the CI-safe paths never reach the human-only prompt."""

    def exploding_prompt(dest, kind):
        raise AssertionError("must not prompt when a side or --dry-run is given")

    monkeypatch.setattr(sync, "_prompt", exploding_prompt)
    repo = _row_target(tmp_path, actions_repo)
    _sync_one(repo, actions_repo, "ROW4.md", dry_run=True)
    _sync_one(repo, actions_repo, "ROW4.md", side="ours")
    _sync_one(repo, actions_repo, "ROW4.md", side="theirs")


# ------------------------------------------------------------- no-stamp path


def test_no_stamp_degrades_to_two_way_and_agreeing_files_are_still_fine(actions_repo, tmp_path):
    repo = _make_target_repo(tmp_path, stamp=None)
    _seed(repo, "ROW1.md", "same\n")
    result = _sync_one(repo, actions_repo, "ROW1.md")
    assert result.action == "up-to-date"
    assert not result.pending


def test_no_stamp_never_guesses_which_side_is_right(actions_repo, tmp_path):
    """No recorded base -- ROW2 here is genuinely ambiguous: it could be a
    stale copy or a deliberate edit. Without --ours/--theirs this must be
    reported, never auto-resolved."""
    repo = _make_target_repo(tmp_path, stamp=None)
    _seed(repo, "ROW2.md", "a\nb\nc\n")
    result = _sync_one(repo, actions_repo, "ROW2.md", dry_run=True)
    assert result.action == "diff-pending"
    assert result.pending
    assert (repo / "ROW2.md").read_text() == "a\nb\nc\n"


def test_no_stamp_missing_local_file_is_also_a_decision_not_an_auto_create(actions_repo, tmp_path):
    repo = _make_target_repo(tmp_path, stamp=None)
    result = _sync_one(repo, actions_repo, "ROW1.md", dry_run=True)
    assert result.action == "absent-pending"
    assert result.pending
    assert not (repo / "ROW1.md").exists()


# ------------------------------------------------------------- absent managed files


def test_absent_file_unchanged_upstream_is_reported_as_absent_not_local_edit(
    actions_repo, tmp_path
):
    """Regression: a managed file that was never installed must not be
    mistaken for `ours_changed and not theirs_changed` just because
    `ours is None` differs from `base`. ROW1 never changes upstream
    between v1.0.0 and HEAD. Before the fix, that made it fall straight
    into 'local-edit -- left alone' at non-blocking severity. That was
    wrong on two counts: there is no local edit, and a missing required
    file must not be silently non-blocking."""
    repo = _make_target_repo(tmp_path, stamp="v1.0.0")
    result = _sync_one(repo, actions_repo, "ROW1.md", dry_run=True)
    assert result.action == "absent-pending"
    assert result.pending
    assert result.detail == "no ROW1.md; not installed"
    assert not (repo / "ROW1.md").exists()


def test_absent_file_changed_upstream_is_still_reported_as_absent_not_conflict(
    actions_repo, tmp_path
):
    """Regression: ROW2 *did* change upstream since v1.0.0, so before the
    fix a missing local copy landed in the real-conflict branch
    ('conflict-pending') instead of being recognised as simply absent."""
    repo = _make_target_repo(tmp_path, stamp="v1.0.0")
    result = _sync_one(repo, actions_repo, "ROW2.md", dry_run=True)
    assert result.action == "absent-pending"
    assert result.pending
    assert result.detail == "no ROW2.md; not installed"


def test_absent_file_matches_fleet_status_vocabulary():
    """fleet_status.py's check_templates() reports the same condition as
    `f"no {entry.dest}; not installed"` at warn severity. sync.py must not
    describe the same repo state differently. This check reads the source
    text directly rather than importing fleet_status.py, so it doesn't
    couple to that module's unrelated import chain (it dynamically loads
    lint_gate.py)."""
    fleet_status_path = Path(__file__).resolve().parents[1] / "scripts" / "fleet_status.py"
    assert 'f"no {entry.dest}; not installed"' in fleet_status_path.read_text()


def test_theirs_installs_an_absent_managed_file(actions_repo, tmp_path):
    repo = _make_target_repo(tmp_path, stamp="v1.0.0")
    result = _sync_one(repo, actions_repo, "ROW1.md", side="theirs")
    assert result.action == "updated"
    assert not result.pending
    assert (repo / "ROW1.md").read_text() == "same\n"


def test_ours_leaves_an_absent_managed_file_absent(actions_repo, tmp_path):
    """--ours must not fabricate a file -- there is no local content to
    keep, so the only sensible meaning is 'leave it missing'."""
    repo = _make_target_repo(tmp_path, stamp="v1.0.0")
    result = _sync_one(repo, actions_repo, "ROW1.md", side="ours")
    assert result.action == "left-absent"
    assert not result.pending
    assert not (repo / "ROW1.md").exists()


def test_missing_file_prompt_is_worded_for_absence_not_edits(actions_repo, tmp_path, monkeypatch):
    repo = _make_target_repo(tmp_path, stamp="v1.0.0")
    seen = {}

    def fake_input(msg):
        seen["msg"] = msg
        return "t"

    monkeypatch.setattr(sync, "input", fake_input, raising=False)
    result = _sync_one(repo, actions_repo, "ROW1.md")
    assert "install from template" in seen["msg"]
    assert result.action == "updated"


def test_absent_file_blocks_dry_run_completion(actions_repo, tmp_path):
    """The bug this guards: an absent required file used to slip through as
    a non-blocking 'info' finding. It must now cause --dry-run to report a
    pending decision and exit 2, exactly like a real conflict does."""
    repo = _make_target_repo(tmp_path, stamp="v1.0.0")
    _seed(repo, "ROW3.md", "x\ny\nz\n")  # everything else present and in sync
    code = sync.main(["--repo-path", str(repo), "--dry-run", "--only", "ROW1.md"])
    assert code == 2


def test_uncommitted_template_error_hints_at_the_working_tree(actions_repo, tmp_path):
    """Lower-priority ask: distinguish 'not committed yet' from 'deleted
    upstream' in the error message, since both look like `git show` failing
    the same way."""
    (actions_repo / "templates" / "UNCOMMITTED.md").write_text("draft\n")
    entry = sync.onboard.TemplateEntry(source="UNCOMMITTED.md", dest="U.md", policy="managed")
    repo = _make_target_repo(tmp_path, stamp="v1.0.0")
    result = sync.decide_and_apply(
        repo, actions_repo, entry, stamp="v1.0.0", dry_run=True, side=None
    )
    assert result.action == "error"
    assert "not committed" in result.detail


# --------------------------------------------------------------- untrusted stamp


def test_usable_stamp_rejects_the_moving_v1_alias():
    assert sync.usable_stamp("v1") is None


def test_usable_stamp_accepts_point_releases_and_commit_shas():
    assert sync.usable_stamp("v1.5.0") == "v1.5.0"
    assert sync.usable_stamp("32ed6e0") == "32ed6e0"  # short SHA fallback
    assert sync.usable_stamp("32ed6e0e1a4cff2f6c3542c030c305dc9ed4c00a") is not None


def test_usable_stamp_passes_through_none():
    assert sync.usable_stamp(None) is None


def test_v1_stamp_does_not_hide_genuine_staleness(actions_repo, tmp_path):
    """The exact failure this guards against: `v1` was force-moved to HEAD
    after this repo synced (see `_make_actions_repo`, which mirrors
    .github/RELEASING.md).

    If a `v1` stamp were trusted as a base, `git show
    v1:templates/ROW2.md` would now resolve HEAD's content, identical to
    `theirs`. `theirs_changed` would then read False. A file that is
    genuinely still on the old v1.0.0 content would land in 'local-edit
    -- left alone' (ours differs from that wrong base) instead of ever
    being flagged for an update. That's non-blocking, so it would never
    resurface.

    `pending` must be True here. That's the signal that actually
    distinguishes the fixed behaviour from the bug: the untrusted stamp
    degrades to the no-stamp two-way path instead, which always treats a
    real difference as a decision."""
    repo = _make_target_repo(tmp_path, stamp="v1")
    _seed(repo, "ROW2.md", "a\nb\nc\n")  # the real v1.0.0 content -- genuinely stale
    result = _sync_one(repo, actions_repo, "ROW2.md", dry_run=True)
    assert result.action not in ("up-to-date", "local-edit"), "a v1 stamp must never be a base"
    assert result.pending


def test_v1_stamp_degrades_to_the_no_stamp_two_way_path(actions_repo, tmp_path):
    """A no-false-positive check, not a guard on the degrade path itself.
    (That's `test_v1_stamp_does_not_hide_genuine_staleness`.) This test
    confirms the fix doesn't over-trigger by flagging a file that's
    identical on both sides just because the stamp is untrusted. It
    passes with or without `usable_stamp` in place: ours == theirs
    short-circuits before the (missing) base is ever consulted either
    way."""
    repo = _make_target_repo(tmp_path, stamp="v1")
    _seed(repo, "ROW1.md", "same\n")  # identical on both sides regardless of base
    result = _sync_one(repo, actions_repo, "ROW1.md", dry_run=True)
    assert result.action == "up-to-date", "identical content is fine to report even without a base"


def test_v1_stamp_is_never_silently_used_even_when_side_is_given(actions_repo, tmp_path):
    """--theirs must still resolve via the (missing) base, not quietly treat
    `v1` as valid and skip straight to a clean 'updated' -- the two-way
    detail wording ('no recorded base version') must be what's reported."""
    repo = _make_target_repo(tmp_path, stamp="v1")
    _seed(repo, "ROW2.md", "a\nb\nc\n")
    result = _sync_one(repo, actions_repo, "ROW2.md", side="theirs")
    assert result.detail == "no recorded base version -- cannot tell stale copy from local edit"


def test_v1_stamp_self_repairs_via_the_advance_on_clean_sync_mechanism(actions_repo, tmp_path):
    """Verifies the actual repair: a completed full sync advances
    `templates_version` via `current_templates_version()`, which (per
    onboard.py's fix) can never emit `v1`. The one clean run this test
    performs is what turns a `"v1"`-stamped repo into one stamped a real
    point release or SHA. That's what makes it self-heal, without
    `sync.py` ever having trusted the bad value along the way.

    This test is insensitive to `usable_stamp` being broken. On a clean
    run (nothing pending), whether the stamp was trusted makes no
    observable difference here, since every file already matches the
    template regardless of which base gets used. It does NOT cover the
    refusing-to-trust half; see
    `test_v1_stamp_does_not_hide_genuine_staleness` for the test that
    actually depends on `usable_stamp` rejecting "v1"."""
    repo = _make_target_repo(tmp_path, stamp="v1")
    # Every managed file already matches the current template exactly, so
    # the run completes cleanly regardless of which base path is taken.
    # This isn't testing conflict resolution, just that a completed run
    # is what triggers the stamp to be rewritten.
    _seed(repo, "ROW1.md", "same\n")
    _seed(repo, "ROW2.md", "a\nb\nc\nd\n")
    _seed(repo, "ROW3.md", "x\ny\nz\n")
    _seed(repo, "ROW4.md", "line1\nline2\nline3-theirs\nline4\nline5\n")

    code = sync.main(["--repo-path", str(repo)])
    assert code == 0

    stamp_after = _stamp(repo)
    assert stamp_after != "v1"
    assert sync.usable_stamp(stamp_after) == stamp_after, "repaired stamp must itself be trusted"

    # Sanity check only, not evidence of the mechanism above. By this
    # point the stamp is already an ordinary trusted value. A local edit
    # made afterward reads as 'local-edit' the same way it would for any
    # freshly-onboarded repo. This doesn't distinguish repaired from
    # never-broken.
    _seed(repo, "ROW3.md", "x\ny\nz\nlocal-addition\n")
    result = _sync_one(repo, actions_repo, "ROW3.md", dry_run=True)
    assert result.action == "local-edit"
    assert result.detail == "local edit only -- left alone"


# ------------------------------------------------------------------ seed-once


def test_seed_once_templates_are_never_synced(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo)
    _seed(repo, "SEEDED.md", "onboarded content, deliberately stale\n")
    results = sync.sync_repo(repo, actions_repo, dry_run=False, side="theirs", only=None)
    assert "SEEDED.md" not in {r.dest for r in results}
    assert (repo / "SEEDED.md").read_text() == "onboarded content, deliberately stale\n"


# --------------------------------------------------------------------- --only


def test_only_filters_to_the_requested_dest(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo)
    results = sync.sync_repo(repo, actions_repo, dry_run=False, side="theirs", only=["ROW1.md"])
    assert [r.dest for r in results] == ["ROW1.md"]


def test_only_rejects_an_unknown_dest(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo)
    with pytest.raises(sync.SyncError):
        sync.sync_repo(repo, actions_repo, dry_run=False, side="theirs", only=["NOPE.md"])


# --------------------------------------------------------------------- errors


def test_template_missing_at_head_is_reported_as_an_error(actions_repo, tmp_path):
    repo = _make_target_repo(tmp_path)
    entry = sync.onboard.TemplateEntry(source="DOES-NOT-EXIST.md", dest="X.md", policy="managed")
    result = sync.decide_and_apply(
        repo, actions_repo, entry, stamp="v1.0.0", dry_run=False, side=None
    )
    assert result.action == "error"


# ------------------------------------------------------------------ idempotency


def test_full_run_advances_the_stamp_and_a_second_run_is_a_noop(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo)
    argv = ["--repo-path", str(repo), "--theirs"]

    code = sync.main(argv)
    assert code == 0, "ROW3's local-edit report doesn't block completion"
    assert _stamp(repo) == sync.onboard.current_templates_version(actions_repo)

    row2_after_first = (repo / "ROW2.md").read_text()
    row4_after_first = (repo / "ROW4.md").read_text()

    results = by_dest(sync.sync_repo(repo, actions_repo, dry_run=False, side=None, only=None))
    assert results["ROW1.md"].action == "up-to-date"
    assert results["ROW2.md"].action == "up-to-date", "already synced -- nothing left to update"
    assert results["ROW3.md"].action == "local-edit", "still a local edit, still just reported"
    assert results["ROW4.md"].action == "up-to-date", "conflict already resolved last run"
    assert (repo / "ROW2.md").read_text() == row2_after_first
    assert (repo / "ROW4.md").read_text() == row4_after_first

    code2 = sync.main(argv)
    assert code2 == 0


def test_only_does_not_advance_the_stamp(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo)
    sync.main(["--repo-path", str(repo), "--theirs", "--only", "ROW2.md"])
    assert _stamp(repo) == "v1.0.0", "a partial (--only) sync must not claim the whole repo synced"


def test_pending_decisions_do_not_advance_the_stamp(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo)
    code = sync.main(["--repo-path", str(repo), "--dry-run"])
    assert code == 2
    assert _stamp(repo) == "v1.0.0"


# --------------------------------------------------------------- pre-stamp repos


def test_write_templates_version_adds_the_missing_key(tmp_path):
    """`[tool.em-release]` present (genuinely onboarded) but predates the
    templates_version field -- `_make_target_repo(stamp=None)` is exactly
    that shape."""
    repo = _make_target_repo(tmp_path, stamp=None)
    sync.write_templates_version(repo, "v9.9.9")
    assert _stamp(repo) == "v9.9.9"


def test_write_templates_version_still_advances_when_the_key_already_exists(tmp_path):
    """Not a regression risk on its own, but pins that adding and advancing
    share one code path rather than the fix accidentally forking it."""
    repo = _make_target_repo(tmp_path, stamp="v1.0.0")
    sync.write_templates_version(repo, "v9.9.9")
    assert _stamp(repo) == "v9.9.9"


def test_write_templates_version_errors_when_em_release_block_is_missing_entirely(tmp_path):
    """The one boundary that must still error: no `[tool.em-release]` block
    at all means this repo was never onboarded, and adding the key would
    fake an onboarding that didn't happen."""
    repo = _make_never_onboarded_target_repo(tmp_path)
    with pytest.raises(sync.SyncError, match="onboard.py"):
        sync.write_templates_version(repo, "v9.9.9")


def test_clean_full_sync_adds_the_stamp_to_a_pre_stamp_repo(actions_repo, tmp_path):
    """Regression: sync.py used to have no way out of this state. Every
    managed template could sync correctly (0 pending, 0 errors) and the
    run would still exit 1, because write_templates_version only knew how
    to advance an existing key, never add a missing one. That's the exact
    state every repo onboarded before this PR is in. Re-onboarding to
    pick up one field is a heavy remedy that invites skipping it."""
    repo = _make_target_repo(tmp_path, stamp=None)
    _seed(repo, "ROW1.md", "same\n")
    _seed(repo, "ROW2.md", "a\nb\nc\n")  # stale -- cleanly resolved by --theirs below
    _seed(repo, "ROW3.md", "x\ny\nz\n")
    _seed(repo, "ROW4.md", "line1\nline2\nline3\nline4\nline5\n")

    code = sync.main(["--repo-path", str(repo), "--theirs"])
    assert code == 0

    stamp_after = _stamp(repo)
    assert stamp_after is not None
    assert sync.usable_stamp(stamp_after) == stamp_after, "the added stamp must itself be trusted"


def test_second_sync_of_a_repaired_pre_stamp_repo_uses_a_real_base(actions_repo, tmp_path):
    """The key added by the first run must actually function as a base on
    the next run, not just silence the error. A genuine local edit made
    afterward must be recognised as 'local-edit', which is only reachable
    through the three-way matrix with a real base. It must not degrade to
    the untrusted two-way wording ('no recorded base version')."""
    repo = _make_target_repo(tmp_path, stamp=None)
    _seed(repo, "ROW1.md", "same\n")
    _seed(repo, "ROW2.md", "a\nb\nc\nd\n")
    _seed(repo, "ROW3.md", "x\ny\nz\n")
    _seed(repo, "ROW4.md", "line1\nline2\nline3-theirs\nline4\nline5\n")

    code = sync.main(["--repo-path", str(repo)])  # already matches theirs -- nothing to resolve
    assert code == 0
    assert _stamp(repo) is not None

    _seed(repo, "ROW3.md", "x\ny\nz\nlocal-addition\n")
    result = _sync_one(repo, actions_repo, "ROW3.md", dry_run=True)
    assert result.action == "local-edit"
    assert result.detail == "local edit only -- left alone"


def test_pending_decisions_on_a_pre_stamp_repo_do_not_add_the_key(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo, stamp=None)
    code = sync.main(["--repo-path", str(repo), "--dry-run"])
    assert code == 2
    assert _stamp(repo) is None


def test_never_onboarded_repo_still_errors_and_points_at_onboard(actions_repo, tmp_path, capsys):
    repo = _make_never_onboarded_target_repo(tmp_path)
    _seed(repo, "ROW1.md", "same\n")
    _seed(repo, "ROW2.md", "a\nb\nc\nd\n")
    _seed(repo, "ROW3.md", "x\ny\nz\n")
    _seed(repo, "ROW4.md", "line1\nline2\nline3-theirs\nline4\nline5\n")

    code = sync.main(["--repo-path", str(repo)])  # clean, but nothing to add the key to
    assert code == 1
    assert "onboard.py" in capsys.readouterr().err


# -------------------------------------------------------------------- CLI exit codes


def test_main_exits_2_when_decisions_are_pending(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo)
    assert sync.main(["--repo-path", str(repo), "--dry-run"]) == 2


def test_main_exits_0_when_fully_resolved(actions_repo, tmp_path):
    repo = _row_target(tmp_path, actions_repo)
    assert sync.main(["--repo-path", str(repo), "--ours"]) == 0


def test_main_rejects_ours_and_theirs_together():
    with pytest.raises(SystemExit):
        sync.main(["--repo-path", "/nonexistent", "--ours", "--theirs"])


# ------------------------------------------------------------------- git plumbing


def test_three_way_merge_reports_clean_for_non_overlapping_changes():
    # Enough unchanged context (c, e) between the two edits for git to place
    # them independently rather than calling the whole region ambiguous.
    merged, clean = sync.three_way_merge(
        b"a\nb-ours\nc\nd\ne\n", b"a\nb\nc\nd\ne\n", b"a\nb\nc\nd-theirs\ne\n"
    )
    assert clean
    assert merged == b"a\nb-ours\nc\nd-theirs\ne\n"


def test_three_way_merge_reports_conflict_for_overlapping_changes():
    _, clean = sync.three_way_merge(b"a\nb-ours\nc\n", b"a\nb\nc\n", b"a\nb-theirs\nc\n")
    assert not clean


def test_read_template_at_returns_none_for_a_path_that_never_existed(actions_repo):
    assert sync.read_template_at(actions_repo, "HEAD", "NOPE.md") is None


def test_read_template_at_reads_a_historical_tag(actions_repo):
    assert sync.read_template_at(actions_repo, "v1.0.0", "ROW2.md") == b"a\nb\nc\n"
    assert sync.read_template_at(actions_repo, "HEAD", "ROW2.md") == b"a\nb\nc\nd\n"
