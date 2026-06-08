"""Select tool: pick edges and faces and delete them.

Behavior:
- Left click on / near an edge: select that edge. Click on a face interior
  (when no edge is closer): select the face. Edges win ties because they sit
  on top of faces, matching SketchUp.
- Shift-click adds to the current selection; plain click replaces it.
- Left click on empty space: clear the selection.
- Hover highlights whatever the click would pick, so the user sees the target
  before committing.
- Delete / Backspace: remove the selected edges and faces from the scene.
"""
from __future__ import annotations

from PySide6.QtCore import Qt

from core.mesh import Edge, Face
from core.history import (
    CompoundCommand,
    DeleteEdgesCommand,
    DeleteFaceCommand,
)
from tools.base import Tool, ToolContext


def _pt_in_rect(p, rect) -> bool:
    return rect[0] <= p[0] <= rect[2] and rect[1] <= p[1] <= rect[3]


def _seg_seg_2d(p1, p2, p3, p4) -> bool:
    """Whether 2D segments ``p1-p2`` and ``p3-p4`` properly cross."""
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])
    d1 = ccw(p3, p4, p1)
    d2 = ccw(p3, p4, p2)
    d3 = ccw(p1, p2, p3)
    d4 = ccw(p1, p2, p4)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def _seg_rect_overlap(a, b, rect) -> bool:
    """Whether segment ``a-b`` touches the rectangle (endpoint inside or an
    edge crossing) — the crossing-selection test."""
    if _pt_in_rect(a, rect) or _pt_in_rect(b, rect):
        return True
    x0, y0, x1, y1 = rect
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return any(_seg_seg_2d(a, b, corners[i], corners[(i + 1) % 4]) for i in range(4))


class SelectTool(Tool):
    name = "Select"
    shortcut = "S"
    uses_snap = False  # selecting picks geometry; no snap markers
    box_select = True   # supports the rubber-band window / crossing box

    def on_activate(self, viewport) -> None:
        pass

    def on_deactivate(self, viewport) -> None:
        viewport.set_hover(None)

    def _pick(self, viewport, screen_x: float, screen_y: float):
        """Edge under the cursor (screen-space priority), else the front face."""
        edge = viewport.pick_edge(screen_x, screen_y)
        if edge is not None:
            return edge
        return viewport.pick_face(screen_x, screen_y)

    def on_click(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        entity = self._pick(viewport, ctx.screen.x(), ctx.screen.y())
        additive = bool(ctx.modifiers & Qt.ShiftModifier)
        if entity is None:
            if not additive:
                viewport.scene.clear_selection()
        else:
            viewport.scene.select([entity], additive=additive)
        viewport.update()

    def on_hover(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        viewport.set_hover(self._pick(viewport, ctx.screen.x(), ctx.screen.y()))

    def on_box_select(self, viewport, rect, crossing: bool, additive: bool) -> None:
        w2p = viewport._world_to_pixel
        picked = []
        for edge in viewport.scene.edges:
            pa = w2p(edge.a)
            pb = w2p(edge.b)
            if pa is None or pb is None:
                continue
            if crossing:
                if _seg_rect_overlap(pa, pb, rect):
                    picked.append(edge)
            elif _pt_in_rect(pa, rect) and _pt_in_rect(pb, rect):
                picked.append(edge)
        for face in viewport.scene.faces:
            pts = [w2p(v) for v in face.vertices]
            if any(p is None for p in pts):
                continue
            if crossing:
                n = len(pts)
                touches = any(_pt_in_rect(p, rect) for p in pts) or any(
                    _seg_rect_overlap(pts[i], pts[(i + 1) % n], rect) for i in range(n)
                )
                if touches:
                    picked.append(face)
            elif all(_pt_in_rect(p, rect) for p in pts):
                picked.append(face)
        viewport.scene.select(picked, additive=additive)
        viewport.update()

    def on_key(self, viewport, key: int, modifiers: Qt.KeyboardModifiers) -> bool:
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            selection = viewport.scene.selection
            if selection:
                edges = [e for e in selection if isinstance(e, Edge)]
                faces = [f for f in selection if isinstance(f, Face)]
                commands = []
                if edges:
                    commands.append(DeleteEdgesCommand(edges))
                commands.extend(DeleteFaceCommand(f) for f in faces)
                if commands:
                    cmd = commands[0] if len(commands) == 1 else CompoundCommand(commands)
                    viewport.history.execute(cmd)
                    viewport.update()
            return True
        return False
