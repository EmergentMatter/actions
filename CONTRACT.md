# Behavioural spec — EmergentMatter/actions v1

The contract between the reusable workflows in this repo and the repos that call them:
script CLIs, `workflow_call` inputs, and the exact sequence `version.yml` performs.

**If you are onboarding a repo, you want [`docs/onboarding.md`](docs/onboarding.md) instead.**
This file is for people changing how release control itself behaves — it records not just
what the system does but why, so a future change doesn't quietly break a guarantee.

Requirement IDs (A1, V4, C2 …) refer to §09 of the "Release Control" proposal, which is the
source of truth for intent. Changes here are changes to the contract every consuming repo
depends on: they need review from the owners in `CODEOWNERS`, and anything that alters the
inputs or the behaviour of a shared workflow is a breaking change for every repo pinned to
`@v1`.

## Non-negotiables

- **Python only.** No Node/npm backend anywhere in v1.
- **No long-lived tokens.** The bot uses `secrets.GITHUB_TOKEN`. No PATs, no GitHub App in v1.
- **The shared repo is passive** — it holds no secrets and reaches nothing. Consumers run the
  workflows in their own context.
- **Zero third-party runtime deps in `scripts/`.** stdlib only (`tomllib` is stdlib on 3.11+).
- Target Python **3.13**.

## Script CLIs (scripts/)

### compute_bump.py

    compute_bump.py [--notes-dir changelog.d] [--pyproject pyproject.toml]
                    [--format github|json]

Reads pending note files, returns the release that should be cut.

- Note filename grammar: `+<hex>.<level>.md` where level ∈ {major, minor, patch}.
  The leading `+` is towncrier's orphan-fragment marker (no issue number). Other towncrier
  fragment names (`123.minor.md`) must also parse — take the part after the last `.` before `.md`.
- `level` is the **maximum** across all pending notes: one major among twenty patches → major. (V1)
- Standard semver. **No special handling below 1.0** — major on `0.4.2` → `1.0.0`. (V2)
- Unknown/unparseable fragment type → **exit 1 with a loud message**. Never guess. (V3)
- No notes at all → `level=none`, exit 0. The caller stops; this is not an error.
- Current version is read from `[project] version` in pyproject via `tomllib`.
- `--format json` → one JSON object on stdout.
  `--format github` → `key=value` lines appended to `$GITHUB_OUTPUT`.
- Keys emitted: `level`, `current`, `next`, `count`.

### sync_version.py

    sync_version.py --version X.Y.Z [--pyproject pyproject.toml] [--check]

Writes the version into every location the repo declares, and nothing else.

- Reads `[tool.em-release] version_files = ["path:symbol", ...]`.
  A `path:symbol` entry means "in that file, the assignment `symbol = "..."` gets the version".
- **Every declared location is written.** Partial updates are the exact failure mode that makes
  `importlib.metadata.version()` disagree with the module. (V4)
- **Nothing not on the list is ever touched** — some repos have deliberately independent data
  or schema versions. (V5)
- Missing declared file, or a declared symbol not found in it → exit 1. Silent no-ops are the bug.
- `--check` writes nothing and exits nonzero if any declared location disagrees with `--version`.
  This makes it usable as a standalone CI check in consuming repos.
- `[project] version` in pyproject is handled by `uv version --bump` in the workflow, NOT by this
  script. Do not write pyproject's own version here.

## Workflow `workflow_call` inputs (.github/workflows/)

| Workflow | Inputs (all optional unless noted, with defaults) |
|---|---|
| `changelog-check.yml` | `notes-dir`=`changelog.d`, `skip-label`=`skip-changelog`, `bot-actors`=`dependabot[bot],github-actions[bot]`, `python-version`=`3.13` |
| `version.yml` | `release-branch`=`release/next`, `notes-dir`=`changelog.d`, `python-version`=`3.13`, `uv-version`=pinned explicit version (NOT `latest`), `skip-label`=`skip-changelog`, `actions-ref`=`v1`, `publish`=`false` (bool), `environment`=non-empty name |
| `build-release.yml` | `python-version`=`3.13`, `uv-version`=pinned, `publish`=`false` (bool), `environment`=non-empty name — **no `actions-ref`**: it never checks out this repo's `scripts/` |

Consumers pin `@v1` — never a branch (P1) — and pass `actions-ref` matching that pin, on
**every** stub that has it. There is no context giving a reusable workflow its own ref:
`github.workflow_ref` is the *caller's*, and `github.job_workflow_sha` does not exist. The
workflows prefer `job.workflow_sha` (the commit of the reusable workflow file itself, which
cannot drift from the pin) and fall back to `actions-ref`, logging which path was taken. An
unresolvable ref is refused outright, because an empty checkout ref silently means "the
default branch" — which is how two earlier attempts passed while ignoring the pin entirely.

`environment` must be **non-empty**: `environment:` is validated when the workflow is parsed,
before any `if:` runs, so an empty name fails every run with `startup_failure` even with
publishing off.

## version.yml behaviour

On push to `main` in the consuming repo:

1. Is HEAD the release commit (i.e. the release PR just merged)?
   - **Yes** → push tag `vX.Y.Z`. Done. (S1)
   - **No** → continue.
2. `compute_bump.py` → if `level=none`, stop.
3. `uv version --bump <level>` → writes `[project] version` **and refreshes `uv.lock`** in the
   same step, so the bump commit is self-consistent.
4. `sync_version.py --version <next>` → every other declared location.
5. `towncrier build --yes --version <next>` → writes `CHANGELOG.md` and **deletes the consumed
   note files in the same commit**. (A7)
6. `peter-evans/create-pull-request@v8` → force-updates the fixed branch `release/next` and
   opens **or updates** the single release PR. Title states the version. (R2/R4)
   - The PR body must contain only: version bumps, changelog update, note deletions,
     lockfile refresh. Nothing else. (R3)
   - Auto-apply the `skip-label` value at creation so the release PR exempts its own note
     check. (E6) **`version.yml`'s `skip-label` input must be set to the same value the repo
     passes to `changelog-check.yml`** — a repo that overrides one and not the other breaks E6
     silently. Defense in depth: the release PR is authored by `github-actions[bot]`, which
     `changelog-check.yml`'s default `bot-actors` already exempts independently.

7. **Same run**: `uv build` → GitHub Release with wheel + sdist attached → optional index
   publish over OIDC, opt-in and off by default. (S2/S3/S4)

**Why the release is not tag-triggered.** The obvious design — tag `v*` fires
`build-release.yml` — cannot work. **A tag pushed with `GITHUB_TOKEN` never triggers a
workflow**; GitHub suppresses that to prevent recursion. Shipped as drawn, the tag would land
and nothing would ever be built or published, silently. So the tag push and the release
happen in the same `version.yml` run. `build-release.yml` is kept for a
**human-pushed** tag (which does trigger workflows) and for `workflow_dispatch` rebuilds — it
is deliberately not the automatic path.

**`permissions:` on a `workflow_call` is a ceiling for every job in the called workflow**, not
a per-job grant. So a repo setting `publish: true` must **also** add `id-token: write` to its
stub's `permissions:` block, or OIDC yields an empty token and publishing fails. It is not
granted by default, because publishing is opt-in (S3) and an unused elevated permission is
worth avoiding.

## The known limitation — document it, do not work around it

A PR opened with `GITHUB_TOKEN` does **not** trigger further workflow runs, so the release PR's
own checks will not run. This is accepted, matching the precedent at `~/Sites/icon/satcom`:

1. Plain `GITHUB_TOKEN`.
2. The consuming repo's stub runs its **own CI on `main` first**, via a `needs:` edge, before
   `version.yml` drafts anything — so the code being released was verified.
3. The escape hatch is documented for humans: **close and immediately reopen the release PR**
   to force its checks to run.

Every consumer-facing doc must state #3 plainly.

## Consumer stub (this exact shape)

```yaml
# .github/workflows/version.yml
name: Version
on:
  push:
    branches: [main]
jobs:
  ci:
    uses: ./.github/workflows/ci.yml      # the repo's OWN checks (C4)
    # `secrets: inherit` belongs HERE, and only if your own ci.yml needs a repo
    # secret. A local reusable-workflow call does not inherit secrets implicitly
    # either. Real example: sdm-core's CI fetches the private sibling
    # emergent-matter-materials with MATERIALS_REPO_TOKEN, so its ci job needs it.
    # A repo whose CI needs no secrets should omit this line.
    secrets: inherit
  version:
    needs: ci
    uses: EmergentMatter/actions/.github/workflows/version.yml@v1
    with:
      # MUST match the @ref above; the two change together. Add
      # `id-token: write` to permissions below only if this repo publishes.
      actions-ref: v1
    permissions: { contents: write, pull-requests: write }
    # NO `secrets: inherit` here, deliberately. None of the three shared
    # workflows reads `secrets.*` — they use only the auto-injected
    # `github.token`. Granting org secrets to a workflow hosted in a public repo
    # and pinned by a movable `v1` tag would make that tag equivalent to secret
    # access across every consuming repo. Don't add it back without a concrete
    # secret the shared workflow actually reads.
```

## Where each guarantee lives

| Path | What it owns |
|---|---|
| `scripts/compute_bump.py` | Notes → bump level → next version (V1, V2, V3) |
| `scripts/sync_version.py` | Writing the version to every declared location, and nothing else (V4, V5) |
| `.github/workflows/version.yml` | The release-PR loop and tagging (R1–R5, S1) |
| `.github/workflows/changelog-check.yml` | The PR gate (E2, E4, E5, E6) |
| `.github/workflows/build-release.yml` | Build, GitHub Release, opt-in publish (S2, S3, S4) |
| `templates/` | What gets copied into consuming repos — changes here propagate by hand |

Changing `templates/` is the expensive one: those files are copied into each repo rather than
referenced, so a fix does not reach repos already onboarded. Prefer putting behaviour in the
reusable workflows, which every repo picks up from `@v1` automatically.
