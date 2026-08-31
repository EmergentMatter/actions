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
version. That stamp must name an immutable ref. Pointing it at the moving
`v1` alias collapses the merge base toward the current template and
silently stops detecting staleness, which looks like success. See the
`usable_stamp` docstring in `scripts/sync.py`.

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
form rather than the longer reusable-workflow path a consumer's branch
protection would otherwise have to match.

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
  human-pushed tag and the manual rebuild. Do not fold it away. The
  behaviour it works around is in `CONTRACT.md`, under its section on why
  the release is not tag-triggered.
- **`scripts/changeset.py` belongs at a consumer repo's root, outside
  the package.** Moving it, or wiring a console entry point, ships a
  contributor-only tool onto the PATH of everyone who installs the
  library. The reasoning is in `templates/changeset.py`'s module
  docstring, which travels with the file.
- **No stub grants secrets to a shared workflow.** The reusable workflows
  read no `secrets.*`; they use only the auto-injected `github.token`, and
  publishing goes over OIDC trusted publishing (`id-token: write`) when
  configured, not a token. `stub-changelog-check.yml` omits
  `secrets: inherit` because it triggers on `pull_request`, which can be a
  fork PR in a public repo, and there is nothing to grant.
  `stub-build-release.yml` omits it for the OIDC reason above.
  `stub-version.yml` puts `secrets: inherit` on the *consumer's own* `ci:`
  job, conditionally, only if that repo's own CI needs a named secret
  (concrete example: `sdm-core`'s CI needs `MATERIALS_REPO_TOKEN` to
  check out the private `emergent-matter-materials` sibling), never on
  the job that calls into this repo. Granting a public repo pinned by a
  movable `v1` tag your org's secrets would make that tag equivalent to
  secret access everywhere it's pinned; see `docs/onboarding.md` for the
  full explanation aimed at onboarders.
- **Never add a `permissions:` or `environment:` value to a job in
  `version.yml` without reading the publish job's own comment first.**
  Both are validated at parse time, before any `if:` runs, so getting one
  wrong does not degrade: it breaks every run for every onboarded repo
  with `startup_failure` and no message via the API. The comment on that
  job in `.github/workflows/version.yml` records what happened and why the
  job declares no permissions of its own; `docs/onboarding.md`'s
  "Publishing to a package index" section gives it the prominence it
  earned.
- **Every stub with an `actions-ref` input must set it to match the
  `@ref` it's pinned to** (`version.yml` and `changelog-check.yml`'s
  stubs; `build-release.yml`'s doesn't take this input, since it never
  checks out this repo's `scripts/`). There is no context field that
  lets a reusable workflow discover its own ref: `github.workflow_ref`
  resolves to the *caller's* ref and `github.job_workflow_sha` does not
  exist, both confirmed live. An unresolvable ref is refused rather than
  silently falling back to this repo's default branch.
- **A shared composite action for the build/release steps was tried and
  withdrawn, but not because it was proven broken.** It was removed
  while chasing the `startup_failure` above, whose real cause was the
  publish-job permissions issue, not the composite action. Don't restate
  "a local `./` action can't work in a reusable workflow" as settled fact
  in any doc; it was never isolated as the actual problem. What's
  genuinely awkward is that `uses:` can't take a templated ref, so a
  composite action couldn't follow the same `@ref` this workflow is
  pinned at without a hardcoded ref, and a `./`-relative path's
  resolution for a reusable workflow called cross-repo was never tested
  in isolation. The build/release steps are therefore duplicated between
  `version.yml` and `build-release.yml` on purpose, and verified working
  end to end. Keep the two copies in step when either changes, and only
  revisit sharing them with an isolated test of the `./` resolution
  question specifically.

## Naming Conventions

Standard playbook conventions apply (`../engineering-playbook/conventions/naming.md`);
this repo is plain Python, not JAX/PicoGK, so the Hungarian-prefix table
mostly doesn't come up outside scalar CLI args.
