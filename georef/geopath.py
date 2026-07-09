# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""GeoPath — a traced terrain/road polyline (Track G).

A first-class georef entity, deliberately **separate from the modelling engine**
(``Scene.mesh``). Per the heterogeneous-Scene principle, a road or a boundary
drawn over the base map is not topology-engine geometry — it never welds, heals
or push/pulls. It lives in ``Scene.geo_paths`` alongside ``dimensions`` and the
``tile_layer``, has its own draw tool, node editing, render and profile.

An ordered list of nodes in local scene metres (Z is the drawing plane — Z=0 on
the flat base map today, terrain height once G2 drapes). ``closed`` distinguishes
an open path (a road/pipeline) from a closed loop (a lot/boundary). The profile
tool samples terrain elevation *under* the path.
"""
from __future__ import annotations

import math

from PySide6.QtGui import QVector3D


class GeoPath:
    """An ordered polyline of local-metre nodes (a road, boundary, alignment)."""

    def __init__(self, points, closed: bool = False, name: str = "") -> None:
        self.points: list[QVector3D] = [QVector3D(p) for p in points]
        self.closed = bool(closed)
        self.name = name

    # ---- Geometry -----------------------------------------------------------
    def segments(self):
        """Yield ``(a, b)`` node pairs, including the closing edge if closed."""
        pts = self.points
        for a, b in zip(pts, pts[1:]):
            yield a, b
        if self.closed and len(pts) > 2:
            yield pts[-1], pts[0]

    def length(self) -> float:
        """Horizontal (XY) length along the path."""
        return sum(math.hypot(b.x() - a.x(), b.y() - a.y())
                   for a, b in self.segments())

    def profile_points(self) -> list[QVector3D]:
        """The ordered nodes the profile walks (closes the loop when closed)."""
        pts = list(self.points)
        if self.closed and len(pts) > 2:
            pts.append(QVector3D(pts[0]))
        return pts

    # ---- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict:
        entry = {"points": [[p.x(), p.y(), p.z()] for p in self.points]}
        if self.closed:
            entry["closed"] = True
        if self.name:
            entry["name"] = self.name
        return entry

    @classmethod
    def from_dict(cls, data: dict) -> "GeoPath":
        return cls([QVector3D(*p) for p in data.get("points", [])],
                   closed=data.get("closed", False),
                   name=data.get("name", ""))
