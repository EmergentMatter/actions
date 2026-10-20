# 9. A workspace releases all its packages together, at one version, by opt-in

## Status

Accepted.

## Context

The release workflows assume one package per repository: `uv build` and
`uv version --bump` act on the root project, `verify-wheel` expects one wheel,
and the static index files everything in `dist/` under one name. A repository
that is a uv workspace and ships more than one distribution from the same
source has no way to say so.

Releasing each member from its own tag and release PR was considered. A
release PR is one computed bump from one set of notes, and a tag is one
version of one repository, so per-member releases would need per-member notes,
tags and PRs, none of which the contract has a place for.

## Decision

- **One input, `all-packages`, default `false`, on `version.yml` and
  `build-release.yml`, kept in step.** Off, every step behaves as it did
  before the input existed. `verify-wheel` has the same input, also off by
  default, so a stray second wheel still fails a single-package build.
- **Lockstep.** When on, every workspace package takes the bump
  `compute_bump.py` computed and the run fails unless each one then carries the
  expected version. A member that starts out of step fails the release rather
  than being silently corrected, because a correction would publish a version
  nobody reviewed.
- **The package list comes from `[tool.uv.workspace]`**, read by
  `scripts/workspace_packages.py`, not from a list the caller restates. A
  package with a dynamic version is refused: lockstep cannot be asserted about
  a version that cannot be read.
- **Wheels are verified together and named by their own METADATA.** They are
  installed into one clean environment, so a member that depends on a sibling
  resolves it from the sibling's wheel, and they must share one version.
- **Publishing is unchanged and name-agnostic, except for the static index.**
  `uv publish` and the release globs take every file in `dist/`. The static
  index files a release under a single package's prefix and page, so it refuses
  a `dist/` with more than one distribution name, and `all-packages` combined
  with `static-index` or `both` is refused before any credential is assumed.
- **The licence tier is the repository's.** `[tool.em-release] tier` is read
  from the root `pyproject.toml` and applies to every package built from it. A
  repository that needs two tiers needs two repositories.

- **Exact pins between packages move with the bump; ranges do not.** `uv version`
  leaves a `==` pin from one workspace package to another at the old version, and
  `uv lock` does not enforce it, so the built wheel would require the sibling's
  previous release. The bump rewrites each such pin to the new version, and the
  final check fails naming any that is not `==` it. A range such as `>=1.2,<2`
  states compatibility, which a version bump does not change, so it is left
  alone.

## Consequences

Cross-package constraints are the caller's to choose: an exact pin follows every
release automatically, a range stays where it was written. Only pins in
`[project].dependencies` and `[project.optional-dependencies]` are rewritten;
one in a dependency group is not.

`version_files` entries are synced from the root only; a version string
declared inside a member's source tree is not touched by `sync_version.py`
unless the root's `version_files` names it.

A repository can adopt the input without changing its tags or its changelog:
there is still one tag, one release PR and one changelog, and the GitHub
Release carries every member's wheel and sdist.
