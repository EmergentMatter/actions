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
- `.github/workflows/{changelog-check,version,build-release}.yml` — the
  three reusable `workflow_call` workflows consuming repos pin to `@v1`.
- `templates/` — everything a consuming repo copies in at onboarding time:
  the three workflow stubs, `changeset.py` (→ `scripts/changeset.py` at
  the consumer's root, **not** inside their package), `CONTRIBUTING.md`,
  and `pyproject-snippet.toml` (the `[tool.towncrier]` + `[tool.em-release]`
  config block).
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
  fire — silently never runs on the automated path. Confirmed on the
  rehearsal repo: `v1.0.0` was tagged and zero tag-triggered runs appear
  in its history. `version.yml` pushes the tag **and** builds/publishes
  the release **in the same run** instead. `build-release.yml` still
  exists, but only for a human-pushed tag (which does trigger normally)
  or a manual `workflow_dispatch` rebuild — it is deliberately not the
  automatic path, not a redundant leftover.
- **`scripts/changeset.py` must live at a consumer repo's root, outside
  the package, invoked as `uv run scripts/changeset.py`** — never as a
  `[project.scripts]` console entry. Confirmed by building a wheel during
  rehearsal: putting it inside `src/<package>/` (or wiring the console
  script) shipped a `changeset` command onto the PATH of anyone who
  installs the library. A contributor-only tool has no business in a
  published package.
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
  breaks the whole workflow.** Two real incidents from this one rule
  during rehearsal: `version.yml`'s publish job briefly declared
  `permissions: {id-token: write}` on itself, which broke **every**
  run of `version.yml` for **every** onboarded repo, publish on or off,
  with `startup_failure` and no message via the API — fixed by removing
  that job-level `permissions:` entirely so the job now inherits
  whatever the *consumer's own stub* grants. Separately, `environment:`
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
2026-08-19 documentation sweep -- its "Consumer stub" example missing the
`with: { actions-ref: v1 }` block that `templates/stub-version.yml`
actually carries, and its workflow-inputs table wrongly listing
`actions-ref` as a `build-release.yml` input -- were both fixed in
`CONTRACT.md` the same day, by its owner.

## Current Work

`v1` is tagged, verified end to end against a real rehearsal repo
(`em-release-control-test`), and ready to onboard production repos.
`v1.0.0` and `v1.0.1` have both shipped through the full cycle: PR →
note check → release PR → (close/reopen to force checks under branch
protection) → approve → merge → tag → build → GitHub Release. No
production repo consumes this yet; `emergent-matter-materials` is the
intended first pilot, then `emergent-matter-sdm-core`.
