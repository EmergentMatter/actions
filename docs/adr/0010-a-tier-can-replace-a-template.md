# 10. A tier can replace a template

## Status

Accepted.

## Context

A closed-tier repo needs different stubs, CI and Dependabot configuration from
the common ones, because it reads a private package index. The manifest could
already hold two entries for one destination, but only for files every repo
receives in some form, and a repo with no declared tier got neither.

## Decision

A tiered manifest entry replaces the untiered entry for the same destination
when the repo declares that tier. The untiered entry stays the default, so a
repo with no tier, or an open one, is unchanged. `sync.py` reports a missing
tier only for destinations that have no untiered entry.

The closed stubs differ from the common ones by their text, not by a switch
inside shared workflows. A repo that publishes is a variant of the same
stub, turned on once at onboarding with marked lines.

## Consequences

A template change for a closed repo is made in its own file and must be kept
in step with the common one. Changing a repo's tier later does not rewrite
files already installed; the conversion is documented in `docs/onboarding.md`.
