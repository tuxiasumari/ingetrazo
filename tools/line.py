"""Line tool: click points to draw edges; auto-close polygons.

Behavior mirrors SketchUp:
- First click sets the start point of a fresh chain.
- Each next click finalises a segment and chains into the next one.
- Snapping to the chain's first point (snap kind ``"close"``) finishes the
  polygon and resets the chain.
- Esc cancels the chain without committing the pending segment.

The tool exposes ``start_point``, ``hover_point`` and ``chain_first_point``
so the viewport can:
  * draw the rubber-band preview during ``paintGL``,
  * feed those into the snap engine for close-polygon detection.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from tools.base import Tool, ToolContext


class LineTool(Tool):
    name = "Line"
    shortcut = "L"

    def __init__(self) -> None:
        self.start_point: QVector3D | None = None
        self.hover_point: QVector3D | None = None
        self.chain_first_point: QVector3D | None = None

    # ---- Lifecycle ----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        self._reset()

    def on_deactivate(self, viewport) -> None:
        self._reset()
        self.hover_point = None

    # ---- Spatial input ------------------------------------------------------
    def on_click(self, ctx: ToolContext) -> None:
        clicked = ctx.world
        if self.start_point is None:
            self.start_point = clicked
            self.chain_first_point = clicked
        else:
            ctx.viewport.scene.add_edge(self.start_point, clicked)
            if ctx.snap.kind == "close":
                # Polygon finished; start a fresh chain on the next click.
                self._reset()
            else:
                self.start_point = clicked
        ctx.viewport.update()

    def on_hover(self, ctx: ToolContext) -> None:
        self.hover_point = ctx.world
        ctx.viewport.update()

    def on_cancel(self, viewport) -> None:
        self._reset()
        viewport.update()

    def on_value(self, viewport, value: float) -> bool:
        """Commit a segment of exact length in the current rubber-band direction."""
        if (
            self.start_point is None
            or self.hover_point is None
            or value <= 0.0
        ):
            return False
        delta = self.hover_point - self.start_point
        if delta.length() < 1e-9:
            return False
        direction = delta.normalized()
        new_endpoint = self.start_point + direction * value
        viewport.scene.add_edge(self.start_point, new_endpoint)
        # Chain into the next segment, leaving the rubber-band aligned.
        self.start_point = new_endpoint
        self.hover_point = new_endpoint
        viewport.update()
        return True

    # ---- Internals ----------------------------------------------------------
    def _reset(self) -> None:
        self.start_point = None
        self.chain_first_point = None
