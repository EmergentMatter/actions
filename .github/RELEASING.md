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
  restricted to maintainers/release automation). Consumer stubs deliberately
  carry **no** `secrets: inherit` on the jobs that call this repo's
  workflows (CONTRACT.md's Consumer stub block — `secrets: inherit`, where
  it appears at all, sits on the consumer's own local `ci:` job, never on
  the call into `EmergentMatter/actions/...@v1`), so a compromised or
  force-pushed `v1` cannot read a consuming repo's named secrets. It is
  still a real blast radius, just a smaller one: whatever `v1` resolves to
  runs with the `contents: write` / `pull-requests: write` /
  (when a repo has opted into `publish: true`) `id-token: write`
  permissions that repo's own stub grants — enough to push arbitrary tags,
  open or merge PRs, and, for any repo that publishes, mint an OIDC token
  scoped to that repo's trusted-publisher config. `id-token: write` is
  granted directly by the consumer's stub and doesn't route through
  `secrets: inherit` at all, so it isn't mitigated by the point above —
  it's the reason tag protection stays a hard prerequisite (see the risk
  list already raised with the user) rather than something the
  `secrets: inherit` removal made optional.
  Point releases (`v1.x.y`) should be protected too, for the same reason
  applied to anyone who pinned one directly during testing — but `v1` is
  the one that matters in the common case, since it's what every stub
  actually references.

## Build + release run inline in `version.yml`, not on tag push

`version.yml` builds the wheel/sdist and creates the GitHub Release itself,
in the same job run that pushes the release tag — not in a separately
tag-triggered workflow. Reason: that tag is pushed using `GITHUB_TOKEN`
credentials, and GitHub does not trigger further workflow runs from events
created by `GITHUB_TOKEN` (found by the `em-release-control-test`
end-to-end rehearsal). A `build-release.yml` stub listening for
`push: tags: ["v*"]` alone would simply never fire on the automated path.

`build-release.yml` still exists, but only for the two cases that aren't
that path: a human pushing a `v*` tag by hand (which does trigger
workflows), and `workflow_dispatch` for manually rebuilding or
republishing a past release. Don't be confused by its apparent redundancy
with `version.yml` — they cover disjoint triggers.

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
