# Releasing this repo

This is about how **this repo** (`EmergentMatter/actions`) cuts its own
tags, not about the release-control system it ships to other repos. For
that, see [`README.md`](../README.md) and
[`docs/onboarding.md`](../docs/onboarding.md).

## How a release happens

This repo releases itself with the same workflow it ships. Every change that
matters to a consumer carries a note in `changelog.d/`, named
`+<hex>.<level>.md` with `<level>` one of `minor` or `patch`, the same as any
onboarded repo. On each push to
`main`, `.github/workflows/release.yml` computes the next `v1.x.y` from the
pending notes and opens or updates a release pull request that bumps the
version and builds `CHANGELOG.md`.

Merging that pull request creates a lightweight point tag `v1.x.y` and a GitHub
Release, in the same run. Notes live in `CHANGELOG.md` and the Release, not in
the tag.

**That still changes nothing for any consumer.** Moving `v1` is a separate,
deliberate act, done by hand by an owner. The moment `v1` moves, every repo
pinned to it runs the new code on its next run, without any of them doing
anything or being asked. It is the highest blast-radius action in this system,
and it gets treated like a release rather than a side effect of merging.

Between the merge and the move there is a window in which the point tag exists
and `v1` does not point at it. The tag reaches nobody in that window. Close it
deliberately, using the checklist below.

Merging a change to `main` that has no note releases nothing. A release pull
request that is open and unmerged releases nothing.

## Promoting a release to consumers

Restricted to the owners in [`CODEOWNERS`](CODEOWNERS) by the tag ruleset. Do
this after the release pull request has merged and its run is green.

1. Confirm the run created the tag and the GitHub Release for `v1.X.Y`.
2. Check the release against what consumers see: no removed or renamed input, no
   changed default, no new required input, no new permission. If any of those
   apply it is a breaking change, see below; do not move `v1`.
3. Move the pin, naming the old value so the push fails if `v1` moved under you:

```bash
git fetch --tags --force origin
old=$(git rev-parse refs/tags/v1)
git tag -f v1 v1.X.Y
git push --force-with-lease=refs/tags/v1:"$old" origin v1
```

4. Confirm `git ls-remote origin refs/tags/v1 refs/tags/v1.X.Y` shows the same
   commit for both.

A point tag without moving `v1` reaches nobody; moving `v1` to anything that is
not an immutable point tag leaves no record of what consumers are now running.

## Release pull request checks that never ran

A release pull request is opened by `github-actions[bot]` with `GITHUB_TOKEN`,
and events created that way do not trigger other workflows, so its required
checks can sit as never run. CONTRACT.md's section on the known limitation is
the account of it. Two ways through:

- Close and reopen the pull request as a human. The `reopened` event is then
  attributed to a person and the checks run.
- An owner merges it with admin rights, which branch protection allows
  (`enforce_admins` is off). Do this only once the same checks are green on
  the commit the release branch was cut from, and say so on the pull request.

## When the self-release workflow is broken

`release.yml` calls `version.yml` from this same repo, so a defect in
`version.yml` on `main` can stop the release of its own fix. The manual
procedure is the fallback and stays supported. It is also the only way to cut a
new major.

```bash
git checkout main && git pull

# 1. The immutable point tag. Annotated, and the message says what changed
#    FOR CONSUMERS, not what changed in the diff.
git tag -a v1.X.Y -m "..."
git push origin v1.X.Y

# 2. Move the pin that consumers actually follow.
old=$(git rev-parse refs/tags/v1)
git tag -f v1 v1.X.Y
git push --force-with-lease=refs/tags/v1:"$old" origin v1
```

A manual release must also leave `pyproject.toml`'s version and `CHANGELOG.md`
agreeing with the tag, in a pull request, so the next automated release starts
from the right number.

## `v1.x.y` versus a new `v2`

- **`v1.x.y`** -- anything backwards-compatible for a consumer: bug fixes, new
  *optional* inputs, internal changes to how a workflow does its job.
- **A new `v2` tag** -- anything that breaks an existing stub: renaming or
  removing an input, changing a default, or requiring a permission the stub
  does not already grant. Consumers migrate by editing their pin, deliberately,
  instead of finding their pipeline broken one morning.

Do not push a breaking change out under `v1`. The automated release enforces
this for the major number: it fails when the computed version is not `1.x.y`.
A note of type `major` therefore stops the release rather than producing `v2`,
and a new major always goes through the manual procedure above.

**Adding a `permissions:` requirement to a reusable workflow is a breaking
change**, because a caller's `permissions:` is a ceiling for every job in the
called workflow and a job asking for more than the caller granted does not
degrade: it takes down every run in that repo. Adding a new required input with
no default is breaking for the same reason. The mechanism is in
[ADR 0003](../docs/adr/0003-the-publish-job-declares-no-permissions.md).

## Why moving `v1` is not automated

Deliberate, and recorded in
[ADR 0008](../docs/adr/0008-this-repo-releases-v1-point-tags-from-its-own-notes.md)
and [ADR 0001](../docs/adr/0001-consumers-pin-a-moving-major-tag.md), along with
the alternatives that were rejected. The workflow runs as `github-actions[bot]`,
which the tag ruleset blocks, and a bypass for it would let any workflow in this
repo move `v1`. Do not weaken the tag rules to get there.

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
