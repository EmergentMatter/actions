# Releasing this repo

This is about how **this repo** (`EmergentMatter/actions`) cuts its own
tags — not about the release-control system it ships to other repos. For
that, see [`README.md`](../README.md) and
[`docs/onboarding.md`](../docs/onboarding.md).

## Merging to `main` is not releasing

This is the same rule this whole repo tells every consumer: a PR merging
to `main` never releases anything by itself. That's true here too, for
the same reason, at higher stakes. Work lands on `main` through a
reviewed PR and changes nothing for anyone -- the pin exists precisely
so `main` can be a normal working branch. **Moving `v1` is a separate,
deliberate act**, because the instant it moves, every repo pinned to it
runs the new code on its next run -- around thirty repos, none of them
doing anything to trigger it. That makes moving `v1` the single
highest-blast-radius action anyone can take against this system, and
it's why the rest of this document treats it as its own procedure with
its own owners, not a side effect of merging.

## Promoting a `v1.x.y` release

Restricted to the code owners named by the `v1*` tag ruleset --
@telafifi, @evanmj, @ecarrig:

```bash
git checkout main && git pull

# 1. Immutable version tag, annotated, message says what changed FOR CONSUMERS
git tag -a v1.1.0 -m "..."
git push origin v1.1.0

# 2. Move the pin consumers actually follow
git tag -f v1
git push -f origin v1
```

Optionally create a GitHub Release on `v1.1.0` so consumers have a
changelog to read. Both tag pushes above are enforced, not just
convention -- see "Branch and tag protection, as configured today"
below.

## Versioning rules for this repo

- **`v1.x.y`** -- anything backwards-compatible for a consumer: bug
  fixes, new optional inputs, internal changes.
- **A new `v2` tag** -- anything that breaks an existing stub: a renamed
  or removed input, a changed default, a newly required permission.
  Consumers migrate by editing their pin, deliberately, instead of
  finding their pipeline broken one morning. Do **not** push a breaking
  change out under `v1`.

The sharpest concrete example of a breaking change, because it's the one
this repo already lived through: **adding a `permissions:` requirement
to a reusable workflow is breaking**, not additive. A caller that hasn't
granted the new permission doesn't get a degraded run -- it gets
`startup_failure` on every single run, because a `workflow_call`
permissions ceiling is validated when the workflow is parsed, before any
`if:` runs. See `docs/onboarding.md`'s "Publishing to a package index"
section for the incident that taught us this.

## Why this isn't automated

Stated plainly so it isn't mistaken for an oversight: a workflow could,
in principle, move `v1` automatically the moment a `v1.x.y` tag is
pushed. It doesn't, because `GITHUB_TOKEN` runs as `github-actions[bot]`,
which is not a repository admin, and the `v1*` tag ruleset blocks
updating those refs for anyone else. Granting the bot an Integration
bypass would let *any* workflow in this repo move `v1` -- which gives
away the exact protection the ruleset exists to provide, for the
convenience of skipping one manual `git push -f`. Revisit only if this
repo gets quiet enough that hand-verifying the request is a bottleneck,
and don't weaken the tag rules to get there.

Worth noting separately: this repo can't dogfood its own release-control
system to release itself -- doing so would need a working `v1` already,
to release the commit that becomes `v1`.

## Branch and tag protection, as configured today

Documented here so it can be re-created or audited without reverse
engineering it from GitHub's settings UI:

- **`main` branch protection**: required status checks `lint` and
  `test`; at least one approving review; code-owner review required; no
  force pushes; no deletions.
- **Code owners can merge without a separate approval**
  (`enforce_admins: false`). Worth stating the tradeoff honestly rather
  than glossing over it: classic branch protection can't separate "skip
  the review" from "skip a failing check" into two different bypasses --
  a code owner merging without waiting for another reviewer can, by the
  same mechanism, also merge past a failing check. It's an escape hatch
  for exactly the three code owners, and using it to skip a real failure
  should be rare and deliberate, not routine.
- **`v1*` tag ruleset** ("Protect `v1` release tags", covering
  `refs/tags/v1` and `refs/tags/v1.*`): blocks deletion, non-fast-forward
  pushes, and update -- i.e. moving or removing either kind of tag at
  all -- with bypass limited to the repository-admin role. This is the
  mechanism "Why this isn't automated" above depends on: `github-actions[bot]`
  isn't in that bypass list, so no workflow running as `GITHUB_TOKEN` can
  move `v1`, automated or not.
- **`.github/CODEOWNERS`** covers `.github/workflows/`, `scripts/`,
  `templates/`, and `CONTRACT.md` -- the parts that execute in other
  repos or define the contract they depend on. Everything else in this
  repo (docs included) is owned too, by the repo-wide `*` entry, but
  those four paths are called out explicitly because a change there is a
  change to every consuming repo's CI.

## Tag strategy

- **Consumers pin `@v1`, never a branch.** (P1, CONTRACT.md) Every
  `uses: EmergentMatter/actions/.github/workflows/<name>.yml@v1` in the org
  resolves through this one tag.
- **`v1` is a moving major tag.** Every `v1.x.y` release re-points `v1` to
  that commit. This is the standard GitHub Actions major-tag convention —
  it's what lets consumers get compatible fixes without editing every
  stub in the org.
- **`v1.0.0`, `v1.1.0`, etc. are immutable point tags.** They are cut once
  and never re-pointed. `v1` always points at the latest one.
- **The `v1` tag is protected** by a repository ruleset ("Protect v1 release
  tags") covering `refs/tags/v1` and `refs/tags/v1.*`, blocking deletion,
  non-fast-forward and update, with bypass limited to repository admins — in
  practice the owners in [`CODEOWNERS`](CODEOWNERS). Consumer stubs deliberately
  carry **no** `secrets: inherit` on the jobs that call this repo's
  workflows (CONTRACT.md's Consumer stub block — `secrets: inherit`, where
  it appears at all, sits on the consumer's own local `ci:` job, never on
  the call into `EmergentMatter/actions/...@v1`), so a compromised or
  force-pushed `v1` cannot read a consuming repo's named secrets. It is
  still a real blast radius, just a smaller one: whatever `v1` resolves to
  runs with the `contents: write` / `pull-requests: write` /
  (when a repo has opted into `publish: true`) `id-token: write`
  permissions that repo's own stub grants — enough to push arbitrary tags,
  open or merge PRs, and, for any repo that publishes, mint an OIDC token
  scoped to that repo's trusted-publisher config. `id-token: write` is
  granted directly by the consumer's stub and doesn't route through
  `secrets: inherit` at all, so it isn't mitigated by the point above —
  it's the reason tag protection stays a hard prerequisite (see the risk
  list already raised with the user) rather than something the
  `secrets: inherit` removal made optional.
  Point releases (`v1.x.y`) should be protected too, for the same reason
  applied to anyone who pinned one directly during testing — but `v1` is
  the one that matters in the common case, since it's what every stub
  actually references.

## Merging to `main` is not releasing

Work lands on `main` through a reviewed pull request and **changes nothing for
any consuming repo.** Moving `v1` is a separate, deliberate act.

That separation is the entire point of the pin. The moment `v1` moves, every
repo pinned to it runs the new code on its next run — around thirty of them,
without any of them doing anything or being asked. It is the highest
blast-radius action in this system, and it gets treated like a release rather
than a side effect of merging. It is the same principle the product itself is
built on, turned on the repo that implements it: merging a pull request never
releases anything.

So `main` can be a normal working branch. Land things there freely.

## Promoting a change to consumers

Restricted to the owners in [`CODEOWNERS`](CODEOWNERS) by the tag ruleset.

```bash
git checkout main && git pull

# 1. The immutable point tag. Annotated, and the message says what changed
#    FOR CONSUMERS -- not what changed in the diff.
git tag -a v1.1.0 -m "..."
git push origin v1.1.0

# 2. Move the pin that consumers actually follow.
git tag -f v1
git push -f origin v1
```

Optionally cut a GitHub Release on the point tag, so consumers have something
to read.

Both steps, or neither. A point tag without moving `v1` reaches nobody; moving
`v1` without a point tag leaves no immutable record of what consumers are now
running.

## `v1.x.y` versus a new `v2`

- **`v1.x.y`** — anything backwards-compatible for a consumer: bug fixes, new
  *optional* inputs, internal changes to how a workflow does its job.
- **A new `v2` tag** — anything that breaks an existing stub: renaming or
  removing an input, changing a default, or requiring a permission the stub
  does not already grant. Consumers migrate by editing their pin, deliberately,
  instead of finding their pipeline broken one morning.

Do not push a breaking change out under `v1`.

The sharpest example, because it cost us a long debugging session: **adding a
`permissions:` requirement to a reusable workflow is a breaking change.** A
caller's `permissions:` is a ceiling for every job in the called workflow, so a
job asking for more than the caller granted does not degrade — GitHub rejects
the entire workflow when it parses it, and every run in that repo fails with
`startup_failure` before a single job starts, whether or not the job would have
run. Same for adding a new required input with no default.

## Why the promotion is not automated

Deliberate, not an oversight. A workflow could move `v1` whenever a `v1.x.y`
tag is pushed, but `GITHUB_TOKEN` runs as `github-actions[bot]`, which is not a
repository admin, so the tag ruleset blocks it. Granting an Integration bypass
would let *any* workflow in this repo move `v1`, which gives away exactly the
protection the ruleset exists to provide.

Worth revisiting when this repo is quieter. Do not weaken the tag rules to get
there.

Dogfooding release control on this repo is also circular: it would need a
working `v1` to release itself with.

## Branch protection, as configured

Recorded so it can be audited or re-created:

| Setting | Value |
|---|---|
| Required status checks | `lint`, `test` |
| Approving reviews | 1, with code-owner review required |
| Force pushes / deletions | Blocked |
| `enforce_admins` | **false** — owners may merge without a separate approval |

The last row is a deliberate tradeoff and worth stating plainly: classic branch
protection cannot separate "owners skip the review requirement" from "owners
can merge past a failing check." Turning off admin enforcement grants both. It
is an escape hatch; using it to bypass a red check should be rare, deliberate,
and explained in the pull request.

## Build + release run inline in `version.yml`, not on tag push

`version.yml` builds the wheel/sdist and creates the GitHub Release itself,
in the same job run that pushes the release tag — not in a separately
tag-triggered workflow. Reason: that tag is pushed using `GITHUB_TOKEN`
credentials, and GitHub does not trigger further workflow runs from events
created by `GITHUB_TOKEN` (found by the `em-release-control-test`
end-to-end rehearsal). A `build-release.yml` stub listening for
`push: tags: ["v*"]` alone would simply never fire on the automated path.

`build-release.yml` still exists, but only for the two cases that aren't
that path: a human pushing a `v*` tag by hand (which does trigger
workflows), and `workflow_dispatch` for manually rebuilding or
republishing a past release. Don't be confused by its apparent redundancy
with `version.yml` — they cover disjoint triggers.

## The known limitation, briefly

Release PRs opened by `version.yml` use `secrets.GITHUB_TOKEN`, which does
not trigger further workflow runs — so a release PR's own status checks
can show as not run. This is intentional (see CONTRACT.md's "known
limitation" section and `CLAUDE.md`), not a bug: the code being released
was already gated by the consuming repo's own CI before the release PR was
even drafted. The human escape hatch — close the release PR and
immediately reopen it to force its checks to run — is documented in full
in `templates/CONTRIBUTING.md`, which every consuming repo carries; this
file doesn't repeat it beyond this pointer.
