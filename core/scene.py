"""Scene container: edges, selection, version counter.

The scene is a flat list at this stage. A proper scene graph with nested
groups and transforms lands once components are introduced.

``version`` bumps on every mutation so the viewport can cheaply decide
whether to rebuild its dynamic VBOs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from PySide6.QtGui import QVector3D

from core.geometry import Edge, Face


@dataclass
class Scene:
    edges: list[Edge] = field(default_factory=list)
    faces: list[Face] = field(default_factory=list)
    selection: set = field(default_factory=set)
    version: int = 0

    def add_edge(self, a: QVector3D, b: QVector3D) -> Edge:
        edge = Edge(a, b)
        self.edges.append(edge)
        self.version += 1
        return edge

    def select(self, edges: Iterable[Edge], additive: bool = False) -> None:
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
        self.edges = [e for e in self.edges if e not in self.selection]
        self.selection.clear()
        self.version += 1

    def clear(self) -> None:
        if self.edges or self.faces or self.selection:
            self.edges.clear()
            self.faces.clear()
            self.selection.clear()
            self.version += 1

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
