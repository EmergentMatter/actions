# Changelog

<!-- towncrier release notes start -->

## 2.0.0 (2026-09-25)

### Major

- Move `pyvista` from a required dependency into the optional `preview` extra. `pip install emergent-matter-sdm-core` no longer pulls in VTK, matplotlib, and cyclopts by default; `preview_part()` and `python -m software_defined_matter.preview` now raise a clear install hint (`pip install 'emergent-matter-sdm-core[preview]'`) instead of a bare import error. `sdm-core`'s own Python API is unchanged, but this breaks anyone relying on `pyvista` being installed transitively without asking for it.
- Replace CouplingNode with parameter-dependent Port frames and add schema 0.5 assembly bundles, scoped instance bindings, and port/motion promotion. Legacy coupling records require explicit migration.

### Minor

- Add schema 0.5 port mates and differentiable grounded assembly placement, including nested world frames, closure diagnostics and a mated actuator preview.
- Evaluate assembly DOF expression bindings in dependency order, expose resolved coordinates and world body transforms, and compose rigid and flexure point motion with mate placement. Diagnose non-finite internal motion and provide checked point evaluation.
- Evaluate compiled body motion, flexures, material membership and attached port frames with explicit differentiable design inputs and assembly-scoped bindings.

### Patch

- Install sdm-materials from the package index instead of a sibling checkout.
- Preserve already-converted placement defaults when converting omitted DOFs, and add degree-sensitive placement conformance expectations.


## 1.0.0 (2026-09-21)

### Major

- Now depends on `emergent-matter-sdm-materials` instead of `emergent-matter-materials`, following that repository's rename. The sibling checkout this repo expects beside it is now `../emergent-matter-sdm-materials`, and CI checks it out under that name.

### Minor

- Preview swept profiles with literal paths in GLSL, using live profile parameters and texture-backed frames for large scenes while explicitly refusing dynamic path coordinates.
- Resolve internal weld intervals when a structural ownership-invariance proof
  establishes solid coverage under coaxial rotation. Tighten CPU/GLSL ray bounds
  using the segment radius and direction. Add opt-in, tolerance-bounded grazing
  contacts backed by inside/outside witness samples, distinct from exact crossing
  results. Preserve unresolved outcomes for unproved cases and document the
  extended pose packet and contact contract in the flexure ADR.
- Rigid renderers can now use actual owned material geometry, with conservative motion bounds for cases that require bounded material queries.
- Sweep interior vertices are ball joints instead of flat caps. Two flat caps meeting at an angle left a wedge void on the outside of every bend, thinner than a voxel but real, so every vertex of a curved polyline or sampled spline meshed as a notch and a swept ring rendered with a fringe of hairs. The axial overshoot past an interior vertex now folds into the profile-plane radius (`_sweep_joint`, mirrored by `sdm_sweep_joint` in GLSL), which is exact for a circular profile and a sphere-swept approximation for any other; the two true ends of an open path keep their flat caps. The sweep table header carries the loop's closedness in its second component so `sdm_sweep_tab` can tell an open end from an interior vertex, and the shipped sweep conformance corpus is regenerated for the new distances at points past a vertex. Consumers with their own sweep evaluator must adopt the joint to stay conformant.

### Patch

- Added `3rdPartySoftware.md`, listing every third-party package this repository depends on, direct and transitive, with its license.

