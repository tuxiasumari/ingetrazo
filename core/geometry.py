"""Geometry primitives: vertex, edge, face, solid.

Only ``Edge`` is implemented so far — enough to support the line tool.
Faces and solids land when push/pull is implemented.

``Edge`` uses identity-based equality so each instance is a distinct
entity that can be tracked in a selection ``set``. Two edges with the
same endpoints are still treated as separate objects.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QVector3D


@dataclass(eq=False)
class Edge:
    """A line segment between two 3D points (identity-equal)."""

    a: QVector3D
    b: QVector3D

    def length(self) -> float:
        return (self.b - self.a).length()
