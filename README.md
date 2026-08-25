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

**Status:** `v1.5.0` is the newest tag; consumers pin `@v1`, never a
branch. Release control is live in production: `emergent-matter-materials`
has released through `v1.11.0` on this system. `em-release-control-test`,
a disposable end-to-end rehearsal repo, proved the automated path first,
through four releases up to `v1.1.1`.

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
on for months without an onboarded repo ever finding out. With roughly
thirty repos already pinned to `@v1` (see
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
[`templates/manifest.toml`](templates/manifest.toml), with a `dest` (where
it lands, often not the same name as the source) and a `policy`:

- **`managed`**: should always match the template. `sync.py` updates it
  silently when it's merely stale, and leaves it alone, reporting it,
  when the repo has deliberately edited it.
- **`seed-once`**: written at onboarding if absent, never touched again.
  Only `ci.yml` is `seed-once`, because repos legitimately
  customize their own CI, and syncing it would fight them.

**Release-control plumbing**, the files that make the system itself work:

| File | Lands at | What it's for | Policy |
|---|---|---|---|
| `stub-changelog-check.yml` | `.github/workflows/changelog-check.yml` | PR gate: fails a PR missing a `changelog.d/` note (unless labeled `skip-changelog`) | managed |
| `stub-version.yml` | `.github/workflows/version.yml` | On push to `main`: runs the repo's own CI, then drafts or updates the release PR, or, on the release commit, tags and builds/publishes, all in one run | managed |
| `stub-build-release.yml` | `.github/workflows/build-release.yml` | Not the automatic path. Only fires for a human-pushed tag or a manual rebuild via `workflow_dispatch` | managed |
| `changeset.py` | `scripts/changeset.py` | Interactive tool contributors run to write a changelog note; must stay outside the package, or it ships in the built wheel | managed |
| `CONTRIBUTING.md` | `CONTRIBUTING.md` | Contributor-facing instructions: how to add a note, what the three levels mean, the release-PR checks caveat | managed |
| `ci.yml` | `.github/workflows/ci.yml` | Starter CI (lint, format, typecheck, test, build) for a repo with none, plus an optional `ts` job a repo with a TypeScript package uncomments; `version.yml`'s own `needs: ci` depends on it | seed-once |
| `STYLE.md` | `STYLE.md` | The org's style guide: Python and TypeScript conventions, naming, docstrings, typing, testing, documentation, open-source hygiene | managed |
| `ruff-base.toml` | `ruff-base.toml` | The shared ruff lint and format ruleset | managed |
| `ruff.toml` | `ruff.toml` | `extend`s `ruff-base.toml`, plus room for this repo's own additions | seed-once |

**Community-health files**, about being a decent citizen of the org:

| File | Lands at | What it's for | Policy |
|---|---|---|---|
| `SECURITY.md` | `SECURITY.md` | Vulnerability reporting policy: email security@emergentmatter.com, or the repo's own Security tab | managed |
| `SUPPORT.md` | `SUPPORT.md` | Where to ask questions versus where to file a bug | managed |
| `CODE_OF_CONDUCT.md` | `CODE_OF_CONDUCT.md` | Contributor Covenant v2.1 | managed |
| `NOTICE` | `NOTICE` | Copyright and third-party attribution (credits the Contributor Covenant text) | managed |
| `LICENSE` | `LICENSE` | Apache License 2.0 | managed |
| `CODEOWNERS` | `.github/CODEOWNERS` | Default reviewers for the repo | managed |
| `dependabot.yml` | `.github/dependabot.yml` | Weekly dependency PRs, for both `uv` deps and the pinned Action SHAs in workflows | managed |
| `PULL_REQUEST_TEMPLATE.md` | `.github/PULL_REQUEST_TEMPLATE.md` | What changed and why, linked issue, test plan, checklist | managed |
| `ISSUE_TEMPLATE/bug_report.yml` | `.github/ISSUE_TEMPLATE/bug_report.yml` | Structured bug report form | managed |
| `ISSUE_TEMPLATE/feature_request.yml` | `.github/ISSUE_TEMPLATE/feature_request.yml` | Structured feature request form | managed |
| `ISSUE_TEMPLATE/config.yml` | `.github/ISSUE_TEMPLATE/config.yml` | Disables blank issues; links to emergent-matter-sdm's Discussions and the security policy | managed |

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

Seven scripts in `scripts/`, two run inside a consuming repo's own
workflows and five run against a target repo from a clone of this one.
Full CLIs, flags, and exit codes are in
[`docs/tooling.md`](docs/tooling.md); the one-line version:

| Script | What it does |
|---|---|
| `compute_bump.py` | Reads a repo's pending `changelog.d/` notes and returns the version/level to cut. Called by `version.yml`. |
| `sync_version.py` | Writes a version string into every location a repo declares via `version_files`, and nothing else. Called by `version.yml`. |
| `onboard.py` | Installs release control and the templates into a new repo: the stubs, the config block, `changelog.d/`, the `skip-changelog` label. |
| `sync.py` | Brings an onboarded repo's `managed` templates forward with the three-way compare described above. |
| `fleet_status.py` | Sweeps every repo pinned to `@v1` over the API and reports drift: broken stubs, missing checks, stale templates, and more. |
| `lint_gate.py` | Turns a repo's lint gate on or off, keeping its two halves (the `ci.yml` line and the required check) in agreement. |
| `verify_wheel.py` | Called by the `verify-wheel` action; imports every top-level module of a built wheel to catch one that builds but ships no code. |

## Opting a repo in

Onboarding a repo is **the release-control files, a config block, and a label**, plus the
community-health files above, which need no repo-specific decisions. The
full walkthrough, using `emergent-matter-materials`'s three-version-strings
case as the worked example, is [`docs/onboarding.md`](docs/onboarding.md).
The contributor-facing half (what you do day to day once a repo is
onboarded) is [`templates/CONTRIBUTING.md`](templates/CONTRIBUTING.md),
meant to be copied verbatim into the consuming repo.

## Links

- [`docs/onboarding.md`](docs/onboarding.md): how to onboard a repo, start to finish
- [`docs/tooling.md`](docs/tooling.md): the seven maintenance scripts, in full
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
