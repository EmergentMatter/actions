# The EmergentMatter style guide

This is the org's style guide. It's a `managed` template (see
`templates/manifest.toml` in `EmergentMatter/actions`): a rule change here
means editing the template and cutting a tag, and `sync.py` rolls it out.
A hand-edited copy in your repo is reported by `fleet_status.py`, never
silently overwritten.

It codifies what the strongest repos in the org already do. Most of this
should feel familiar if you've read good code here before.

- [Python style](#python-style)
- [Naming](#naming)
- [Docstrings and comments](#docstrings-and-comments)
- [Typing](#typing)
- [Python version policy](#python-version-policy)
- [Testing](#testing)
- [TypeScript](#typescript)
- [Documentation](#documentation)
- [Open-source hygiene](#open-source-hygiene)

## Python style

- **`uv` is the only entry point.** `uv sync`, `uv run pytest`, `uv run
  ruff check .`. No bare `pip`, no `poetry`, no `python -m venv` by hand.
- **src-layout + hatchling.** `src/<package>/`, not a top-level package
  directory.
- **`uv.lock` is committed**, in every repo, never gitignored. CI syncs
  with `uv sync --locked`, so a stale lockfile fails loudly instead of
  silently resolving something different than what's checked in.
- **Ruff owns everything mechanical**: linting and formatting both. Humans
  don't argue about formatting in review. Every repo carries two files:
  - `ruff-base.toml` -- managed, the shared ruleset. Don't edit it locally;
    edit the template in `EmergentMatter/actions` instead.
  - `ruff.toml` -- seed-once, yours from onboarding. `extend =
    "ruff-base.toml"` plus whatever this repo needs on top (see the file's
    own comments for the shape of a per-file ignore).
  - Line length is 100, everywhere. Target Python is `py313` in the base;
    a repo on the 3.11 floor (see [Python version
    policy](#python-version-policy)) overrides `target-version` in its own
    `ruff.toml`.
  - Every mechanical reformat commit belongs in `.git-blame-ignore-revs`,
    so `git blame` still points at the change that mattered.
  - A catalog or data module with hand-aligned literals opts out per-file
    with `# fmt: off` / `# fmt: on` around the literal. Never a repo-wide
    `[format]` override.
- **`from __future__ import annotations`** at the top of every module.
  The one documented exception is a Blender `PropertyGroup` module, where
  the framework itself requires evaluated annotations at class-definition
  time -- state the reason in a comment at the import, don't just drop it
  silently.
- **Lazy imports for heavy or host-bound dependencies** (`jax`, `bpy`,
  `openvdb`, and similar): import them at the point of use, not at module
  load time, and raise a clear error that tells the user how to get the
  dependency if the import fails. Keeps a boot path free of dependencies
  it doesn't actually need yet, and keeps that testable (a test asserting
  the boot path stays free of a given import is the reference pattern).
- **Frozen dataclasses with `__post_init__` validation** are the house
  schema idiom. Validate at the boundary, and name the class, the field,
  the expected value, and the actual value in the error message. A new
  mutable dataclass in schema code needs a stated reason in review.
- **CLI scripts** follow this repo's own shape: `argparse`, `def
  main(argv: list[str] | None = None) -> int`, errors printed to stderr
  with an `error:` prefix, and `return 1` -- never a silent skip on
  something ambiguous.

## Naming

- **The typed-prefix convention on primitive scalars**: `d_` for `float`,
  `n_` for `int`, `b_` for `bool`, `s_` for `str`. This is deliberate and
  applied consistently across the org -- if it's new to you, it exists so
  a variable's type is visible at every call site without following it
  back to a definition. It applies to scalars only; a composite value
  (a vector, a dataclass, a mapping) gets a bare name plus a type
  annotation instead of a prefix.
- **Units belong in the name**, not just the docstring, when the value is
  a scalar accessor: `d_min_wall_mm`, not `d_min_wall`. A caller shouldn't
  need to open the function to find out what unit a number is in.
- `SCREAMING_SNAKE` for module-level constants.
- `_leading_underscore` for private names, and they never appear in
  `__all__`.
- Every public module declares `__all__` explicitly.
- Exceptions are `PascalCase` and end in `Error`.

## Docstrings and comments

Boundary-verbose: rich at module and API boundaries, lean inside function
bodies. This is a followed convention, not something a linter enforces.

1. **Every module opens with a docstring that says why it exists**, and
   where it sits relative to the rest of the package. State the rule,
   then the failure it prevents, then (where it helps) the evidence.
2. **Every public function or class gets a docstring.** One line when the
   signature is self-explanatory. Google-style `Args:` / `Returns:` /
   `Raises:` sections when it isn't. (Google over NumPy: it's the more
   compact of the two, and matches "verbose enough, not overwhelming.")
3. **Inline comments explain why** -- invariants, units, coordinate
   frames, a rejected alternative and the reason it lost -- never what the
   next line does. If a comment just restates the code, delete it.
4. **Private helpers get a docstring only when they're non-obvious.** A
   one-line `d_*` property whose name already encodes quantity and unit
   needs nothing further.

What moves out of code entirely:

- **Migration archaeology.** "Moved here from X on date Y", "the first
  version folded this twice" -- that belongs in the changelog or the
  commit body, not in a docstring or a loop-body comment that everyone
  reading the code from now on has to wade through.
- **No `TODO` / `FIXME` / `HACK` markers.** Unfinished work is a GitHub
  issue, a `NotImplementedError` with a message explaining what's missing,
  or an honest "Known Limitations" section in the docs. Not a comment that
  quietly ships forever.
- **Every `noqa` carries its rule code and a one-line justification.**
  `# noqa: E501` with no reason is not acceptable; `# noqa: E501 -- this
  URL can't be wrapped` is.

## Typing

- **Every package ships `py.typed`.** Careful annotations are invisible to
  a consumer without this one-line marker file.
- **mypy runs in CI.** Strict-ish on structural code
  (`disallow_untyped_defs`, modern syntax), relaxed per-module for
  JAX-kernel or `bpy`-bound code via explicit, commented overrides in
  `[[tool.mypy.overrides]]` -- never a blanket project-wide relaxation.
  `ignore_missing_imports` is fine for the usual scientific-stack
  offenders (`jax`, `pyvista`, `trimesh`, `skimage`, `bpy`), stated as an
  override, not assumed.
- **`Literal` enums use `typing.get_args()`** for their runtime
  counterpart, never a hand-copied tuple that can silently drift from the
  `Literal` it's supposed to mirror.
- **Multi-value returns crossing a function boundary are a `NamedTuple` or
  a dataclass**, never a bare positional tuple. A caller unpacking `a, b,
  c = f()` has no way to tell what broke when the order changes; a
  `NamedTuple` field does.

## Python version policy

- The org default is `requires-python = ">=3.13"`.
- A library with **zero runtime dependencies**, or stdlib-only ones, may
  declare `>=3.11` instead -- but only paired with a CI matrix that
  actually runs its tests on 3.11. A lower floor claimed without the
  matrix proving it doesn't count as supported; it's a number in
  `pyproject.toml` nobody has verified.

## Testing

- **Test names are behavior sentences**:
  `test_removed_workflow_path_is_broken_not_merely_stale`, not
  `test_check_3`. A failing test name should tell you what's wrong before
  you open the file.
- **Module-level test functions, no test classes.** A flat `tests/`
  directory, with files named for the module or the invariant they pin,
  not for an arbitrary grouping.
- **Test-module docstrings state the invariant the file pins.** Read the
  top of the file and know what would have to break for it to matter.
- **Helpers over fixtures.** Module-level builder functions
  (`_make_header(**overrides)`) plus the built-in `tmp_path` and
  `monkeypatch` cover almost everything. A `conftest.py` appears only when
  data is genuinely shared across files, with a comment explaining why --
  not as a default home for setup code.
- **Parametrize once cases exceed about three**, with `ids=` so a failure
  reads as a case name, not `test_foo[2]`.
- **Negative tests pin the message**: `pytest.raises(SomeError,
  match="...")` on the meaningful fragment, not just the exception type.
- **Numeric asserts use `pytest.approx` with an explicit, justified
  tolerance.** Suite-wide tolerances get a named constant with a comment
  explaining what it absorbs (`RATE_TOL = 1.02  # margin absorbs sampling
  luck, not real error`). Never a bare `==` on a computed float.
- **Every public API gets a happy-path test and an edge-case test. Every
  bugfix lands with a regression test.** A gate test (provenance, units,
  a cross-file contract) is first-class, and named as a gate so its
  purpose is obvious from the test list.
- **Coverage is reported, not gated**, by default. A repo may opt into a
  coverage gate once it's ready; that's a per-repo decision, not an org
  default.
- **Machine-specific paths come from environment variables with a loud
  skip** when the variable isn't set, never a hardcoded absolute path that
  only works on one contributor's machine.

## TypeScript

For repos with a TypeScript surface:

- **`tsconfig` is strict, plus**: `noUncheckedIndexedAccess`,
  `exactOptionalPropertyTypes`, `noImplicitOverride`,
  `verbatimModuleSyntax`.
- **ESLint flat config plus Prettier.** `no-explicit-any` is an error, not
  a warning. No empty `catch` blocks.
- **Function components, named exports only.**
- **Pure logic lives in React-free modules**, so it can be unit-tested
  without a DOM.

## Documentation

- **README needs a working Install section and a copy-pasteable
  quickstart that actually runs**, from a clean clone, not from a
  contributor's already-configured machine. Version numbers, test counts,
  and layout trees in a README must match reality at release time.
- **`CLAUDE.md` holds the stable guide only**: build/run commands,
  architecture, naming, constraints. Dated journals, roadmaps, and
  sign-off notes go to GitHub issues or `docs/journal/`, not into the file
  every session reads on every task.
- **ADRs** live at `docs/adr/NNNN-kebab-title.md`, with `Status`,
  `Context`, `Decision`, and `Consequences` sections.

## Open-source hygiene

A checklist to run before a repo goes public, or as part of a PR review on
one that already is:

- **No personal names as decision provenance** in shipped source or docs.
  "Alex's working figure" becomes an institutional source string plus a
  dated entry in a private decision log -- the substance stays, the name
  goes.
- **No absolute personal paths** (`/Users/someone/...`). Derive the path
  from `__file__`, or read it from an environment variable with a loud
  skip when it's unset.
- **No private-repo references presented as resolvable**: a relative link
  to a sibling repo nobody outside the org can open, a "see
  internal-repo/path/to/file.py" citation, a line-number reference into a
  repo that isn't public. Describe the pattern in place instead, or link
  a public doc.
- **No internal tracker or doc citations** in user-facing strings: an
  internal ticket ID in an error message, a path into an internal wiki in
  a data file. Use a public issue, an in-repo doc, or plain prose instead.
- **No internal process artifacts**: agent-facing journals, "note to
  future me" comments, bootstrap prompt transcripts. Move them to
  `docs/history/` or delete them.
- **A `.mailmap`** normalizing any laptop-hostname commit emails, added
  before history goes public.
- **Complete `[project]` metadata**: institutional `authors`, `urls`,
  `readme`, and `classifiers`. Fix anything that reads like a personal
  signature rather than a project attribution.
- **Cross-repo installs work from a public clone.** A sibling dependency
  pinned as a local path (`../other-repo`) is a release blocker until it's
  published to an index or a wheelhouse.
