# OpenSKP collaboration

**Status:** introduction issue **posted** upstream
([iamahsanmehmood/openskp#2](https://github.com/iamahsanmehmood/openskp/issues/2),
2026-07-21). This doc keeps the rationale for how IngeTrazo supports
[OpenSKP](https://github.com/iamahsanmehmood/openskp) without becoming dependent
on it, plus the issue text for reference.

## Strategy: upstream-first, not upstream-dependent

We want to help OpenSKP become a pure-Python parser that opens **any** `.skp`
(old → recent), and use it in IngeTrazo to replace the Wine + Trimble-DLL path.

Because OpenSKP is **MIT**, our ability to ship it never depends on upstream
merging our work:

- **Layer A — upstream-first.** Open PRs, discuss, aim to get changes merged.
  Best case: everyone benefits, zero maintenance for us.
- **Layer B — maintained downstream (insurance).** Whatever upstream doesn't
  take lives in a friendly fork (`main` tracks upstream + a branch carrying our
  patches, rebased as upstream moves). IngeTrazo vendors a **pinned** version of
  Layer B behind the `formats/skp.py` seam — so a rejected PR costs us
  *maintenance*, never *capability*.

A fork only becomes its own project (a natural "libreskp") if upstream is
unresponsive/misaligned, or if we ever want a copyleft guarantee. Until then:
contribute, and keep a quiet downstream as insurance. Preserve OpenSKP's MIT
notices when vendoring; MIT is GPL-compatible, so the combined IngeTrazo ships
GPL while the parser files keep their MIT header + attribution.

## Clean-room boundary

Our differential validation uses the Trimble SDK (via skp2dae) strictly as a
**black-box oracle**: feed a `.skp` in, compare the output against OpenSKP's
parse. This is legitimate output comparison — **never** DLL decompilation or
copying SDK headers/internals into the parser. It matches the "observed `.skp`
files + their COLLADA exports" methodology the reverse-engineering already uses.

## Findings — OpenSKP 0.2.0 wired into IngeTrazo (2026-07-21)

OpenSKP is **wired and working** (`formats/skp_openskp.py`). Measured against
the skp2dae/Trimble oracle on real files (`demuna.skp`, SketchUp 2022):

- ✅ **Bounding box exact** — units (inches→m), Z-up and instance transforms all
  correct.
- ✅ **Geometry ~90–95% complete** — faces/vertices/triangles within ~5–9% of
  the oracle.

Contribution targets, most valuable first:

1. ✅ **Expose `Material.id`** — **PR submitted**
   ([openskp#3](https://github.com/iamahsanmehmood/openskp/pull/3), 2026-07-21):
   `Material.id` + `SkpModel.materials_by_id`, surfacing the join the internal
   exporter already had. Validated 19/19 face material_ids on a real SU2022
   file. Layer B insurance: branch `expose-material-id` on
   `tuxiasumari/openskp`; IngeTrazo's adapter uses the join when present
   (guarded, so PyPI 0.2.0 still imports, just uncoloured).
2. ✅ **Texture extraction** — **PR submitted**
   ([openskp#4](https://github.com/iamahsanmehmood/openskp/pull/4), 2026-07-21):
   `Material.texture` (`Texture` dataclass — filename, tile size in inches, raw
   image bytes, `save()`), read from the material's ZIP folder with a sibling
   fallback for name mismatches. Validated 2/2 textures on a real SU2022 file.
   Integration branch `ingetrazo` on `tuxiasumari/openskp` merges #3 + #4 for
   IngeTrazo's venv until they ship on PyPI. Measured after both: **18/18
   materials, 2/2 textures — exact parity with the skp2dae oracle.**
3. ✅ **"~5–9% skipped faces" — resolved 2026-07-21: measurement artefact, not
   parser loss.** Raw DAE = 4516 tris = OpenSKP's parse exactly; surface area
   matches to 0.00% (327.268 vs 327.269 m²). The deltas came from comparing a
   fused path against raw polygons. Harness now uses `area_m2` as the
   fusion-invariant truth metric; IngeTrazo's `apply_payload` runs the same
   fusion pipeline as its DAE import. **No upstream work needed.**
4. ✅ **Instance-level materials — PR submitted**
   ([openskp#5](https://github.com/iamahsanmehmood/openskp/pull/5), 2026-07-21):
   `Instance.material_id` (the `D007`/`D107` under the `6419` node — SketchUp's
   "paint the component"). Found on the plaza: 24/274 instances carry a
   material (granite pergolas, wood floors). IngeTrazo's adapter now resolves
   the inheritance (face material `None` → nearest painted ancestor;
   prototypes split per inherited material so a red and a green copy don't
   wrongly share). Also fixed on our side: texture filenames that are full
   Windows paths (`C:\Users\...\toro.png`, `P:/SketchUp projects/...png`)
   are reduced to a safe basename before writing.
5. **Image entities (upstream, the next real gap)** — SketchUp *Image*
   objects (a photo placed as an object — the signature of 2.5D tree
   foliage) are a separate TLV entity class the parser doesn't extract.
   Measured on the plaza: **444 m² of Celtis tree foliage** present in the
   oracle but absent from the pure parse; the `Celtis_australis` material
   exists but no face references it (`Material.id is None`). Everything else
   in the per-material area diff was name-normalisation noise (spaces vs
   underscores). Needs real reverse engineering of the image-entity tags.
6. ✅ **Per-face texture mapping — DECODED and shipped** (2026-07-21). The
   user authored the controlled experiment (`textura.skp`: untouched /
   rotated / distorted squares) and it cracked the convention: the stored
   3×3 row-major matrix maps **texture space → face plane**, so
   ``uvq = [p·xr, p·yr, 1] @ inv(M)``, ``u = uvq[0]/uvq[2]/tile_w`` (plane
   basis ``xr = normalize(Z×n)``, ``yr = n×xr``, inches; projective for
   4-pin distortion). Validated to rms < 1e-5 on 150 photo-fitted flag
   triangles vs SDK ground truth. **PR submitted**
   ([openskp#6](https://github.com/iamahsanmehmood/openskp/pull/6)):
   `Face.uv_transform` / `uv_transform_back` with the recipe documented.
   IngeTrazo's adapter bakes the exact per-vertex UVs into the per-face
   `uvw` affine its renderer already consumes — the Peru flag now shows
   red *and* white. Original investigation notes kept below.

   **(original notes)** Per-face texture mapping (upstream, located but not yet decoded) —
   photo-fitted/positioned textures (e.g. a waving Peru-flag mesh) carry a
   per-face mapping the parser doesn't read, so IngeTrazo's planar fallback
   shows a single corner of the image (the user's "solid red flag"). We
   FOUND where it lives, per face, under the face's entity-info block:
   `D007 → DC05 → DD05 → B136 → B236 → 1027 → 1127 (front) / 1227 (back)
   → 1327 → { 1427: flag(=1), 1527: 9×f64 3×3 matrix, 1627: 3×f64 }` —
   and the 9-double matrix is per-face and projective-looking (its [8]
   element varies ≈0.98–1.06, the signature of SketchUp's 4-pin distorted
   mapping). What's missing is the exact convention: tested affine and
   homography readings (row/col-major, inverse, SketchUp-style plane axes)
   against ground-truth UVs recovered from the DAE oracle (200 matched
   triangles) — none closes (best rms ≈0.30 in wrapped UV). The 2D basis
   SketchUp uses is not the naive normal-derived axes. Next step: a
   CONTROLLED experiment — a minimal .skp authored in SketchUp with a
   known positioned texture (unrotated square / 90°-rotated / distorted)
   to calibrate the basis cleanly, instead of a photo-fitted waving mesh.
   Related finds while digging: `D207` under the face's `D007` = the BACK
   material (428921 on the flag), and `AF0D` = a repeated material ref —
   both worth exposing upstream too (back-painted faces currently import
   colourless).
6. **Instance-tree misplacement (upstream, latent)** — found while digging
   into #3: in a real SU2022 file, an instance is attached to the wrong parent
   definition (Rodeo#2 under Derrick instead of the root) and a pure-wireframe
   component (`CASCO.dwg`, 137 verts / 156 edges, 0 faces) is never instanced.
   Positions happen to come out right; the hierarchy is wrong. Candidate for
   an upstream issue with repro.
7. **Legacy MFC (v8–v20)** version coverage, if not already handled.

The differential harness lives at **`scripts/skp_diff.py`**:
`python scripts/skp_diff.py model.skp` converts with skp2dae (oracle) and diffs a
structural fingerprint against the pure backend's parse (unavailable until a
backend is wired, in which case the run still validates the skp2dae output).

## Introduction issue (post upstream)

**Title:** IngeTrazo (free SketchUp-alternative modeler) would like to
contribute — roadmap, legacy formats & governance?

---

Hi! First, thank you for OpenSKP — a clean-room, MIT, cross-platform `.skp`
parser is exactly the missing piece for the free/libre 3D ecosystem.

I maintain [IngeTrazo](https://github.com/tuxiasumari/ingetrazo), a GPL-3.0,
Linux-first 3D modeler (a free SketchUp alternative aimed at civil engineering /
architecture in Latin America, with a BIM→IFC bridge). Today we open `.skp`
files by shelling out to Trimble's proprietary `SketchUpAPI.dll` through Wine —
it works, but it's a proprietary, Windows-only, offline-hostile dependency we'd
love to **replace with a pure-Python parser like OpenSKP**. I've already built a
pluggable import seam so OpenSKP can drop in as the preferred backend, with the
DLL path kept only as a fallback.

I want to **contribute substantially and long-term**, not just file wishlist
issues. Before I dive in, three questions to align:

1. **Legacy formats.** Our users bring models from *any* SketchUp version.
   OpenSKP currently targets the VFF container (2021+). Is decoding the older
   **MFC binary format (v8–v20)** on the roadmap, or out of scope? That range
   matters a lot for real-world adoption.
2. **Fidelity gaps.** Where do you feel the parser is weakest today (textured
   materials, per-corner UVs, component/group hierarchy + instance transforms,
   layers/scenes)? I'd like to pick a slice to own — I have deep experience with
   texture UV mapping and component instancing from IngeTrazo's DAE importer.
3. **License & governance.** Confirming MIT stays MIT, and how you like
   contributions to flow (PR conventions, direction, any CLA).

**What I can bring that's uncommon:** a **differential validation harness +
real-world `.skp` corpus across versions**. Because we already run the Trimble
SDK (as a black box), I can generate ground-truth output for a given `.skp` and
**diff it against OpenSKP's parse** (geometry, materials, hierarchy) to pinpoint
discrepancies — a fast feedback loop toward "as faithful as the Blender
importer." This is strictly **black-box output comparison** (feed file in,
compare results), never DLL decompilation, keeping the clean-room lineage
intact — the same "observed `.skp` files + their COLLADA exports" methodology
the format work already relies on.

Happy to start with whatever's most useful to you: the diff harness, sample
files (with permission), or a specific fidelity fix. Thanks again for building
this!
