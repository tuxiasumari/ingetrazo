# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""OBJ export — indexed vertices + triangles, with per-face colour as materials.

Wavefront OBJ with a sidecar ``.mtl``: vertices are de-duplicated by position,
every face is triangulated, and triangles are grouped by their material colour
(``Face.attrs["color"]``, default cream) so each colour becomes one ``usemtl``
material. Opens in Blender, MeshLab, etc. with the painted colours intact.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QVector3D

# Cream painted on faces with no material colour (mirrors the viewport default).
_DEFAULT_COLOR = (0.96, 0.95, 0.925)


def _faces(scene):
    if hasattr(scene, "render_faces"):
        yield from scene.render_faces()
    elif hasattr(scene, "mesh"):
        yield from scene.mesh.faces
    else:
        yield from scene.faces


def save_obj(scene, path) -> None:
    """Write the scene as ``path`` (.obj) + a sibling ``.mtl``. Solid colours
    become ``Kd`` materials; textured faces become ``map_Kd`` materials with the
    image copied next to the .obj and per-vertex ``vt`` from the same planar
    projection the viewport uses — so the model opens with matching textures in
    SketchUp/Blender."""
    import shutil
    from core.texture import planar_uv

    path = Path(path)
    verts: list[tuple[float, float, float]] = []
    vindex: dict[tuple, int] = {}
    uvs: list[tuple[float, float]] = []
    uvindex: dict[tuple, int] = {}

    def vidx(p) -> int:
        key = (round(p.x(), 6), round(p.y(), 6), round(p.z(), 6))
        i = vindex.get(key)
        if i is None:
            verts.append((p.x(), p.y(), p.z()))
            i = vindex[key] = len(verts)  # OBJ indices are 1-based
        return i

    def uvidx(uv) -> int:
        key = (round(uv[0], 6), round(uv[1], 6))
        i = uvindex.get(key)
        if i is None:
            uvs.append((uv[0], uv[1]))
            i = uvindex[key] = len(uvs)
        return i

    # material key -> {"color": rgb, "map": basename|None} and its triangles
    # (each triangle a list of (vi, ti|None)).
    materials: dict[tuple, dict] = {}
    groups: dict[tuple, list] = {}
    for face in _faces(scene):
        tex = face.attrs.get("texture")
        if tex is not None and tex.get("path"):
            src = Path(tex["path"])
            key = ("tex", src.name)
            materials.setdefault(key, {"color": (1.0, 1.0, 1.0),
                                       "map": src.name, "src": src})
            n = face.normal()
            sw = tex.get("sw", 1.0) or 1.0
            sh = tex.get("sh", 1.0) or 1.0
            rot = float(tex.get("rot", 0.0))
            for tri in face.triangulate():
                uv = planar_uv(n, list(tri), sw, sh, rot)
                groups.setdefault(key, []).append(
                    [(vidx(tri[k]), uvidx(uv[k])) for k in range(3)])
        else:
            col = tuple(face.attrs.get("color") or _DEFAULT_COLOR)
            key = ("color", col)
            materials.setdefault(key, {"color": col, "map": None})
            for tri in face.triangulate():
                groups.setdefault(key, []).append(
                    [(vidx(tri[k]), None) for k in range(3)])

    keys = list(groups.keys())
    matname = {k: f"mat{i}" for i, k in enumerate(keys)}

    # Copy texture images next to the .obj so map_Kd resolves.
    for k in keys:
        mat = materials[k]
        if mat.get("map"):
            dst = path.parent / mat["map"]
            try:
                if mat["src"].resolve() != dst.resolve():
                    shutil.copy(mat["src"], dst)
            except Exception:  # noqa: BLE001 — best-effort; export still valid
                pass

    mtl_path = path.with_suffix(".mtl")
    with open(mtl_path, "w") as m:
        for k in keys:
            mat = materials[k]
            r, g, b = mat["color"]
            m.write(f"newmtl {matname[k]}\n")
            m.write(f"Kd {r:.4f} {g:.4f} {b:.4f}\n")
            if mat.get("map"):
                m.write(f"map_Kd {mat['map']}\n")
            m.write("\n")

    with open(path, "w") as o:
        o.write("# IngeTrazo OBJ export\n")
        o.write(f"mtllib {mtl_path.name}\n")
        for x, y, z in verts:
            o.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
        for u, v in uvs:
            o.write(f"vt {u:.6f} {v:.6f}\n")
        for k in keys:
            o.write(f"usemtl {matname[k]}\n")
            for tri in groups[k]:
                toks = [(f"{vi}/{ti}" if ti is not None else f"{vi}")
                        for vi, ti in tri]
                o.write("f " + " ".join(toks) + "\n")


# ---- Import --------------------------------------------------------------------

def _parse_mtl(path: Path) -> dict:
    """Map material name → ``{"color": (r,g,b), "map": filename|None}`` from a
    ``.mtl`` file's ``Kd`` / ``map_Kd`` lines."""
    mats: dict[str, dict] = {}
    if not path.exists():
        return mats
    current = None
    for line in path.read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "newmtl":
            current = parts[1]
            mats[current] = {"color": None, "map": None}
        elif current is None:
            continue
        elif parts[0] == "Kd" and len(parts) >= 4:
            mats[current]["color"] = (float(parts[1]), float(parts[2]),
                                      float(parts[3]))
        elif parts[0] == "map_Kd" and len(parts) >= 2:
            mats[current]["map"] = parts[-1]  # last token = filename
    return mats


def load_obj(scene, path) -> None:
    """Add the faces of a Wavefront OBJ at ``path`` to ``scene``'s mesh, then
    weld + merge coplanar so a triangulated file (e.g. our own export, or a
    SketchUp OBJ) comes back as clean editable polygons. Material ``Kd`` colours
    become per-face ``attrs["color"]`` (skipped when they match the default
    cream, so plain faces stay unpainted). Adds to the current scene; the caller
    wraps it for undo."""
    from core.history import run_stitch
    from core.orient import orient_outward
    from core.topology import _key

    path = Path(path)
    verts: list[QVector3D] = []
    materials: dict = {}
    current_mat = None
    pending: list[tuple[list[QVector3D], object]] = []

    for line in path.read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        tag = parts[0]
        if tag == "v":
            verts.append(QVector3D(float(parts[1]), float(parts[2]), float(parts[3])))
        elif tag == "mtllib":
            materials = _parse_mtl(path.with_name(parts[1]))
        elif tag == "usemtl":
            current_mat = materials.get(parts[1])
        elif tag == "f":
            idxs = []
            for tok in parts[1:]:
                raw = int(tok.split("/")[0])
                idxs.append(raw - 1 if raw > 0 else len(verts) + raw)
            if len(idxs) >= 3 and all(0 <= i < len(verts) for i in idxs):
                pending.append(([verts[i] for i in idxs], current_mat))

    seed: set = set()
    new_faces = set()
    for loop, mat in pending:
        try:
            face = scene.mesh.add_face(loop)
        except Exception:  # noqa: BLE001 — skip a degenerate polygon
            continue
        new_faces.add(face)
        if mat is not None:
            if mat.get("map"):
                # Resolve the image next to the .obj. Tile size isn't in the OBJ
                # (the vt carry it), so default to 1 m — the texture shows; the
                # exact tiling can be re-set with the Paint tool.
                img = path.with_name(mat["map"])
                face.attrs["texture"] = {"path": str(img), "sw": 1.0, "sh": 1.0}
            else:
                color = mat.get("color")
                if color is not None and tuple(round(c, 4) for c in color) != \
                        tuple(round(c, 4) for c in _DEFAULT_COLOR):
                    face.attrs["color"] = list(color)
        for v in loop:
            seed.add(_key(v))

    # Weld coincident vertices and fuse the coplanar triangles back into the
    # polygons they were exported from (a triangulated cube → 6 quads). The
    # coplanar merge is winding-tolerant, so give a closed result a consistent
    # outward orientation — what the engine and STL re-export expect.
    run_stitch(scene.mesh, seed, new_faces, coplanar_merge=True)
    orient_outward(scene.mesh)
    scene.version += 1
