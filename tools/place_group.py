# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Place-component tool: a freshly built Group follows the cursor and a click
drops it — SketchUp's component-placement feel.

The group is anchored at the CENTRE OF ITS BASE (bbox bottom), so by default
it *tries* to sit on the ground plane: hovering empty ground lands the base
at z=0 (the viewport's work plane), hovering a slab lands it on the slab —
guidance, not a constraint (any snap target wins). Esc discards the pending
component without inserting anything.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.history import InsertGroupCommand
from tools.base import Tool, ToolContext

#: Preview segments are plenty for the bundled starters; a hard cap keeps a
#: huge future component from turning the hover into a slideshow.
_MAX_PREVIEW_EDGES = 2000


class PlaceGroupTool(Tool):
    name = "Place component"
    uses_snap = True
    wireframe_color = (0.13, 0.17, 0.23, 1.0)
    wireframe_depth_tested = False      # the pending component floats on top

    def __init__(self, group, align_to_face: bool = False) -> None:
        self._group = group
        self._anchor = self._base_center(group)
        self._offset = QVector3D(0.0, 0.0, 0.0)
        # SketchUp's 3D-text glue: when enabled, hovering a FACE re-orients
        # the group so its front (-Y) points along the face normal — a sign
        # on a wall, text lying on a slab. No face → upright on the ground.
        self._align = align_to_face
        self._face_normal: QVector3D | None = None
        # Local preview segments, relative to the anchor (computed once).
        self._segments = [
            (QVector3D(e.a) - self._anchor, QVector3D(e.b) - self._anchor)
            for e in group.mesh.edges[:_MAX_PREVIEW_EDGES]
        ]
        if not self._segments:
            self._segments = self._bbox_segments(group)

    def _bbox_segments(self, group):
        """Wireframe box fallback for meshes without edges (e.g. billboards)."""
        xs, ys, zs = [], [], []
        for f in group.mesh.faces:
            for v in f.loop:
                p = v.position
                xs.append(p.x()), ys.append(p.y()), zs.append(p.z())
        if not xs:
            return []
        a = QVector3D(min(xs), min(ys), min(zs)) - self._anchor
        b = QVector3D(max(xs), max(ys), max(zs)) - self._anchor
        c = [QVector3D(x, y, z)
             for z in (a.z(), b.z()) for y in (a.y(), b.y())
             for x in (a.x(), b.x())]
        idx = [(0, 1), (1, 3), (3, 2), (2, 0), (4, 5), (5, 7), (7, 6),
               (6, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
        return [(c[i], c[j]) for i, j in idx]

    @staticmethod
    def _base_center(group) -> QVector3D:
        xs, ys, zs = [], [], []
        for v in group.mesh.vertices:
            p = v.position
            xs.append(p.x()), ys.append(p.y()), zs.append(p.z())
        if not xs:
            return QVector3D(0.0, 0.0, 0.0)
        return QVector3D((min(xs) + max(xs)) / 2.0,
                         (min(ys) + max(ys)) / 2.0,
                         min(zs))

    # ---- Lifecycle ----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        pass

    def on_deactivate(self, viewport) -> None:
        self._group = None

    # ---- Face alignment (3D text) --------------------------------------------
    @staticmethod
    def _face_frame(normal: QVector3D):
        """Orthonormal frame turning the group's local axes onto a face:
        local -Y (its front) → the face normal, keeping the text's up
        world-up on walls and world-north when lying on a horizontal face.
        Returns ``(right, y_axis, up)`` = images of local +X, +Y, +Z."""
        n = QVector3D(normal).normalized()
        hint = (QVector3D(0.0, 1.0, 0.0) if abs(n.z()) > 0.99
                else QVector3D(0.0, 0.0, 1.0))
        right = QVector3D.crossProduct(hint, n)
        if right.length() < 1e-9:
            right = QVector3D(1.0, 0.0, 0.0)
        right = right.normalized()
        up = QVector3D.crossProduct(n, right).normalized()
        return right, -n, up

    def _rotate(self, p: QVector3D) -> QVector3D:
        if self._face_normal is None:
            return QVector3D(p)
        right, y_axis, up = self._face_frame(self._face_normal)
        return right * p.x() + y_axis * p.y() + up * p.z()

    def _update_alignment(self, ctx: ToolContext) -> None:
        if not self._align:
            return
        try:
            hit = ctx.viewport.pick_face_any(ctx.screen.x(), ctx.screen.y())
        except Exception:
            hit = None
        face = hit[0] if isinstance(hit, tuple) else hit
        self._face_normal = face.normal() if face is not None else None

    # ---- Spatial input ------------------------------------------------------
    def on_hover(self, ctx: ToolContext) -> None:
        if self._group is None:
            return
        self._update_alignment(ctx)
        self._offset = ctx.world - self._rotate(self._anchor)
        ctx.viewport.update()

    def on_click(self, ctx: ToolContext) -> None:
        if self._group is None:
            return
        self._update_alignment(ctx)
        shift = ctx.world - self._rotate(self._anchor)
        # Re-pose the group's isolated mesh BEFORE it enters the scene
        # (registry-safe per-vertex move; undo of the insert removes the
        # whole group, so no separate move step lands in history).
        for v in list(self._group.mesh.vertices):
            target = self._rotate(v.position) + shift
            delta = target - v.position
            if delta.length() > 1e-9:
                self._group.mesh.move_vertex(v, delta)
        group = self._group
        self._group = None
        ctx.viewport.history.execute(InsertGroupCommand(group))
        ctx.viewport.flash_status(self._placed_message())
        window = ctx.viewport.window()
        if hasattr(window, "_activate_tool"):
            window._activate_tool("select")
        ctx.viewport.update()

    @staticmethod
    def _placed_message() -> str:
        from core.i18n import tr
        return tr("Component placed — Move (M) adjusts it")

    def on_cancel(self, viewport) -> None:
        self._group = None
        viewport.update()

    # ---- Visual preview -----------------------------------------------------
    def rubber_band_lines(self):
        if self._group is None:
            return []
        off = self._offset
        if self._face_normal is not None:
            return [(self._rotate(a + self._anchor) + off,
                     self._rotate(b + self._anchor) + off)
                    for a, b in self._segments]
        return [(a + self._anchor + off, b + self._anchor + off)
                for a, b in self._segments]
