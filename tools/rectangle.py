"""Rectangle tool: two clicks define opposite corners on the work plane.

Output is a closed loop of four edges, axis-aligned to the world X / Y
axes (Z=0 work plane). All four edges are committed as a single
:class:`CompoundCommand` so Undo treats the rectangle as one atomic step.

Notes:
- Axis lock and reference lock are accepted by the snap engine but rarely
  useful for a rectangle (they degenerate it). The second corner does
  benefit from endpoint / origin snaps to align with existing geometry.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.history import AddEdgeCommand, AddFaceCommand, CompoundCommand
from tools.base import Tool, ToolContext


class RectangleTool(Tool):
    name = "Rectangle"
    shortcut = "R"

    def __init__(self) -> None:
        self.start_point: QVector3D | None = None
        self.hover_point: QVector3D | None = None
        # Aliased so the snap engine's close-polygon path doesn't fire on
        # the rectangle's first corner.
        self.chain_first_point: QVector3D | None = None

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
        corners = self._corners(self.start_point, ctx.world)
        commands = [
            AddEdgeCommand(corners[i], corners[(i + 1) % 4]) for i in range(4)
        ]
        commands.append(AddFaceCommand(list(corners)))
        ctx.viewport.history.execute(CompoundCommand(commands))
        self._reset()
        ctx.viewport.update()

    def on_hover(self, ctx: ToolContext) -> None:
        self.hover_point = ctx.world
        ctx.viewport.update()

    def on_cancel(self, viewport) -> None:
        self._reset()
        viewport.update()

    # ---- Visual preview -----------------------------------------------------
    def rubber_band_lines(self):
        if self.start_point is None or self.hover_point is None:
            return []
        c = self._corners(self.start_point, self.hover_point)
        return [
            (c[0], c[1]),
            (c[1], c[2]),
            (c[2], c[3]),
            (c[3], c[0]),
        ]

    # ---- Internals ----------------------------------------------------------
    @staticmethod
    def _corners(a: QVector3D, b: QVector3D) -> list[QVector3D]:
        z = a.z()
        return [
            QVector3D(a.x(), a.y(), z),
            QVector3D(b.x(), a.y(), z),
            QVector3D(b.x(), b.y(), z),
            QVector3D(a.x(), b.y(), z),
        ]

    def _reset(self) -> None:
        self.start_point = None
        self.chain_first_point = None
