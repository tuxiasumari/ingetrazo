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
