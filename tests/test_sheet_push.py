# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Pushing a region of a flat drawing while a solid stands elsewhere in the
scene (the xc.igz report). 'Solid' must be a property of the REGION being
pushed: with the whole-mesh gate, the volumetric plane rebuild read the open
sheet's regions as phantoms and DELETED the neighbours — pushing the
circle∩square lens made the rest of the drawing disappear."""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QVector3D

from core.edits import build_add_edges
from core.history import AddFaceCommand, History
from core.scene import Scene
from tools.base import ToolContext
from tools.pushpull import PushPullTool


class _Vp:
    def __init__(self, scene):
        self.scene = scene
        self.history = History(scene)
        self.messages = []

    def set_hover(self, *_):
        pass

    def set_suppressed_faces(self, *_):
        pass

    def update(self):
        pass

    def flash_status(self, text, msec=2500):
        self.messages.append(text)


def _rect(scene, hist, x0, y0, x1, y1):
    pts = [QVector3D(x0, y0, 0), QVector3D(x1, y0, 0),
           QVector3D(x1, y1, 0), QVector3D(x0, y1, 0)]
    hist.execute(build_add_edges(
        scene, [(pts[i], pts[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(pts))]))


def _box(mesh, x0, y0, x1, y1, h):
    bot = [QVector3D(x0, y0, 0), QVector3D(x1, y0, 0),
           QVector3D(x1, y1, 0), QVector3D(x0, y1, 0)]
    top = [QVector3D(p.x(), p.y(), h) for p in bot]
    mesh.add_face(list(reversed(bot)))
    mesh.add_face(top)
    for i in range(4):
        a, b = bot[i], bot[(i + 1) % 4]
        mesh.add_face([a, b, QVector3D(b.x(), b.y(), h),
                       QVector3D(a.x(), a.y(), h)])


def _push(vp, face, dist):
    pp = PushPullTool()
    pp.hovered_face = face
    pp._hover_group = None
    pp.on_click(ToolContext(viewport=vp, world=QVector3D(0, 0, 0),
                            screen=QPointF(400, 300),
                            modifiers=Qt.NoModifier, snap=None))
    pp.extrusion = dist
    pp._commit(vp)


def test_push_sheet_region_keeps_neighbours_despite_solid_elsewhere():
    scene = Scene()
    vp = _Vp(scene)
    # overlapping rectangles → three regions (middle 2x2 = area 4)
    _rect(scene, vp.history, 0, 0, 6, 4)
    _rect(scene, vp.history, 4, -2, 9, 2)
    # a separate solid box makes the whole mesh 'not flat'
    _box(scene.mesh, 20, 20, 24, 24, 3)
    middle = next(f for f in scene.mesh.faces if abs(f.area() - 4.0) < 1e-6)
    _push(vp, middle, -1.0)                       # downward: the natural drag
    flat0 = [f for f in scene.mesh.faces
             if all(abs(v.position.z()) < 1e-6 for v in f.loop)
             and f.loop[0].position.x() < 15]
    areas = sorted(round(f.area(), 1) for f in flat0)
    assert 16.0 in areas and 20.0 in areas        # both remainders survive
    low = [f for f in scene.mesh.faces
           if all(abs(v.position.z() + 1) < 1e-6 for v in f.loop)]
    assert len(low) == 1                          # the pushed cap at z=-1
    assert abs(low[0].area() - 4.0) < 1e-6


def test_push_up_also_keeps_neighbours():
    scene = Scene()
    vp = _Vp(scene)
    _rect(scene, vp.history, 0, 0, 6, 4)
    _rect(scene, vp.history, 4, -2, 9, 2)
    _box(scene.mesh, 20, 20, 24, 24, 3)
    middle = next(f for f in scene.mesh.faces if abs(f.area() - 4.0) < 1e-6)
    _push(vp, middle, 1.5)
    flat0 = [f for f in scene.mesh.faces
             if all(abs(v.position.z()) < 1e-6 for v in f.loop)
             and f.loop[0].position.x() < 15]
    areas = sorted(round(f.area(), 1) for f in flat0)
    assert 16.0 in areas and 20.0 in areas
