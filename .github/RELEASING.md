# Releasing this repo

This is about how **this repo** (`EmergentMatter/actions`) cuts its own
tags — not about the release-control system it ships to other repos. For
that, see [`README.md`](../README.md) and
[`docs/onboarding.md`](../docs/onboarding.md).

## Tag strategy

- **Consumers pin `@v1`, never a branch.** (P1, CONTRACT.md) Every
  `uses: EmergentMatter/actions/.github/workflows/<name>.yml@v1` in the org
  resolves through this one tag.
- **`v1` is a moving major tag.** Every `v1.x.y` release re-points `v1` to
  that commit. This is the standard GitHub Actions major-tag convention —
  it's what lets consumers get compatible fixes without editing every
  stub in the org.
- **`v1.0.0`, `v1.1.0`, etc. are immutable point tags.** They are cut once
  and never re-pointed. `v1` always points at the latest one.
- **The `v1` tag must be branch-protected** (tag protection rule on `v1`,
  restricted to maintainers/release automation). This is the one piece
  that actually matters for security, not just hygiene: every consuming
  repo's stub uses `secrets: inherit`, so whatever `v1` resolves to at the
  moment a consumer's workflow runs executes with that consumer's full
  inherited secret set. An unprotected, force-pushable `v1` tag is
  equivalent to write access to every consuming repo's secrets, org-wide.
  Point releases (`v1.x.y`) should be protected too, for the same reason
  applied to anyone who pinned one directly during testing — but `v1` is
  the one that matters in the common case, since it's what every stub
  actually references.

## The known limitation, briefly

Release PRs opened by `version.yml` use `secrets.GITHUB_TOKEN`, which does
not trigger further workflow runs — so a release PR's own status checks
can show as not run. This is intentional (see CONTRACT.md's "known
limitation" section and `CLAUDE.md`), not a bug: the code being released
was already gated by the consuming repo's own CI before the release PR was
even drafted. The human escape hatch — close the release PR and
immediately reopen it to force its checks to run — is documented in full
in `templates/CONTRIBUTING.md`, which every consuming repo carries; this
file doesn't repeat it beyond this pointer.
