# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""GeoPath tool: trace a terrain/road path over the base map (Track G).

Draws with the familiar SketchUp feel — click nodes, rubber-band preview, type a
segment length in the VCB — but the result is a :class:`~georef.geopath.GeoPath`
in ``scene.geo_paths``, **never** mesh geometry. It stays on the Z=0 ground
plane (the flat base map); the modelling topology engine is untouched.

Finish a path with double-click or Enter (open), or by clicking back on the
first node (closed loop, e.g. a boundary). Esc discards the in-progress path.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QVector3D

from core.history import AddGeoPathCommand
from georef.geopath import GeoPath
from tools.base import Tool, ToolContext

_CLOSE_PX = 10  # click within this of the first node closes the loop


class GeoPathTool(Tool):
    name = "Path"
    shortcut = "T"
    vcb_label = "Length"
    uses_snap = False  # a georef trace snaps to nothing in the modelling mesh

    def __init__(self) -> None:
        self.nodes: list[QVector3D] = []
        self.hover_point: QVector3D | None = None
        # Fixed Z=0 ground plane — the base map. The viewport reads this so
        # every click lands flat on the imagery regardless of camera tilt.
        self.work_plane = (QVector3D(0.0, 0.0, 0.0), QVector3D(0.0, 0.0, 1.0))

    # ---- Lifecycle ----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        self._reset()

    def on_deactivate(self, viewport) -> None:
        self._reset()
        self.hover_point = None

    # ---- Spatial input ------------------------------------------------------
    def on_click(self, ctx: ToolContext) -> None:
        pt = QVector3D(ctx.world.x(), ctx.world.y(), 0.0)
        if len(self.nodes) >= 3 and self._near_first(ctx):
            self._finish(ctx.viewport, closed=True)
            return
        self.nodes.append(pt)
        ctx.viewport.update()

    def on_double_click(self, ctx: ToolContext) -> None:
        # Qt sends a press before the double-click, so the point is already in;
        # just finish the open path (don't add a duplicate node).
        self._finish(ctx.viewport, closed=False)

    def on_hover(self, ctx: ToolContext) -> None:
        self.hover_point = QVector3D(ctx.world.x(), ctx.world.y(), 0.0)
        ctx.viewport.update()

    def on_value(self, viewport, value) -> bool:
        if not self.nodes or self.hover_point is None:
            return False
        if isinstance(value, tuple):
            if len(value) != 3:
                return False
            last = self.nodes[-1]
            nxt = QVector3D(last.x() + value[0], last.y() + value[1], 0.0)
        else:
            if value <= 0.0:
                return False
            direction = self.hover_point - self.nodes[-1]
            if direction.length() < 1e-9:
                return False
            nxt = self.nodes[-1] + direction.normalized() * value
            nxt.setZ(0.0)
        self.nodes.append(nxt)
        self.hover_point = nxt
        viewport.update()
        return True

    def on_key(self, viewport, key, modifiers) -> bool:
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._finish(viewport, closed=False)
            return True
        return False

    def on_cancel(self, viewport) -> None:
        self._reset()
        viewport.update()

    # ---- Preview ------------------------------------------------------------
    def rubber_band_lines(self):
        segs = list(zip(self.nodes, self.nodes[1:]))
        if self.nodes and self.hover_point is not None:
            segs.append((self.nodes[-1], self.hover_point))
        return segs

    def value_label(self):
        if not self.nodes or self.hover_point is None:
            return None
        d = self.hover_point - self.nodes[-1]
        mid = (self.nodes[-1] + self.hover_point) * 0.5
        return (f"{d.length():.2f} m", mid)

    # ---- Internals ----------------------------------------------------------
    def _near_first(self, ctx: ToolContext) -> bool:
        first = ctx.viewport._world_to_pixel(self.nodes[0])
        if first is None:
            return False
        return math.hypot(first[0] - ctx.screen.x(),
                          first[1] - ctx.screen.y()) <= _CLOSE_PX

    def _finish(self, viewport, closed: bool) -> None:
        if len(self.nodes) >= 2:
            path = GeoPath(self.nodes, closed=closed)
            viewport.history.execute(AddGeoPathCommand(path))
        self._reset()
        viewport.update()

    def _reset(self) -> None:
        self.nodes = []
