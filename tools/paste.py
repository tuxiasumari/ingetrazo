# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Paste tool: drop a copied set of geometry, placing it with the cursor.

After Copy/Cut fills ``viewport.clipboard``, Paste activates this tool: the copied
faces and edges follow the cursor as a live preview (snapping like any draw), and
a click stamps them into the scene. It stays active so you can place several
copies; switch tools (or Esc) when done.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.geometry import Face as PreviewFace
from core.history import AddEdgeCommand, AddFaceCommand, CompoundCommand
from tools.base import Tool, ToolContext


class PasteTool(Tool):
    name = "Paste"
    uses_snap = True  # place exactly on a vertex / edge / face
    wireframe_color = (0.13, 0.17, 0.23, 1.0)
    wireframe_depth_tested = True

    def __init__(self) -> None:
        self._clip = None
        self._offset = QVector3D(0.0, 0.0, 0.0)

    # ---- Lifecycle ----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        self._clip = getattr(viewport, "clipboard", None)
        self._offset = QVector3D(0.0, 0.0, 0.0)

    def on_deactivate(self, viewport) -> None:
        self._clip = None

    # ---- Spatial input ------------------------------------------------------
    def on_hover(self, ctx: ToolContext) -> None:
        if self._clip is None:
            return
        self._offset = ctx.world - self._clip["ref"]
        ctx.viewport.update()

    def on_click(self, ctx: ToolContext) -> None:
        if self._clip is None:
            return
        off = ctx.world - self._clip["ref"]
        commands: list = []
        for loop, holes in self._clip["faces"]:
            commands.append(AddFaceCommand(
                [p + off for p in loop],
                holes=[[p + off for p in h] for h in holes] or None,
                auto=False,
            ))
        # Soft/curve flags travel with the copy; curve ids are remapped to
        # FRESH ones so each pasted circle/arc is its own selectable contour
        # (never entangled with the original's).
        from core.mesh import Mesh
        id_map: dict[int, int] = {}
        for a, b, soft, curve in self._clip["edges"]:
            if curve is not None and curve not in id_map:
                id_map[curve] = Mesh.next_curve_id()
            commands.append(AddEdgeCommand(
                a + off, b + off, soft=soft or None,
                curve=id_map.get(curve)))
        if not commands:
            return
        cmd = commands[0] if len(commands) == 1 else CompoundCommand(commands)
        ctx.viewport.history.execute(cmd)
        ctx.viewport.update()  # stay active for further stamps

    def on_cancel(self, viewport) -> None:
        self._clip = None
        viewport.update()

    # ---- Visual preview -----------------------------------------------------
    def rubber_band_lines(self):
        if self._clip is None:
            return []
        off = self._offset
        segments = []
        for loop, holes in self._clip["faces"]:
            for lp in (loop, *holes):
                n = len(lp)
                for i in range(n):
                    segments.append((lp[i] + off, lp[(i + 1) % n] + off))
        for a, b, _, _ in self._clip["edges"]:
            segments.append((a + off, b + off))
        return segments

    def preview_faces(self):
        if self._clip is None:
            return []
        off = self._offset
        return [
            PreviewFace([p + off for p in loop],
                        [[p + off for p in h] for h in holes])
            for loop, holes in self._clip["faces"]
        ]
