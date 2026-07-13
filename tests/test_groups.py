# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Groups: isolate geometry into its own mesh so it doesn't weld to the rest."""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.history import (
    ExplodeGroupCommand,
    History,
    MakeGroupCommand,
    MoveGroupCommand,
)
from core.scene import Scene


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(float(x), float(y), float(z))


def _two_squares():
    scene = Scene()
    hist = History(scene)
    a = scene.mesh.add_face([V(0, 0), V(2, 0), V(2, 2), V(0, 2)])
    scene.mesh.add_face([V(5, 0), V(7, 0), V(7, 2), V(5, 2)])
    return scene, hist, a


def test_make_group_moves_geometry_off_the_loose_mesh():
    scene, hist, a = _two_squares()
    hist.execute(MakeGroupCommand([a], []))
    assert len(scene.groups) == 1
    assert len(scene.mesh.faces) == 1           # only the other square stays loose
    assert len(scene.groups[0].mesh.faces) == 1
    assert scene.groups[0] in scene.selection   # the group is selected
    assert hist.undo() is True
    assert len(scene.groups) == 0
    assert len(scene.mesh.faces) == 2


def test_moving_a_group_does_not_drag_loose_geometry():
    scene, hist, a = _two_squares()
    hist.execute(MakeGroupCommand([a], []))
    g = scene.groups[0]
    loose_x_before = sorted({round(v.position.x()) for v in scene.mesh.vertices})

    hist.execute(MoveGroupCommand(g, V(10, 0, 0)))
    group_x = sorted({round(v.position.x()) for v in g.mesh.vertices})
    loose_x = sorted({round(v.position.x()) for v in scene.mesh.vertices})
    assert group_x == [10, 12]                  # the group moved
    assert loose_x == loose_x_before            # the loose square did not

    assert hist.undo() is True
    assert sorted({round(v.position.x()) for v in g.mesh.vertices}) == [0, 2]


def test_explode_merges_group_back_into_loose_mesh():
    scene, hist, a = _two_squares()
    hist.execute(MakeGroupCommand([a], []))
    g = scene.groups[0]
    hist.execute(ExplodeGroupCommand(g))
    assert len(scene.groups) == 0
    assert len(scene.mesh.faces) == 2           # back among the loose geometry
    assert hist.undo() is True
    assert len(scene.groups) == 1


def test_group_keeps_soft_and_curve_flags_round_trip(tmp_path):
    # Grouping a smooth cylinder must not expose its facet seams: the group's
    # fresh mesh copies positions, so the soft/curve flags have to travel with
    # it — and travel back out on explode. (The report: push a circle → clean
    # cylinder; make a group → every vertical seam shows.)
    import math

    from PySide6.QtCore import QPointF, Qt

    from core.history import ExplodeGroupCommand, History, MakeGroupCommand
    from formats import igz
    from tools.base import ToolContext
    from tools.circle import CircleTool
    from tools.pushpull import PushPullTool

    class _Vp:
        def __init__(self, scene):
            self.scene = scene
            self.history = History(scene)

        def update(self):
            pass

        def set_hover(self, *_):
            pass

        def set_suppressed_faces(self, *_):
            pass

        def flash_status(self, *a, **k):
            pass

    scene = Scene()
    vp = _Vp(scene)
    t = CircleTool()
    t.work_plane = None
    for x in (0, 3):
        t.on_click(ToolContext(viewport=vp, world=QVector3D(x, 0, 0),
                               screen=QPointF(0, 0),
                               modifiers=Qt.NoModifier, snap=None))
    pp = PushPullTool()
    pp.hovered_face = scene.mesh.faces[0]
    pp._hover_group = None
    pp.on_click(ToolContext(viewport=vp, world=QVector3D(0, 0, 0),
                            screen=QPointF(0, 0),
                            modifiers=Qt.NoModifier, snap=None))
    pp.extrusion = 2.0
    pp._commit(vp)
    soft_before = sum(1 for e in scene.mesh.edges if e.soft)
    curve_before = sum(1 for e in scene.mesh.edges if e.curve is not None)
    assert soft_before == 24                      # the cylinder's facet seams

    vp.history.execute(MakeGroupCommand(list(scene.mesh.faces),
                                        list(scene.mesh.edges)))
    group = scene.groups[0]
    assert sum(1 for e in group.mesh.edges if e.soft) == soft_before
    assert sum(1 for e in group.mesh.edges
               if e.curve is not None) == curve_before

    # .igz round-trip keeps the group smooth.
    p = tmp_path / "cyl.igz"
    igz.save_scene(scene, p)
    scene2 = Scene()
    igz.load_into(scene2, p)
    assert sum(1 for e in scene2.groups[0].mesh.edges if e.soft) == soft_before

    # Explode: flags travel back to the loose mesh.
    vp.history.execute(ExplodeGroupCommand(group))
    assert sum(1 for e in scene.mesh.edges if e.soft) == soft_before
    assert sum(1 for e in scene.mesh.edges
               if e.curve is not None) == curve_before


def test_group_keeps_face_attrs_round_trip():
    # Colour + texture painted on faces must survive Make Group and travel
    # back out on Explode (user report 2026-07-12: grouping a textured plaza
    # stripped every material).
    scene, hist, a = _two_squares()
    a.attrs["color"] = [0.8, 0.2, 0.1]
    a.attrs["texture"] = {"path": "brick.png", "sw": 1.0, "sh": 1.0}
    hist.execute(MakeGroupCommand([a], []))
    gf = scene.groups[0].mesh.faces[0]
    assert gf.attrs.get("color") == [0.8, 0.2, 0.1]
    assert gf.attrs.get("texture") == {"path": "brick.png", "sw": 1.0, "sh": 1.0}
    hist.execute(ExplodeGroupCommand(scene.groups[0]))
    back = next(f for f in scene.mesh.faces
                if f.attrs.get("color") == [0.8, 0.2, 0.1])
    assert back.attrs.get("texture") == {"path": "brick.png", "sw": 1.0, "sh": 1.0}
