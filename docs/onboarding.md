# Onboarding a repo

Onboarding a repo into release control means installing **six files that
carry real behavior, a config block, and a label**. It also seeds a set
of standard repo-hygiene files that need no decisions from you. This doc
walks through all of it, using
[`emergent-matter-materials`](https://github.com/EmergentMatter/emergent-matter-materials)
as the worked example for the one genuinely hard decision: which version
strings must move together. That repo has **three**, not one.

If you haven't read the top-level concept yet, read the [README](../README.md)
first. This doc assumes you know what a changelog note is and what the
release PR does.

`v1` is live. Each release is cut as an immutable `v1.x.y` point tag, and
`v1` is force-moved to the newest of them. See
[`.github/RELEASING.md`](../.github/RELEASING.md). **Pin `@v1`**, not a
point tag: `v1` is the ref this system is designed around, and it is what
gets you fixes without editing anything. `git ls-remote --tags` on this
repo lists what actually exists; this doc deliberately doesn't enumerate
them, because an enumerated list here went stale and claimed a `v1.0.1`
that was never cut.

**Most repos only need the [Quick path](#quick-path) below.** Everything
after it is reference material: read a section when you hit the situation
it covers, not front-to-back.

- [Quick path](#quick-path)
- [Before you start](#before-you-start)
- [The six files](#the-six-files)
- [A private sibling your package depends on (optional)](#a-private-sibling-your-package-depends-on-optional)
- [Publishing to a package index (optional)](#publishing-to-a-package-index-optional)
- [The config block](#the-config-block)
- [Worked example: emergent-matter-materials](#worked-example-emergent-matter-materials)
- [The label](#the-label)
- [`changelog.d/`: nothing but notes and `.gitkeep`](#changelogd-nothing-but-notes-and-gitkeep)
- [Inserting the towncrier marker](#inserting-the-towncrier-marker-into-an-existing-changelogmd)
- [Verifying it locally before you rely on it](#verifying-it-locally-before-you-rely-on-it)
- [Installed metadata versus the source tree](#installed-metadata-versus-the-source-tree)
- [Setting up branch protection](#setting-up-branch-protection)
- [Releasing under branch protection](#releasing-under-branch-protection)
- [Prove the gate actually works](#prove-the-gate-actually-works-before-you-trust-it)
- [After onboarding](#after-onboarding)

## Quick path

1. From a clone of this repo, dry-run `onboard.py` against yours:

   ```bash
   uv run python scripts/onboard.py --repo-path ../your-repo --dry-run
   ```

2. It prints what it would do, then stops and asks you to decide one
   thing: which version strings must move on every release. Re-run with
   `--version-file PATH:SYMBOL` for each one, this time for real with no
   `--dry-run`, and it writes everything, including the six files below,
   the config block, and the `skip-changelog` label.
3. Commit the result and open the onboarding PR.
4. Work through the three things `onboard.py` cannot do for you (table
   below), in order: declare `version_files` correctly, set branch
   protection, and [prove the gate actually
   works](#prove-the-gate-actually-works-before-you-trust-it) before you
   trust it.

What it cannot do for you, and why:

| Step | Why it stays manual |
|---|---|
| Declaring `version_files` | Whether a data version should track the package version is a judgement about *your* repo. See the [worked example](#worked-example-emergent-matter-materials) below, which exists to prevent one specific mistake. |
| Branch protection | The required contexts have to be read off a real pull request run, not predicted. |
| Proving the gate works | You have to watch it go red. |

The rest of this document is what the script is doing and why, which is
worth reading once, particularly [the worked example](#worked-example-emergent-matter-materials)
and [proving the gate](#prove-the-gate-actually-works-before-you-trust-it).
Tool reference, including everything `onboard.py` does beyond these six
files, lives in [tooling.md](tooling.md).

## Before you start

Release control runs your repo's own CI first and only drafts a release if
it passes. See `version.yml behaviour` in
[CONTRACT.md](https://github.com/EmergentMatter/actions/blob/v1/CONTRACT.md)
for why. So it needs a workflow at `.github/workflows/ci.yml` that is
callable via **`workflow_call`**, because the `version.yml` stub reaches it
with `uses: ./.github/workflows/ci.yml`.

Two ways in, and most repos in this org are the first:

### You have no CI yet

Copy [`templates/ci.yml`](../templates/ci.yml) to
`.github/workflows/ci.yml`. It is file 6 in the list below, and it is not
an afterthought: it is the floor every repo gets on day one. Three jobs,
named `lint` / `test` / `build` identically across every repo that copies
it, so "the build job" means the same thing everywhere. It already
triggers on both `pull_request` and `workflow_call`, and its `test` job
passes rather than erroring if the repo has no `tests/` directory yet, so
a repo with no suite can still onboard today and grow one later.

Adjust it to the repo afterwards. It is a starting point, not a contract:
only the job *names* matter to anything outside the repo, because branch
protection matches them.

### You already have CI

Two things to check, both easy to miss:

1. **Is it callable via `workflow_call`?** Most workflows written for a
   single repo are not. Add it alongside whatever triggers already exist:

   ```yaml
   on:
     pull_request:
     workflow_call:      # add this; version.yml calls the file by reference
   ```

   Without it, `version.yml` fails at parse time on every push to `main`.

2. **What are its job names?** They become your required status check
   contexts, and they are almost certainly not `lint` / `test` / `build`.
   `emergent-matter-materials`, the worked example below, has a single job
   called `test`, so its contexts are `test` and `changelog`, not the four
   this doc's branch-protection section lists. Use *your* names. Nothing
   requires you to rename jobs to match the template.

Keeping your own CI is the expected choice for a repo that has one. The
template exists for repos starting from nothing.

## The six files

| # | File | What it does |
|---|---|---|
| 1 | `.github/workflows/changelog-check.yml` | On every PR: fails the PR if it's missing a `changelog.d/` note and doesn't carry the `skip-changelog` label. |
| 2 | `.github/workflows/version.yml` | On push to `main`: runs your own CI first, then either drafts/updates the release PR from pending notes, or, if HEAD is the release commit that PR's merge just created, tags the release and builds + publishes it, all in the same run. |
| 3 | `.github/workflows/build-release.yml` | **Not** the automatic release path (see below). A fallback for a human-pushed tag or a manual `workflow_dispatch` rebuild. |
| 4 | `scripts/changeset.py` | The interactive note-writing tool contributors run before opening a PR. Must live at the repo root, **outside** the package (see below). |
| 5 | `CONTRIBUTING.md` | The contributor-facing instructions: how to add a note, what the three levels mean, the release-PR checks caveat. |
| 6 | `.github/workflows/ci.yml` | Your repo's own checks. **Only copy this if you don't already have CI.** See "Before you start". Unlike 1–3 this is a full file, not a stub, and it stops tracking `templates/ci.yml` the moment it lands. |

Files 1–3 are thin stubs pinned to `@v1`, copied from `EmergentMatter/actions`'s
`templates/` directory into your repo's `.github/workflows/`. Files 2 and 3
point at reusable workflows this repo hosts
(`uses: EmergentMatter/actions/.github/workflows/<name>.yml@v1`); file 1
points at a composite action instead
(`uses: EmergentMatter/actions/changelog-check@v1`, inside a normal job).
See "The changelog check doesn't need `actions-ref`" below for what that
changes. File 5 is a direct copy of
[`templates/CONTRIBUTING.md`](../templates/CONTRIBUTING.md) into your repo
root, unmodified. It's generic, not repo-specific. File 4 needs its own
explanation, below.

**Files 4, 5 and 6 are copies, not references, and that distinction has a
cost worth understanding up front.** Files 1–3 point at code hosted here,
so a fix lands in every consuming repo the moment `v1` moves, and nobody
has to do anything. Files 4–6 are yours from the moment you copy them: an improvement
to `templates/ci.yml` will never reach a repo that onboarded last month.
`scripts/fleet_status.py` in this repo exists to make that drift visible
rather than silent; run it after onboarding, and periodically after that.

`onboard.py` also seeds a set of standard repo-hygiene files:
`SECURITY.md`, `SUPPORT.md`, `CODE_OF_CONDUCT.md`, `LICENSE`, `NOTICE`,
`CODEOWNERS`, `dependabot.yml`, a PR template, and issue templates. They
need no repo-specific decisions, so they don't get their own row above;
the full, current list is [`templates/manifest.toml`](../templates/manifest.toml).
One of them has a side effect worth knowing: on a **public** repo,
`onboard.py` also turns on GitHub's private vulnerability reporting,
because `SECURITY.md` tells a researcher to use it. On a private repo the
feature doesn't exist at all, so `onboard.py` skips it and prints a
reminder to turn it on the day the repo goes public.
`scripts/fleet_status.py`'s `security` check is what catches a repo that
forgot.

### Why `build-release.yml` isn't the automatic path

The obvious design looks like: push a `v*` tag, let `build-release.yml`
fire, done. **That design doesn't work: a tag pushed with
`GITHUB_TOKEN` never triggers a workflow**, because GitHub suppresses it
to prevent recursion. Shipped that way, every onboarded repo would tag a
version and nothing would ever build or publish it, silently. That is
platform behaviour, not a permissions setting: no grant on `GITHUB_TOKEN`
makes those tag-triggered runs occur.

So `version.yml` pushes the tag **and** builds and publishes the release
**in the same run** (its final step, once it detects HEAD is the
just-merged release commit). `build-release.yml` still exists, but only
for a **human-pushed** tag (which does trigger workflows normally) or a
manual `workflow_dispatch` rebuild. It's a deliberate fallback, not a
redundant leftover you can skip copying.

### Why `scripts/changeset.py` must live outside the package

Put `changeset.py` inside `src/<package>/`, or wire it up as a
`[project.scripts]` console-script entry, and it **ships inside the
wheel**, putting a `changeset` command on the PATH of anyone who installs
the library. `changeset.py` is a contributor-only tool. It has no business
in a package your users install.

Copy `templates/changeset.py` to `scripts/changeset.py` at your repo
root, and invoke it as `uv run scripts/changeset.py`. That's a longer
command than `uv run changeset` would be, on purpose. Don't "tidy" it
back into a console-script entry to shorten it.

The `version.yml` stub is the one to get exactly right, because it's the
one that wires your own CI in as a gate. This is its exact shape:

```yaml
# .github/workflows/version.yml
name: Version
on:
  push:
    branches: [main]
jobs:
  ci:
    uses: ./.github/workflows/ci.yml
    secrets: inherit          # only if your own ci.yml needs a repo secret
  version:
    needs: ci
    uses: EmergentMatter/actions/.github/workflows/version.yml@v1
    with:
      actions-ref: v1          # MUST match the @ref above (see below)
    permissions: { contents: write, pull-requests: write }
                              # deliberately NO secrets: inherit
```

Pin `@v1`, never a branch. See CONTRACT.md's non-negotiables. **`version.yml`'s
stub is now the only one with an `actions-ref` input** (`build-release.yml`'s
never needed it, since that workflow never checks out this repo's
`scripts/`; `changelog-check.yml`'s stub no longer needs it either, now
that the changelog check is a composite action, covered below). There is no
context field that lets a *reusable workflow* discover its own ref:
`github.workflow_ref` resolves to the *caller's* ref, not this repo's,
and `github.job_workflow_sha` does not exist, both confirmed live rather
than assumed. An unresolvable ref is refused outright rather than
silently defaulting to this repo's `main`, which is how an earlier
version of this exact stub quietly ran unpinned code while believing it
had pinned `@v1`. If you ever change the `@v1` at the end of `version.yml`'s
`uses:` line, change the matching `actions-ref:` in the same edit.

### The changelog check doesn't need `actions-ref`

`changelog-check.yml`'s stub is a composite action call, not a
`workflow_call` reference:

```yaml
# .github/workflows/changelog-check.yml
name: Changelog
on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]
jobs:
  changelog:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: read
    steps:
      - uses: EmergentMatter/actions/changelog-check@v1
```

A composite action's own repo is checked out automatically for the runner
that calls it, and `${{ github.action_path }}` inside it points straight
at that checkout, so `scripts/compute_bump.py` is reachable without a
second checkout, a ref to resolve, or the `actions-ref` apparatus above.
That's a property of composite actions generally, not something this repo
built. It's the reason the changelog check moved to this shape in the
first place (see CONTRACT.md's "Composite action inputs"). `version.yml`
and `build-release.yml` stay reusable `workflow_call` workflows, which
don't get that free checkout, so `version.yml` keeps `actions-ref` for the
reason above.

Composite actions also can't declare their own `permissions:`, because
there's no `runs.permissions` key. So the `contents: read` /
`pull-requests: read` the check needs come from the job's own
`permissions:` block, shown above. Don't drop it: without it, the check's
own checkout of the PR head and its `gh api` call to list PR files both
fail.

Notice `secrets: inherit` sits on the `ci:` job, not on `version:`, and
it's conditional even there:

- **On `ci:`, only if your CI needs a secret.** A local `uses:` call
  doesn't inherit secrets implicitly either: `workflow_call` never does,
  local or remote. If your own `ci.yml` needs a repo secret to run (the
  concrete example: `sdm-core`'s CI checks out the private sibling
  `emergent-matter-materials`, which needs a `MATERIALS_REPO_TOKEN`),
  the `ci:` job has to say so explicitly. If your CI needs no secrets,
  **omit the line entirely** rather than adding it out of habit.
- **On `version:`, deliberately absent: this is a security decision,
  not an oversight.** The shared workflows read exactly one optional
  secret between them: `version.yml`'s `sibling-token`, for the private
  sibling checkout described below, and that one is mapped **by name**
  (`secrets: { sibling-token: ... }`), never inherited. Everything else
  uses the auto-injected `github.token`, and both `version.yml` and
  `build-release.yml` publish over OIDC trusted publishing when
  configured, which is secret-less by design.
  `EmergentMatter/actions` is a **public** repo pinned by a **movable**
  `v1` tag. Granting it your org's secrets via `secrets: inherit` would
  make that tag equivalent to secret access across every repo that pins
  it. So don't add it back as boilerplate, even though it looks like
  it's "missing" next to the `ci:` job above it.

## A private sibling your package depends on (optional)

Skip this unless your `pyproject.toml` has a `[tool.uv.sources]` entry
pointing at a **local path**, a dependency that isn't published to an
index yet:

```toml
[tool.uv.sources]
emergent-matter-materials = { path = "../emergent-matter-materials" }
```

Two different jobs need that sibling on disk, and they're solved in two
different places:

1. **Your own `ci.yml`** resolves the path in every job that runs
   `uv sync --locked`. You own that file, so check the sibling out there
   yourself, with a fine-grained PAT: `secrets.GITHUB_TOKEN` cannot read
   a *different* private repo. Note `actions/checkout`'s `path:` cannot
   escape `$GITHUB_WORKSPACE`, so your own repo has to be checked out
   into a subdirectory too, making the two siblings on the runner.
2. **The shared `version.yml`** hits it at the bump step, which runs
   `uv version --bump` and `uv lock`, both of which resolve
   `[tool.uv.sources]` and fail hard when the path is missing. You cannot add steps to a
   workflow you call by reference, so pass it in instead:

```yaml
  version:
    needs: ci
    uses: EmergentMatter/actions/.github/workflows/version.yml@v1
    with:
      actions-ref: v1
      sibling-repo: EmergentMatter/emergent-matter-materials
      sibling-ref: 9834441a6a4b95d6f491e129893fb37d5cecf320
      sibling-path: emergent-matter-materials
    secrets:
      sibling-token: ${{ secrets.MATERIALS_REPO_TOKEN }}
    permissions: { contents: write, pull-requests: write }
```

**Miss step 2 and the failure is quiet in the worst way:** CI stays
green, so the PR looks fine and merges. Then the `Version` run on
`main` dies at the bump step, the release PR is never opened, and no
release is ever cut. It repeats on every merge that has pending notes.

`sibling-ref` must be a **pinned SHA**, not a branch. Your `uv.lock`
embeds the sibling's resolved version, and `uv sync --locked` refuses to
proceed when that disagrees with what's checked out. So a floating ref
turns any merge into the sibling into a red run in your repo, on a
change nobody made there. Pin it, and bump the SHA and `uv.lock`
together in one commit. Keep the SHA in your `ci.yml` and the
`sibling-ref` here in step, too: they lock against the same thing.

All of it is temporary: when the dependency ships to an index, drop the
`[tool.uv.sources]` entry, the extra checkout in `ci.yml`, these three
inputs, the secret, and the PAT.

## Publishing to a package index (optional)

Off by default. Most repos stop at "GitHub Release with a wheel and
sdist attached" and never touch this. If you do want `version.yml` to
also publish to a package index over OIDC trusted publishing, follow the
steps below exactly. Getting the permission wrong does not fail the publish:
it stops every workflow in the repo from loading at all.

**The rule underneath everything here:** `permissions:` on a
`workflow_call` is a **ceiling for every job inside the called
workflow**, not a per-job grant, and it is checked when the workflow is
**parsed**, before any `if:` condition is ever evaluated. Two consequences
follow, both already handled on this repo's side:

- The shared `version.yml`'s publish job declares **no `permissions:` of
  its own** and inherits whatever the consumer's stub granted. A job
  requesting more than its caller granted fails the whole workflow to
  start, for every onboarded repo whether it publishes or not. It fails
  as `startup_failure`, with no further message available via the API.
- `environment:` is required on that job even when `publish` is `false`,
  because GitHub validates it at the same parse-time step, and an empty
  environment name is invalid. The shared workflow ships a non-empty
  default (`release`).

What is left on your side: **a repo enabling `publish: true` without also
granting `id-token: write` on its own stub gets no clean error. The job
runs, the OIDC token comes back empty, and the publish step fails.**


1. **Add `id-token: write` to the `version:` job's `permissions:`
   block on your own stub.** This is the only place it can come from now:
   the shared workflow deliberately doesn't grant it to itself.
2. **Pass `publish: true`.** Leave `environment:` unset to use the
   shipped default (`release`), or pass your own name if you already
   have a different [GitHub
   Environment](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
   you want to use.
3. **Create that GitHub Environment in your repo's own settings** (named
   `release` unless you overrode it in step 2), **and attach its PyPI
   trusted publisher** in PyPI's project settings, pointing at this
   repo, this workflow, and this environment name.

```yaml
  version:
    needs: ci
    uses: EmergentMatter/actions/.github/workflows/version.yml@v1
    with:
      actions-ref: v1
      publish: true
      # environment: release   (only needed if you want a name other
      #                          than the shipped default)
    permissions: { contents: write, pull-requests: write, id-token: write }
```

It's off by default on purpose: an unused elevated permission
(`id-token: write`) is worth avoiding, and publishing is a decision a
repo owner makes explicitly, not something that starts happening the
first time a release PR merges.

## The config block

One block, added to your existing `pyproject.toml`, copied from
[`templates/pyproject-snippet.toml`](../templates/pyproject-snippet.toml):
a `[tool.towncrier]` section (turns your `changelog.d/` notes into
`CHANGELOG.md` entries) and a `[tool.em-release]` section (tells the
release workflow every place your version string lives besides
`pyproject.toml` itself).

`onboard.py` also stamps a `templates_version` into that `[tool.em-release]`
block: the version of `EmergentMatter/actions` your copies of `templates/`
came from. It's how `scripts/sync.py` later tells a stale copy of a template
apart from one you've deliberately edited, so it can pull in later template
changes without clobbering your edits. See [tooling.md](tooling.md) for how
to run it.

That second section, `version_files`, is the part every repo gets
slightly wrong the first time. See the worked example right below.

## Worked example: emergent-matter-materials

`emergent-matter-materials` has its version recorded in three places, and
all three have to agree:

1. `pyproject.toml` -> `[project] version`
2. `src/emergent_matter_materials/__init__.py` -> `__version__`
3. `src/emergent_matter_materials/__init__.py` -> `__catalog_version__`

The third one is the interesting case. `__catalog_version__` is a *data*
version: it says which revision of the materials catalog you're getting,
separately from the Python package's own API version. In this repo the two
are documented as deliberately kept in lockstep (see the repo's own
`tests/test_catalog_versioning.py`, which asserts `__version__ ==
__catalog_version__` and explains why: an optimizer artifact that records
"ran against `emergent-matter-materials` `__version__=X`,
`__catalog_version__=Y`" with `X != Y` is a provenance-recording footgun
waiting to happen). So `__catalog_version__` belongs on the declared list,
right alongside `__version__`:

```toml
[tool.em-release]
version_files = [
  "src/emergent_matter_materials/__init__.py:__version__",
  "src/emergent_matter_materials/__init__.py:__catalog_version__",
]
```

This isn't hypothetical. A simulated `1.10.0 -> 1.11.0` bump run against a
copy of this repo updated exactly those three lines across the two files
above, and nothing else, and `emergent-matter-materials`'s own
pre-existing `tests/test_catalog_versioning.py` went **7 passed**
afterward. That suite was written by someone who'd never heard of this
tooling, so it passing on a synthetic bump is independent evidence that
`sync_version.py` does what CONTRACT.md's V4 requirement asks of it
("every declared location is written"). The same dry run also confirmed
`--check` catches real drift and nothing else: it correctly flagged a
hand-broken `__catalog_version__` while staying silent on the untouched,
still-correct `__version__`.

Note what's *not* on the list: `pyproject.toml`'s own `[project] version`.
That one is written by `uv version --bump <level>` as part of the release
workflow itself, not by `sync_version.py`. Never add it to
`version_files`.

### The distinction that makes this example worth reading

The reason `__catalog_version__` is listed here isn't "it's a version
string that exists in the file." It's listed because this repo made a
specific, documented decision that this particular data version **tracks**
the package version. That's a property of *this* repo's data, not a rule
about data versions in general.

If a different repo has a schema or data version that's meant to move
**independently** of the package version, bumped on its own schedule and
by its own rules, it must be **left off** `version_files`. The release
workflow only ever writes what's declared; anything not on the list is
untouched, on purpose (see `sync_version.py`'s V5 requirement in
CONTRACT.md: "some repos have deliberately independent data or schema
versions"). Declaring a version string just because you found it is the
mistake this example is here to prevent. Ask "should this move every time
the package version moves?" before adding an entry, not "is this a version
number?".

You can also get this wrong in the other direction, by declaring a
location whose `symbol = "..."` assignment isn't findable exactly as
written. When that happens the release workflow fails loudly
(`sync_version.py` exits 1 on a missing file or symbol) rather than
silently skipping it. That's deliberate too: a silent no-op here is the
exact failure mode this tool exists to prevent.

## The label

The changelog check's escape hatch is the `skip-changelog` label.
`onboard.py` creates it for you automatically, on a real (non-dry-run)
run, once it can read your repo's `owner/name` off `git remote`:

```
label `skip-changelog` ready
```

It runs `gh label create --force`, so re-running `onboard.py` later
doesn't fail on a label that already exists. If it can't detect the
slug, it says so instead of failing the whole run, and you create the
label by hand, matching what it would have created:

```bash
gh label create skip-changelog \
  --description "Exempts this PR from the changelog note requirement" \
  --color cfd3d7
```

**GitHub doesn't auto-create labels**, which is why this step exists at
all: `gh pr edit --add-label skip-changelog` (or the same thing clicked
in the GitHub UI) fails with `'skip-changelog' not found` until the label
actually exists in the repo. `onboard.py` closes that gap before anyone
needs the label.

Use the label for work that genuinely ships nothing user-visible: CI
tuning, a comment fix, a test-only change. It's the counterpart to the
rule `templates/CONTRIBUTING.md` already states: if a change isn't worth
a version, it isn't worth a note either.

**Why the workflow itself doesn't create the label:** `changelog-check.yml`
could create it the first time it's missing, but labels are the issues
API, and that would mean granting the workflow `issues: write`. That's
the workflow that triggers on `pull_request`: the one that can run
against a fork PR in a public repo, and the one this system deliberately
keeps narrowly scoped (see `CLAUDE.md`'s "no stub grants secrets to a
shared workflow," which applies the same reasoning to permissions
generally, not just secrets). Expanding its permissions to save one `gh label
create` call is a bad trade. `onboard.py`, running with *your* `gh`
credentials at setup time, does it instead.

## `changelog.d/`: nothing but notes and `.gitkeep`

You also need a `changelog.d/` directory at your repo root. Git doesn't
track empty directories, so commit it with a single `.gitkeep` placeholder
file. Nothing else goes in there until a contributor's first changeset.

**Never put a `README.md`, or anything else, in `changelog.d/`.**
`compute_bump.py` treats every non-dotfile `*.md` in that directory as a
changelog fragment, and hard-errors (exit 1) if the part before `.md`
isn't `major`/`minor`/`patch`. A `changelog.d/README.md` would break every
release computation in the repo. That strictness is deliberate:
CONTRACT.md's V3 requirement says never guess a bump level, so an
unrecognized file in that directory has to surface as a loud, red check,
not a silently wrong version number. Dotfiles are skipped, so `.gitkeep`
is safe; explaining what the directory is for belongs in this doc and in
`templates/CONTRIBUTING.md`, not in a file inside the directory itself.

The notes themselves are named `+<8 hex characters>.<level>.md`, e.g.
`+a1b2c3d4.minor.md`. `compute_bump.py` also parses the standard
towncrier issue-numbered form (`123.minor.md`), but `uv run changeset`
always generates the random `+hex` form; that's the one you'll actually
see, and it exists specifically so contributors never have to make a
naming decision or worry about a collision.

Anything in `changelog.d/` that isn't a properly-named note (a stray
file, a typo'd extension, an unrecognized level) fails the release job
loudly rather than being silently ignored. That is intended.

## Inserting the towncrier marker into an existing CHANGELOG.md

If your repo already has a hand-written `CHANGELOG.md` (most do by the
time they onboard), you don't rewrite it. Add one line above your existing
history:

```markdown
# Changelog

<!-- towncrier release notes start -->

## 1.10.0 (2026-06-22)
...your existing entries, unchanged, below...
```

`towncrier build` writes each new release's section directly below the
marker and never touches anything beneath it. Your hand-written history
stays exactly as it was. The marker string has to match
`start_string` in your `[tool.towncrier]` config byte-for-byte
(`templates/pyproject-snippet.toml` has the exact value to copy).

## Verifying it locally before you rely on it

`compute_bump.py` and `sync_version.py` are the two scripts CI runs on
your behalf, at PR-check time and at release time. They're plain Python
(stdlib only), so you can run the same checks yourself before you push,
from inside **your** repo (they read `changelog.d/` and `pyproject.toml`
relative to where you run them), pointing at this repo's copy of the
scripts:

```bash
cd ../your-repo

# What would the next release be, given whatever notes are sitting in
# changelog.d/ right now?
uv run python /path/to/actions/scripts/compute_bump.py --format json

# Does every declared version_files location currently agree with a
# given version? (--check writes nothing)
uv run python /path/to/actions/scripts/sync_version.py --version 1.10.0 --check
```

If `compute_bump.py` exits 1 with "unrecognized bump level," a note file
in `changelog.d/` doesn't end in `.major.md` / `.minor.md` / `.patch.md`.
Fix the filename rather than working around the check; this is usually a
stray file that shouldn't be there at all (see the `changelog.d/` section
above). If `sync_version.py --check` fails, either a declared file/symbol
doesn't exist as written, or you have a real drift to fix.

## Installed metadata versus the source tree

`emergent-matter-materials` also has
`test_installed_package_metadata_matches_module_version`, which checks
`importlib.metadata.version(...)`: the **installed** package's metadata,
not anything read from the source tree. That has two consequences worth
knowing before you bump a version locally:

- If the package isn't installed at all, that test **skips** rather than
  failing: a broken setup can look green.
- If it **is** installed at the old version and you then bump the tree
  (edit `pyproject.toml`, run `sync_version.py`) without re-syncing the
  environment, the test **fails** until `uv sync` (or `pip install -e .`)
  refreshes the installed metadata to match.

The rule this produces: **install/sync comes after checkout and before
tests, with no version-mutating step in between.** CI gets this for free,
because it runs from a clean checkout every time (CONTRACT.md's stub runs
your own CI before drafting anything). It's local runs and hand-ordered
scripts where the stale-metadata trap actually bites. If your own repo has
a test that asserts against installed metadata rather than the source
tree, the same ordering applies to you, not just to this example.

## Setting up branch protection

The six files and the label only *offer* the gate; a repo without
branch protection can still merge a PR with a failing (or missing)
check, or push straight to `main` and skip every check entirely. Turn on
protection for `main` (repo Settings -> Branches) with:

- **Require these status checks to pass before merging**: `changelog`,
  plus one context per job in your `ci.yml`.

  If you copied `templates/ci.yml`, that is exactly:

  ```
  test
  build
  changelog
  ```

  If you brought your own CI, use **your** job names. They are almost
  certainly different, and nothing requires you to rename them.
  `emergent-matter-materials` has a single job called `test`, so its list
  is:

  ```
  test
  changelog
  ```

  Take the names from a real pull request run rather than from this doc.
  `gh pr checks <PR>` prints contexts.

- **Require at least one approving review** before merging.
- **Do not allow direct pushes to `main`** (no bypassing via a force
  push or an un-reviewed merge).

All of them are bare job ids: the CI ones are your `ci.yml`'s job names,
and `changelog` is the job id in `changelog-check.yml`'s stub.

**`lint` is deliberately absent**, even though `templates/ci.yml` ships a
lint job. It gets added later, in step 3 of the staged rollout below, not
now. If you brought your own CI and its linting already passes, you can
skip straight to that step; the staging exists for repos turning a linter
on over an established codebase, not for repos that are already clean.

### Staging the lint rollout

`templates/ci.yml` ships the lint job with `continue-on-error: true`. That
line is often misread as "lint can't block anything yet." It cannot do
that, and the difference is worth being exact about, because it decides
whether the rollout works:

| Level | On a lint failure |
|---|---|
| Job conclusion | `failure` |
| Workflow **run** conclusion | `success`: the only thing `continue-on-error` changes |
| **Check run** conclusion | `failure`: what branch protection matches |

The check still fails. So adding `lint` to required contexts blocks PRs
immediately, `continue-on-error` or not. That's exactly the pile-of-
unrelated-findings problem the staging exists to avoid.

What that line actually buys is that a lint failure doesn't fail the whole
run, so it doesn't take down `version.yml`'s `needs: ci` and stall a
release while the backlog is still being cleared.

So the two levers move together:

1. **Onboard.** Keep `continue-on-error`, require only `test` / `build` /
   `changelog`. Lint runs and is visible to anyone who looks, but gates
   nothing.
2. **Clear the backlog.** One dedicated PR that fixes the existing
   findings and does nothing else, so the diff is reviewable as "lint
   fixes" rather than tangled into a feature change.
3. **Enforce.** Delete `continue-on-error` from the lint job *and* add
   `lint` to the required contexts. Both, in the same window.

#### Don't do this by hand

`scripts/lint_gate.py` moves both halves together, so the two can't drift:

```bash
# from the target repo's root (or pass --repo-path)
python3 /path/to/actions/scripts/lint_gate.py status
python3 /path/to/actions/scripts/lint_gate.py on     # step 3
python3 /path/to/actions/scripts/lint_gate.py off    # back to step 1
```

`status` reports both halves and the state they imply (`OFF`, `ON`, or
`INCONSISTENT`), and exits non-zero on `INCONSISTENT`, so it works as a
check in its own right. The two inconsistent states are reported
differently, because they fail in opposite directions.

`on` **refuses if the repo still has lint findings**, since enforcing over
a dirty backlog blocks every open PR with errors unrelated to its changes.
That is step 2 made mandatory rather than merely advised. `--skip-backlog-check`
overrides it, and you should expect to regret that.

The file edit is left uncommitted deliberately: commit it and open a PR.
Branch protection is updated immediately, so until that PR merges the repo
and its protection disagree.

Doing step 3 by halves is worse than not doing it. Removing the line
without adding the context leaves lint unenforced on PRs while newly able
to stall a release; adding the context without removing the line enforces
on PRs but still lets a lint failure sail through the release path.

### The name you see is not the name you type

The Checks tab shows the workflow's `name:` prepended to the context, and
branch protection matches the context only. `Changelog / changelog` in the
UI is the context `changelog`. Read them with `gh pr checks <PR>`, which
prints contexts, rather than copying off the UI.

If you ever rename a job or change a stub's shape, swap the required
context in the same window. Don't let the two drift, or `main` blocks on
a context that can no longer be produced.

**Still get these from a pull request run, not a push-to-main run:
they are not the same strings.** `ci.yml` (per `templates/ci.yml`)
triggers directly on `pull_request` as well as via `workflow_call`, and
those two triggers display differently. On a PR it runs standalone, so
GitHub shows its job names bare: `lint`, `test`, `build`. On a push to
`main` it runs wrapped inside `version.yml`'s own `ci:` job via
`uses: ./.github/workflows/ci.yml`, and GitHub prefixes those same
checks: `ci / lint`, `ci / test`, `ci / build`. Those prefixed names
**never appear on a pull request run** and so can never satisfy a
required check there. Anyone who copies check names off a push run
instead of a PR run ends up with required contexts that no PR can
ever produce, and `main` is blocked from merging anything, permanently,
until someone notices and fixes the list.

The exact names above assume `templates/ci.yml` and
`templates/stub-changelog-check.yml` copied in as-is (job ids `lint` /
`test` / `build` / `changelog`). If your repo already had its own CI
with different job names before onboarding, use those names instead.
Same bare-vs-prefixed rule, different strings.

## Releasing under branch protection

`templates/CONTRIBUTING.md` says merging the release PR is the release,
and that's still true in spirit. But under branch protection it takes
three manual actions, not one click, and it's worth knowing that going
in rather than discovering it mid-release:

1. **Close the release PR, then immediately reopen it.** It was opened by
   `GITHUB_TOKEN`, which doesn't trigger further workflow runs on its own
   PR. See `templates/CONTRIBUTING.md`'s section on this. Its checks
   show as not-run until you do this.
2. **Get it approved** (or merge it yourself if you're listed as a
   code owner for this repo, in which case CODEOWNERS review rules let
   you merge without waiting on someone else).
3. **Merge.** This is the moment that actually ships: it pushes the
   release tag, and in the same `version.yml` run, builds and publishes
   it.

With protection configured, a pull request without a changelog note is
refused with *"the base branch policy prohibits the merge"* until the
`skip-changelog` label is applied. Merging the release PR tags the version,
attaches a wheel and sdist to the GitHub Release, and leaves `changelog.d/`
holding only `.gitkeep`.

## Prove the gate actually works, before you trust it

Do this on the onboarding PR itself. It costs one push and it is the only
step here that can tell you the difference between a working gate and one
that is broken open, because both render as a green checkmark.

1. **Open the onboarding PR without a changelog note.** The `changelog`
   check must go **red**, with
   `No changelog note was added under 'changelog.d/' by this PR.`
   A green check here does not mean you got away with it; it means the gate
   is not wired up, and no future PR will be stopped either.
2. **Add a note** with `uv run scripts/changeset.py`. It must go green, and
   the log must show `compute_bump.py` returning a level: that is the
   grammar validation running, not just the file-exists check.
3. **Apply `skip-changelog`.** It must pass via the exemption path and log
   the reason.

Then confirm the repo looks right from the outside:

```bash
python3 /path/to/actions/scripts/fleet_status.py --repo <owner>/<name>
```

Exit 0 means no `broken` or `warn` finding across any of its checks (see
[tooling.md](tooling.md#fleet_statuspy) for the full list). An `info`
finding, like a stamp-free repo, can still show up in the output without
affecting the exit code. Run it again periodically. Files 4-6 are
copies, so drift starts accumulating from the day you onboard.

Skipping step 1 is the tempting one, because everything already looks
green. That is exactly the state a gate that checks nothing produces.

## After onboarding

Read [`templates/CONTRIBUTING.md`](../templates/CONTRIBUTING.md). That's
what you just copied into the repo, and it's what every future contributor
(including you) will actually follow day to day. In particular, don't
skip its section on the release PR's checks showing as "not run": that's
expected, not broken, and the fix (close, then immediately reopen the PR)
is easy to miss if you haven't seen it before.

Onboarding copies `templates/` in as a snapshot; it does not stay current on
its own. When something in `templates/` changes later, run `scripts/sync.py`
against this repo to pull the change in. See [tooling.md](tooling.md#syncpy).
