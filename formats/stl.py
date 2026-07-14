# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
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
    """Every renderable face in WORLD space: loose mesh + groups. Component
    instances share a prototype mesh in local coordinates, so their faces
    come from a transformed copy."""
    if hasattr(scene, "render_faces"):
        groups = getattr(scene, "groups", [])
        if not any(getattr(g, "xform", None) is not None for g in groups):
            yield from scene.render_faces()
            return
        from core.group import world_mesh
        for f in scene.loose_mesh.faces:
            if scene.entity_visible(f):
                yield f
        for g in groups:
            if not scene.entity_visible(g) or getattr(g, "billboard", False):
                continue
            yield from world_mesh(g).faces
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
