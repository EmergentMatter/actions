# Changelog

Releases up to and including 1.15.0 are listed on the
[GitHub Releases page](https://github.com/EmergentMatter/actions/releases).
Later releases are recorded here.

<!-- towncrier release notes start -->

## 1.17.0 (2026-10-25)

### Minor

- `em-dev` finds a package that is a member of a sibling checkout's uv workspace (for example `emergent-matter-sdm-tools` inside `../emergent-matter-sdm-engine/packages/sdm-tools`) and installs it editable from there, reporting which workspace it came from. A folder of the package's own name still wins, and a package found nowhere stays on the published version.


## 1.16.0 (2026-10-20)

### Minor

- A uv workspace can release all its packages together: the new opt-in `all-packages` input on `version.yml`, `build-release.yml` and the `verify-wheel` action builds every member, bumps every member to the same version (failing if any ends up elsewhere), moves exact (`==`) pins between members to that version (ranges are untouched), and verifies every wheel. The static index refuses a multi-package build.

### Patch

- The shared scripts checkout is moved out of the consumer workspace before anything builds, so a release sdist no longer contains an `.em-actions/` directory, and the build fails if one ever does. `build-release.yml` now installs Python and uv in its release job before building, which it never did, and a test fails when any job runs uv before setting it up.


## 1.15.1 (2026-10-07)

### Patch

- Fleet status measures how far behind a repo is against the newest release `v1` contains, not the newest tag.
