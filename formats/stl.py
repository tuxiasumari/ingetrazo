"""STL export — triangle soup for 3D printing and mesh interchange.

Binary STL: every face (the loose mesh plus every group) is triangulated and
written with its outward geometric normal. The engine keeps solids
outward-consistent (``orient_outward``), so the normals come out right for
slicers. STL carries no colour or units — it is pure geometry in the model's
own coordinates (metres).
"""
from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtGui import QVector3D


def _faces(scene):
    """Every renderable face: loose mesh + groups (already world-space)."""
    if hasattr(scene, "render_faces"):
        yield from scene.render_faces()
    elif hasattr(scene, "mesh"):
        yield from scene.mesh.faces
    else:
        yield from scene.faces


def iter_triangles(scene):
    """Yield ``(a, b, c)`` world-space triangles for the whole scene."""
    for face in _faces(scene):
        yield from face.triangulate()


def _normal(a: QVector3D, b: QVector3D, c: QVector3D) -> QVector3D:
    n = QVector3D.crossProduct(b - a, c - a)
    length = n.length()
    return n / length if length > 1e-12 else QVector3D(0.0, 0.0, 0.0)


def save_stl(scene, path) -> None:
    """Write the scene as a binary STL to ``path``."""
    tris = list(iter_triangles(scene))
    with open(Path(path), "wb") as f:
        f.write(b"IngeTrazo STL export".ljust(80, b"\x00"))  # 80-byte header
        f.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            n = _normal(a, b, c)
            f.write(struct.pack(
                "<12fH",
                n.x(), n.y(), n.z(),
                a.x(), a.y(), a.z(),
                b.x(), b.y(), b.z(),
                c.x(), c.y(), c.z(),
                0,  # attribute byte count
            ))
