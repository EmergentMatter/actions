# Build contract — EmergentMatter/actions v1

**Owned by Izuku. Do not edit.** All workstreams code against this file so they can run in
parallel. If you believe something here is wrong, message Izuku — do not change it unilaterally.

Source of truth for intent: the "Release Control" proposal. Requirement IDs (A1, V4, C2 …)
below refer to its §09.

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
| `version.yml` | `release-branch`=`release/next`, `notes-dir`=`changelog.d`, `python-version`=`3.13`, `uv-version`=pinned explicit version (NOT `latest`), `skip-label`=`skip-changelog` |
| `build-release.yml` | `python-version`=`3.13`, `uv-version`=pinned, `publish`=`false` (bool), `environment`=`''`, `retention-days`=`90` |

All three accept `secrets: inherit`. Consumers pin `@v1` — never a branch. (P1)

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

Tag `v*` then triggers `build-release.yml`: `uv build` → GitHub Release with wheel + sdist
attached → optional index publish via OIDC, opt-in and off by default. (S2/S3/S4)

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
    permissions: { contents: write, pull-requests: write }
    # NO `secrets: inherit` here, deliberately. None of the three shared
    # workflows reads `secrets.*` — they use only the auto-injected
    # `github.token`. Granting org secrets to a workflow hosted in a public repo
    # and pinned by a movable `v1` tag would make that tag equivalent to secret
    # access across every consuming repo. Don't add it back without a concrete
    # secret the shared workflow actually reads.
```

## File ownership — do not edit outside your lane

| Path | Owner |
|---|---|
| `scripts/`, `tests/`, `templates/changeset.py` | W1 backend-engineer |
| `.github/`, `templates/ci.yml`, `templates/stub-*.yml` | W2 devops-infrastructure-engineer |
| `README.md`, `docs/`, `templates/*.md`, `templates/pyproject-snippet.toml`, `CLAUDE.md` | W3 technical-writer |
| `CONTRACT.md`, `LICENSE`, `.gitignore` | Izuku |
