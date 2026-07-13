# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The vectorised pick index (viewport picks batched over NumPy arrays).

Every mouse-move pick used to walk the mesh in Python re-running earcut per
face — ~1–2 s per move against an imported 17k-triangle building. These pin
the batched replacements to the same answers headless (no GL needed)."""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.history import History, MakeGroupCommand
from core.scene import Scene
from views.viewport import Viewport


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


class _VP:
    pick_threshold_px = 10.0

    def __init__(self, scene):
        self.scene = scene

    def width(self):
        return 100

    def height(self):
        return 100


def _bind(vp):
    for name in ("_pick_index", "_ray_hits", "pick_face", "pick_face_any",
                 "pick_edge", "_project_px", "_np_mvp"):
        setattr(vp, name, getattr(Viewport, name).__get__(vp))
    return vp


def _ray_down(x=0.0, y=0.0):
    return (QVector3D(x, y, 10.0), QVector3D(0.0, 0.0, -1.0))


def test_pick_face_prefers_smallest_coplanar():
    scene = Scene()
    big = scene.mesh.add_face([V(-5, -5), V(5, -5), V(5, 5), V(-5, 5)])
    small = scene.mesh.add_face([V(-1, -1), V(1, -1), V(1, 1), V(-1, 1)])
    assert big is not small
    vp = _bind(_VP(scene))
    vp._pixel_to_ray = lambda sx, sy: _ray_down(0, 0)
    hit = vp.pick_face(0, 0)
    # Both faces overlap at the origin at the same depth: the smallest wins.
    assert hit is small or (hit.area() < 5)


def test_pick_face_any_reaches_group_faces():
    scene = Scene()
    hist = History(scene)
    f = scene.mesh.add_face([V(-2, -2), V(2, -2), V(2, 2), V(-2, 2)])
    hist.execute(MakeGroupCommand([f], []))
    vp = _bind(_VP(scene))
    vp._pixel_to_ray = lambda sx, sy: _ray_down(0, 0)
    face, grp = vp.pick_face_any(0, 0)
    assert grp is scene.groups[0]
    assert face in scene.groups[0].mesh.faces
    # pick_face (loose only) sees nothing.
    assert vp.pick_face(0, 0) is None


def test_pick_index_refreshes_on_scene_change():
    scene = Scene()
    vp = _bind(_VP(scene))
    vp._pixel_to_ray = lambda sx, sy: _ray_down(0, 0)
    assert vp.pick_face(0, 0) is None
    scene.mesh.add_face([V(-2, -2), V(2, -2), V(2, 2), V(-2, 2)])
    scene.version += 1
    assert vp.pick_face(0, 0) is not None
