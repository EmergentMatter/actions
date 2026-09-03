# Behavioural spec: EmergentMatter/actions v1

The contract between the reusable workflows in this repo and the repos that call them:
script CLIs, `workflow_call` inputs, and the exact sequence `version.yml` performs.

**If you are onboarding a repo, you want [`docs/onboarding.md`](docs/onboarding.md) instead.**
This file is for people changing how release control itself behaves. It records not just
what the system does but why, so a future change doesn't quietly break a guarantee.

Requirement IDs (A1, V4, C2 …) refer to §09 of the "Release Control" proposal, which is the
source of truth for intent. Changes here are changes to the contract every consuming repo
depends on: they need review from the owners in `CODEOWNERS`, and anything that alters the
inputs or the behaviour of a shared workflow is a breaking change for every repo pinned to
`@v1`.

## Non-negotiables

- **Python only.** No Node/npm backend anywhere in v1.
- **No long-lived tokens.** The bot uses `secrets.GITHUB_TOKEN`. No PATs, no GitHub App in v1.
- **The shared repo is passive.** It holds no secrets and reaches nothing; consumers run the
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
  fragment names (`123.minor.md`) must also parse: take the part after the last `.` before `.md`.
- `level` is the **maximum** across all pending notes: one major among twenty patches → major. (V1)
- Standard semver, with **no special handling below 1.0**: a major bump on `0.4.2` produces
  `1.0.0`. (V2)
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
- **Nothing not on the list is ever touched**, because some repos deliberately keep independent
  data or schema versions. (V5)
- Missing declared file, or a declared symbol not found in it → exit 1. Silent no-ops are the bug.
- `--check` writes nothing and exits nonzero if any declared location disagrees with `--version`.
  This makes it usable as a standalone CI check in consuming repos.
- `[project] version` in pyproject is handled by `uv version --bump` in the workflow, NOT by this
  script. Do not write pyproject's own version here.

### sync.py

    sync.py --repo-path ../repo [--dry-run] [--ours|--theirs] [--only DEST] [--json]

Brings an already-onboarded repo's `managed` templates forward. `onboard.py`'s copy is a
snapshot taken once; nothing else keeps it current until this runs. See "templates/manifest.toml"
below for what `managed` means and where the entries it reads come from.

Three-way compare per entry, not two-way:

- **base** = the `templates_version` recorded in the target repo's `[tool.em-release]` block
  (written by `onboard.py`, updated here on every run that changes something).
- **ours** = the file currently in the target repo.
- **theirs** = the file currently in this repo's `templates/`.

| ours vs. base | theirs vs. base | What it means | What happens |
|---|---|---|---|
| equal | equal (so ours == theirs) | Nothing has moved | Nothing to do |
| equal | different | The repo's copy hasn't moved since its last sync; the template has. That's staleness, not a choice someone made | **Updated silently** |
| different | equal | The repo's copy has moved and the template hasn't: a **deliberate local edit** | **Left alone, and reported.** Never silently overwritten |
| different | different (and ours != theirs) | Both sides moved since the last sync: a genuine **conflict** | Prompts, unless `--ours` or `--theirs` says which side wins outright |

**Why base matters**: comparing only ours vs. theirs can't tell "stale" from "deliberately
customized," because both just look like "differs from the template." A repo with no
`templates_version` stamp at all (onboarded before this existed) has no base to compare against,
so every diff there is treated as a possible edit and reported, never overwritten.

The decision and the constraint it puts on the stamp (it must name an immutable ref, never the
moving `v1`) are recorded in [ADR 0005](docs/adr/0005-sync-uses-a-three-way-compare.md).

- `--dry-run` reports what would change and writes nothing.
- `--ours` / `--theirs` resolve every conflict for this run without prompting; mutually
  exclusive.
- `--only DEST` restricts the run to the entries whose `dest` matches; repeatable, for pulling in
  one or a few files' changes without touching the rest.
- `--json` for scripted/CI use, same semantics as the interactive run.
- Never touches `seed-once` entries. Those are the repo's own from the moment `onboard.py` seeds
  them; this script has no opinion about them at all, not even to report drift.

## templates/manifest.toml

One `[[template]]` entry per file `templates/` ships, read by both `sync.py` and
`fleet_status.py`. It is not itself something a consuming repo copies:

```toml
[[template]]
source = "SECURITY.md"      # path relative to templates/
dest = "SECURITY.md"        # path relative to the target repo root
policy = "managed"          # required on every entry; no default

[[template]]
source = "ci.yml"
dest = ".github/workflows/ci.yml"
policy = "seed-once"
```

The policies mean opposite things:

- **`managed`.** The target repo's copy should always equal this repo's `templates/` copy. A
  difference is either staleness or a local edit, distinguished the way `sync.py` above
  describes. `policy` has no default: every entry in `manifest.toml` sets it explicitly, and
  `load_manifest()` raises on an unrecognised value rather than guessing.
- **`seed-once`.** Written once, at onboarding, only if the target file doesn't already exist.
  Nothing in this repo ever touches it again. It is the policy for a file a repo is entitled to
  shape for itself, a repo's own CI being the case that drove it: neither `sync.py` nor
  `fleet_status.py`'s drift check treats a difference there as a finding. `manifest.toml` is where
  each entry's policy is declared, and the only place that says which are which.

## Workflow `workflow_call` inputs (.github/workflows/)

| Workflow | Inputs (all optional unless noted, with defaults) |
|---|---|
| `version.yml` | `release-branch`=`release/next`, `notes-dir`=`changelog.d`, `python-version`=`3.13`, `uv-version`=pinned explicit version (NOT `latest`), `skip-label`=`skip-changelog`, `actions-ref`=`v1`, `publish`=`false` (bool), `environment`=non-empty name |
| `build-release.yml` | `python-version`=`3.13`, `uv-version`=pinned, `publish`=`false` (bool), `environment`=non-empty name |

`build-release.yml` takes **no `actions-ref`**, because it never checks out this repo's
`scripts/`. The changelog check is not in this table: it is a composite action, not a
`workflow_call` workflow. See "Composite action inputs" below.

`version.yml`'s `environment` must be **non-empty**, whether or not that repo publishes. The
publish job's own comment in `.github/workflows/version.yml` says why an empty name is not merely
ignored.

Consumers pin `@v1`, never a branch (P1). `version.yml` additionally passes `actions-ref`
matching that pin.

No context field gives a reusable workflow its own ref: `github.workflow_ref` resolves to the
*caller's* ref, and `github.job_workflow_sha` does not exist. The workflow prefers
`job.workflow_sha` (the commit of the reusable workflow file itself, which cannot drift from the
pin), falling back to `actions-ref` and logging which path it took.

An unresolvable ref is refused outright. An empty checkout ref silently means "the default
branch," and that is exactly how two earlier attempts passed while ignoring the pin entirely.

## Composite action inputs (changelog-check/)

| Action | Inputs (all optional, with defaults) |
|---|---|
| `changelog-check/action.yml` | `notes-dir`=`changelog.d`, `skip-label`=`skip-changelog`, `bot-actors`=`dependabot[bot],github-actions[bot]`, `python-version`=`3.13` |

Same input names and defaults as the reusable-workflow version of this check, deliberately: the
contract this table describes didn't change, only the mechanism.

**No `actions-ref` input.** A composite action isn't loaded through a `uses:` reference to a
`workflow_call` workflow. The consumer's own `uses: EmergentMatter/actions/changelog-check@v1`
step causes the runner to check out this repo at that ref directly and set `github.action_path`
to point at it, so `scripts/compute_bump.py` is reached at
`${{ github.action_path }}/../scripts/compute_bump.py` with no second checkout, no ref to
resolve, and no fallback to reason about.

That's why the whole `actions-ref` / `job.workflow_sha`-fallback apparatus above (the one where
an empty checkout ref silently means the default branch) exists only for `version.yml`. A
reusable `workflow_call` workflow genuinely has no way to discover its own ref; a composite
action does, automatically. The decision to move this check to the composite-action shape, and
what it bought, are recorded in
[ADR 0004](docs/adr/0004-changelog-check-is-a-composite-action.md).

Composite actions also cannot declare `permissions:`: there is no `runs.permissions` key. The
`contents: read` (to check out the PR head commit) and `pull-requests: read` (to list PR files via
`gh api`) this action needs come from the **calling job's** own `permissions:` block instead; see
`templates/stub-changelog-check.yml`.

## version.yml behaviour

On push to `main` in the consuming repo:

1. Is HEAD the release commit (i.e. the release PR just merged)?
   - **Yes:** push tag `vX.Y.Z`. Done. (S1)
   - **No:** continue.
2. Run `compute_bump.py`. If `level=none`, stop.
3. `uv version --bump <level>` writes `[project] version` **and refreshes `uv.lock`** in the same
   step, so the bump commit is self-consistent.
4. `sync_version.py --version <next>` writes every other declared location.
5. `towncrier build --yes --version <next>` writes `CHANGELOG.md` and **deletes the consumed note
   files in the same commit**. (A7)
6. `peter-evans/create-pull-request@v8` force-updates the fixed branch `release/next` and opens
   **or updates** the single release PR. Title states the version. (R2/R4)
   - The PR body must contain only: version bumps, changelog update, note deletions, lockfile
     refresh. Nothing else. (R3)
   - The `skip-label` value is auto-applied at creation, so the release PR exempts its own note
     check. (E6)
   - **`version.yml`'s `skip-label` input must be set to the same value passed to the changelog
     check's `skip-label` input** (`changelog-check/action.yml`'s): a repo that overrides one and
     not the other breaks E6 silently. Defense in depth: the release PR is authored by
     `github-actions[bot]`, which the changelog check's default `bot-actors` already exempts
     independently.
7. **Same run:** `uv build` produces a GitHub Release with wheel and sdist attached, plus an
   optional index publish over OIDC, opt-in and off by default. (S2/S3/S4)

### `publish: true` requires the consumer to grant `id-token: write`

**`permissions:` on a `workflow_call` is a ceiling for every job in the called workflow**, not a
per-job grant. So a repo setting `publish: true` must **also** add `id-token: write` to its
stub's `permissions:` block, or OIDC yields an empty token and publishing fails. It is not granted
by default, because publishing is opt-in (S3) and an unused elevated permission is worth avoiding.

Why the shared workflow's publish job declares no `permissions:` of its own, and why getting this
wrong takes down every run in a repo rather than only its publish step, is a comment on that job
in `.github/workflows/version.yml`.

## Why the release is not tag-triggered

**An event created with `GITHUB_TOKEN` does not trigger further workflow runs.** GitHub suppresses
that to prevent recursion. It is platform behaviour, not a permission anything can grant, and it
is the same rule behind the known limitation below.

The behaviour that follows: `version.yml` pushes the tag and builds, releases, and optionally
publishes **in the same run**. `build-release.yml` is kept for a human-pushed tag (which does
trigger workflows) and for `workflow_dispatch` rebuilds. It is deliberately not the automatic
path.

The decision, the design that was rejected, and why the resulting duplication is deliberate are
recorded in [ADR 0002](docs/adr/0002-build-and-release-run-inline-not-on-tag-push.md).

## The known limitation: document it, don't work around it

Stated here once. Everywhere else points at this section.

A pull request opened with `GITHUB_TOKEN` does **not** trigger further workflow runs, by the same
platform rule as the section above, so the release PR `version.yml` opens shows its own checks as
never run. This is accepted rather than worked around, matching the precedent set by an earlier
internal repo that hit the same platform rule:

1. The bot stays on plain `GITHUB_TOKEN`. Every workaround needs a PAT or a GitHub App, and no
   long-lived token is a non-negotiable above.
2. The code being released was already verified: the consuming repo's stub runs its **own CI on
   `main` first**, via a `needs:` edge, before `version.yml` drafts anything.
3. The escape hatch, for a human who wants those checks to run: **close the release PR and
   immediately reopen it.** Reopening it under your own account is not a `GITHUB_TOKEN` event, so
   the checks run.

`templates/CONTRIBUTING.md` states #3 for consumers, because a contributor in an onboarded repo
reads that file and does not have this one.

## Consumer stub

`templates/stub-version.yml` is the file `onboard.py` actually installs into a consuming repo,
so it is the authoritative source for what a repo's stub looks like. The block below is
illustrative, not a copy: it shows the pieces this contract makes guarantees about. Diff against
the real file for the exact text a repo should have.

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
      actions-ref: v1   # must match the @ref above (P1)
    permissions: { contents: write, pull-requests: write }
    # NO `secrets: inherit` here, deliberately. The shared workflows read
    # exactly ONE optional secret between them: version.yml's
    # `sibling-token`, for a private-sibling checkout (see below). It is
    # mapped by name when needed. Everything else uses the auto-injected
    # `github.token`. Granting org secrets to a workflow hosted in a public
    # repo and pinned by a movable `v1` tag would make that tag equivalent to
    # secret access across every consuming repo. Map the one secret you need;
    # never inherit the whole store.
```

### Private sibling checkout (optional)

For a repo whose `pyproject.toml` pins a dependency as a **local path
source** because that dependency isn't on an index yet:

```toml
[tool.uv.sources]
foo = { path = "../foo" }
```

Your own `ci.yml` can check that sibling out itself, but the shared
`version.yml` is called **by reference**, so you cannot inject steps into
it. Its bump step runs `uv version --bump` and `uv lock`, both of
which resolve `[tool.uv.sources]` and fail outright when the path is
missing (`error: Distribution not found at: file:///.../foo`). Without
this, the release PR is never opened and the run goes red on every merge
to `main` that has pending notes.

Pass the sibling in, and map the one secret explicitly:

```yaml
  version:
    needs: ci
    uses: EmergentMatter/actions/.github/workflows/version.yml@v1
    with:
      actions-ref: v1
      sibling-repo: EmergentMatter/foo
      sibling-ref: <SHA>          # required; a float breaks `uv sync --locked`
      sibling-path: foo           # must match the `../foo` above
    secrets:
      sibling-token: ${{ secrets.FOO_REPO_TOKEN }}   # NOT `secrets: inherit`
    permissions: { contents: write, pull-requests: write }
```

`sibling-ref` must be a pinned SHA, not a branch: your `uv.lock` embeds
the sibling's resolved version, and `uv sync --locked` fails the moment
that disagrees with what's checked out. A floating ref turns any
merge into the sibling into a red run in **your** repo, on a change
nobody made there. Bump the SHA and `uv.lock` in one commit.

Omit the sibling inputs and those steps skip entirely. The whole
block comes out when the dependency ships to an index.

### Second (nested) private sibling checkout (optional)

For the one hop further out: `foo`'s **own** `pyproject.toml` pins a
private path dependency too (`consumer -> foo -> bar`). `uv version
--bump` / `uv lock` resolve `foo`'s `[tool.uv.sources]` as part of
resolving the consumer, so `bar` has to land on disk as well, at whatever
path `foo`'s own source entry names -- the identical failure mode as the
single-sibling case, one link further down the chain.

`sibling2-repo` / `sibling2-ref` / `sibling2-path` / `sibling2-token` are
the same shape as the `sibling-*` inputs, purely additive, and
**require `sibling-repo` to also be set** -- a nested sibling with no
direct sibling is refused as a misconfigured stub:

```yaml
  version:
    needs: ci
    uses: EmergentMatter/actions/.github/workflows/version.yml@v1
    with:
      actions-ref: v1
      sibling-repo: EmergentMatter/foo
      sibling-ref: <SHA>
      sibling-path: foo
      sibling2-repo: EmergentMatter/bar
      sibling2-ref: <SHA>          # required; a float breaks `uv sync --locked`
      sibling2-path: bar           # must differ from sibling-path; must match
                                    # foo's OWN `[tool.uv.sources]` entry for bar
    secrets:
      sibling-token: ${{ secrets.FOO_REPO_TOKEN }}
      sibling2-token: ${{ secrets.BAR_REPO_TOKEN }}
    permissions: { contents: write, pull-requests: write }
```

Both siblings land at the **same** staging level -- as siblings of each
other and of the consumer, one directory above `$GITHUB_WORKSPACE`, not
nested inside one another on disk, because `uv` resolves each repo's
`[tool.uv.sources]` relative to **that repo's own** checkout. `sibling-path`
and `sibling2-path` must therefore differ: a collision would silently
overwrite one checkout with the other, so it's refused before either
checkout runs, not discovered afterward.

Omit `sibling2-repo` and this whole block skips, exactly like the
single-sibling case, and every existing single-sibling consumer is
unaffected. The deepest chain in the fleet today is a direct private
sibling that itself pins a private sibling, and there is no `sibling3`
input set.

**`build-release.yml` has no sibling support at all, single or nested.**
That's a pre-existing gap, not something this section's inputs cover; see
that workflow's own header comment. It only matters for that workflow's
non-automatic triggers (a human-pushed tag, or `workflow_dispatch`), since
`version.yml` is the automated release path and builds inline once it
detects the release commit.

## Where each guarantee lives

| Path | What it owns |
|---|---|
| `scripts/compute_bump.py` | Notes → bump level → next version (V1, V2, V3) |
| `scripts/sync_version.py` | Writing the version to every declared location, and nothing else (V4, V5) |
| `.github/workflows/version.yml` | The release-PR loop and tagging (R1–R5, S1) |
| `changelog-check/action.yml` | The PR gate (E2, E4, E5, E6) |
| `.github/workflows/build-release.yml` | Build, GitHub Release, opt-in publish (S2, S3, S4) |
| `templates/` | What gets copied into consuming repos at onboarding, and (for `managed` entries) pulled forward later by `scripts/sync.py` |

Changing `templates/` is still the expensive one relative to the reusable workflows: a fix here
does not reach an onboarded repo the moment it merges, the way a change to `version.yml` does
just by every repo pinning `@v1`. `sync.py` closes most of that gap for `managed` entries, but
someone still has to run it (or `fleet_status.py`'s `templates`/`stamp` checks have to flag that
they should); it is not automatic. A `seed-once` entry never propagates after onboarding,
deliberately. Prefer putting behaviour in the reusable workflows when the choice
exists; reach for `templates/` when the repo genuinely needs its own copy.
