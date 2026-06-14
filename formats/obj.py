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
_DEFAULT_COLOR = (0.92, 0.89, 0.81)


def _faces(scene):
    if hasattr(scene, "render_faces"):
        yield from scene.render_faces()
    elif hasattr(scene, "mesh"):
        yield from scene.mesh.faces
    else:
        yield from scene.faces


def save_obj(scene, path) -> None:
    """Write the scene as ``path`` (.obj) plus a sibling ``.mtl``."""
    path = Path(path)
    verts: list[tuple[float, float, float]] = []
    index: dict[tuple, int] = {}

    def vidx(p) -> int:
        key = (round(p.x(), 6), round(p.y(), 6), round(p.z(), 6))
        i = index.get(key)
        if i is None:
            verts.append((p.x(), p.y(), p.z()))
            i = index[key] = len(verts)  # OBJ indices are 1-based
        return i

    # color -> list of (i, j, k) triangles
    groups: dict[tuple, list[tuple[int, int, int]]] = {}
    for face in _faces(scene):
        col = face.attrs.get("color")
        key = tuple(col) if col is not None else _DEFAULT_COLOR
        bucket = groups.setdefault(key, [])
        for t0, t1, t2 in face.triangulate():
            bucket.append((vidx(t0), vidx(t1), vidx(t2)))

    colors = list(groups.keys())
    matname = {c: f"mat{i}" for i, c in enumerate(colors)}

    mtl_path = path.with_suffix(".mtl")
    with open(mtl_path, "w") as m:
        for c in colors:
            r, g, b = c
            m.write(f"newmtl {matname[c]}\n")
            m.write(f"Kd {r:.4f} {g:.4f} {b:.4f}\n\n")

    with open(path, "w") as o:
        o.write("# IngeTrazo OBJ export\n")
        o.write(f"mtllib {mtl_path.name}\n")
        for x, y, z in verts:
            o.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
        for c in colors:
            o.write(f"usemtl {matname[c]}\n")
            for i, j, k in groups[c]:
                o.write(f"f {i} {j} {k}\n")


# ---- Import --------------------------------------------------------------------

def _parse_mtl(path: Path) -> dict:
    """Map material name → (r, g, b) from a ``.mtl`` file's ``Kd`` lines."""
    colors: dict[str, tuple[float, float, float]] = {}
    if not path.exists():
        return colors
    current = None
    for line in path.read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "newmtl":
            current = parts[1]
        elif parts[0] == "Kd" and current is not None and len(parts) >= 4:
            colors[current] = (float(parts[1]), float(parts[2]), float(parts[3]))
    return colors


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
    current_color = None
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
            current_color = materials.get(parts[1])
        elif tag == "f":
            idxs = []
            for tok in parts[1:]:
                raw = int(tok.split("/")[0])
                idxs.append(raw - 1 if raw > 0 else len(verts) + raw)
            if len(idxs) >= 3 and all(0 <= i < len(verts) for i in idxs):
                pending.append(([verts[i] for i in idxs], current_color))

    seed: set = set()
    new_faces = set()
    for loop, color in pending:
        try:
            face = scene.mesh.add_face(loop)
        except Exception:  # noqa: BLE001 — skip a degenerate polygon
            continue
        new_faces.add(face)
        if color is not None and tuple(round(c, 4) for c in color) != tuple(
                round(c, 4) for c in _DEFAULT_COLOR):
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
