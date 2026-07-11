# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Rotate tool (Q) and Center Arc tool (O): scripted clicks on stub viewports."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QVector3D

from core.edits import build_add_edges
from core.history import AddFaceCommand, History, MakeGroupCommand
from core.scene import Scene
from tools.base import ToolContext
from tools.arc import CenterArcTool
from tools.rotate import RotateTool


class _Vp:
    def __init__(self, scene):
        self.scene = scene
        self.history = History(scene)
        self.messages = []

    def update(self):
        pass

    def set_hover(self, *_):
        pass

    def flash_status(self, text, msec=2500):
        self.messages.append(text)

    def pick_group(self, x, y):
        return None

    def pick_edge(self, x, y):
        return None

    def pick_face(self, x, y):
        return None


def _click(vp, tool, x, y, z=0.0):
    tool.on_click(ToolContext(viewport=vp, world=QVector3D(x, y, z),
                              screen=QPointF(0, 0),
                              modifiers=Qt.NoModifier, snap=None))


def _hover(vp, tool, x, y, z=0.0):
    tool.on_hover(ToolContext(viewport=vp, world=QVector3D(x, y, z),
                              screen=QPointF(0, 0),
                              modifiers=Qt.NoModifier, snap=None))


def _rect(scene, hist, x0, y0, x1, y1):
    pts = [QVector3D(x0, y0, 0), QVector3D(x1, y0, 0),
           QVector3D(x1, y1, 0), QVector3D(x0, y1, 0)]
    hist.execute(build_add_edges(
        scene, [(pts[i], pts[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(pts))]))


def _keys(mesh):
    return sorted((round(v.position.x(), 3), round(v.position.y(), 3),
                   round(v.position.z(), 3)) for v in mesh.vertices)


def test_rotate_selection_90_degrees_and_undo():
    scene = Scene()
    vp = _Vp(scene)
    _rect(scene, vp.history, 2, 0, 6, 2)          # 4x2 slab east of origin
    scene.selection.update(scene.mesh.faces)
    scene.selection.update(scene.mesh.edges)
    before = _keys(scene.mesh)

    t = RotateTool()
    t.on_activate(vp)
    _click(vp, t, 0, 0)                            # centre at the origin
    _click(vp, t, 1, 0)                            # reference arm = +X
    _hover(vp, t, 0, 1)                            # live preview toward +Y
    _click(vp, t, 0, 1)                            # commit at +90°

    got = _keys(scene.mesh)
    # (2,0) → (0,2); (6,2) → (-2,6): the rect turned into the +Y quadrant.
    assert (0.0, 2.0, 0.0) in got
    assert (-2.0, 6.0, 0.0) in got
    assert len(scene.mesh.faces) == 1
    assert vp.history.undo()
    assert _keys(scene.mesh) == before             # exact restore
    assert vp.history.redo()
    assert _keys(scene.mesh) == got


def test_rotate_typed_angle_via_vcb():
    scene = Scene()
    vp = _Vp(scene)
    _rect(scene, vp.history, 2, 0, 6, 2)
    scene.selection.update(scene.mesh.faces)
    scene.selection.update(scene.mesh.edges)
    t = RotateTool()
    t.on_activate(vp)
    _click(vp, t, 0, 0)
    _click(vp, t, 1, 0)
    _hover(vp, t, 1, 1)                            # dragging counter-clockwise
    assert t.on_value(vp, 90.0) is True
    assert (0.0, 2.0, 0.0) in _keys(scene.mesh)


def test_rotate_group_as_unit():
    scene = Scene()
    vp = _Vp(scene)
    _rect(scene, vp.history, 2, 0, 6, 2)
    vp.history.execute(MakeGroupCommand(list(scene.mesh.faces),
                                        list(scene.mesh.edges)))
    group = scene.groups[0]
    scene.selection.clear()
    scene.selection.add(group)
    t = RotateTool()
    t.on_activate(vp)
    _click(vp, t, 0, 0)
    _click(vp, t, 1, 0)
    _hover(vp, t, 0, 1)
    _click(vp, t, 0, 1)                            # +90°
    gk = sorted((round(v.position.x(), 3), round(v.position.y(), 3))
                for v in group.mesh.vertices)
    assert (0.0, 2.0) in gk and (-2.0, 6.0) in gk
    assert len(scene.mesh.edges) == 0              # loose mesh untouched
    vp.history.undo()
    assert (2.0, 0.0) in sorted((round(v.position.x(), 3),
                                 round(v.position.y(), 3))
                                for v in group.mesh.vertices)


def test_rotate_with_nothing_flashes_hint():
    scene = Scene()
    vp = _Vp(scene)
    t = RotateTool()
    t.on_activate(vp)
    _click(vp, t, 0, 0)
    assert t.start_point is None                   # did not lock a centre
    assert vp.messages


def test_center_arc_draws_tagged_curve_quarter():
    scene = Scene()
    vp = _Vp(scene)
    t = CenterArcTool()
    t.on_activate(vp)
    _click(vp, t, 0, 0)                            # centre
    _click(vp, t, 3, 0)                            # radius 3, 0° arm = +X
    _hover(vp, t, 0, 3)
    _click(vp, t, 0, 3)                            # sweep +90°
    assert len(scene.mesh.edges) == 6              # 15° pitch → 6 segments
    ids = {e.curve for e in scene.mesh.edges}
    assert len(ids) == 1 and None not in ids       # one selectable contour
    ends = {(round(v.position.x(), 3), round(v.position.y(), 3))
            for v in scene.mesh.vertices}
    assert (3.0, 0.0) in ends and (0.0, 3.0) in ends


def test_center_arc_typed_angle_and_circle_weld():
    # The 15° pitch matches the 24-side circle: a concentric centre arc lands
    # on the same lattice and welds instead of duplicating near-vertices.
    from tools.circle import CircleTool

    scene = Scene()
    vp = _Vp(scene)
    c = CircleTool()
    c.work_plane = None
    _click(vp, c, 0, 0)
    _click(vp, c, 3, 0)
    verts_before = len(scene.mesh.vertices)
    t = CenterArcTool()
    t.on_activate(vp)
    _click(vp, t, 0, 0)
    _click(vp, t, 3, 0)
    _hover(vp, t, 1, 1)                            # counter-clockwise side
    assert t.on_value(vp, 90.0) is True
    assert len(scene.mesh.vertices) == verts_before   # welded, no duplicates


def test_scale_selection_doubles_about_anchor():
    from tools.scale import ScaleTool

    scene = Scene()
    vp = _Vp(scene)
    _rect(scene, vp.history, 2, 0, 6, 2)
    scene.selection.update(scene.mesh.faces)
    scene.selection.update(scene.mesh.edges)
    before = _keys(scene.mesh)
    t = ScaleTool()
    t.on_activate(vp)
    _click(vp, t, 0, 0)                            # anchor at the origin
    _click(vp, t, 1, 0)                            # reference distance 1.0
    _hover(vp, t, 2, 0)                            # live ×2
    _click(vp, t, 2, 0)                            # commit ×2
    got = _keys(scene.mesh)
    assert (4.0, 0.0, 0.0) in got and (12.0, 4.0, 0.0) in got
    assert vp.history.undo()
    assert _keys(scene.mesh) == before
    assert vp.history.redo()
    assert _keys(scene.mesh) == got


def test_scale_typed_factor_and_mirror():
    from tools.scale import ScaleTool

    scene = Scene()
    vp = _Vp(scene)
    _rect(scene, vp.history, 2, 0, 6, 2)
    scene.selection.update(scene.mesh.faces)
    scene.selection.update(scene.mesh.edges)
    t = ScaleTool()
    t.on_activate(vp)
    _click(vp, t, 0, 0)
    _click(vp, t, 1, 0)
    _hover(vp, t, 1.5, 0)
    assert t.on_value(vp, -1.0) is True            # mirror through the anchor
    got = _keys(scene.mesh)
    assert (-2.0, 0.0, 0.0) in got and (-6.0, -2.0, 0.0) in got
    assert len(scene.mesh.faces) == 1              # still one clean face


def test_scale_group_as_unit():
    from tools.scale import ScaleTool

    scene = Scene()
    vp = _Vp(scene)
    _rect(scene, vp.history, 2, 0, 6, 2)
    vp.history.execute(MakeGroupCommand(list(scene.mesh.faces),
                                        list(scene.mesh.edges)))
    group = scene.groups[0]
    scene.selection.clear()
    scene.selection.add(group)
    t = ScaleTool()
    t.on_activate(vp)
    _click(vp, t, 2, 0)                            # anchor on the slab corner
    _click(vp, t, 3, 0)
    _hover(vp, t, 3.5, 0)
    _click(vp, t, 3.5, 0)                          # ×1.5 about (2,0)
    gk = sorted((round(v.position.x(), 3), round(v.position.y(), 3))
                for v in group.mesh.vertices)
    assert (2.0, 0.0) in gk and (8.0, 3.0) in gk   # corner fixed, far corner ×1.5
    vp.history.undo()
    assert (6.0, 2.0) in sorted((round(v.position.x(), 3),
                                 round(v.position.y(), 3))
                                for v in group.mesh.vertices)
