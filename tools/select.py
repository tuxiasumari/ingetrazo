# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
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

from core.dimension import Dimension
from core.group import Group
from core.mesh import Edge, Face
from core.history import (
    CompoundCommand,
    DeleteDimensionsCommand,
    DeleteGeoPathsCommand,
    DeleteGroupCommand,
    EraseSelectionCommand,
)
from georef.geopath import GeoPath
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
    shortcut = ""  # Space, bound in main_window; "S" is Scale
    uses_snap = False  # selecting picks geometry; no snap markers
    box_select = True   # supports the rubber-band window / crossing box

    def on_activate(self, viewport) -> None:
        pass

    def on_deactivate(self, viewport) -> None:
        viewport.set_hover(None)

    def _pick(self, viewport, screen_x: float, screen_y: float):
        """A group (picked as a unit) takes priority; then the edge under the
        cursor (screen-space priority), then a dimension annotation, then the
        front face."""
        group = viewport.pick_group(screen_x, screen_y)
        if group is not None:
            return group
        edge = viewport.pick_edge(screen_x, screen_y)
        if edge is not None:
            return edge
        dim = viewport.pick_dimension(screen_x, screen_y)
        if dim is not None:
            return dim
        path = viewport.pick_geopath(screen_x, screen_y)
        if path is not None:
            return path
        return viewport.pick_face(screen_x, screen_y)

    def on_click(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        entity = self._pick(viewport, ctx.screen.x(), ctx.screen.y())
        additive = bool(ctx.modifiers & Qt.ShiftModifier)
        if entity is None:
            if viewport.scene.edit_group is not None and not additive \
                    and not viewport.scene.selection:
                viewport.end_group_edit()       # click outside leaves the group
                return
            if not additive:
                viewport.scene.clear_selection()
        else:
            picked = self._expand(viewport, entity)
            viewport.scene.select(picked, additive=additive)
        viewport.update()

    @staticmethod
    def _expand(viewport, entity):
        """Grow a pick to its natural whole: a curved surface (faces joined by
        soft edges) for a face, or the whole drawn curve (circle/arc) for one of
        its segments — SketchUp-style. Plain entities select alone."""
        if isinstance(entity, Face):
            return viewport.scene.mesh.surface_of(entity)
        if isinstance(entity, Edge) and getattr(entity, "curve", None) is not None:
            return viewport.scene.mesh.curve_edges(entity)
        return [entity]

    def on_double_click(self, ctx: ToolContext) -> None:
        """SketchUp double click: a face selects itself plus its bounding
        edges; an edge selects itself plus its faces — and a GROUP opens for
        editing (Groups v2: draw, push, erase inside it)."""
        viewport = ctx.viewport
        entity = self._pick(viewport, ctx.screen.x(), ctx.screen.y())
        if isinstance(entity, Group):
            viewport.begin_group_edit(entity)
            return
        if not isinstance(entity, (Face, Edge)):
            self.on_click(ctx)
            return
        mesh = viewport.scene.mesh
        picked = list(self._expand(viewport, entity))
        if isinstance(entity, Face):
            for f in list(picked):
                if not isinstance(f, Face):
                    continue
                for lp in (f.loop, *f.hole_loops):
                    n = len(lp)
                    for i in range(n):
                        e = mesh.find_edge(lp[i], lp[(i + 1) % n])
                        if e is not None:
                            picked.append(e)
        else:
            for e in list(picked):
                if isinstance(e, Edge):
                    picked.extend(e.faces)
        additive = bool(ctx.modifiers & Qt.ShiftModifier)
        viewport.scene.select(picked, additive=additive)
        viewport.update()

    def on_triple_click(self, ctx: ToolContext) -> None:
        """SketchUp triple click: everything physically connected to the
        picked entity (the whole solid), walked through shared vertices."""
        viewport = ctx.viewport
        entity = self._pick(viewport, ctx.screen.x(), ctx.screen.y())
        if not isinstance(entity, (Face, Edge)):
            self.on_click(ctx)
            return
        if isinstance(entity, Face):
            seeds = list(entity.loop) + [v for h in entity.hole_loops
                                         for v in h]
        else:
            seeds = [entity.v0, entity.v1]
        seen_v = set(seeds)
        edges: set = set()
        faces: set = set()
        stack = list(seeds)
        while stack:
            v = stack.pop()
            for e in v.edges:
                if e in edges:
                    continue
                edges.add(e)
                for f in e.faces:
                    if f in faces:
                        continue
                    faces.add(f)
                    for lp in (f.loop, *f.hole_loops):
                        for w in lp:
                            if w not in seen_v:
                                seen_v.add(w)
                                stack.append(w)
                w = e.other(v)
                if w not in seen_v:
                    seen_v.add(w)
                    stack.append(w)
        additive = bool(ctx.modifiers & Qt.ShiftModifier)
        viewport.scene.select(list(edges) + list(faces), additive=additive)
        viewport.update()

    def on_hover(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        viewport.set_hover(self._pick(viewport, ctx.screen.x(), ctx.screen.y()))

    def on_box_select(self, viewport, rect, crossing: bool, additive: bool) -> None:
        w2p = viewport._world_to_pixel
        picked = []
        for edge in viewport.scene.edges:
            if not viewport.scene.entity_selectable(edge):
                continue                        # hidden or locked layer
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
            if not viewport.scene.entity_selectable(face):
                continue                        # hidden or locked layer
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
        for dim in getattr(viewport.scene, "dimensions", []):
            ap, bp = dim.line_points()
            pa, pb = w2p(ap), w2p(bp)
            if pa is None or pb is None:
                continue
            if crossing:
                if _seg_rect_overlap(pa, pb, rect):
                    picked.append(dim)
            elif _pt_in_rect(pa, rect) and _pt_in_rect(pb, rect):
                picked.append(dim)
        viewport.scene.select(picked, additive=additive)
        viewport.update()

    def on_key(self, viewport, key: int, modifiers: Qt.KeyboardModifiers) -> bool:
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            selection = viewport.scene.selection
            if selection:
                edges = [e for e in selection if isinstance(e, Edge)]
                faces = [f for f in selection if isinstance(f, Face)]
                groups = [g for g in selection if isinstance(g, Group)]
                dims = [d for d in selection if isinstance(d, Dimension)]
                paths = [p for p in selection if isinstance(p, GeoPath)]
                commands = []
                if edges or faces:
                    # Erasing an edge between two coplanar faces merges them back
                    # into one (SketchUp); any other erased edge takes its faces.
                    commands.append(EraseSelectionCommand(edges, faces))
                commands.extend(DeleteGroupCommand(g) for g in groups)
                if dims:
                    commands.append(DeleteDimensionsCommand(dims))
                if paths:
                    commands.append(DeleteGeoPathsCommand(paths))
                if commands:
                    cmd = (commands[0] if len(commands) == 1
                           else CompoundCommand(commands))
                    viewport.history.execute(cmd)
                    viewport.update()
            return True
        return False
