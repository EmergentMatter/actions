# 8. This repo cuts its v1.x.y point tags from its own changelog notes

## Status

Accepted. Supersedes the "Promotion is not automated" paragraph of
[ADR 0001](0001-consumers-pin-a-moving-major-tag.md) and nothing else in it.

## Context

Until now a point tag was cut by hand: write the tag message, push the tag,
move `v1`. Nothing recorded what each release contained for a reader, and
every release depended on someone remembering the whole procedure.

This repo ships the release workflow that every consumer uses. Running it on
itself removes the manual version bump and changelog, and puts this repo under
the same rule as every consumer: merging a pull request never releases
anything, only merging the release pull request does.

The part that must not be automated is moving `v1`. It is the one action that
changes what every pinned repo runs, and the ruleset that protects it admits
only repository admins. A workflow runs as `github-actions[bot]`, which is not
one, and granting it a bypass would let any workflow in this repo move `v1`.

## Decision

This repo calls its own `version.yml` by local path from a caller workflow,
and releases `v1.x.y` from `changelog.d/` notes.

- **Merging the release pull request creates the point tag and the GitHub
  Release.** That is accepted, and is the same rule as in every consumer. The
  point tag reaches nobody until `v1` moves, so creating it is not a release to
  consumers.
- **Moving `v1` stays a manual owner step.** The bot cannot bypass the ruleset,
  and a bypass for it would hand every workflow in the repo the `v1` pin.
- **A new major is never automated.** The release fails when the computed
  version has a major other than 1. A breaking change goes through the manual
  `v2` process in [`.github/RELEASING.md`](../../.github/RELEASING.md).
- **Bot tags are lightweight.** `version.yml` creates the tag without a
  message. What changed for consumers is recorded in `CHANGELOG.md` and the
  GitHub Release, not in a tag message.
- **The manual procedure stays as the fallback** for when the self-release
  workflow is broken. It is the only way to release a fix to the workflow that
  releases.

## Consequences

Every release has a changelog entry and a GitHub Release without anyone writing
them by hand, and the version in `pyproject.toml` is the version of the tag.

There is a window between the merge and the move of `v1` in which a point tag
exists that no consumer follows. Pinning `v1.x.y` directly is the only way to
reach it, and the post-merge checklist in `RELEASING.md` closes it.

The repo calls the workflow it is releasing, so a broken `version.yml` on
`main` breaks the release of its own repair. The manual procedure exists for
that case, and it must stay documented and working.

The caller grants `contents: write` and `pull-requests: write` and nothing
else, with no `id-token` and no inherited secrets, because the workflow
publishes nothing and a caller's permissions are a ceiling for the called
workflow (see [ADR 0003](0003-the-publish-job-declares-no-permissions.md) and
[ADR 0006](0006-no-stub-grants-secrets-to-a-shared-workflow.md)).
