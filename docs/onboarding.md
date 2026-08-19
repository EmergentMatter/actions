# Onboarding a repo

Onboarding a repo into release control is **four files and a config
block**. This walks through all of them, using
[`emergent-matter-materials`](https://github.com/EmergentMatter/emergent-matter-materials)
as the worked example — it's the hard case, because it has **three**
version strings that have to move in lockstep, not one.

If you haven't read the top-level concept yet, read the [README](../README.md)
first — this doc assumes you know what a changelog note is and what the
release PR does.

## Before you start

You need a repo that already has its own CI workflow at
`.github/workflows/ci.yml` (unit tests, lint — whatever "this code is good"
means for your repo), callable via `workflow_call`. Release control runs
your CI first and only drafts a release if it passes — see
`version.yml behaviour` in [CONTRACT.md](https://github.com/EmergentMatter/actions/blob/v1/CONTRACT.md)
for why. If you don't have one yet, that's a prerequisite to sort out
before wiring this up, not a step this doc covers.

## The four files

| # | File | What it does |
|---|---|---|
| 1 | `.github/workflows/changelog-check.yml` | On every PR: fails the PR if it's missing a `changelog.d/` note and doesn't carry the `skip-changelog` label. |
| 2 | `.github/workflows/version.yml` | On push to `main`: runs your own CI first, then computes the next version from pending notes and opens/updates the release PR. |
| 3 | `.github/workflows/build-release.yml` | On the release tag: builds the wheel/sdist and attaches them to a GitHub Release. |
| 4 | `CONTRIBUTING.md` | The contributor-facing instructions — how to add a note, what the three levels mean, the release-PR checks caveat. |

Files 1–3 are stub workflows: a few lines each that just point at the
reusable workflow this repo hosts, pinned to `@v1`. Copy them from
`EmergentMatter/actions`'s `templates/` directory into your repo's
`.github/workflows/`. File 4 is a direct copy of
[`templates/CONTRIBUTING.md`](../templates/CONTRIBUTING.md) into your repo
root, unmodified — it's generic, not repo-specific.

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
    permissions: { contents: write, pull-requests: write }
                              # deliberately NO secrets: inherit
```

Pin `@v1`, never a branch — see CONTRACT.md's non-negotiables.

Notice `secrets: inherit` sits on the `ci:` job, not on `version:`, and
it's conditional even there:

- **On `ci:`, only if your CI needs a secret.** A local `uses:` call
  doesn't inherit secrets implicitly either — `workflow_call` never does,
  local or remote. If your own `ci.yml` needs a repo secret to run (the
  concrete example: `sdm-core`'s CI checks out the private sibling
  `emergent-matter-materials`, which needs a `MATERIALS_REPO_TOKEN`),
  the `ci:` job has to say so explicitly. If your CI needs no secrets,
  **omit the line entirely** rather than adding it out of habit.
- **On `version:`, deliberately absent — this is a security decision,
  not an oversight.** None of the three shared workflows reads
  `secrets.*`; they use only the auto-injected `github.token`, and
  `build-release.yml` publishes over OIDC trusted publishing, which is
  secret-less by design. `EmergentMatter/actions` is a **public** repo
  pinned by a **movable** `v1` tag. Granting it your org's secrets via
  `secrets: inherit` would make that tag equivalent to secret access
  across every repo that pins it — so don't add it back as boilerplate,
  even though it looks like it's "missing" next to the `ci:` job above it.

## The config block

One block, added to your existing `pyproject.toml`, copied from
[`templates/pyproject-snippet.toml`](../templates/pyproject-snippet.toml):
a `[tool.towncrier]` section (turns your `changelog.d/` notes into
`CHANGELOG.md` entries) and a `[tool.em-release]` section (tells the
release workflow every place your version string lives besides
`pyproject.toml` itself).

That second section — `version_files` — is the part every repo gets
slightly wrong the first time, so the rest of this doc is the worked
example.

## `changelog.d/`: nothing but notes and `.gitkeep`

You also need a `changelog.d/` directory at your repo root. Git doesn't
track empty directories, so commit it with a single `.gitkeep` placeholder
file — nothing else goes in there until a contributor's first changeset.

**Never put a `README.md`, or anything else, in `changelog.d/`.**
`compute_bump.py` treats every non-dotfile `*.md` in that directory as a
changelog fragment, and hard-errors (exit 1) if the part before `.md`
isn't `major`/`minor`/`patch`. A `changelog.d/README.md` would break every
release computation in the repo. That strictness is deliberate --
CONTRACT.md's V3 requirement says never guess a bump level, so an
unrecognized file in that directory has to surface as a loud, red check,
not a silently wrong version number. Dotfiles are skipped, so `.gitkeep`
is safe; explaining what the directory is for belongs in this doc and in
`templates/CONTRIBUTING.md`, not in a file inside the directory itself.

The notes themselves are named `+<8 hex characters>.<level>.md` — e.g.
`+a1b2c3d4.minor.md`. `compute_bump.py` also parses the standard
towncrier issue-numbered form (`123.minor.md`), but `uv run changeset`
always generates the random `+hex` form; that's the one you'll actually
see, and it exists specifically so contributors never have to make a
naming decision or worry about a collision.

**Gotcha to expect, not a bug to work around:** anything you drop into
`changelog.d/` that isn't a properly-named note — a stray file, a typo'd
extension, an unrecognized level — fails the release job loudly rather
than being silently ignored. That's the intended behavior.

## Worked example: emergent-matter-materials

`emergent-matter-materials` has its version recorded in three places, and
all three have to agree:

1. `pyproject.toml` → `[project] version`
2. `src/emergent_matter_materials/__init__.py` → `__version__`
3. `src/emergent_matter_materials/__init__.py` → `__catalog_version__`

The third one is the interesting case. `__catalog_version__` is a *data*
version — it says which revision of the materials catalog you're getting,
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

This isn't hypothetical. A simulated `1.10.0 → 1.11.0` bump run against a
copy of this repo updated exactly those three lines across the two files
above — and nothing else — and `emergent-matter-materials`'s own
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
workflow itself, not by `sync_version.py` — never add it to
`version_files`.

### The distinction that makes this example worth reading

The reason `__catalog_version__` is listed here isn't "it's a version
string that exists in the file." It's listed because this repo made a
specific, documented decision that this particular data version **tracks**
the package version. That's a property of *this* repo's data, not a rule
about data versions in general.

If a different repo has a schema or data version that's meant to move
**independently** — bumped on its own schedule, by its own rules, not tied
to the package's semver — it must be **left off** `version_files`. The
release workflow only ever writes what's declared; anything not on the
list is untouched, on purpose (see `sync_version.py`'s V5 requirement in
CONTRACT.md: "some repos have deliberately independent data or schema
versions"). Declaring a version string just because you found it is the
mistake this example is here to prevent — ask "should this move every time
the package version moves?" before adding an entry, not "is this a version
number?".

If you get this wrong in the other direction — you declare a location and
its `symbol = "..."` assignment isn't findable exactly as written — the
release workflow fails loudly (`sync_version.py` exits 1 on a missing file
or symbol) rather than silently skipping it. That's deliberate too: a
silent no-op here is the exact failure mode this tool exists to prevent.

## Inserting the towncrier marker into an existing CHANGELOG.md

If your repo already has a hand-written `CHANGELOG.md` (most do by the
time they onboard), you don't rewrite it. Add one line above your existing
history:

```markdown
# Changelog

<!-- towncrier release notes start -->

## v1.10.0 — 2026-06-22 — consumer-safe structural strength resolver
...your existing entries, unchanged, below...
```

`towncrier build` writes each new release's section directly below the
marker and never touches anything beneath it — your hand-written history
stays exactly as it was. The marker string has to match
`start_string` in your `[tool.towncrier]` config byte-for-byte
(`templates/pyproject-snippet.toml` has the exact value to copy).

## Verifying it locally before you rely on it

Both scripts are plain Python (stdlib only) and runnable outside CI, so
you can dry-run the wiring before you push:

```bash
# What would the next release be, given whatever notes are sitting in
# changelog.d/ right now?
uv run python scripts/compute_bump.py --format json

# Does every declared version_files location currently agree with a
# given version? (--check writes nothing)
uv run python scripts/sync_version.py --version 1.10.0 --check
```

If `compute_bump.py` exits 1 with "unrecognized bump level," a note file
in `changelog.d/` doesn't end in `.major.md` / `.minor.md` / `.patch.md` --
fix the filename, don't work around the check (see the `changelog.d/`
section above — this is usually a stray file that shouldn't be there at
all). If `sync_version.py --check` fails, either a declared file/symbol
doesn't exist as written, or you have a real drift to fix.

## Gotcha: installed metadata vs. the source tree

`emergent-matter-materials` also has
`test_installed_package_metadata_matches_module_version`, which checks
`importlib.metadata.version(...)` — the **installed** package's metadata
-- not anything read from the source tree. That has two consequences worth
knowing before you bump a version locally:

- If the package isn't installed at all, that test **skips** rather than
  failing — a broken setup can look green.
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

## After onboarding

Read [`templates/CONTRIBUTING.md`](../templates/CONTRIBUTING.md) — that's
what you just copied into the repo, and it's what every future contributor
(including you) will actually follow day to day. In particular, don't
skip its section on the release PR's checks showing as "not run" — that's
expected, not broken, and the fix (close, then immediately reopen the PR)
is easy to miss if you haven't seen it before.
