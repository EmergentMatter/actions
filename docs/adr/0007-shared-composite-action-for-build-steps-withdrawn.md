# 7. A shared composite action for the build steps was withdrawn

## Status

Rejected, with remaining work. Recorded so the withdrawal is not mistaken for
proof that a local composite action cannot work.

## Context

The build and release steps are duplicated between `version.yml` and
`build-release.yml`, which exist to cover disjoint triggers; see
[ADR 0002](0002-build-and-release-run-inline-not-on-tag-push.md). Duplicated
steps have to be kept in step by hand, so factoring them into a local composite
action at `./` was tried.

It was removed again while diagnosing a `startup_failure` that affected every
onboarded repo.

## Decision

Keep the two copies. Do not reintroduce a shared composite action for these
steps without first testing the specific question below in isolation.

## Consequences

The obvious inference from the withdrawal is wrong. The composite action was
removed **while** diagnosing the `startup_failure`, and the real cause of that failure turned out to be the
publish job's `permissions:` block, documented in
[ADR 0003](0003-the-publish-job-declares-no-permissions.md). The composite
action was never isolated as the problem, and was very likely never the problem
at all.

So "a local `./` action cannot work in a reusable workflow" must not be
restated anywhere as established fact. It was never established, and a repeated
guess cannot be told apart from a finding by the next reader.

What is genuinely awkward, and separate from that failure, is that `uses:`
cannot take a templated ref. A composite action could not follow the same
`@ref` its calling workflow is pinned at without hardcoding one, which
reintroduces the class of drift that
[ADR 0004](0004-changelog-check-is-a-composite-action.md) removed for the
changelog check.

Remaining work, should anyone want to revisit this: test in isolation how a
`./`-relative path resolves for a reusable workflow called cross-repo. That
single question was never answered, and it is the only thing standing between
this record and a different decision. Until it is answered, the duplication
stays, and the two copies are kept in step whenever either changes.
