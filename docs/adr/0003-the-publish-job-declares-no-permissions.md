# 3. The publish job declares no permissions of its own

## Status

Accepted.

## Context

`version.yml`'s publish job needs `id-token: write` to mint an OIDC token for
trusted publishing to a package index (PyPI, or AWS for the static index and
CodeArtifact).

## Decision

The publish job declares no `permissions:` block of its own. `version.yml`
also declares no top-level `permissions:` key at all, not even `{}`. Both are
required together. The permission comes from the consumer's own stub, which
adds `id-token: write` (alongside the `contents: write` / `pull-requests:
write` it already grants the `version` job) to its `version.yml` stub, and
creates a GitHub Environment with a trusted publisher (PyPI) or an OIDC trust
policy (AWS) attached.

`environment:` is set unconditionally on the job rather than gated on
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
   The publish job depends on having no block of its own so that it falls
   through to the caller's grant; a top-level key defeats that for every job
   in the file that has no block, not just the publish job.

The practical rule that follows: never add a `permissions:` value to the
publish job in `version.yml`, and never add a top-level `permissions:` key
anywhere in that file. Adding a permission requirement to a reusable workflow
is a breaking change requiring a new major, not a `v1.x.y` release. See
[ADR 0001](0001-consumers-pin-a-moving-major-tag.md).

There is no third option that keeps a workflow-level default in this file and
also lets the publish job inherit `id-token: write` from the caller. A file
that needs a workflow-level default for some other job has to give every job
in it an explicit `permissions:` block instead, none relying on that default
-- `build-release.yml` does this: its top-level `permissions: {}` is inert
because both of its jobs declare their own.

A repo that does not publish grants nothing extra, and the publish job never
runs.
