# EmergentMatter/actions

Shared GitHub Actions workflows and scripts that give every Python repo in
the EmergentMatter org **one way** to version and release: a note per PR,
a computed version bump, and one release PR whose merge is the release.

**Status:** `v1` is tagged; pin `@v1`. Rollout to production repos
(`emergent-matter-materials` first) is starting now.

## The concept

1. **Every PR adds one small note** to `changelog.d/` — a bump level
   (`major`/`minor`/`patch`) and one sentence on what changed for someone
   using the code. A check fails the PR if the note is missing (unless
   it's labeled `skip-changelog`).
2. **Merging a PR releases nothing.** Notes just accumulate on `main`.
   Nothing is versioned, tagged, or published when your PR merges.
3. **A workflow drafts the release.** It computes the next version from
   whatever notes are pending, writes that version everywhere it appears
   in the repo, folds the notes into `CHANGELOG.md`, deletes them in the
   same commit, and opens (or updates) a single **"Release vX.Y.Z"** pull
   request. **Merging that PR is the release** — that's the one moment a
   tag gets cut and (if configured) a package gets published.

The point: nothing ships behind the repo owner's call, and the mechanical
work — version strings, changelog, lockfile, tag — is already done and
sitting in a diff by the time anyone has to make that call. The only thing
left is the judgment call itself.

## Architecture

This repo is **passive** — it holds no secrets and reaches nothing at
runtime. It just hosts reusable workflows and small stdlib-only scripts.
Every consuming repo runs the actual work in its own context, using its
own `secrets.GITHUB_TOKEN`:

```
EmergentMatter/actions  (this repo -- shared, passive, no secrets)
 ├── .github/workflows/changelog-check.yml   ─┐
 ├── .github/workflows/version.yml            ├─ reusable workflow_call workflows
 ├── .github/workflows/build-release.yml     ─┘
 └── scripts/{compute_bump,sync_version}.py     stdlib-only, called by the workflows above
        ▲
        │ uses: EmergentMatter/actions/.github/workflows/<name>.yml@v1
        │
 consuming repo (e.g. emergent-matter-materials)
 ├── .github/workflows/changelog-check.yml   ─┐  thin stubs -- a few lines
 ├── .github/workflows/version.yml            ├─ each, calling back into
 ├── .github/workflows/build-release.yml     ─┘  this repo, pinned to @v1
 ├── pyproject.toml   [tool.towncrier] + [tool.em-release]
 ├── scripts/changeset.py     NOT inside the package -- see docs/onboarding.md
 ├── CONTRIBUTING.md
 └── changelog.d/     pending notes, one per unreleased PR
```

Consumers always pin `@v1` — never a branch.

## Opting a repo in

Onboarding a repo is **five files, a config block, and a label**. The full
walkthrough — using `emergent-matter-materials`'s three-version-strings
case as the worked example — is [`docs/onboarding.md`](docs/onboarding.md).
The contributor-facing half (what you do day to day once a repo is
onboarded) is [`templates/CONTRIBUTING.md`](templates/CONTRIBUTING.md),
meant to be copied verbatim into the consuming repo.

## Links

- [`docs/onboarding.md`](docs/onboarding.md) — how to onboard a repo, start to finish
- [`templates/CONTRIBUTING.md`](templates/CONTRIBUTING.md) — the contributor-facing half, copied into consuming repos
- [`templates/pyproject-snippet.toml`](templates/pyproject-snippet.toml) — the copy-pasteable config block
- [`CONTRACT.md`](CONTRACT.md) — the full behavioral spec (script CLIs, workflow inputs, exact failure modes)
- [`CLAUDE.md`](CLAUDE.md) — architecture notes for Claude Code sessions working in this repo

## Org Playbook

This project follows the EmergentMatter engineering playbook. See
`../engineering-playbook/CLAUDE.md` for org-wide patterns, conventions,
and the repo catalog. When in doubt, check the playbook before inventing
a new pattern.
