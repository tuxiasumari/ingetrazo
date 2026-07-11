# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Eraser tool (E): erase by clicking or by dragging over geometry.

SketchUp behaviour: press marks the edge under the cursor, dragging keeps
marking everything the cursor sweeps over (shown highlighted red), and release
erases the whole stroke as ONE undo step. Erasing an edge takes its faces with
it (and rubbing out a divider between coplanar faces merges them back — the
EraseSelectionCommand semantics). A segment of a drawn curve (circle/arc)
erases its whole contour, curves being single entities. Guides, dimensions and
georef paths are erased too. Esc cancels the in-progress stroke.
"""
from __future__ import annotations

from PySide6.QtCore import Qt

from core.dimension import Dimension
from core.guide import Guide
from core.history import (
    CompoundCommand,
    DeleteDimensionsCommand,
    DeleteGeoPathsCommand,
    DeleteGuidesCommand,
    EraseSelectionCommand,
)
from core.mesh import Edge
from georef.geopath import GeoPath
from tools.base import Tool, ToolContext


class EraserTool(Tool):
    name = "Eraser"
    shortcut = "E"
    uses_snap = False
    wireframe_color = (0.90, 0.20, 0.15, 1.0)   # stroke marks show red

    def __init__(self) -> None:
        self._stroke = False
        self.marked: set = set()

    # ---- Lifecycle ----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        self._reset()

    def on_deactivate(self, viewport) -> None:
        self._reset()

    # ---- Spatial input ------------------------------------------------------
    def on_click(self, ctx: ToolContext) -> None:
        """Press: start a stroke and mark whatever is under the cursor."""
        self._stroke = True
        self._mark(ctx.viewport, ctx.screen.x(), ctx.screen.y())
        ctx.viewport.update()

    def on_hover(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        if self._stroke:
            self._mark(viewport, ctx.screen.x(), ctx.screen.y())
        else:
            # Preview what a click would take (curve segments show as their
            # whole contour, like Select).
            edge = viewport.pick_edge(ctx.screen.x(), ctx.screen.y())
            viewport.set_hover(edge)
        viewport.update()

    def on_release(self, viewport) -> None:
        """Release: erase everything the stroke marked, as one undo step."""
        if not self._stroke:
            return
        marked, self.marked = self.marked, set()
        self._stroke = False
        if not marked:
            viewport.update()
            return
        edges = [m for m in marked if isinstance(m, Edge)]
        guides = [m for m in marked if isinstance(m, Guide)]
        dims = [m for m in marked if isinstance(m, Dimension)]
        paths = [m for m in marked if isinstance(m, GeoPath)]
        cmds = []
        if edges:
            cmds.append(EraseSelectionCommand(edges, []))
        if guides:
            cmds.append(DeleteGuidesCommand(guides))
        if dims:
            cmds.append(DeleteDimensionsCommand(dims))
        if paths:
            cmds.append(DeleteGeoPathsCommand(paths))
        if cmds:
            viewport.history.execute(
                cmds[0] if len(cmds) == 1 else CompoundCommand(cmds))
        viewport.update()

    def on_cancel(self, viewport) -> None:
        self._reset()
        viewport.update()

    # ---- Preview ------------------------------------------------------------
    def rubber_band_lines(self):
        """The marked stroke, drawn in the tool's red wireframe colour."""
        segs = []
        for m in self.marked:
            if isinstance(m, Edge):
                segs.append((m.a, m.b))
            elif isinstance(m, Guide):
                segs.append(m.segment())
            elif isinstance(m, Dimension):
                segs.append(m.line_points())
            elif isinstance(m, GeoPath):
                segs.extend(m.segments())
        return segs

    # ---- Internals ----------------------------------------------------------
    def _mark(self, viewport, sx: float, sy: float) -> None:
        edge = viewport.pick_edge(sx, sy)
        if edge is not None:
            # A curve is one entity: marking a segment marks its contour.
            for e in viewport.scene.mesh.curve_edges(edge):
                self.marked.add(e)
            return
        guide = viewport.pick_guide(sx, sy)
        if guide is not None:
            self.marked.add(guide)
            return
        dim = viewport.pick_dimension(sx, sy)
        if dim is not None:
            self.marked.add(dim)
            return
        path = viewport.pick_geopath(sx, sy)
        if path is not None:
            self.marked.add(path)

    def _reset(self) -> None:
        self._stroke = False
        self.marked = set()
