# 3. The publish job declares no permissions of its own

## Status

Accepted.

## Context

`version.yml`'s publish job needs `id-token: write` to mint an OIDC token for
trusted publishing to a package index. The natural way to express that is a
`permissions:` block on the job that needs it, scoped as narrowly as possible.
That is the standard advice, and following it here took down every onboarded
repo.

## Decision

The publish job declares no `permissions:` block at all. The permission comes
from the consumer's own stub, which adds `id-token: write` to its
`version.yml` stub and creates a GitHub Environment with a trusted publisher
attached.

`environment:` is set unconditionally on the job rather than gated on
`inputs.publish`, and its input ships a non-empty default.

## Consequences

A caller's `permissions:` on a `workflow_call` is a ceiling for every job in
the called workflow, not a per-job grant. GitHub checks a called job's declared
permissions against that ceiling when it **parses** the workflow, before any
`if:` is evaluated. A job asking for more than the caller granted does not
degrade and does not fail when it runs: the whole workflow fails to load with
`startup_failure`, no job runs at all, and the API returns no message saying
why.

The blast radius is every onboarded repo, on every push to `main`, whether or
not it publishes anything. A repo with `publish: false` has the job skipped by
its `if:`, but the parse happens first, so it breaks too. This was observed
live: every run of every consumer failed until the `permissions:` block came
out.

`environment:` is validated at that same parse step and broke the same way for
the same reason. Its input default was once the empty string, which is not a
valid environment name, so every run of every consumer died at
`startup_failure` regardless of the publish gate.

The practical rule that follows: never add a `permissions:` or `environment:`
value to a job in `version.yml` without understanding this. Both are parse-time
validated, so getting one wrong does not degrade gracefully in any way that
testing on one repo would reveal.

Adding a permission requirement to a reusable workflow is therefore a breaking
change requiring a new major, not a `v1.x.y` release. See
[ADR 0001](0001-consumers-pin-a-moving-major-tag.md).

A repo that does not publish grants nothing extra, and this job never runs.
