# 2. Build and release run inline, not on tag push

## Status

Accepted.

## Context

The obvious design for a release pipeline is to separate concerns by trigger:
one workflow computes the version and pushes the tag, and a second workflow
fires on `push: tags: v*` to build the artifacts and cut the GitHub Release.
That is how most repositories do it, and it reads better than doing everything
in one job.

It does not work here, for a reason that is platform behaviour rather than
anything a permission can grant.

## Decision

`version.yml` pushes the release tag and then builds, releases, and optionally
publishes in the same run. `build-release.yml` is kept, but only for the paths
that are not the automated one: a human pushing a `v*` tag by hand, and
`workflow_dispatch` for rebuilding or republishing a past release.

## Consequences

An event created with `GITHUB_TOKEN` does not trigger further workflow runs.
GitHub suppresses that to prevent recursion. Applied to tags, the separated
design would push the tag and then build nothing, silently, on every release.
The failure would be invisible: the tag would exist, and no artifact would.

`build-release.yml` therefore looks redundant and is not. The two workflows
cover disjoint triggers, and folding either away reintroduces the silent
failure for the path it covered. Anyone reading them side by side will see
near-identical build steps and reasonably conclude one is dead code.

Because the steps are duplicated rather than shared, the two copies have to be
kept in step by hand whenever either changes. An attempt to remove that
duplication was made and withdrawn; see
[ADR 0007](0007-shared-composite-action-for-build-steps-withdrawn.md) for what
happened and what would have to be true to revisit it.

The same platform rule produces the known limitation that a release pull
request's own checks show as never run, which is documented in `CONTRACT.md`
rather than here, because it is a behaviour consumers observe rather than a
decision this repo made.
