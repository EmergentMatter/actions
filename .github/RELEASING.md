# Releasing this repo

This is about how **this repo** (`EmergentMatter/actions`) cuts its own
tags, not about the release-control system it ships to other repos. For
that, see [`README.md`](../README.md) and
[`docs/onboarding.md`](../docs/onboarding.md).

## Merging to `main` is not releasing

Work lands on `main` through a reviewed pull request and **changes nothing for
any consuming repo.** Moving `v1` is a separate, deliberate act.

That separation is the entire point of the pin. The moment `v1` moves, every
repo pinned to it runs the new code on its next run, without any of them doing
anything or being asked. It is the highest blast-radius action in this system,
and it gets treated like a release rather than a side effect of merging. It
is the same principle the product itself is built on, turned on the repo
that implements it: merging a pull request never releases anything.

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

- **`v1.x.y`** -- anything backwards-compatible for a consumer: bug fixes, new
  *optional* inputs, internal changes to how a workflow does its job.
- **A new `v2` tag** -- anything that breaks an existing stub: renaming or
  removing an input, changing a default, or requiring a permission the stub
  does not already grant. Consumers migrate by editing their pin, deliberately,
  instead of finding their pipeline broken one morning.

Do not push a breaking change out under `v1`.

**Adding a `permissions:` requirement to a reusable workflow is a breaking
change**, because a caller's `permissions:` is a ceiling for every job in the
called workflow and a job asking for more than the caller granted does not
degrade: it takes down every run in that repo. Adding a new required input with
no default is breaking for the same reason. The mechanism, and the incident that
established it, are in
[ADR 0003](../docs/adr/0003-the-publish-job-declares-no-permissions.md).

## Why the promotion is not automated

Deliberate, not an oversight, and recorded in
[ADR 0001](../docs/adr/0001-consumers-pin-a-moving-major-tag.md) along with the
alternatives that were rejected.

Worth revisiting when this repo is quieter. Do not weaken the tag rules to get
there.

## Tag strategy

- **Consumers pin `@v1`, never a branch.** (P1, CONTRACT.md) Every
  `uses: EmergentMatter/actions/.github/workflows/<name>.yml@v1` in the org
  resolves through this one tag.
- **`v1` is a moving major tag.** Every `v1.x.y` release re-points `v1` to
  that commit. This is the standard GitHub Actions major-tag convention.
  It's what lets consumers get compatible fixes without editing every
  stub in the org.
- **`v1.0.0`, `v1.1.0`, etc. are immutable point tags.** They are cut once
  and never re-pointed. `v1` always points at the latest one.
- **The `v1` tag is protected** by a repository ruleset ("Protect v1 release
  tags") covering `refs/tags/v1` and `refs/tags/v1.*`, blocking deletion,
  non-fast-forward and update, with bypass limited to repository admins, in
  practice the owners in [`CODEOWNERS`](CODEOWNERS).
  Point releases (`v1.x.y`) are protected too, for the same reason applied to
  anyone who pinned one directly during testing, but `v1` is the one that
  matters in the common case, since it's what every stub actually references.

Why the pin has this shape, and what the protection is guarding against, are in
[ADR 0001](../docs/adr/0001-consumers-pin-a-moving-major-tag.md). Consumer stubs
deliberately carry no `secrets: inherit` on the jobs that call this repo's
workflows; what that does and does not close, and why tag protection stays a
hard prerequisite rather than an optional extra, are in
[ADR 0006](../docs/adr/0006-no-stub-grants-secrets-to-a-shared-workflow.md).

## Branch protection, as configured

Recorded so it can be audited or re-created:

| Setting | Value |
|---|---|
| Required status checks | `lint`, `test` |
| Approving reviews | 1, with code-owner review required |
| Force pushes / deletions | Blocked |
| `enforce_admins` | **false** (owners may merge without a separate approval) |

The last row is a deliberate tradeoff and worth stating plainly: classic branch
protection cannot separate "owners skip the review requirement" from "owners
can merge past a failing check." Turning off admin enforcement grants both. It
is an escape hatch; using it to bypass a red check should be rare, deliberate,
and explained in the pull request.

[`.github/CODEOWNERS`](CODEOWNERS) is what makes "code-owner review" and the
tag ruleset's admin bypass mean the same people everywhere. It sets owners for
the repo as a whole, then names the highest-blast-radius paths again
explicitly: the ones that execute inside other repos' CI, or define the
contract those repos depend on. The repo-wide entry already covers them, so
that repetition buys nothing mechanically. It exists so a reviewer skimming the
file sees those paths named rather than implied.

## Build + release run inline in `version.yml`, not on tag push

`version.yml` builds the wheel and sdist and creates the GitHub Release in
the same run that pushes the release tag. `build-release.yml` covers the
cases that are not that path: a human pushing a `v*` tag by hand, and
`workflow_dispatch` for rebuilding or republishing a past release. The two
look redundant and are not; they cover disjoint triggers. Why the automated
path cannot be tag-triggered is in
[ADR 0002](../docs/adr/0002-build-and-release-run-inline-not-on-tag-push.md).

## The known limitation, briefly

A release PR's own status checks can show as never run. That is expected, and
CONTRACT.md's section on the known limitation is the account of it: why it is
accepted, and the close-and-reopen escape hatch for a human who wants those
checks to run.

## What a consumer actually sees

None of the above is visible from a consuming repo -- this is where it
surfaces. A consumer pins `EmergentMatter/actions/.github/workflows/<name>.yml@v1`
and, on the stubs that take it, passes a matching `actions-ref: v1` (there's
no context field that lets a reusable workflow discover its own ref, so this
is passed explicitly -- see `docs/onboarding.md`). **Both change together, and
only when migrating to a new major** (`v2`, once one exists): edit the `@ref`
at the end of every `uses:` line in every stub, and the matching
`actions-ref:` input alongside it, in the same PR. A `v1.x.y` promotion never
touches a consumer's stub at all -- that's the entire point of pinning the
moving major tag instead of a point release.
