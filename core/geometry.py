"""Geometry primitives: vertex, edge, face, solid.

Edges and faces use identity-based equality so each instance is a distinct
entity that can be tracked in a selection ``set``. Two edges (or two
faces) with the same coordinates are still treated as separate objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtGui import QVector3D


@dataclass(eq=False)
class Edge:
    """A line segment between two 3D points (identity-equal)."""

    a: QVector3D
    b: QVector3D

    def length(self) -> float:
        return (self.b - self.a).length()


@dataclass(eq=False)
class Face:
    """A planar polygon defined by its outer vertex loop, with optional holes.

    No internal triangulation is stored — consumers (renderer, picker) call
    :meth:`triangulate`. A face with no holes and a convex loop reduces to a
    cheap fan; a face that has been divided by an inner loop (``holes``) is a
    "donut" and is triangulated by :mod:`core.triangulate`.

    Each entry in ``holes`` is an inner vertex loop that has been subtracted
    from this face (e.g. a small rectangle drawn inside a larger one). The
    inner loop is also usually its own :class:`Face`; the hole here is what
    keeps this (mother) face from overlapping it.

    The face normal is derived with Newell's method so it works for
    arbitrary polygons (including non-planar ones, where it returns the
    best-fit plane normal).
    """

    vertices: list[QVector3D] = field(default_factory=list)
    holes: list[list[QVector3D]] = field(default_factory=list)

    def normal(self) -> QVector3D:
        n = QVector3D(0.0, 0.0, 0.0)
        count = len(self.vertices)
        if count < 3:
            return QVector3D(0.0, 0.0, 1.0)
        for i in range(count):
            curr = self.vertices[i]
            nxt = self.vertices[(i + 1) % count]
            n = n + QVector3D(
                (curr.y() - nxt.y()) * (curr.z() + nxt.z()),
                (curr.z() - nxt.z()) * (curr.x() + nxt.x()),
                (curr.x() - nxt.x()) * (curr.y() + nxt.y()),
            )
        if n.length() < 1e-9:
            return QVector3D(0.0, 0.0, 1.0)
        return n.normalized()

    def centroid(self) -> QVector3D:
        count = len(self.vertices)
        if count == 0:
            return QVector3D(0.0, 0.0, 0.0)
        cx = sum(v.x() for v in self.vertices) / count
        cy = sum(v.y() for v in self.vertices) / count
        cz = sum(v.z() for v in self.vertices) / count
        return QVector3D(cx, cy, cz)

    def triangulate(self) -> list[tuple[QVector3D, QVector3D, QVector3D]]:
        """Triangles covering this face (outer loop minus any holes).

        Delegates to :mod:`core.triangulate`, which fans a convex loop, ear-
        clips a concave one, and bridges holes when present — so a concave or
        holed face renders and picks correctly while convex faces keep the
        cheap fan path.
        """
        if len(self.vertices) < 3:
            return []
        # Imported lazily to keep geometry.py dependency-light at import time.
        from core.triangulate import triangulate

        return triangulate(self.vertices, self.holes, self.normal())
