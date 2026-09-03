# 5. sync.py compares three ways, not two

## Status

Accepted.

## Context

`onboard.py` copies `templates/` into a repo once. Nothing re-syncs them
afterwards, so a consumer's copy drifts from the day it lands. `sync.py` exists
to close that gap.

The hard part is not copying files. It is telling "this repo's copy is stale"
apart from "this repo deliberately customised this file". A two-way diff cannot
make that distinction: both cases look identical, because both simply differ
from the current template.

`fleet_status.py`'s `templates` check made exactly that mistake before it had a
stamp to compare against. Every divergent file was flagged the same way, stale
copy and knowing edit alike, which trains a reader to ignore the report.

## Decision

Compare three ways per entry, against the template as it stood at the version
the repo last synced from:

- **base**: the template at the `templates_version` recorded in the target
  repo's `[tool.em-release]` block.
- **ours**: the file currently in the target repo.
- **theirs**: the template currently in this repo.

Staleness updates silently. A deliberate local edit is left alone and reported,
never overwritten. A genuine conflict prompts, unless `--ours` or `--theirs`
decides it outright.

## Consequences

The stamp is what makes the distinction possible, so it has to name an
immutable ref. Pointing it at the moving `v1` alias collapses the merge base
toward the current template and silently stops detecting staleness, which looks
exactly like success. A point tag or a commit SHA is required; see
[ADR 0001](0001-consumers-pin-a-moving-major-tag.md) for why `v1` moves.

A repo with no stamp at all, onboarded before provenance tracking existed, has
no base. That case degrades to a two-way diff and treats every difference as a
decision for a human. It never guesses which side is right, because that guess
is the bug this design exists to prevent.

A stale stamp also limits what `fleet_status.py` can conclude: it cannot tell a
deliberate edit from drift without a usable base, so it reports both. `sync.py`
can, and its answer is the one to trust when deciding what a change will
contain.

`seed-once` entries are never touched, not even to report drift. They belong to
the repo from the moment `onboard.py` writes them.
