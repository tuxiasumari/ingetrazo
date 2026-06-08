"""Offset tool: offset a face's boundary in its plane (walls with thickness).

SketchUp's Offset (F): pick a face, drag (or type a distance) and a parallel
loop appears offset from the boundary. The face splits into a ring (the wall
footprint) and an inner face (the room), so the ring can then be pushed up into
walls with thickness — the casita's first hito.

Inward (toward the interior) is the common case for walls; dragging the cursor
outside the face offsets outward instead. The exact thickness is usually typed
into the VCB.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.history import AddFaceCommand, CompoundCommand, DeleteFaceCommand
from core.mesh import Face
from core.topology import offset_loop
from tools.base import Tool, ToolContext


def _point_segment_distance(p: QVector3D, a: QVector3D, b: QVector3D) -> float:
    ab = b - a
    length_sq = QVector3D.dotProduct(ab, ab)
    if length_sq < 1e-12:
        return (p - a).length()
    t = max(0.0, min(1.0, QVector3D.dotProduct(p - a, ab) / length_sq))
    return (p - (a + ab * t)).length()


class OffsetTool(Tool):
    name = "Offset"
    shortcut = "F"
    uses_snap = False  # picks a face; no snap markers
    vcb_label = "Offset"
    wireframe_color = (0.13, 0.17, 0.23, 1.0)
    wireframe_depth_tested = True

    def __init__(self) -> None:
        self.hovered_face: Face | None = None
        self.base_face: Face | None = None
        self.distance: float = 0.0  # signed; >0 inward, <0 outward
        self.dragging: bool = False
        self._loop: list[QVector3D] = []
        self._normal: QVector3D | None = None
        self._ref_point: QVector3D | None = None   # a point on the reference edge
        self._ref_inward: QVector3D | None = None   # its in-plane inward normal

    # ---- Lifecycle ----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        self._reset()

    def on_deactivate(self, viewport) -> None:
        viewport.set_hover(None)
        self._reset()
        viewport.update()

    # ---- Spatial input ------------------------------------------------------
    def on_hover(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        if not self.dragging:
            self.hovered_face = viewport.pick_face(ctx.screen.x(), ctx.screen.y())
            viewport.set_hover(self.hovered_face)
            return
        cursor = self._cursor_on_plane(viewport, ctx.screen.x(), ctx.screen.y())
        if cursor is not None and self._ref_point is not None:
            self.distance = QVector3D.dotProduct(cursor - self._ref_point, self._ref_inward)
        viewport.update()

    def on_click(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        if not self.dragging:
            face = self.hovered_face
            if face is None or len(face.vertices) < 3:
                return
            self.base_face = face
            self._loop = [QVector3D(v) for v in face.vertices]
            self._normal = face.normal()
            self.distance = 0.0
            self.dragging = True
            self._pick_reference(viewport, ctx.screen.x(), ctx.screen.y())
            viewport.set_hover(None)
            viewport.update()
            return
        if abs(self.distance) < 1e-6:
            return
        self._commit(viewport)

    def on_value(self, viewport, value) -> bool:
        if isinstance(value, tuple):
            return False
        if not self.dragging or self.base_face is None or value <= 0.0:
            return False
        # Keep the side the user is dragging toward; default to inward.
        sign = -1.0 if self.distance < 0.0 else 1.0
        self.distance = sign * value
        self._commit(viewport)
        return True

    def on_cancel(self, viewport) -> None:
        viewport.set_hover(None)
        self._reset()
        viewport.update()

    # ---- Visual preview -----------------------------------------------------
    def rubber_band_lines(self):
        if not self.dragging or not self._loop or abs(self.distance) < 1e-6:
            return []
        off = offset_loop(self._loop, self._normal, self.distance)
        if off is None:
            return []
        n = len(off)
        return [(off[i], off[(i + 1) % n]) for i in range(n)]

    # ---- Internals ----------------------------------------------------------
    def _pick_reference(self, viewport, sx, sy) -> None:
        """Lock onto the boundary edge nearest the click; offset is measured as
        the cursor's perpendicular distance from it (so dragging across that edge
        flips inward/outward naturally)."""
        cursor = self._cursor_on_plane(viewport, sx, sy)
        loop = self._loop
        count = len(loop)
        best_i, best_d = 0, float("inf")
        for i in range(count):
            a, b = loop[i], loop[(i + 1) % count]
            d = _point_segment_distance(cursor, a, b) if cursor is not None else 0.0
            if d < best_d:
                best_d, best_i = d, i
        a, b = loop[best_i], loop[(best_i + 1) % count]
        e = (b - a).normalized()
        self._ref_point = QVector3D(a)
        self._ref_inward = QVector3D.crossProduct(self._normal.normalized(), e).normalized()

    def _cursor_on_plane(self, viewport, sx, sy):
        """Cast the cursor ray onto the base face's plane."""
        origin, direction = viewport._pixel_to_ray(sx, sy)
        if origin is None or direction is None:
            return None
        n = self._normal
        denom = QVector3D.dotProduct(direction, n)
        if abs(denom) < 1e-9:
            return None
        t = QVector3D.dotProduct(self.base_face.centroid() - origin, n) / denom
        return origin + direction * t

    def _commit(self, viewport) -> None:
        off = offset_loop(self._loop, self._normal, self.distance)
        if off is None:
            self._reset()
            viewport.update()
            return
        # Inward: the original boundary is the outer ring, the offset is the hole
        # and the inner face. Outward: swapped.
        if self.distance > 0:
            outer, inner = self._loop, off
        else:
            outer, inner = off, self._loop
        commands = [
            DeleteFaceCommand(self.base_face),
            AddFaceCommand(list(outer), auto=False, holes=[list(inner)]),  # ring
            AddFaceCommand(list(inner), auto=False),                        # inner
        ]
        viewport.history.execute(CompoundCommand(commands))
        self._reset()
        viewport.update()

    def _reset(self) -> None:
        self.hovered_face = None
        self.base_face = None
        self.distance = 0.0
        self.dragging = False
        self._loop = []
        self._normal = None
        self._ref_point = None
        self._ref_inward = None
