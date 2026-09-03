# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Before starting work

Read the board first. What is planned, in flight, or known broken is not
recorded in this repo. Before proposing or starting anything:

```bash
gh issue list                                    # open work, with the reasoning in each body
gh api repos/:owner/:repo/milestones --jq '.[].title'   # what the next release commits to
git tag --sort=-v:refname | head -1              # the current version
```

Do not add a roadmap, a "current work" note, or a known-issues list to any
file here. File an issue instead, and put the reason and the definition of
done in its body.

## Org Playbook

This project follows the EmergentMatter engineering playbook. See
`../engineering-playbook/CLAUDE.md` for org-wide patterns, conventions,
and the repo catalog. When in doubt, check the playbook before inventing
a new pattern.

## Project Overview

`EmergentMatter/actions` is the org's shared release-control system: a
public, passive repo of reusable GitHub Actions workflows plus small
stdlib-only Python scripts that let every Python repo in the org version
and release the same way: one changelog note per PR, one computed release
PR, merging that PR is the release. See [README.md](README.md) for the
concept, [docs/onboarding.md](docs/onboarding.md) to onboard a repo,
[docs/tooling.md](docs/tooling.md) to run the maintenance scripts, and
[CONTRACT.md](CONTRACT.md) for the full behavioural spec. Read CONTRACT.md
before changing how release control behaves.

This repo holds no secrets and calls nothing at runtime. Consuming repos
run the actual workflows in their own context using their own
`secrets.GITHUB_TOKEN`. See `conventions/release-control.md` in the
engineering playbook for the org-wide convention this repo implements.

## Build & Run

```bash
uv sync                                    # scripts/ deps are stdlib-only; this is dev tooling only
uv run pytest                              # tests/ -- script behavior against CONTRACT.md
uv run ruff check .                        # lint (line-length 100, target py313)
uv run python scripts/compute_bump.py --format json          # dry-run the bump computation
uv run python scripts/sync_version.py --version 1.2.3 --check  # dry-run version-string agreement
```

## How the pieces fit

Each script opens with a module docstring stating why it exists. What
follows is only the reasoning that spans files, which no single docstring
can hold.

**Where a script runs decides what it may assume**, and the split is
described in README.md's "The scripts" section. Keep a script on one side
of it. A change that lets a workflow-called script reach for `gh`, or that
wires a maintenance script into any repo's CI, is the one to catch in
review.

**Onboarding is one-time; drift is forever.** `onboard.py` copies
`templates/` into a repo once, and nothing about that snapshot re-checks
itself. `sync.py` closes that gap and `fleet_status.py` watches it. This
is why a template change is not finished when the template changes: it
lands in a consumer only when someone runs sync.

**The `templates_version` stamp is what makes sync possible.** A copied
file that differs from its template is either stale or deliberately
edited, and a two-way diff cannot tell those apart, so `sync.py` compares
three ways against the template as it stood at the repo's recorded
version. That stamp must name an immutable ref, never the moving `v1`
alias. See [ADR 0005](docs/adr/0005-sync-uses-a-three-way-compare.md) and
the `usable_stamp` docstring in `scripts/sync.py`.

**Adding a template is one entry in `templates/manifest.toml`.** The
manifest is the single declaration of what a consumer receives, read at
runtime by `onboard.py`, `sync.py`, and `fleet_status.py`; its header
comment defines the policies. `pyproject-snippet.toml` is deliberately
absent from it: consumers copy-paste that block into their own
`pyproject.toml`, so there is nothing to sync.

**`onboard.py` enables private vulnerability reporting on a public repo**
because it has just installed a `SECURITY.md` promising that route. The
promise and the setting have to land together.

**`changelog-check` is a composite action, not a `workflow_call`
workflow.** Composite so it reaches `scripts/` through
`github.action_path`, with no `actions-ref` input to keep in sync, and so
its required-check name is the short `EmergentMatter/actions/changelog-check`
form. See
[ADR 0004](docs/adr/0004-changelog-check-is-a-composite-action.md).

## Technical Context

- **Python only, no Node/npm.** Zero third-party runtime deps in
  `scripts/`: stdlib only (`tomllib` is stdlib on 3.11+). Target Python
  3.13.
- **No long-lived tokens.** Every workflow uses `secrets.GITHUB_TOKEN`; no
  PATs, no GitHub App, in v1.
- **No special handling below 1.0.** A major-level note on `0.4.2` bumps
  to `1.0.0` like any other major bump. Semver, no exceptions.
- **Silent no-ops are the bug to avoid**, not the failure to avoid: the
  scripts exit 1 loudly on anything ambiguous (unparseable bump level,
  missing declared file/symbol) rather than guessing or skipping.
- **The release PR's own checks show as not-run, and that is accepted.**
  The full account, including why it is not worked around and the escape
  hatch for a human who wants the checks to run, is in `CONTRACT.md`,
  under its section on the known limitation.
- **`build-release.yml` is not a redundant leftover.** `version.yml`
  tags and builds in one run because the tag-triggered design silently
  never fires on the automated path; `build-release.yml` covers the
  human-pushed tag and the manual rebuild. Do not fold it away. See
  [ADR 0002](docs/adr/0002-build-and-release-run-inline-not-on-tag-push.md).
- **`scripts/changeset.py` belongs at a consumer repo's root, outside
  the package.** Moving it, or wiring a console entry point, ships a
  contributor-only tool onto the PATH of everyone who installs the
  library. The reasoning is in `templates/changeset.py`'s module
  docstring, which travels with the file.
- **No stub grants secrets to a shared workflow.** The reusable workflows
  read no `secrets.*`; they use only the auto-injected `github.token`, and
  publishing goes over OIDC trusted publishing (`id-token: write`) when
  configured, not a token. `secrets: inherit` appears only on a
  *consumer's own* `ci:` job, conditionally, never on the job that calls
  into this repo. See
  [ADR 0006](docs/adr/0006-no-stub-grants-secrets-to-a-shared-workflow.md)
  for the residual blast radius this does and does not close, and
  `docs/onboarding.md` for the explanation aimed at onboarders.
- **Never add a `permissions:` or `environment:` value to a job in
  `version.yml` without reading
  [ADR 0003](docs/adr/0003-the-publish-job-declares-no-permissions.md)
  first.** Both are validated at parse time, before any `if:` runs, so
  getting one wrong does not degrade: it breaks every run for every
  onboarded repo with `startup_failure` and no message via the API. The
  comment on that job in `.github/workflows/version.yml` carries the
  mechanism inline, and `docs/onboarding.md`'s "Publishing to a package
  index" section gives it the prominence it earned.
- **Every stub with an `actions-ref` input must set it to match the
  `@ref` it's pinned to** (`version.yml` and `changelog-check.yml`'s
  stubs; `build-release.yml`'s doesn't take this input, since it never
  checks out this repo's `scripts/`). There is no context field that
  lets a reusable workflow discover its own ref: `github.workflow_ref`
  resolves to the *caller's* ref and `github.job_workflow_sha` does not
  exist, both confirmed live. An unresolvable ref is refused rather than
  silently falling back to this repo's default branch.
- **A shared composite action for the build/release steps was tried and
  withdrawn, but not because it was proven broken.** The build/release
  steps are duplicated between `version.yml` and `build-release.yml` on
  purpose. Keep the two copies in step when either changes, and read
  [ADR 0007](docs/adr/0007-shared-composite-action-for-build-steps-withdrawn.md)
  before reintroducing a shared action: it records what was actually
  established, and what was only assumed during an incident.

## Decisions

Major decisions are recorded in [`docs/adr/`](docs/adr/), one per file,
and are not restated here. A record states what was true when it was
accepted, so it does not go stale the way the same reasoning written as
ordinary prose does. Add one when a decision is made rather than
explaining it again in this file.

## Naming Conventions

Standard playbook conventions apply (`../engineering-playbook/conventions/naming.md`);
this repo is plain Python, not JAX/PicoGK, so the Hungarian-prefix table
mostly doesn't come up outside scalar CLI args.
