# EmergentMatter/actions

This repo does two things for every Python repo in the EmergentMatter org,
and they're worth telling apart:

1. **Release control**: one way to version and release. A note per PR, a
   computed version bump, one release PR whose merge is the release.
2. **Shared templates**: the single source for the files every repo in
   the org carries, both the plumbing release control needs and the
   community-health files that make a repo a decent open-source citizen.
   Onboarding installs them once; `sync.py` is what keeps them current
   after that.

## The concept

1. **Every PR adds one small note** to `changelog.d/`: a bump level
   (`major`/`minor`/`patch`) and one sentence on what changed for someone
   using the code. A check fails the PR if the note is missing (unless
   it's labeled `skip-changelog`).
2. **Merging a PR releases nothing.** Notes just accumulate on `main`.
   Nothing is versioned, tagged, or published when your PR merges.
3. **A workflow drafts the release.** It computes the next version from
   whatever notes are pending, writes that version everywhere it appears
   in the repo, folds the notes into `CHANGELOG.md`, deletes them in the
   same commit, and opens (or updates) a single **"Release vX.Y.Z"** pull
   request. **Merging that PR is the release**, the one moment a tag gets
   cut and (if configured) a package gets published.

The point: nothing ships behind the repo owner's call, and the mechanical
work (version strings, changelog, lockfile, tag) is already done and
sitting in a diff by the time anyone has to make that call. The only
thing left is the judgment call itself.

## Shared templates

Onboarding a repo copies templates in as a one-time snapshot. Nothing
about that snapshot re-checks itself: `templates/` in this repo can move
on for months without an onboarded repo ever finding out. With the org's
Python repos already pinned to `@v1` (see
[`.github/RELEASING.md`](.github/RELEASING.md)), that drift is not a
hypothetical, it's the default outcome of doing nothing. **Onboarding
happens once; drift accumulates forever; something has to re-sync.**
That something is `scripts/sync.py`.

The hard part isn't copying files, it's telling "this copy is just
stale" apart from "this repo deliberately customized this file." Both
look identical to a plain diff: the file simply differs from the current
template. `sync.py` resolves that with a three-way compare instead, the
same idea git uses for a merge: it reads the `templates_version` a repo
recorded at its last sync (`[tool.em-release]` in its `pyproject.toml`)
to get a common base, then compares the repo's copy and this repo's
current copy against it. See `scripts/sync.py`'s module docstring for the
full breakdown of what each combination means.

Every file a consuming repo receives is declared once, in
[`templates/manifest.toml`](templates/manifest.toml): a source under
`templates/`, the `dest` it lands at (often not the same name as the
source), and a `policy`. That manifest is the source of truth rather than a
description of one. `onboard.py`, `sync.py`, and `fleet_status.py` all read
it at runtime, so adding a template is one entry in one file, and reading
it is how you find out what a repo receives.

The `policy` is the part worth understanding before you edit anything:

- **`managed`**: should always match the template. `sync.py` updates it
  silently when it's merely stale, and leaves it alone, reporting it, when
  the repo has deliberately edited it. Change a managed file here, in
  `templates/`, and let sync carry it out. A local edit in a consumer is
  indistinguishable from staleness.
- **`seed-once`**: written at onboarding if absent, then never touched
  again. Reserved for the files a repo is meant to make its own, such as
  its CI workflow and its local lint overrides. Syncing those would fight
  every repo that customized them, which is the whole reason they are
  seeded rather than managed.

## Architecture

This repo is **passive**: it holds no secrets, and nothing that ships to a
consumer reaches out at runtime. Every consuming repo runs the actual
release and changelog-check work in its own context, using its own
`secrets.GITHUB_TOKEN`. The maintenance scripts (`onboard.py`, `sync.py`,
`fleet_status.py`) are the one exception, and only in the sense that a
person runs them by hand, from a clone, authenticated as themselves via
`gh`, never as part of any repo's CI.

```
EmergentMatter/actions (this repo: shared, passive, no secrets)
 ├── .github/workflows/version.yml           ─┐
 ├── .github/workflows/build-release.yml     ─┘ reusable workflow_call workflows
 ├── changelog-check/action.yml              composite action, the PR gate
 ├── scripts/{compute_bump,sync_version}.py     stdlib-only, called by the above
 └── templates/                              what a consumer's copy starts from
        ▲                                        (templates/manifest.toml: dest + policy)
        │ uses: EmergentMatter/actions/.github/workflows/<name>.yml@v1
        │ uses: EmergentMatter/actions/changelog-check@v1
        │
 consuming repo (e.g. emergent-matter-materials)
 ├── .github/workflows/{changelog-check,version,build-release}.yml   thin stubs, pinned @v1
 ├── .github/workflows/ci.yml                 seeded once, then the repo's own to keep
 ├── pyproject.toml   [tool.towncrier] + [tool.em-release] (+ templates_version)
 ├── scripts/changeset.py                     not inside the package, see docs/onboarding.md
 ├── CONTRIBUTING.md, SECURITY.md, SUPPORT.md, and the rest of the community-health files
 └── changelog.d/                             pending notes, one per unreleased PR

 maintainer's machine, run on demand, never wired into either repo's CI
 └── onboard.py, sync.py, fleet_status.py     install, re-sync, and audit templates over gh
```

Consumers always pin `@v1`, never a branch.

## The scripts

The scripts in `scripts/` divide by where they run, and that division is
what the directory listing can't show you:

- **Called from a workflow.** These run on a runner, inside the consuming
  repo, with that repo's own `GITHUB_TOKEN`, and read only that checkout.
- **Run by a maintainer.** These run against a target repo from a clone of
  this one, by hand, authenticated as a person through `gh`. None of them
  is ever copied into a consuming repo or wired into any repo's CI.

Every script opens with a module docstring saying which of the two it is
and why it exists. Full CLIs, flags, and exit codes are in
[`docs/tooling.md`](docs/tooling.md).

## Opting a repo in

Onboarding a repo is **the release-control files, a config block, and a label**, plus the
community-health files the manifest declares, which need no repo-specific
decisions. The
full walkthrough, using `emergent-matter-materials`'s several declared
version strings as the worked example, is
[`docs/onboarding.md`](docs/onboarding.md).
The contributor-facing half (what you do day to day once a repo is
onboarded) is [`templates/CONTRIBUTING.md`](templates/CONTRIBUTING.md),
meant to be copied verbatim into the consuming repo.

## Links

- [`docs/onboarding.md`](docs/onboarding.md): how to onboard a repo, start to finish
- [`docs/tooling.md`](docs/tooling.md): the maintenance scripts, in full
- [`templates/manifest.toml`](templates/manifest.toml): the source of truth for what every repo receives
- [`templates/CONTRIBUTING.md`](templates/CONTRIBUTING.md): the contributor-facing half, copied into consuming repos
- [`templates/pyproject-snippet.toml`](templates/pyproject-snippet.toml): the copy-pasteable config block
- [`CONTRACT.md`](CONTRACT.md): the full behavioral spec (script CLIs, workflow inputs, exact failure modes)
- [`CLAUDE.md`](CLAUDE.md): architecture notes for Claude Code sessions working in this repo

## Org Playbook

This project follows the EmergentMatter engineering playbook. See
`../engineering-playbook/CLAUDE.md` for org-wide patterns, conventions,
and the repo catalog. When in doubt, check the playbook before inventing
a new pattern.
