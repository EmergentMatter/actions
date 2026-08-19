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
concept and [CONTRACT.md](CONTRACT.md) for the full behavioral spec; every
workstream in this repo builds against CONTRACT.md as source of truth.

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

- `CONTRACT.md` — the build contract. Owned by Izuku; source of truth for
  every script CLI, workflow input, and behavior in this repo. Read this
  before touching anything.
- `scripts/compute_bump.py` — reads `changelog.d/` notes, returns the
  release level/version that should be cut.
- `scripts/sync_version.py` — writes a version string into every location
  a consuming repo declares via `[tool.em-release] version_files`.
- `.github/workflows/{changelog-check,version,build-release}.yml` — the
  three reusable `workflow_call` workflows consuming repos pin to `@v1`.
- `templates/` — everything a consuming repo copies in at onboarding time:
  the three workflow stubs, `CONTRIBUTING.md`, and `pyproject-snippet.toml`
  (the `[tool.towncrier]` + `[tool.em-release]` config block).
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
- **No stub grants secrets to a shared workflow.** None of the three
  reusable workflows reads `secrets.*` — they use only the auto-injected
  `github.token`, and `build-release.yml` publishes over OIDC trusted
  publishing (`id-token: write`), not a token. `stub-changelog-check.yml`
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

## Naming Conventions

Standard playbook conventions apply (`../engineering-playbook/conventions/naming.md`);
this repo is plain Python, not JAX/PicoGK, so the Hungarian-prefix table
mostly doesn't come up outside scalar CLI args.

## Known Issues

None yet — repo is new.

## Current Work

Initial parallel build of the v1 release-control system per CONTRACT.md:
scripts (`scripts/`, `tests/`), workflows (`.github/`, `templates/ci.yml`,
`templates/stub-*.yml`), and documentation (this file, `README.md`,
`docs/`, `templates/*.md`, `templates/pyproject-snippet.toml`) landing
together. Not yet used by any consuming repo.
