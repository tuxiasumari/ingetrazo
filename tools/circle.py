# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Circle and Polygon tools: a centre click, then a radius.

Both draw a regular N-gon on the work plane (a circle is just a many-sided one,
SketchUp-style). First click sets the centre; moving sets the radius; a second
click (or typing the radius in the VCB) commits the loop + face. One vertex
points toward the cursor, so a hexagon's orientation follows the mouse.
"""
from __future__ import annotations

import math

from PySide6.QtGui import QVector3D

from core.edits import build_add_edges
from core.history import AddFaceCommand
from core.i18n import tr
from core.triangulate import plane_axes
from tools.base import Tool, ToolContext


class _RadialTool(Tool):
    """Shared centre+radius regular-polygon tool. Subclasses set ``sides``."""

    sides: int = 24
    vcb_label = "Radius"

    def vcb_caption(self) -> str:
        """SketchUp shows 'Sides' before the centre, 'Radius' after.

        Returns the English source label; the status bar translates it.
        """
        return "Radius" if self.start_point is not None else "Sides"

    def __init__(self) -> None:
        self.start_point: QVector3D | None = None   # centre (also drives work plane)
        self.hover_point: QVector3D | None = None
        self.work_plane: tuple[QVector3D, QVector3D] | None = None

    # ---- Lifecycle ----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        self._reset()

    def on_deactivate(self, viewport) -> None:
        self._reset()
        self.hover_point = None

    # ---- Spatial input ------------------------------------------------------
    def on_click(self, ctx: ToolContext) -> None:
        if self.start_point is None:
            self.start_point = ctx.world
            return
        pts = self._points(self.start_point, ctx.world)
        if pts:
            self._commit(ctx.viewport, pts)

    def on_hover(self, ctx: ToolContext) -> None:
        self.hover_point = ctx.world
        ctx.viewport.update()

    def on_value(self, viewport, value) -> bool:
        """Before the centre is placed, a typed number sets the **side count**
        (SketchUp: type sides + Enter); after it, the number is the **radius**."""
        if isinstance(value, tuple):
            return False
        if self.start_point is None:
            n = int(round(value))
            if n >= 3:
                self.sides = n
                viewport.flash_status(tr("{n} sides", n=n))
                return True
            return False
        if self.hover_point is None or value <= 0.0:
            return False
        # Keep the cursor's direction, override only the radius.
        u, v = self._axes()
        d = self.hover_point - self.start_point
        ang = math.atan2(QVector3D.dotProduct(d, v), QVector3D.dotProduct(d, u))
        rim = self.start_point + (u * math.cos(ang) + v * math.sin(ang)) * value
        pts = self._points(self.start_point, rim)
        if pts:
            self._commit(viewport, pts)
        return True

    def on_cancel(self, viewport) -> None:
        self._reset()
        viewport.update()

    # ---- Preview ------------------------------------------------------------
    def rubber_band_lines(self):
        if self.start_point is None or self.hover_point is None:
            return []
        pts = self._points(self.start_point, self.hover_point)
        return [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))] \
            if pts else []

    def value_label(self):
        if self.start_point is None or self.hover_point is None:
            return None
        r = (self.hover_point - self.start_point).length()
        return (f"R {r:.2f} m  ({self.sides} lados)", self.hover_point)

    # ---- Internals ----------------------------------------------------------
    def _axes(self) -> tuple[QVector3D, QVector3D]:
        if self.work_plane is None:
            return QVector3D(1.0, 0.0, 0.0), QVector3D(0.0, 1.0, 0.0)
        return plane_axes(self.work_plane[1])

    def _points(self, center: QVector3D, rim: QVector3D) -> list[QVector3D]:
        u, v = self._axes()
        d = rim - center
        du, dv = QVector3D.dotProduct(d, u), QVector3D.dotProduct(d, v)
        r = math.hypot(du, dv)
        if r < 1e-6:
            return []
        a0 = math.atan2(dv, du)  # one vertex toward the cursor
        out = []
        for k in range(self.sides):
            a = a0 + 2.0 * math.pi * k / self.sides
            out.append(center + (u * math.cos(a) + v * math.sin(a)) * r)
        return out

    def _commit(self, viewport, pts: list[QVector3D]) -> None:
        n = len(pts)
        segments = [(pts[i], pts[(i + 1) % n]) for i in range(n)]
        # The outline is drawn (a 24-segment circle reads round). What hides is
        # only a *swept* curve's vertical facets — done by Push/Pull, not here.
        cmd = build_add_edges(
            viewport.scene, segments, detect_faces=False,
            extra=[AddFaceCommand(list(pts))])
        viewport.history.execute(cmd)
        self._reset()
        viewport.update()

    def _reset(self) -> None:
        self.start_point = None
        self.work_plane = None


class CircleTool(_RadialTool):
    name = "Circle"
    shortcut = "C"
    sides = 24


class PolygonTool(_RadialTool):
    name = "Polygon"
    shortcut = "G"   # P is taken by perspective toggle
    sides = 6
