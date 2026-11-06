# Changelog

Releases up to and including 1.15.0 are listed on the
[GitHub Releases page](https://github.com/EmergentMatter/actions/releases).
Later releases are recorded here.

<!-- towncrier release notes start -->

## 1.20.0 (2026-11-06)

### Minor

- Closed-tier repos get their own `version.yml`, `build-release.yml`, `ci.yml` and `dependabot.yml`: they sign in to the private package index (new `codeartifact-read` action), grant `id-token: write` where the sign-in needs it, check lock sources, run a `floor` job, and onboard with `--publish` when they release to the private index. A tiered manifest entry now replaces the untiered entry for the same destination.
- New `check-duplicate-modules` action and `scripts/check_duplicate_modules.py`: a companion package fails when a module duplicates the public package it extends, by relative path or by a class or function name, with an allowlist for a quarterly move. The closed-tier CI template runs it in `test`.
- New `check-lock-sources` action and `scripts/check_lock_sources.py`: fail when an organization package was locked from the public index (even if declared), from an index the project does not declare, or, for a closed package, from anything but the private index. Names outside the prefix can be listed. It also judges `uv pip compile` output.
- `em-dev token` prints the shell lines that sign uv in to a private package index from an AWS SSO profile. The domain, its owner and the region come from a flag, an environment variable or an organization variable; nothing is built in.

### Patch

- `em-dev` passes every index the project declares on its `uv pip install` command line, so a published package that lives only on an `explicit = true` index is found even though `--no-sources` ignores the source that pins it. Because that makes the index an ordinary one, it first resolves the install and refuses an organization package that would come from the public index or an undeclared one. A named index is passed as `name=url`, so credentials keyed on the name keep working in tool mode too.
- `verify-wheel` installs the built wheel with every index the project declares, so a wheel whose dependencies live on a declared index no longer needs `UV_INDEX` set on the job. It resolves first and refuses an organization package that would come from the public index, an undeclared index, or (for a closed package) anything but the private index.


## 1.19.0 (2026-11-05)

### Minor

- A package whose tarball is built output can now be published without the publish job running its install or build. The caller builds and packs in its own job with no credential and uploads the tarball, and a new `npm-tarballs-artifact` input on version.yml and build-release.yml makes the publish job verify that tarball (name, version equal to the tag, files list, plain members in normal form, each named once) and publish a canonical tarball rebuilt from the bytes it checked. npm now runs in a temporary directory with a minimal environment instead of the caller's checkout, so a committed .npmrc cannot redirect the token, and publishConfig may hold only access and tag, since npm applies any other key (proxy, strict-ssl, ca) it finds there. Member names are limited to ASCII and may not collide after case folding. Without the input nothing else changes.
- A repo with a package.json and no pyproject.toml now versions and releases through version.yml and build-release.yml, and the changelog-check action works there. The version is read from and written to package.json, and the publish tier is read from its em-release block. Repos with a pyproject.toml are unchanged. Separately, publishing an npm package now fails before any upload if a plain path in its `files` list is not in the tarball, for example a build output that was never built.


## 1.18.0 (2026-10-30)

### Minor

- The release workflows take an optional `npm-packages` input: with `publish-target: codeartifact`, each listed npm package is checked and packed before any upload (manifest rules, release version, and byte-identity with a built wheel when the package declares it), then published before the wheels with a scope-only `.npmrc` in the runner temp dir and the token only in an environment variable. Publishing is safe to re-run: the same version with the same content is skipped, with different content it fails. `node-version` pins the Node that does it. The version and build-release stubs carry a commented `npm-packages` example.


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
