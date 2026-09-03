# 1. Consumers pin a moving major tag

## Status

Accepted.

## Context

Every onboarded repo references this repo's reusable workflows by ref. That
ref decides what code runs inside another repo's CI, so the choice of what
consumers pin to is the single highest-leverage decision in the system.

Three shapes were available:

- **A branch**, `@main`. Every merge would reach every consumer immediately.
  Merging a pull request would become a release, with no step in between at
  which anyone decides that consumers should get it.
- **An immutable point release**, `@v1.4.2`. Nothing reaches a consumer until
  someone edits every stub in the org. A one-line fix would need a pull
  request in each onboarded repo.
- **A moving major tag**, `@v1`, re-pointed at each point release.

## Decision

Consumers pin `@v1`. Point tags (`v1.0.0`, `v1.1.0`, and so on) are cut once
and never re-pointed; `v1` is force-moved to the newest of them as a separate,
deliberate act after the merge.

Merging to `main` changes nothing for any consumer. Moving `v1` is what
releases.

## Consequences

Compatible fixes reach the whole fleet without editing a single stub, which is
the property the pin exists to buy.

The cost is that `v1` becomes the highest-blast-radius artifact in the system:
the moment it moves, every repo pinned to it runs the new code on its next
run, without any of them being asked. That is why a repository ruleset
protects `refs/tags/v1` and `refs/tags/v1.*` against deletion, non-fast-forward
and update, with bypass limited to repository admins.

Promotion is not automated, and that is deliberate rather than an oversight. A
workflow could move `v1` on every point tag, but `GITHUB_TOKEN` runs as
`github-actions[bot]`, which is not a repository admin, so the ruleset blocks
it. Granting an Integration bypass would let any workflow in this repo move
`v1`, giving away the exact protection the ruleset provides. Dogfooding release
control here is also circular: it would need a working `v1` to release itself
with.

Anything that breaks an existing stub needs a new major rather than a `v1.x.y`
release. Renaming or removing an input, changing a default, adding a required
input with no default, or requiring a permission the stub does not already
grant all qualify. See [ADR 0003](0003-the-publish-job-declares-no-permissions.md)
for why the permissions case is not merely inconvenient but fatal.

Migrating to a future major means editing two things together in the same pull
request: the `@ref` at the end of every `uses:` line, and the matching
`actions-ref:` input on the stubs that take one. A `v1.x.y` promotion never
touches a consumer's stub at all.
