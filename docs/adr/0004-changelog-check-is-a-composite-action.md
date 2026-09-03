# 4. changelog-check is a composite action, not a reusable workflow

## Status

Accepted. Supersedes the `workflow_call` shape this check originally shipped
in, which was removed in v1.1.0.

## Context

The changelog check needs to run `scripts/compute_bump.py` from this repo
inside a consumer's pull-request CI. As a `workflow_call` workflow it had to
check this repo out to reach `scripts/`, which meant knowing its own ref, which
a reusable workflow cannot discover.

There is no context field that solves this. `github.workflow_ref` resolves to
the caller's ref, and `github.job_workflow_sha` does not exist; both were
confirmed live. The workaround was an explicit `actions-ref` input that every
consumer had to keep in agreement with the `@ref` it was pinned at, plus a
fallback for when it was absent, where an empty checkout ref silently means the
default branch.

## Decision

Ship the check as a composite action at `changelog-check/`.

## Consequences

A consumer's `uses: EmergentMatter/actions/changelog-check@v1` step causes the
runner to check this repo out at that ref directly and set
`github.action_path` to point at it. `scripts/compute_bump.py` is reached
relative to that path, with no second checkout, no ref to resolve, and no
fallback to reason about. The `actions-ref` input disappears entirely for this
check, along with the class of bug where it disagrees with the pin.

The required-check name a consumer's branch protection has to match becomes the
short `EmergentMatter/actions/changelog-check` rather than the longer
reusable-workflow path.

Composite actions cannot declare `permissions:`; there is no `runs.permissions`
key. The `contents: read` and `pull-requests: read` this action needs come from
the calling job's own `permissions:` block instead. That is a difference in
where the grant is written, not in what is granted, and the stub carries it.

The input names and defaults were kept identical to the reusable-workflow
version on purpose. The contract did not change, only the mechanism.

This is why `version.yml` still carries the `actions-ref` apparatus and this
check does not. The asymmetry is not an inconsistency: a reusable workflow
genuinely cannot discover its own ref, and a composite action does so
automatically.
