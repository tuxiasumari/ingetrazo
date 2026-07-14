# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Scale tool: resize geometry uniformly about an anchor point (S).

UX:
- If there's a selection, Scale acts on it; otherwise the first click grabs
  the group / edge / face under the cursor.
- First click places the ANCHOR — the point that stays fixed (a corner, an
  axis intersection…). Second click sets the REFERENCE distance (= factor
  1.0). Moving the mouse resizes live: factor = cursor distance / reference
  distance. Third click commits; typing a factor + Enter (VCB) commits
  exactly. A negative factor mirrors through the anchor.
- Esc cancels and restores.

Uniform (all axes) about an explicit anchor — more precise for civil work
than grip-dragging a bounding box; per-axis grips can come later.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.group import Group
from core.history import ScaleGroupCommand, ScaleVerticesCommand, scale_matrix
from core.i18n import tr
from tools.base import Tool, ToolContext
from tools.move import gather_targets

_MIN_FACTOR = 1e-4


class ScaleTool(Tool):
    name = "Scale"
    shortcut = "S"
    vcb_label = "Factor"

    def __init__(self) -> None:
        self.start_point: QVector3D | None = None   # the anchor
        self.ref_point: QVector3D | None = None     # distance = factor 1.0
        self.hover_point: QVector3D | None = None
        self._group: Group | None = None
        self._positions: list[QVector3D] = []
        self._verts: list = []
        self._preview_factor = 1.0

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
                    tr("Select (or click) the geometry to scale first"))
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
        f = self._factor_to(ctx.world)
        if f is not None:
            self._commit(viewport, f)

    def on_hover(self, ctx: ToolContext) -> None:
        self.hover_point = ctx.world
        if self.ref_point is not None:
            f = self._factor_to(ctx.world)
            if f is not None:
                self._apply_preview(ctx.viewport, f)
        ctx.viewport.update()

    def on_value(self, viewport, value) -> bool:
        if self.ref_point is None or isinstance(value, tuple):
            return False
        if abs(value) < _MIN_FACTOR:
            return False
        self._commit(viewport, value)               # negative mirrors
        return True

    def on_cancel(self, viewport) -> None:
        self._revert_preview(viewport)
        self._reset()
        viewport.update()

    # ---- Visual preview -----------------------------------------------------
    def rubber_band_lines(self):
        if self.start_point is None or self.hover_point is None:
            return []
        segments = [(self.start_point, self.hover_point)]
        if self.ref_point is not None:
            segments.append((self.start_point, self.ref_point))
        return segments

    def value_label(self):
        if self.ref_point is None or self.hover_point is None:
            return None
        f = self._factor_to(self.hover_point)
        if f is None:
            return None
        return (f"×{f:.3f}", self.hover_point)

    def vcb_caption(self) -> str:
        return "Factor" if self.ref_point is not None else "Reference"

    # ---- Internals ----------------------------------------------------------
    def _factor_to(self, point: QVector3D) -> float | None:
        ref = (self.ref_point - self.start_point).length()
        cur = (point - self.start_point).length()
        if ref < 1e-9 or cur / ref < _MIN_FACTOR:
            return None
        return cur / ref

    def _scale_live(self, viewport, step: float) -> None:
        if abs(step - 1.0) < 1e-12:
            return
        m = scale_matrix(self.start_point, step)
        if self._group is not None:
            if getattr(self._group, "xform", None) is not None:
                self._group.xform = m * self._group.xform   # instance: O(1)
            else:
                gmesh = self._group.mesh
                for vx in list(gmesh.vertices):
                    gmesh.move_vertex(vx, m.map(vx.position) - vx.position)
        else:
            for vx in self._verts:
                viewport.scene.mesh.move_vertex(
                    vx, m.map(vx.position) - vx.position)
        viewport.scene.version += 1

    def _apply_preview(self, viewport, target: float) -> None:
        self._scale_live(viewport, target / self._preview_factor)
        self._preview_factor = target

    def _revert_preview(self, viewport) -> None:
        if abs(self._preview_factor - 1.0) > 1e-12:
            self._scale_live(viewport, 1.0 / self._preview_factor)
            self._preview_factor = 1.0

    def _commit(self, viewport, factor: float) -> None:
        self._revert_preview(viewport)
        if abs(factor - 1.0) > 1e-9 and abs(factor) >= _MIN_FACTOR:
            if self._group is not None:
                viewport.history.execute(ScaleGroupCommand(
                    self._group, self.start_point, factor))
            elif self._positions:
                viewport.history.execute(ScaleVerticesCommand(
                    self._positions, self.start_point, factor))
        self._reset()
        viewport.update()

    def _reset(self) -> None:
        self.start_point = None
        self.ref_point = None
        self.hover_point = None
        self._group = None
        self._positions = []
        self._verts = []
        self._preview_factor = 1.0
