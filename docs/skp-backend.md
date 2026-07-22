# SKP import backend seam

IngeTrazo aims to open **any** `.skp` (old → recent). This document describes
the single seam that decouples the app from *how* a `.skp` is read.

## Backends

- **OpenSKP** (pure Python, MIT — https://github.com/iamahsanmehmood/openskp).
  Offline, Linux-native, no Wine, no proprietary DLL. **Wired and working** (see
  "What works / what's missing"). An **optional dependency**: `pip install
  openskp` (pulls `trimesh`). Not in `requirements.txt` yet — the seam falls
  back gracefully when it's absent.
- **skp2dae** (Trimble's `SketchUpAPI.dll` via Wine). The full-coverage
  fallback: a SEPARATE program (the DLL never enters GPL IngeTrazo). Its
  install/dialog/subprocess flow stays in `views/main_window.py`.

## The seam — `formats/skp.py`

**Parse then apply.** A backend *parses* a file into a plain **payload** (world-
space face loops), touching no `Scene`. The heavy parse runs *outside* the undo
history; `apply_payload` then adds the geometry cheaply inside a command — so a
failed or empty parse never leaves a half-applied edit.

Public API:

- `detect_format(path) -> "skp" | "unknown"` — from the first bytes. Real `.skp`
  files (legacy MFC **and** 2021+) begin with the same UTF-16 `SketchUp Model`
  marker (or a `PK` ZIP wrapper), so the *era* is **not** observable from the
  magic bytes — and doesn't need to be, since OpenSKP handles the range.

## Legacy (pre-2021) MFC container — SUPPORTED (2026-07-22)

Classic `.skp` files (SketchUp ≤2020; validated on real 2016/2017/2018
models) are ONE uncompressed MFC `CArchive` stream with a global 1-based
store map — no ZIP, no `model.dat`. Our fork adds
`openskp/legacy.py`: a full walker (materials + textures, layers,
half-edge kernel, definitions/instances/groups, face-camera flags, UV
matrices, dims/texts/guides/section planes) that emits the same
`full_parse()` dict, so `SkpFile.parse()` and the whole IngeTrazo seam
work unchanged. Validated: exact face/edge/area/bbox parity on five
user models against their SketchUp-Web VFF re-saves; `skp_diff`
fingerprints identical through `apply_payload` (incl. materials and
textures). Key decoding notes (where real files differ from the public
2017 spec) are in the module docstring. Known gaps: files with fewer
than 2 materials can't bootstrap the slot base yet (fall back to
skp2dae), legacy colorized materials untinted, CImage entities and doc
thumbnail skipped, positioned-texture UV parity unverified visually.
- `can_handle(path) -> bool` — a pure backend is available and recognises it
  (does not guarantee a non-empty parse).
- `parse_skp(path, progress=None) -> payload` — first backend that yields
  geometry; raises `NeedsConverter` on an unrecognised file, a parser error, or
  an empty parse.
- `apply_payload(scene, payload) -> backend_name` — add the payload as reference
  groups.
- `load_skp(scene, path)` — `apply_payload(parse_skp(...))`, for the diff harness.
- `NeedsConverter`, `backends_status()`.

Backends implement `available()`, `supports(fmt)`, `parse(path, progress)`.

### Cascade in the UI

`views/main_window.py::import_skp_path`:

1. If `can_handle(skp)` → `parse_skp` (outside history). Non-empty → apply
   through `SnapshotImport`. Empty/`NeedsConverter` → step 2.
2. **skp2dae** converter (Wine).

## The OpenSKP adapter — `formats/skp_openskp.py`

Isolated so `import openskp` is lazy. OpenSKP 0.2.0 model (by introspection):

- `SkpFile.open(path).parse()` → `SkpModel(definitions, materials, layers,
  version)`.
- `Definition(id, name, vertices{id→Vertex(x,y,z)}, edges{id→Edge(v1_id,v2_id)},
  faces{id→Face}, instances[Instance])`.
- `Face(loops, normal, material_id)`; each loop is `[(edge_id, sense), …]`,
  first = outer, rest = holes; `sense` 1 walks `v1→v2`.
- `Instance(matrix[13], ref_idx→def id, children)` — 3×3 row-major + translation.

SketchUp is **inches, Z-up** (same up axis as IngeTrazo) → scale ×0.0254, no
axis swap. The instance tree is flattened to world-space polygons (reference
geometry, one group). Enable/disable via `_OpenSkpBackend` in `formats/skp.py`.

## What works / what's missing (measured with `scripts/skp_diff.py`)

Validated against the skp2dae/Trimble oracle on real files (e.g. `demuna.skp`,
SketchUp 2022):

- ✅ **Bounding box exact** — units, Z-up and instance transforms correct.
- ✅ **Geometry ~90–95% complete** — faces/vertices/triangles within ~5–9% of
  the oracle.
- ✅ **Materials / colours** — resolved. `Face.material_id` joins through
  `SkpModel.materials_by_id`, added by **our upstream PR**
  ([openskp#3](https://github.com/iamahsanmehmood/openskp/pull/3)).
- ✅ **Textures** — resolved. `Material.texture` (image bytes + tile size in
  inches), added by **our upstream PR**
  ([openskp#4](https://github.com/iamahsanmehmood/openskp/pull/4)). The adapter
  writes the images to `<stem>/` beside the `.skp` (the SketchUp-export
  convention skp2dae also uses) and maps them with IngeTrazo's planar
  projection at the material's real tile size. Measured: **18/18 materials and
  2/2 textures — exact parity with the oracle**; those rows no longer appear
  in the diff.
  Until the PRs ship on PyPI, install from the integration branch:
  `pip install git+https://github.com/tuxiasumari/openskp@ingetrazo#subdirectory=packages/python`
  (branch `ingetrazo` = upstream `main` + both PR branches merged). With PyPI
  0.2.0 (no joins) faces import uncoloured.
- ✅ **"~5–9% skipped faces" — resolved: it was a measurement artefact, not
  lost geometry.** The raw DAE carries 4516 triangles = exactly what OpenSKP
  parses, and **total surface area matches to 0.00%** (327.268 vs 327.269 m²).
  The count deltas came from comparing a fused path (the DAE import runs
  coplanar fusion + weld + double-face dedupe) against raw SketchUp polygons.
  Two fixes landed: the harness fingerprint now carries **`area_m2`** (the
  fusion-invariant completeness metric — when areas agree, count deltas are
  labelled as post-processing); and `apply_payload` now runs the **same
  fusion pipeline as the DAE import** (`fuse_coplanar_loops` +
  `soften_smooth_edges`, hole-carrying faces added directly), so a `.skp`
  through the pure backend looks identical to one through the converter.
  After both: triangles Δ1.3%, vertices Δ0.7%, faces 262 vs 389 — the pure
  path fuses *better* (it starts from SketchUp's original polygons, not
  reconstructed triangles). Perf: plaza Yanque (34 MB) parses in ~12 s pure
  Python — 97k faces, 42 273 m², 19 materials + 10 textures.
- ✅ **Grouping — resolved (SketchUp-style, with shared components).** The
  adapter now mirrors the DAE reference import: the root's loose faces become
  one group named after the file, each top-level instance becomes its own
  group carrying its SketchUp definition name, and a definition placed ≥2
  times above the DAE sharing thresholds becomes **one prototype** (extracted
  at any depth) with each copy an O(1) `Group.xform` instance. Measured:
  demuna → 3 groups, exact parity with the oracle and with *better* names
  ('Niraj', 'Derrick' vs the DLL's 'node'); plaza Yanque → 39 groups of which
  31 are instances sharing 5 prototypes, and import time dropped 12.1 → 7.5 s
  (each prototype fuses once, not per copy). Library definitions never placed
  in the model are not emitted — same as SketchUp.
- ⚠️ **Instance-tree misplacement (upstream, latent)** — in `demuna.skp` the
  parser hangs Rodeo#2's instance under the *Derrick* definition instead of
  the root, and `CASCO.dwg` (137 verts / 156 edges, a pure-wireframe DWG
  import) is never instanced. Positions still come out right in this file,
  but the hierarchy is wrong — worth an upstream issue with the repro.

These gaps are the concrete contribution targets for OpenSKP (see
`docs/openskp-collaboration.md`). Geometry — the hard part — already works.
