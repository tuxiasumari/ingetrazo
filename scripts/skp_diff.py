# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Differential validation harness for the SKP import backends.

The most valuable thing IngeTrazo can give the pure-Python `.skp` parser effort
(OpenSKP / a fork) is a **differential oracle**: because we already run Trimble's
SDK through the skp2dae converter, we can produce ground-truth for any `.skp` and
diff it against the pure parser's output — a fast feedback loop toward
"as faithful as the Blender importer".

Clean-room boundary: the Trimble SDK is used ONLY as a black-box oracle (feed a
file in, compare the resulting model), never decompiled. See
``docs/openskp-collaboration.md``.

For a `.skp` this tool:

  1. **Ground truth** — converts with skp2dae → COLLADA → loads into a headless
     ``Scene`` → computes a structural *fingerprint* (counts, bbox, materials,
     groups). Runnable today.
  2. **Candidate** — loads the same `.skp` through the pure-backend seam
     (``formats/skp.py``). Until a backend is wired this reports "unavailable",
     and the run still validates the skp2dae output on its own.
  3. **Diff** — compares the two fingerprints and prints the discrepancies.

Usage::

    python scripts/skp_diff.py path/to/model.skp [--json] [--tol 0.001]

No GL is needed — everything here is headless (``Scene`` + ``load_dae``).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---- fingerprint ---------------------------------------------------------------

def fingerprint(scene) -> dict:
    """A structural summary of a loaded ``Scene`` — the comparable signature of
    a parse: geometry counts, bounding box, distinct materials/textures, and the
    group breakdown. Deliberately parser-agnostic (no identity, no ordering)."""
    from formats.meshexport import world_faces

    faces = list(world_faces(scene))
    tris = 0
    vkeys = set()
    colors = set()
    textures = set()
    xmin = ymin = zmin = float("inf")
    xmax = ymax = zmax = float("-inf")
    for f in faces:
        tris += len(f.triangulate())
        tex = f.attrs.get("texture")
        if tex is not None and tex.get("path"):
            textures.add(Path(tex["path"]).name)
        else:
            colors.add(tuple(round(c, 4) for c in (f.attrs.get("color")
                                                   or (0.96, 0.95, 0.925))))
        for v in f.vertices:   # Face.vertices is a property → list[QVector3D]
            p = (round(v.x(), 4), round(v.y(), 4), round(v.z(), 4))
            vkeys.add(p)
            xmin, ymin, zmin = min(xmin, p[0]), min(ymin, p[1]), min(zmin, p[2])
            xmax, ymax, zmax = max(xmax, p[0]), max(ymax, p[1]), max(zmax, p[2])

    groups = getattr(scene, "groups", [])
    bbox = None
    if faces:
        bbox = {"min": [xmin, ymin, zmin], "max": [xmax, ymax, zmax],
                "size": [xmax - xmin, ymax - ymin, zmax - zmin]}
    return {
        "faces": len(faces),
        "triangles": tris,
        "vertices": len(vkeys),
        "materials": len(colors),
        "textures": len(textures),
        "groups": len(groups),
        "group_names": sorted(g.name for g in groups if getattr(g, "name", None)),
        "bbox": bbox,
    }


def compare(ground: dict, candidate: dict, tol: float = 1e-3) -> list[str]:
    """Human-readable discrepancies between two fingerprints. Counts are
    reported with absolute + relative deltas; the bbox per axis against ``tol``
    metres. An empty list means the parses agree structurally."""
    issues: list[str] = []
    for key in ("faces", "triangles", "vertices", "materials", "textures",
                "groups"):
        a, b = ground.get(key, 0), candidate.get(key, 0)
        if a != b:
            rel = (abs(a - b) / a * 100) if a else float("inf")
            issues.append(f"{key}: ground={a} candidate={b} "
                          f"(Δ={b - a}, {rel:.1f}%)")
    gb, cb = ground.get("bbox"), candidate.get("bbox")
    if gb and cb:
        for axis, i in (("x", 0), ("y", 1), ("z", 2)):
            for bound in ("min", "max"):
                d = abs(gb[bound][i] - cb[bound][i])
                if d > tol:
                    issues.append(f"bbox.{bound}.{axis}: "
                                  f"ground={gb[bound][i]:.4f} "
                                  f"candidate={cb[bound][i]:.4f} (Δ={d:.4f} m)")
    elif gb != cb:
        issues.append(f"bbox: ground={gb} candidate={cb}")
    return issues


# ---- loaders -------------------------------------------------------------------

def _locate_converter():
    """skp2dae command list (headless twin of MainWindow._find_skp_converter):
    ``SKP2DAE_EXE`` env → ``~/.local/share/skp2dae/skp2dae.exe`` → PATH. On
    Linux a ``.exe`` runs through Wine."""
    import os
    import shutil
    cands = []
    if os.environ.get("SKP2DAE_EXE"):
        cands.append(Path(os.environ["SKP2DAE_EXE"]))
    cands.append(Path.home() / ".local" / "share" / "skp2dae" / "skp2dae.exe")
    which = shutil.which("skp2dae")
    if which:
        cands.append(Path(which))
    for c in cands:
        if not c.exists():
            continue
        if c.suffix.lower() == ".exe" and sys.platform != "win32":
            wine = shutil.which("wine")
            if wine:
                return [wine, str(c)]
            continue
        return [str(c)]
    return None


def load_ground_truth(skp: Path):
    """Convert ``skp`` with skp2dae and load the COLLADA into a fresh Scene."""
    from core.scene import Scene
    from formats import dae as dae_format

    command = _locate_converter()
    if command is None:
        raise RuntimeError(
            "skp2dae converter not found (set SKP2DAE_EXE or install it via "
            "IngeTrazo ▸ File ▸ Import ▸ SketchUp).")
    dae = skp.with_suffix(".dae")
    result = subprocess.run(command + [str(skp), str(dae)],
                            capture_output=True, timeout=600)
    if result.returncode != 0 or not dae.exists():
        detail = (result.stderr or result.stdout or b"").decode(
            "utf-8", errors="replace").strip()[-500:]
        raise RuntimeError(f"skp2dae failed: {detail}")
    scene = Scene()
    dae_format.load_dae(scene, dae)
    return scene


def load_candidate(skp: Path):
    """Load ``skp`` through the pure-backend seam, or ``None`` when no backend
    can read it yet (``NeedsConverter``)."""
    from core.scene import Scene
    from formats import skp as skp_format

    scene = Scene()
    try:
        skp_format.load_skp(scene, skp)
    except skp_format.NeedsConverter:
        return None
    return scene


# ---- CLI -----------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Diff a .skp: skp2dae oracle vs "
                                             "pure backend.")
    ap.add_argument("skp", type=Path, help="path to the .skp file")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--tol", type=float, default=1e-3,
                    help="bbox tolerance in metres (default 1e-3)")
    args = ap.parse_args(argv)

    if not args.skp.exists():
        print(f"No such file: {args.skp}", file=sys.stderr)
        return 2

    ground = fingerprint(load_ground_truth(args.skp))
    from formats import skp as skp_format
    fmt = skp_format.detect_format(args.skp)
    cand_scene = load_candidate(args.skp)
    candidate = fingerprint(cand_scene) if cand_scene is not None else None

    if args.json:
        print(json.dumps({"format": fmt, "ground_truth": ground,
                          "candidate": candidate,
                          "diff": compare(ground, candidate, args.tol)
                          if candidate else None}, indent=2))
        return 0

    print(f"File: {args.skp.name}   format={fmt}")
    print("\n[ground truth — skp2dae]")
    for k, v in ground.items():
        print(f"  {k}: {v}")
    if candidate is None:
        wired = [n for n, ok in skp_format.backends_status() if ok]
        print("\n[candidate — pure backend]")
        print("  unavailable: no pure backend supports this file yet "
              f"(wired: {wired or 'none'}).")
        print("  This run validated the skp2dae output only. Wire OpenSKP "
              "(formats/skp.py) to enable the diff.")
        return 0
    print("\n[candidate — pure backend]")
    for k, v in candidate.items():
        print(f"  {k}: {v}")
    issues = compare(ground, candidate, args.tol)
    print("\n[diff]")
    if not issues:
        print("  ✓ parses agree structurally")
    else:
        for i in issues:
            print(f"  ✗ {i}")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
