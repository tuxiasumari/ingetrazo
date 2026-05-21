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
    """A planar polygon defined by its vertex loop.

    No internal triangulation is stored — consumers (renderer, picker)
    triangulate on the fly. For MVP we use fan triangulation, which is
    fine for the convex polygons Wasia produces today (rectangles +
    closed line chains the user draws). Ear-clipping for non-convex
    polygons can land later.

    The face normal is derived with Newell's method so it works for
    arbitrary polygons (including non-planar ones, where it returns the
    best-fit plane normal).
    """

    vertices: list[QVector3D] = field(default_factory=list)

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
