# 3. The publish and version jobs declare no permissions of their own

## Status

Accepted. Extended to also cover the `version` job (see below) when it grew
its own opt-in OIDC use: the read-only CodeArtifact sign-in that lets `uv
lock` relock against a CodeArtifact-hosted index during a bump.

## Context

`version.yml`'s publish job needs `id-token: write` to mint an OIDC token for
trusted publishing to a package index (PyPI, or AWS for the static index and
CodeArtifact).

The `version` job later grew the same need for a different reason: when
`codeartifact-read-role-arn` is set, it assumes a read-only role over OIDC
before relocking `uv.lock`, so that a consumer whose `[tool.uv.sources]`
points at a CodeArtifact-hosted package doesn't get a 401 from that index
during the bump step.

## Decision

Neither the `version` job nor the `publish` job declares a `permissions:`
block of its own. `version.yml` also declares no top-level `permissions:` key
at all, not even `{}`. All three are required together. The permission comes
from the consumer's own stub, which already grants the `version` job
`contents: write` / `pull-requests: write` unconditionally, and adds
`id-token: write` to that same grant for either opt-in OIDC use: publishing
(alongside a GitHub Environment with a trusted publisher or OIDC trust policy
attached) or the CodeArtifact read sign-in (no environment needed -- the role
trusts the calling repository directly, the same read-only role every
repository's own CI already assumes for the same reason).

`environment:` is set unconditionally on the publish job rather than gated on
`inputs.publish`, and its input ships a non-empty default.

## Consequences

Two rules, two different failure shapes:

1. **A job-level `permissions:` block is checked at parse time against the
   caller's grant.** A caller's `permissions:` on a `workflow_call` step is a
   ceiling for every job in the called workflow, not a per-job grant. A job
   asking for more than the caller granted does not degrade and does not fail
   only when it runs: the whole workflow fails to load with `startup_failure`,
   no job runs at all, and the API returns no message saying why. The blast
   radius is every onboarded repo, on every push to `main`, whether or not it
   publishes -- a repo with `publish: false` has the job skipped by its `if:`,
   but the parse happens first.

   `environment:` is validated at that same parse step. Its input default
   must stay non-empty, or every run of every consumer dies at
   `startup_failure` regardless of the publish gate.

2. **A top-level `permissions:` key in the file, even `{}`, becomes the
   grant for any job in that file with no job-level block of its own**, in
   place of the caller's grant, not merely a floor a job can still widen past.
   The publish and version jobs both depend on having no block of their own
   so that each falls through to the caller's grant; a top-level key defeats
   that for every job in the file that has no block, not just those two.

The practical rule that follows: never add a `permissions:` value to the
publish job or the version job in `version.yml`, and never add a top-level
`permissions:` key anywhere in that file. Adding a permission requirement to
a reusable workflow is a breaking change requiring a new major, not a
`v1.x.y` release. See [ADR 0001](0001-consumers-pin-a-moving-major-tag.md).

There is no third option that keeps a workflow-level default in this file and
also lets the publish job inherit `id-token: write` from the caller. A file
that needs a workflow-level default for some other job has to give every job
in it an explicit `permissions:` block instead, none relying on that default
-- `build-release.yml` does this: its top-level `permissions: {}` is inert
because both of its jobs declare their own.

A repo that does not publish grants nothing extra, and the publish job never
runs. Likewise, a repo that never sets `codeartifact-read-role-arn` grants
nothing extra, and the version job's read sign-in steps are skipped by their
own `if:` -- they only need whatever `id-token: write` the caller already
granted (or didn't) once a consumer actually turns the input on.
