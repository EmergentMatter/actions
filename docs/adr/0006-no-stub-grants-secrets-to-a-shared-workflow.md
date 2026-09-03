# 6. No stub grants secrets to a shared workflow

## Status

Accepted.

## Context

A consumer's stub calls workflows that live in another repository, pinned to a
tag that moves. `secrets: inherit` on such a call is a single line, it is what
most examples show, and it would hand every named secret in the consuming repo
to whatever `v1` currently resolves to.

That would make the `v1` tag equivalent to secret access in every repo pinned
to it.

## Decision

No stub grants secrets to a shared workflow. The reusable workflows read no
`secrets.*` at all; they use only the auto-injected `github.token`, and
publishing goes over OIDC trusted publishing rather than a token.

`secrets: inherit` appears in exactly one place: on a consumer's **own** local
`ci:` job, conditionally, when that repo's own CI needs a named secret. It is
never placed on the job that calls into this repo.

`stub-changelog-check.yml` omits it because it triggers on `pull_request`,
which can be a fork pull request in a public repo, and there is nothing to
grant. `stub-build-release.yml` omits it for the OIDC reason above.

## Consequences

A compromised or force-pushed `v1` cannot read a consuming repo's named
secrets. That is the property this buys, and it is worth the loss of
convenience.

It is not a complete mitigation, and stating the residue plainly matters more
than the reassurance. Whatever `v1` resolves to still runs with the
`contents: write`, `pull-requests: write`, and, where a repo has opted into
publishing, `id-token: write` permissions that the repo's own stub grants.
That is enough to push arbitrary tags, open or merge pull requests, and mint an
OIDC token scoped to that repo's trusted-publisher configuration.
`id-token: write` is granted directly by the stub and does not route through
`secrets: inherit` at all, so removing `secrets: inherit` does not touch it.

Tag protection is therefore a hard prerequisite rather than something this
decision made optional. See
[ADR 0001](0001-consumers-pin-a-moving-major-tag.md).

The concrete case for the one permitted use: a consumer whose CI checks out a
private sibling repository needs a token for that checkout, on its own `ci:`
job. That is the consumer's own secret used in the consumer's own job, and it
never crosses into a shared workflow.
