"""Push/Pull tool: extrude a face along its normal.

UX (SketchUp-like):
- Hover a face; the cursor picks the front-most face under it.
- First click: lock onto that face and start a drag along its normal.
- Subsequent mouse motion slides the extrusion preview (wireframe of
  the future box) along the normal axis. Length is shown near the
  midpoint, same overlay as the line tool.
- Second click commits at the current distance.
- Typing a number + Enter (VCB) commits at exactly that distance,
  preserving the current direction's sign.
- Esc cancels without committing.

Commit creates:
- N new edges connecting each base vertex to the matching top vertex.
- N new edges around the top face boundary.
- 1 new top face.
- N new side faces (quads, one per base edge).

The original base face stays in place — it becomes the box's "bottom".
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.geometry import Face
from core.history import AddEdgeCommand, AddFaceCommand, CompoundCommand
from tools.base import Tool, ToolContext


class PushPullTool(Tool):
    name = "Push / Pull"
    shortcut = "U"

    def __init__(self) -> None:
        self.hovered_face: Face | None = None
        self.base_face: Face | None = None
        self.extrusion: float = 0.0  # signed distance along normal
        self.dragging: bool = False

    # ---- Lifecycle ----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        self._reset()

    def on_deactivate(self, viewport) -> None:
        self._reset()

    # ---- Spatial input ------------------------------------------------------
    def on_hover(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        if not self.dragging:
            self.hovered_face = viewport.pick_face(ctx.screen.x(), ctx.screen.y())
            viewport.update()
            return

        if self.base_face is None:
            return
        anchor = self.base_face.centroid()
        normal = self.base_face.normal()
        projected = viewport._project_to_lock_line(
            anchor, normal, ctx.screen.x(), ctx.screen.y()
        )
        self.extrusion = QVector3D.dotProduct(projected - anchor, normal)
        viewport.update()

    def on_click(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        if not self.dragging:
            face = self.hovered_face
            if face is None:
                return
            self.base_face = face
            self.extrusion = 0.0
            self.dragging = True
            viewport.update()
            return

        # Already dragging — second click commits.
        if abs(self.extrusion) < 1e-6:
            # No-op extrusion; just stay in drag mode so the user can keep going.
            return
        self._commit(viewport)

    def on_value(self, viewport, value: float) -> bool:
        if not self.dragging or self.base_face is None or value <= 0.0:
            return False
        # Keep the sign the user has been dragging toward; default to +normal.
        sign = -1.0 if self.extrusion < 0.0 else 1.0
        self.extrusion = sign * value
        self._commit(viewport)
        return True

    def on_cancel(self, viewport) -> None:
        self._reset()
        viewport.update()

    # ---- Visual preview -----------------------------------------------------
    def rubber_band_lines(self):
        if not self.dragging or self.base_face is None:
            return []
        n = self.base_face.normal()
        d = self.extrusion
        base = self.base_face.vertices
        top = [v + n * d for v in base]
        segments = []
        # Top boundary
        count = len(top)
        for i in range(count):
            segments.append((top[i], top[(i + 1) % count]))
        # Vertical edges
        for v_base, v_top in zip(base, top):
            segments.append((v_base, v_top))
        return segments

    def value_label(self):
        """Return ``(text, midpoint_world)`` for the floating distance label."""
        if not self.dragging or self.base_face is None:
            return None
        anchor = self.base_face.centroid()
        normal = self.base_face.normal()
        midpoint = anchor + normal * (self.extrusion * 0.5)
        return (f"{abs(self.extrusion):.2f} m", midpoint)

    # ---- Internals ----------------------------------------------------------
    def _commit(self, viewport) -> None:
        face = self.base_face
        if face is None or abs(self.extrusion) < 1e-6:
            self._reset()
            viewport.update()
            return

        normal = face.normal()
        d = self.extrusion
        base = face.vertices
        top = [v + normal * d for v in base]
        count = len(base)

        commands: list = []

        # Top boundary edges.
        for i in range(count):
            commands.append(AddEdgeCommand(top[i], top[(i + 1) % count]))

        # Vertical edges (base i to top i).
        for i in range(count):
            commands.append(AddEdgeCommand(base[i], top[i]))

        # Top face.
        commands.append(AddFaceCommand(list(top)))

        # Side faces (quads).
        for i in range(count):
            j = (i + 1) % count
            commands.append(AddFaceCommand([base[i], base[j], top[j], top[i]]))

        viewport.history.execute(CompoundCommand(commands))
        self._reset()
        viewport.update()

    def _reset(self) -> None:
        self.hovered_face = None
        self.base_face = None
        self.extrusion = 0.0
        self.dragging = False
