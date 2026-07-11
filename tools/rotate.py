# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Rotate tool: turn geometry around a point, SketchUp's protractor (Q).

UX:
- If there's a selection, Rotate acts on it; otherwise the first click grabs
  the group / edge / face under the cursor.
- First click places the protractor CENTER. Clicking on a face aligns the
  rotation plane to it (the viewport's work-plane capture); on empty ground
  the rotation is around the vertical axis — the common plan rotation.
- Second click sets the REFERENCE arm (the 0° direction).
- Moving the mouse swings the geometry live; a third click commits. Typing an
  angle + Enter (VCB, degrees) commits exactly; the sign follows the current
  drag direction.
- Esc cancels and puts the geometry back.

Rotation is rigid on the rotated set, but rotating a subset of a connected
model can warp attached faces — the command autofolds them, same as Move.
"""
from __future__ import annotations

import math

from PySide6.QtGui import QVector3D

from core.group import Group
from core.history import (
    RotateGroupCommand,
    RotateVerticesCommand,
    rotation_matrix,
)
from core.i18n import tr
from core.triangulate import plane_axes
from tools.base import Tool, ToolContext
from tools.move import gather_targets


class RotateTool(Tool):
    name = "Rotate"
    shortcut = "Q"
    vcb_label = "Angle"

    def __init__(self) -> None:
        # ``start_point`` is the protractor centre; the name plugs into the
        # viewport's snap and work-plane capture like every drawing tool.
        self.start_point: QVector3D | None = None
        self.ref_point: QVector3D | None = None
        self.hover_point: QVector3D | None = None
        self.work_plane: tuple[QVector3D, QVector3D] | None = None
        self._group: Group | None = None
        self._positions: list[QVector3D] = []
        self._verts: list = []
        self._preview_deg = 0.0

    # ---- Lifecycle ----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        self._reset()

    def on_deactivate(self, viewport) -> None:
        self._revert_preview(viewport)
        self._reset()

    # ---- Spatial input ------------------------------------------------------
    def on_click(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        if self.start_point is None:
            group, positions = gather_targets(ctx)
            if group is None and not positions:
                viewport.flash_status(
                    tr("Select (or click) the geometry to rotate first"))
                return
            self._group = group
            self._positions = positions
            mesh = viewport.scene.mesh
            self._verts = [v for v in (mesh.vertex_at(p) for p in positions)
                           if v is not None]
            self.start_point = ctx.world
            return
        if self.ref_point is None:
            if (ctx.world - self.start_point).length() < 1e-6:
                return
            self.ref_point = ctx.world
            return
        deg = self._angle_to(ctx.world)
        if deg is not None:
            self._commit(viewport, deg)

    def on_hover(self, ctx: ToolContext) -> None:
        self.hover_point = ctx.world
        if self.ref_point is not None:
            deg = self._angle_to(ctx.world)
            if deg is not None:
                self._apply_preview(ctx.viewport, deg)
        ctx.viewport.update()

    def on_value(self, viewport, value) -> bool:
        if self.ref_point is None or isinstance(value, tuple):
            return False
        # The typed angle turns the way the user is currently dragging.
        sign = -1.0 if self._preview_deg < 0 else 1.0
        self._commit(viewport, sign * abs(value))
        return True

    def on_cancel(self, viewport) -> None:
        self._revert_preview(viewport)
        self._reset()
        viewport.update()

    # ---- Visual preview -----------------------------------------------------
    def rubber_band_lines(self):
        if self.start_point is None or self.hover_point is None:
            return []
        # The protractor circle makes the rotation PLANE legible the moment
        # the centre is placed — clicking a slanted face rotates about its
        # normal, and without this cue the axis was invisible.
        segments = list(self._protractor_segments())
        segments.append((self.start_point, self.hover_point))
        if self.ref_point is not None:
            segments.append((self.start_point, self.ref_point))
            deg = self._angle_to(self.hover_point)
            if deg is not None:
                segments.extend(self._arc_segments(deg))
        return segments

    def _protractor_segments(self):
        """A 24-gon circle in the rotation plane around the centre, sized to
        the reference arm (or the cursor distance before the arm is set)."""
        anchor = self.ref_point or self.hover_point
        if anchor is None:
            return []
        r = (anchor - self.start_point).length()
        if r < 1e-9:
            return []
        u, v = plane_axes(self._axis())
        pts = [self.start_point + (u * math.cos(2 * math.pi * k / 24)
                                   + v * math.sin(2 * math.pi * k / 24)) * r
               for k in range(24)]
        return [(pts[k], pts[(k + 1) % 24]) for k in range(24)]

    def value_label(self):
        if self.ref_point is None or self.hover_point is None:
            return None
        deg = self._angle_to(self.hover_point)
        if deg is None:
            return None
        return (f"{deg:+.1f}°", self.hover_point)

    def vcb_caption(self) -> str:
        return "Angle" if self.ref_point is not None else "Radius"

    # ---- Internals ----------------------------------------------------------
    def _axis(self) -> QVector3D:
        if self.work_plane is not None:
            return self.work_plane[1].normalized()
        return QVector3D(0.0, 0.0, 1.0)

    def _angle_to(self, point: QVector3D) -> float | None:
        """Signed degrees from the reference arm to ``point``, in the
        protractor plane."""
        u, v = plane_axes(self._axis())
        a = self.ref_point - self.start_point
        b = point - self.start_point
        a2 = (QVector3D.dotProduct(a, u), QVector3D.dotProduct(a, v))
        b2 = (QVector3D.dotProduct(b, u), QVector3D.dotProduct(b, v))
        if math.hypot(*a2) < 1e-9 or math.hypot(*b2) < 1e-9:
            return None
        ang = math.degrees(math.atan2(b2[1], b2[0]) - math.atan2(a2[1], a2[0]))
        while ang <= -180.0:
            ang += 360.0
        while ang > 180.0:
            ang -= 360.0
        return ang

    def _arc_segments(self, deg: float):
        """Protractor arc between the two arms, at the reference radius."""
        u, v = plane_axes(self._axis())
        a = self.ref_point - self.start_point
        r = a.length() * 0.75
        a0 = math.atan2(QVector3D.dotProduct(a, v), QVector3D.dotProduct(a, u))
        steps = max(2, int(abs(deg) // 10) + 1)
        pts = []
        for k in range(steps + 1):
            t = a0 + math.radians(deg) * k / steps
            pts.append(self.start_point + (u * math.cos(t) + v * math.sin(t)) * r)
        return list(zip(pts, pts[1:]))

    def _rotate_live(self, viewport, step_deg: float) -> None:
        if abs(step_deg) < 1e-12:
            return
        m = rotation_matrix(self.start_point, self._axis(), step_deg)
        if self._group is not None:
            gmesh = self._group.mesh
            for vx in list(gmesh.vertices):
                gmesh.move_vertex(vx, m.map(vx.position) - vx.position)
        else:
            for vx in self._verts:
                viewport.scene.mesh.move_vertex(
                    vx, m.map(vx.position) - vx.position)
        viewport.scene.version += 1

    def _apply_preview(self, viewport, target_deg: float) -> None:
        self._rotate_live(viewport, target_deg - self._preview_deg)
        self._preview_deg = target_deg

    def _revert_preview(self, viewport) -> None:
        if abs(self._preview_deg) > 1e-12:
            self._rotate_live(viewport, -self._preview_deg)
            self._preview_deg = 0.0

    def _commit(self, viewport, deg: float) -> None:
        self._revert_preview(viewport)
        if abs(deg) > 1e-9:
            if self._group is not None:
                viewport.history.execute(RotateGroupCommand(
                    self._group, self.start_point, self._axis(), deg))
            elif self._positions:
                viewport.history.execute(RotateVerticesCommand(
                    self._positions, self.start_point, self._axis(), deg))
        self._reset()
        viewport.update()

    def _reset(self) -> None:
        self.start_point = None
        self.ref_point = None
        self.hover_point = None
        self.work_plane = None
        self._group = None
        self._positions = []
        self._verts = []
        self._preview_deg = 0.0
