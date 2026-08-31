# The maintenance tools

The scripts below run **from a clone of this repo** against a target
repo. None of them are copied into consuming repos; they are yours, not
theirs.

`compute_bump.py` and `sync_version.py` are the exception. They run from
*inside* a target repo instead, and
[onboarding.md](onboarding.md#verifying-it-locally-before-you-rely-on-it)
covers them.

They need `gh` authenticated as someone with admin on the target repo, and
`uv` for the interpreter (`tomllib` needs Python 3.11+, and system `python3`
on macOS is older than that, so use `uv run python`).

| Script | When |
|---|---|
| [`onboard.py`](#onboardpy) | bringing a new repo into release control |
| [`sync.py`](#syncpy) | pulling template changes into a repo already onboarded |
| [`fleet_status.py`](#fleet_statuspy) | routinely, to catch drift |
| [`lint_gate.py`](#lint_gatepy) | turning a repo's lint gate on or off |
| [`verify_wheel.py`](#verify_wheelpy) | never directly; the `verify-wheel` action calls it |

---

## `onboard.py`

Does the mechanical half of [onboarding](onboarding.md): every file
[`templates/manifest.toml`](../templates/manifest.toml) declares, plus
`changelog.d/`, the config block, the towncrier marker, and the
`skip-changelog` label. Idempotent: run it twice and the second run
reports what is already correct.

Start by asking what it would do:

```bash
uv run python scripts/onboard.py --repo-path ../some-repo --dry-run
```

It stops and prints candidates rather than declaring `version_files`:

```
Stopping: version_files not declared.

Candidates found (assignments matching the current version):
    --version-file src/pkg/__init__.py:__version__
    --version-file src/pkg/__init__.py:__schema_version__
```

**Do not pass all of them reflexively.** For each, ask: *should this move
every time the package version moves?* A schema or data version on its own
release schedule must be left off: the release workflow writes only what
is declared, so leaving it off is what keeps it independent. See the
worked example in [onboarding.md](onboarding.md), which exists to prevent
exactly this mistake.

Then run it for real:

```bash
uv run python scripts/onboard.py --repo-path ../some-repo \
  --version-file src/pkg/__init__.py:__version__
```

Use `--no-version-files` if the version genuinely only lives in
`pyproject.toml`.

### What it will not do

- **Overwrite an existing `ci.yml`.** A repo that already has CI keeps it.
- **Set branch protection.** The contexts have to be read off a real pull
  request run, not predicted. It prints that as a next step.
- **Guess `version_files`.** Above.

### What it does do, that might surprise you

- **Adds `workflow_call:` to an existing `ci.yml`, additively.** That one
  is not left manual because forgetting it does not degrade gracefully:
  `version.yml` reaches the file with `uses: ./.github/workflows/ci.yml`,
  and a workflow that is not `workflow_call`-able fails at **parse time**,
  so every push to `main` errors before a job starts. The inline
  `on: [push, ...]` form has too many shapes to rewrite safely, so that
  one is handed back to you.
- **Creates the `skip-changelog` label itself**, on a real (non-dry-run)
  run where it can detect the repo's `owner/name` from `git remote`. It
  runs `gh label create --force`, so re-running it is safe. If the slug
  can't be detected, it says so and leaves the label for you to create by
  hand. See [onboarding.md](onboarding.md#the-label) for that command.
- **Turns on GitHub's private vulnerability reporting**, but only for a
  *public* repo. The API 404s on a private one, and the feature doesn't
  exist there. The `SECURITY.md` it installs points a researcher at the
  button this enables; a repo onboarded private keeps
  that promise unmet until someone turns it on after the repo goes public.
  `onboard.py` prints that as a reminder when it skips it.

---

## `sync.py`

`onboard.py`'s copy is a snapshot taken once. Nothing else keeps it current:
`templates/` in this repo can move on without an onboarded repo ever finding
out, until something re-checks it. `sync.py` is that re-check, done for real:

```bash
uv run python scripts/sync.py --repo-path ../some-repo --dry-run
```

It three-way compares each `managed` template in
[`templates/manifest.toml`](../templates/manifest.toml) against the
`templates_version` the repo last synced from, so a stale copy is updated
silently and a deliberate local edit is left alone and reported. See the
`sync.py` section of [CONTRACT.md](../CONTRACT.md) for the manifest format,
the outcome for each combination, and why the recorded base version is what
makes the distinction possible at all.

```
sync.py --repo-path ../repo [--dry-run] [--ours|--theirs] [--only DEST] [--json]
```

- `--dry-run`: report what would change, write nothing.
- `--ours` / `--theirs`: resolve every conflict this run without prompting,
  by keeping the repo's version or taking the template's. Mutually
  exclusive.
- `--only DEST`: restrict the run to the entries whose `dest` matches;
  repeatable, for pulling in one or a few files' changes without touching
  the rest.
- `--json`: machine-readable output, for scripting.

A `seed-once` entry is never touched: not updated, not reported as drift.
The repo owns it from the moment `onboard.py` seeds it.

Exit code follows the same convention as `lint_gate.py status`: 0 means done
(nothing pending), 1 an error, 2 that at least one entry is still pending:
a `--dry-run` or a skipped prompt both count, even if other entries in the
same run updated cleanly. A CI job calling this non-interactively (no
`--ours`/`--theirs`) should treat 2 the same as 1: something still needs a
human to look at it.

---

## `fleet_status.py`

Sweeps every repo pinned to this one and reports drift. Onboarding happens
once per repo; `templates/` is **copied** and never re-synced, so drift
starts accumulating the day a repo onboards and nothing else watches it.

```bash
uv run python scripts/fleet_status.py                    # discover consumers
uv run python scripts/fleet_status.py --repo owner/name  # just these
uv run python scripts/fleet_status.py --quiet            # hide info findings
uv run python scripts/fleet_status.py --json
```

Reads over the API, with no clones, and exits non-zero (`1`) if any repo has a
`broken` or `warn` finding, so it can run on a schedule rather than being
remembered. An `info`-only report still exits `0`; use `--json` if you need
to see info findings without deciding they're worth a nonzero exit.

| Check | Severity | What it means |
|---|---|---|
| `stub` | broken | still on the `workflow_call` path removed in v1.1.0, so it 404s on every PR |
| `workflow_call` | broken | `ci.yml` not callable; `version.yml` fails at parse time on every push to main |
| `gate` | broken (else info) | the halves of the `lint` gate disagree, per `lint_gate.py`. Reported at `info` when they agree |
| `format_gate` | broken (else info) | same, for the `format` job. `info` if the repo has no `format:` job yet |
| `typecheck_gate` | broken (else info) | same, for the `typecheck` job |
| `contexts` | warn | required checks missing, or no branch protection at all |
| `verify` | warn | build job runs `uv build` with no `verify-wheel`; a wheel that builds and installs nothing would pass |
| `templates` | warn (info if deliberate) | a managed template (any entry in `templates/manifest.toml`) differs from its source. `info` only when `templates_version` is current, meaning the diff is a known, deliberate edit rather than drift |
| `stamp` | info / warn | the `templates_version` provenance stamp against this repo's newest release tag: `info` if there's no stamp at all (onboarded before it existed), `warn` if it names an unrecognised tag or is behind |
| `security` | warn | `SECURITY.md` documents private vulnerability reporting but the repo has it turned off. Not applicable to private repos, which can't have the feature at all |
| `tooling` | warn | `pyproject.toml` is missing `[tool.mypy]` or `[tool.pytest.ini_options]`; existence only, not exact content (see `templates/pyproject-snippet.toml`) |
| `pins` | warn | an action pinned to a version targeting Node 20 |
| `ruff_config` | warn | `ruff-base.toml` is installed but inert: no `ruff.toml` extends it, or `pyproject.toml` still carries an inline `[tool.ruff]` section |
| `ts_job` | info | a Bun package is present but `ci.yml` has no `ts:` job |
| `naming` | info | `ci.yml` has no `name:`, so checks display the file path |

Severity is load-bearing. A repo on the removed stub path is broken *now*; a
missing `name:` is cosmetic. Reporting them identically would guarantee the
first gets lost among the third, so they roll up separately and broken repos
sort first.

Note `lint` is **not** expected among required contexts. A repo without it is
correctly configured, not incomplete. See below.

---

## `lint_gate.py`

Stages `lint`, `format`, or `typecheck` via `--job` (default: `lint`). The
optional `ts` job in `templates/ci.yml` is staged the same way but isn't
covered here: its working directory isn't the repo root, so it's flipped
by hand.

The gate is a setting in the repo and a setting on GitHub, and they must
agree:

1. `continue-on-error: true` on `ci.yml`'s job: a file in the repo
2. the job's name in the required status checks: a GitHub setting

Flipping one without the other is not a half-measure, it is a broken state,
and the broken states fail in **opposite directions**:

| State | Consequence |
|---|---|
| line removed, context absent | the job gates nothing on PRs, but its failure now fails the run and can stall `version.yml`'s `needs: ci` mid-release |
| context added, line present | the job gates PRs, but its failure still sails through the release path |

```bash
cd ../some-repo
uv run --project ../actions python ../actions/scripts/lint_gate.py status
uv run --project ../actions python ../actions/scripts/lint_gate.py on
uv run --project ../actions python ../actions/scripts/lint_gate.py off

# format / typecheck: same commands, --job goes BEFORE the subcommand
uv run --project ../actions python ../actions/scripts/lint_gate.py --job format status
uv run --project ../actions python ../actions/scripts/lint_gate.py --job typecheck on
```

`status` exits non-zero on `INCONSISTENT`, so it works as a check by itself.
Each inconsistent state gets its own message, because one message for both
would misdirect whoever is reading it.

`on` **refuses if the repo still has findings for that job.** Enforcing over
a dirty backlog blocks every open PR with errors unrelated to its own
changes: the problem the staged rollout exists to avoid. `--skip-backlog-check`
overrides it, and you should expect to regret that. Backlog command per job:
`ruff check .` (lint), `ruff format --check .` (format), `mypy src` (typecheck).

The file edit is left uncommitted deliberately: commit it and open a PR.
Branch protection is changed immediately, so until that PR merges the repo
and its protection disagree.

### Why `continue-on-error` is not the lever

It does not make a check non-blocking. Verified on a real run:

| Level | On a failure |
|---|---|
| Job conclusion | `failure` |
| Workflow **run** conclusion | `success`: the only thing `continue-on-error` changes |
| **Check run** conclusion | `failure`: what branch protection matches |

What that line buys is that a failure does not fail the whole run, so it
cannot take down `version.yml`'s `needs: ci` and stall a release while the
backlog is still being cleared.

---

## `verify_wheel.py`

Called by the `verify-wheel` composite action; you do not run it directly.
It exists as a script rather than inline YAML so it can be unit-tested.

`uv build` exiting 0 does not mean the wheel is usable. This hatchling config
builds **successfully**:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/goodpkg"]
exclude = ["*.py"]
```

and produces a wheel containing nothing but its `.dist-info` metadata. A
`packages` entry naming a directory that does not exist also builds without
complaint.

So the action installs the wheel into a clean environment and imports every
top-level module the distribution declares. **Running from outside the
checkout is the load-bearing part**: from the repo root, `import
yourpackage` resolves against the working tree and passes no matter what the
wheel holds, which is the bug being hunted.

Consumers get it via `templates/ci.yml`:

```yaml
      - run: uv build
      - uses: EmergentMatter/actions/verify-wheel@v1
```

A repo onboarded before v1.3.0 has its own copy of `ci.yml` and will not have
this step. `fleet_status.py` flags that as `verify`.
