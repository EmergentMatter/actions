# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Org Playbook

This project follows the EmergentMatter engineering playbook. See
`../engineering-playbook/CLAUDE.md` for org-wide patterns, conventions,
and the repo catalog. When in doubt, check the playbook before inventing
a new pattern.

## Project Overview

`EmergentMatter/actions` is the org's shared release-control system: a
public, passive repo of reusable GitHub Actions workflows plus small
stdlib-only Python scripts that let every Python repo in the org version
and release the same way — one changelog note per PR, one computed release
PR, merging that PR is the release. See [README.md](README.md) for the
concept, [docs/onboarding.md](docs/onboarding.md) to onboard a repo, and
[CONTRACT.md](CONTRACT.md) for the full behavioural spec.

This repo holds no secrets and calls nothing at runtime — consuming repos
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

## Architecture / Key Files

- `CONTRACT.md` — the behavioural spec: every script CLI, workflow input,
  and the exact sequence `version.yml` performs, with the reasoning behind
  each guarantee. Read it before changing how release control behaves.
- `scripts/compute_bump.py` — reads `changelog.d/` notes, returns the
  release level/version that should be cut.
- `scripts/sync_version.py` — writes a version string into every location
  a consuming repo declares via `[tool.em-release] version_files`.
- `scripts/lint_gate.py` — turns a consuming repo's lint gate on or off, or
  reports its state. The gate has two halves that must agree — a
  `continue-on-error` line in the repo's ci.yml and whether `lint` is a
  required status check — and this moves both together. Maintenance tool,
  run against a target repo; not copied into consumers.
- `scripts/fleet_status.py` — sweeps every repo pinned to this one and
  reports drift: broken changelog stubs, inconsistent lint gates, Node 20
  action pins, missing required checks. Reads over the API, no clones.
  Onboarding happens once per repo; `templates/` is copied and never
  re-synced, so drift is the recurring problem and this is what watches it.
  Exits non-zero on any finding, so it can run on a schedule.
- `changelog-check/action.yml` — the PR gate, a **composite action**
  consuming repos call as `uses: EmergentMatter/actions/changelog-check@v1`
  inside a normal job. Composite rather than `workflow_call` so it reaches
  `scripts/` via `github.action_path` (no `actions-ref` to keep in sync)
  and so its check name is two segments rather than three.
- `.github/workflows/{version,build-release}.yml` — the two reusable
  `workflow_call` workflows consuming repos pin to `@v1`.
- `verify-wheel/action.yml` and `scripts/verify_wheel.py` — the composite
  action that installs the built wheel in a clean venv and imports it, so a
  repo that is green on tests but broken to install fails before release.
  Never invoked directly; the action calls the script.
- `templates/manifest.toml` — the single declaration of what a consuming
  repo receives: one entry per file, each giving a source under `templates/`, the
  `dest` it lands at, and a policy. `managed` means it should always match
  the template; `seed-once` means written at onboarding if absent and never
  touched again. `ci.yml` is the only `seed-once` entry, because repos
  legitimately customise their own CI. Read by `onboard.py`, `sync.py` and
  `fleet_status.py`, so adding a template is one entry in one file.
- `templates/` — the files themselves: the three workflow stubs,
  `changeset.py` (→ `scripts/changeset.py` at the consumer's root,
  **not** inside their package), `CONTRIBUTING.md`, `ci.yml`,
  `pyproject-snippet.toml` (the `[tool.towncrier]` + `[tool.em-release]`
  block, copy-pasted rather than synced, so not in the manifest), and the
  community-health set: `SECURITY.md`, `SUPPORT.md`, `CODE_OF_CONDUCT.md`,
  `NOTICE`, `LICENSE`, `CODEOWNERS`, `dependabot.yml`, a PR template and
  three issue templates.
- `scripts/onboard.py` — installs everything in the manifest into a repo,
  writes the config block, creates the `skip-changelog` label, and on a
  public repo enables private vulnerability reporting (because it just
  installed a `SECURITY.md` promising that route). Records
  `templates_version` so later syncs have a merge base.
- `scripts/sync.py` — re-syncs an already-onboarded repo. A copied file
  that differs from its template is either stale or deliberately edited,
  and a two-way diff cannot tell those apart, so it three-way compares
  against the template as it was at the repo's recorded
  `templates_version`. Stale copies update silently, deliberate edits are
  left alone and reported, and only a real conflict asks a human. The
  stamp must name an immutable ref: the moving `v1` alias would collapse
  `base` toward `theirs` and silently stop detecting staleness.
- `docs/tooling.md` — how to run the maintenance scripts above against a
  target repo, and what each check means.
- `docs/onboarding.md` — the human walkthrough for onboarding a repo,
  worked through `emergent-matter-materials`'s three-version-strings case.

## Technical Context

- **Python only, no Node/npm.** Zero third-party runtime deps in
  `scripts/` — stdlib only (`tomllib` is stdlib on 3.11+). Target Python
  3.13.
- **No long-lived tokens.** Every workflow uses `secrets.GITHUB_TOKEN`; no
  PATs, no GitHub App, in v1.
- **No special handling below 1.0.** A major-level note on `0.4.2` bumps
  to `1.0.0` like any other major bump — semver, no exceptions.
- **The known limitation:** a PR opened with `GITHUB_TOKEN` doesn't trigger
  further workflow runs, so the release PR's own checks show as not-run.
  This is accepted (not worked around) because the consuming repo's own CI
  already gated the code on `main` before the release PR was drafted, via
  the `needs: ci` edge in its `version.yml` stub. The escape hatch for a
  human who wants the checks to actually run: close the release PR and
  immediately reopen it. Every consumer-facing doc states this plainly --
  see `templates/CONTRIBUTING.md`.
- **Silent no-ops are the bug to avoid**, not the failure to avoid: both
  scripts exit 1 loudly on anything ambiguous (unparseable bump level,
  missing declared file/symbol) rather than guessing or skipping.
- **The release is not tag-triggered.** A tag pushed with `GITHUB_TOKEN`
  never triggers a workflow (GitHub suppresses it to prevent recursion),
  so the obvious design — push a `v*` tag, let `build-release.yml`
  fire — silently never runs on the automated path.
  `version.yml` pushes the tag **and** builds/publishes
  the release **in the same run** instead. `build-release.yml` still
  exists, but only for a human-pushed tag (which does trigger normally)
  or a manual `workflow_dispatch` rebuild — it is deliberately not the
  automatic path, not a redundant leftover.
- **`scripts/changeset.py` must live at a consumer repo's root, outside
  the package, invoked as `uv run scripts/changeset.py`** — never as a
  `[project.scripts]` console entry. Putting it inside `src/<package>/`,
  or wiring the console script, ships a `changeset` command onto the PATH
  of anyone who installs the library. A contributor-only tool has no
  business in a published package.
- **No stub grants secrets to a shared workflow.** None of the three
  reusable workflows reads `secrets.*` — they use only the auto-injected
  `github.token`, and `build-release.yml` and `version.yml` publish over
  OIDC trusted publishing (`id-token: write`) when configured, not a
  token. `stub-changelog-check.yml`
  omits `secrets: inherit` because it triggers on `pull_request` (which
  can be a fork PR in a public repo) and there's nothing to grant.
  `stub-build-release.yml` omits it for the OIDC reason above.
  `stub-version.yml` puts `secrets: inherit` on the *consumer's own* `ci:`
  job, conditionally — only if that repo's own CI needs a named secret
  (concrete example: `sdm-core`'s CI needs `MATERIALS_REPO_TOKEN` to
  check out the private `emergent-matter-materials` sibling) — never on
  the job that calls into this repo. Granting a public repo pinned by a
  movable `v1` tag your org's secrets would make that tag equivalent to
  secret access everywhere it's pinned; see `docs/onboarding.md` for the
  full explanation aimed at onboarders.
- **A `permissions:` ceiling on `workflow_call` is checked at parse
  time, before any `if:` runs — getting this wrong doesn't degrade, it
  breaks the whole workflow.** A job declaring
  `permissions: {id-token: write}` that the caller has not granted breaks
  **every** run of `version.yml` for **every** onboarded repo, publish on
  or off, with `startup_failure` and no message via the API. The publish
  job therefore declares no `permissions:` at all and inherits whatever
  the *consumer's own stub* grants. Likewise `environment:`
  (required on that job even when `publish` is `false`, since GitHub
  validates it at the same parse step) used to default to an empty
  string, which is invalid there too and broke every run the same way --
  fixed by shipping a non-empty default (`release`). See the publish
  job's own comment in `.github/workflows/version.yml` for the full
  account; `docs/onboarding.md`'s "Publishing to a package index"
  section is written to give this the prominence it earned.
- **Every stub with an `actions-ref` input must set it to match the
  `@ref` it's pinned to** (`version.yml` and `changelog-check.yml`'s
  stubs; `build-release.yml`'s doesn't take this input, since it never
  checks out this repo's `scripts/`). There is no context field that
  lets a reusable workflow discover its own ref — `github.workflow_ref`
  resolves to the *caller's* ref and `github.job_workflow_sha` does not
  exist, both confirmed live. An unresolvable ref is refused rather than
  silently falling back to this repo's default branch.
- **A shared composite action for the build/release steps was tried and
  withdrawn — but not because it was proven broken.** It was removed
  while chasing the `startup_failure` above, whose real cause was the
  publish-job permissions issue, not the composite action. Don't restate
  "a local `./` action can't work in a reusable workflow" as settled fact
  in any doc — it was never isolated as the actual problem. What's
  genuinely awkward is that `uses:` can't take a templated ref, so a
  composite action couldn't follow the same `@ref` this workflow is
  pinned at without a hardcoded ref, and a `./`-relative path's
  resolution for a reusable workflow called cross-repo was never tested
  in isolation. The build/release steps are therefore duplicated between
  `version.yml` and `build-release.yml` on purpose (four steps, verified
  working end to end) — keep the two copies in step when either changes,
  and only revisit sharing them with an isolated test of the `./`
  resolution question specifically.

## Naming Conventions

Standard playbook conventions apply (`../engineering-playbook/conventions/naming.md`);
this repo is plain Python, not JAX/PicoGK, so the Hungarian-prefix table
mostly doesn't come up outside scalar CLI args.

## Known Issues

None currently open. Two `CONTRACT.md` staleness findings from the
2026-08-19 documentation sweep — its "Consumer stub" example missing the
`with: { actions-ref: v1 }` block that `templates/stub-version.yml`
actually carries, and its workflow-inputs table wrongly listing
`actions-ref` as a `build-release.yml` input — were both fixed in
`CONTRACT.md` the same day, by its owner.

## Current Work

In production. `v1.5.0` is the newest tag, and
`emergent-matter-materials` is genuinely onboarded: real `[tool.em-release]`
block with two declared `version_files`, all four stubs installed, releasing
through this system since August and currently at `v1.11.0`.
`emergent-matter-sdm-core` is next.

`em-release-control-test` remains the rehearsal repo. It is a real consumer
with four releases of its own, and it is where template changes get proven
against real GitHub behaviour before production repos receive them. Issue
forms only render from a default branch, so that repo is the only way to see
them before they ship.
