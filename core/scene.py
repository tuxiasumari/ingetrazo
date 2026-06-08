"""Scene container, backed by a shared-vertex :class:`~core.mesh.Mesh`.

``edges`` and ``faces`` are read-only views onto the mesh (lists of
``mesh.Edge`` / ``mesh.Face``), so render, bounds and ``.igz`` save consume them
unchanged. Every mutation goes through mesh methods (via the ``Command`` layer),
which keep shared-vertex connectivity and incidence in sync — no more
position-matching to rediscover topology.

``version`` bumps on every mutation so the viewport can cheaply decide whether
to rebuild its dynamic VBOs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from PySide6.QtGui import QVector3D

from core.mesh import Edge, Face, Mesh


@dataclass
class Scene:
    mesh: Mesh = field(default_factory=Mesh)
    selection: set = field(default_factory=set)
    version: int = 0

    # ---- Geometry views (read-only over the mesh) ---------------------------
    @property
    def edges(self) -> list[Edge]:
        return self.mesh.edges

    @property
    def faces(self) -> list[Face]:
        return self.mesh.faces

    # ---- Mutations ----------------------------------------------------------
    def add_edge(self, a: QVector3D, b: QVector3D) -> Edge:
        edge = self.mesh.add_edge(a, b)
        self.version += 1
        return edge

    def select(self, edges: Iterable, additive: bool = False) -> None:
        if not additive:
            self.selection.clear()
        self.selection.update(edges)
        self.version += 1

    def clear_selection(self) -> None:
        if self.selection:
            self.selection.clear()
            self.version += 1

    def delete_selection(self) -> None:
        if not self.selection:
            return
        for ent in list(self.selection):
            if isinstance(ent, Edge):
                self.mesh.remove_edge(ent)
            elif isinstance(ent, Face):
                self.mesh.remove_face(ent)
        self.selection.clear()
        self.version += 1

    def clear(self) -> None:
        if self.mesh.edges or self.mesh.faces or self.selection:
            self.mesh.clear()
            self.selection.clear()
            self.version += 1

    # ---- Queries ------------------------------------------------------------
    def bounds(self) -> tuple[QVector3D, QVector3D] | tuple[None, None]:
        """Axis-aligned bounding box of all geometry. ``(None, None)`` if empty."""
        if not self.edges and not self.faces:
            return None, None
        inf = float("inf")
        minx = miny = minz = inf
        maxx = maxy = maxz = -inf

        def absorb(v: QVector3D) -> None:
            nonlocal minx, miny, minz, maxx, maxy, maxz
            x, y, z = v.x(), v.y(), v.z()
            if x < minx: minx = x
            if y < miny: miny = y
            if z < minz: minz = z
            if x > maxx: maxx = x
            if y > maxy: maxy = y
            if z > maxz: maxz = z

        for edge in self.edges:
            absorb(edge.a)
            absorb(edge.b)
        for face in self.faces:
            for v in face.vertices:
                absorb(v)
        return QVector3D(minx, miny, minz), QVector3D(maxx, maxy, maxz)
