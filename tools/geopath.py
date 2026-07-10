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

from core.history import AddGeoPathCommand, MoveGeoPathNodeCommand
from georef.geopath import GeoPath
from tools.base import Tool, ToolContext

_CLOSE_PX = 10  # click within this of the first node closes the loop
_NODE_PX = 9    # grab an existing node within this pixel radius


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
        # Node editing (Google-Earth style): grab an existing node, drag, drop.
        self._drag = None          # (GeoPath, index) while editing a node
        self._orig = None          # its original position, for revert

    # ---- Lifecycle ----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        self._reset()

    def on_deactivate(self, viewport) -> None:
        if self._drag is not None:
            self._revert_drag(viewport)
        self._reset()
        self.hover_point = None
        viewport._hover_geo_node = None

    # ---- Spatial input ------------------------------------------------------
    def on_click(self, ctx: ToolContext) -> None:
        # Dropping a dragged node.
        if self._drag is not None:
            self._commit_node_move(ctx.viewport)
            return
        # Idle (not mid-draw): a click on an existing node grabs it to edit;
        # a click on empty ground starts a new path.
        if not self.nodes:
            hit = self._pick_node(ctx)
            if hit is not None:
                self._drag = hit
                self._orig = QVector3D(hit[0].points[hit[1]])
                return
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
        vp = ctx.viewport
        # Live-drag a grabbed node.
        if self._drag is not None:
            path, i = self._drag
            path.points[i] = QVector3D(ctx.world.x(), ctx.world.y(), 0.0)
            vp.scene.version += 1
            vp.update()
            return
        self.hover_point = QVector3D(ctx.world.x(), ctx.world.y(), 0.0)
        # Highlight a node under the cursor when idle (a grab target).
        vp._hover_geo_node = self._pick_node(ctx) if not self.nodes else None
        vp.update()

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
        if self._drag is not None:
            self._revert_drag(viewport)
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
    def _pick_node(self, ctx: ToolContext):
        """The ``(GeoPath, index)`` node nearest the cursor within reach, else None."""
        vp = ctx.viewport
        best, best_d = None, _NODE_PX
        for path in vp.scene.geo_paths:
            for i, p in enumerate(path.points):
                q = vp._world_to_pixel(vp.drape(p))
                if q is None:
                    continue
                d = math.hypot(q[0] - ctx.screen.x(), q[1] - ctx.screen.y())
                if d < best_d:
                    best_d, best = d, (path, i)
        return best

    def _commit_node_move(self, viewport) -> None:
        path, i = self._drag
        new = QVector3D(path.points[i])
        path.points[i] = QVector3D(self._orig)        # revert the live edit…
        viewport.history.execute(                     # …then apply as one command
            MoveGeoPathNodeCommand(path, i, new))
        self._drag = None
        self._orig = None
        viewport.update()

    def _revert_drag(self, viewport) -> None:
        path, i = self._drag
        path.points[i] = QVector3D(self._orig)
        viewport.scene.version += 1
        self._drag = None
        self._orig = None

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
