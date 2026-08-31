# Contributing

Read [STYLE.md](STYLE.md) before writing code. It's the org's style guide:
Python conventions, naming, docstrings, typing, testing, and the
open-source hygiene checklist, all in one place.

Participation here is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

This repo also uses release control: every PR that changes behavior adds a
small note describing the change, and those notes become the next release.
This file covers the part you actually do as a contributor. For how the
release side works, see
[EmergentMatter/actions](https://github.com/EmergentMatter/actions)'s
`docs/onboarding.md`.

## Dev setup

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

## Add a changelog note

Before opening your PR:

```bash
uv run scripts/changeset.py
```

This asks you two things:

1. **A level**: press `1`-`3` to pick one outright, or move with the
   arrow keys (`k`/`j` also work) and press Enter. `q`, `Esc`, or Ctrl-C
   backs out; nothing is written.
2. **One sentence**: what changed, in **plain, user-facing language**.

It writes a small file into `changelog.d/`. Commit it with the rest of your
PR. A check on the PR fails if this file is missing.

### Running it without a terminal

`scripts/changeset.py` needs a real terminal for its arrow-key picker.
Without one (stdin or stdout not a TTY), it drops to a plain numbered
prompt instead: type a digit to pick the level, then type the summary
line. If stdin has nothing to read at all (closed, or redirected from
an empty source), each prompt hits end of file. The script then prints
`Cancelled.` and exits 0. It never hangs waiting for input that can't
arrive.

### Picking a level

Ask what a downstream user of this code needs to know, not what the diff
looks like:

- **major**: breaks something that worked before. A function signature
  changed incompatibly, a return type changed shape, a previously-guaranteed
  behavior is gone. Below 1.0, this still means major. There's no "0.x
  special case" here.
- **minor**: adds something without breaking anything existing. A new
  function, a new optional field, a new capability someone can opt into.
- **patch**: fixes something without changing the interface. A bug fix, a
  corrected value, a performance improvement, a documentation fix.

**Documentation is `patch`.** A docs-only PR still ships a version, because
the alternative is the fix sitting on `main` unreleased indefinitely. If
it's worth merging, it's worth one line in the changelog and a version bump
-- that's what makes the fix actually reach someone using the code, instead
of existing only in git history.

### Writing the sentence

The sentence is what a person **using** this code needs to know, not a
description of the diff. Compare:

- Diff-shaped (avoid): "Refactored `parse_config` to use a dataclass
  instead of a dict."
- User-facing (write this): "Config values with the wrong type now raise a
  clear error instead of failing silently later."

If the change is purely internal (refactor, test-only, CI-only) and a user
of this code would never notice it either way, it may not need a note at
all. See below.

## When a change doesn't need a note

If a change isn't worth a version bump (a typo fix in a comment, CI
tuning, a test-only change), skip the changeset and add the
`skip-changelog` label to the PR instead. The changelog check treats that
label as an explicit "this PR intentionally has no note," not a missing
one.

Don't reach for `skip-changelog` to avoid deciding on a level. If in doubt,
it's almost always `patch`.

## Merging your PR does not release anything

Your PR merging to `main` only adds your note to the pile of notes waiting
to ship. Nothing is versioned, tagged, or published at that point. A
separate automated workflow computes the next version from everything
pending, updates version strings, rewrites `CHANGELOG.md`, and opens (or
updates) a single **"Release vX.Y.Z"** pull request. **Merging that PR is
the release.** That's the one moment version numbers move, tags get cut,
and (if configured) a package gets published. This is deliberate: nothing
ships without a maintainer choosing to merge that specific PR.

## The release PR's checks may show as "not run": this is expected

The release PR is opened by the `GITHUB_TOKEN` used inside GitHub Actions.
GitHub does not trigger further workflow runs from a PR opened this way, so
its own status checks can appear as not having run at all, even though
nothing is actually broken. The code being released was already verified
on `main` before the PR was drafted (the release workflow only runs after
this repo's own CI passes).

**To force the checks to run: close the release PR, then immediately
reopen it.** That reopen event is enough to trigger the checks normally.
This is a known GitHub Actions limitation (not specific to this repo; see
[peter-evans/create-pull-request's
notes](https://github.com/peter-evans/create-pull-request/blob/main/docs/concepts-guidelines.md#triggering-further-workflow-runs)
on why PRs opened via `GITHUB_TOKEN` don't trigger further runs), and
close-then-reopen is the accepted workaround, not a bug to chase further.

If this repo has branch protection on (it should), the release PR also
needs an approving review before it can merge, same as any other PR --
close/reopen only fixes the checks, not the review requirement. See
`EmergentMatter/actions`'s `docs/onboarding.md` for the full maintainer-
facing walkthrough of releasing under protection.
