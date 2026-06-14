"""OBJ export — indexed vertices + triangles, with per-face colour as materials.

Wavefront OBJ with a sidecar ``.mtl``: vertices are de-duplicated by position,
every face is triangulated, and triangles are grouped by their material colour
(``Face.attrs["color"]``, default cream) so each colour becomes one ``usemtl``
material. Opens in Blender, MeshLab, etc. with the painted colours intact.
"""
from __future__ import annotations

from pathlib import Path

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
