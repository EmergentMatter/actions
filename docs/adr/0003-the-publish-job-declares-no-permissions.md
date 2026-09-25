# 3. The publish job declares no permissions of its own

## Status

Accepted. Amended after a second incident (the beta rehearsal, described below)
surfaced a related but distinct failure mode this record did not originally
cover.

## Context

`version.yml`'s publish job needs `id-token: write` to mint an OIDC token for
trusted publishing to a package index (PyPI, or, later, AWS for the static
index and CodeArtifact). The natural way to express that is a `permissions:`
block on the job that needs it, scoped as narrowly as possible. That is the
standard advice, and following it here took down every onboarded repo.

The obvious fix -- give the job no `permissions:` block and let it inherit
from the caller -- has a second precondition that was not understood when this
record was first written: the file that HOSTS the job must also carry no
top-level `permissions:` key. A `permissions: {}` at the top of `version.yml`,
added defensively (it looks like the safe, minimal thing to declare), silently
defeats the inheritance this whole design depends on. That surfaced only once
a repo actually ran `publish: true` for the first time, in the beta rehearsal:
the publish job's own checkout of the consumer failed with "Repository not
found" on a private repo, because its `GITHUB_TOKEN` had no permissions at
all -- not the consumer stub's `contents: write`, `id-token: write` grant.

## Decision

The publish job declares no `permissions:` block of its own. **`version.yml`
also declares no top-level `permissions:` key at all** -- not even `{}`. Both
absences are required together; either one alone reintroduces a failure. The
permission comes from the consumer's own stub, which adds `id-token: write`
(and already has `contents: write`, `pull-requests: write`, needed by both
jobs) to its `version.yml` stub, and creates a GitHub Environment with a
trusted publisher (PyPI) or an OIDC trust policy (AWS) attached.

`environment:` is set unconditionally on the job rather than gated on
`inputs.publish`, and its input ships a non-empty default.

## Consequences

Two separate mechanisms, two separate failure modes, both confirmed live:

**(1) A job-level `permissions:` block is a parse-time ceiling violation.** A
caller's `permissions:` on a `workflow_call` step is a ceiling for every job in
the called workflow, not a per-job grant. GitHub checks a called job's
declared permissions against that ceiling when it **parses** the workflow,
before any `if:` is evaluated. A job asking for more than the caller granted
does not degrade and does not fail when it runs: the whole workflow fails to
load with `startup_failure`, no job runs at all, and the API returns no
message saying why. The blast radius is every onboarded repo, on every push to
`main`, whether or not it publishes anything -- a repo with `publish: false`
has the job skipped by its `if:`, but the parse happens first, so it breaks
too. This was observed live in an early rehearsal: every run of every consumer
failed until a `permissions: { id-token: write }` block came out of the
publish job.

`environment:` is validated at that same parse step and broke the same way for
the same reason. Its input default was once the empty string, which is not a
valid environment name, so every run of every consumer died at
`startup_failure` regardless of the publish gate.

**(2) A top-level `permissions:` key in the FILE, even `{}`, is a different,
later, silent failure at run time, not parse time.** A job with no
`permissions:` block does not automatically fall through to the caller's
ceiling under all conditions -- it falls through to the caller's ceiling only
when the reusable workflow FILE also declares no top-level `permissions:`.
The moment the file sets one, that value becomes the job's ACTUAL grant, in
place of the caller's ceiling, not merely a floor the job can still widen past.
`version: {}` at the top of the file therefore left the publish job with
NO permissions whatsoever, regardless of what a publish-enabled consumer's
stub granted the calling `version:` step -- confirmed live in the beta
rehearsal: `em-release-control-test`'s stub granted `contents: write,
pull-requests: write, id-token: write`, the `version` job tagged and released
successfully (it has its own explicit `permissions:` block, so it was
unaffected), and the `publish` job's first step, `actions/checkout` of the
consumer at the release tag, failed with "Repository not found" -- a private
repo the token had no `contents: read` to see. The fix was removing the
top-level key, not adding permissions to the job.

The practical rule that follows, expanded from the original: never add a
`permissions:` value to a job in `version.yml`, and never add a top-level
`permissions:` key anywhere in the file, without understanding both mechanisms
above. Neither degrades gracefully in any way that testing against a single
non-publishing repo would reveal -- (1) needs a repo that actually sets
`publish: true` to surface, once, at parse time; (2) needs that same repo's
publish job to actually RUN, which nothing before the beta rehearsal had ever
done, since no repo had enabled publishing until then.

Adding a permission requirement to a reusable workflow is therefore a breaking
change requiring a new major, not a `v1.x.y` release. See
[ADR 0001](0001-consumers-pin-a-moving-major-tag.md).

A repo that does not publish grants nothing extra, and this job never runs.

There is no third option that keeps a workflow-level default AND lets the
publish job inherit `id-token: write` from the caller: a workflow-level
`permissions:` block, present at all, is what the job without its own block
receives, full stop, regardless of what the caller granted. If `version.yml`
ever needs a workflow-level default for an unrelated reason, that need and
this job's OIDC inheritance cannot both be satisfied at once; whichever comes
later has to find a different way to get what it needs (e.g. every job in the
file declaring its own explicit `permissions:` block, none relying on a
workflow-level default -- `build-release.yml` already does this, which is why
its own top-level `permissions: {}` is inert rather than a landmine).
