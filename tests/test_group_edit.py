# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Groups v2: edit-inside-group context (double-click into a group)."""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.edits import build_add_edges
from core.history import AddFaceCommand, History, MakeGroupCommand
from core.scene import Scene


def V(x, y, z=0.0):
    return QVector3D(x, y, z)


def _rect(scene, hist, x0, y0, x1, y1):
    pts = [V(x0, y0), V(x1, y0), V(x1, y1), V(x0, y1)]
    hist.execute(build_add_edges(
        scene, [(pts[i], pts[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(pts))]))


def _grouped_scene():
    scene = Scene()
    hist = History(scene)
    _rect(scene, hist, 0, 0, 4, 4)
    hist.execute(MakeGroupCommand(list(scene.mesh.faces),
                                  list(scene.mesh.edges)))
    return scene, hist, scene.groups[0]


def test_enter_edit_swaps_mesh_and_exit_restores():
    scene, hist, group = _grouped_scene()
    loose = scene.mesh
    scene.begin_group_edit(group)
    assert scene.mesh is group.mesh
    assert scene.edit_group is group
    assert scene.loose_mesh is loose
    scene.end_group_edit()
    assert scene.mesh is loose
    assert scene.edit_group is None


def test_drawing_inside_lands_in_the_group():
    scene, hist, group = _grouped_scene()
    scene.begin_group_edit(group)
    _rect(scene, hist, 1, 1, 2, 2)                 # drawn INSIDE the context
    scene.end_group_edit()
    assert len(scene.mesh.faces) == 0              # loose mesh untouched
    assert len(group.mesh.faces) == 2              # group gained the rect


def test_undo_after_exiting_restores_the_group_not_the_loose_mesh():
    # The critical cross-context case: a command executed INSIDE the group,
    # undone AFTER leaving. Its snapshot must land on the group's mesh.
    scene, hist, group = _grouped_scene()
    _rect(scene, hist, 10, 10, 12, 12)             # loose slab (outside)
    scene.begin_group_edit(group)
    _rect(scene, hist, 1, 1, 2, 2)                 # inside the group
    scene.end_group_edit()
    loose_faces = len(scene.mesh.faces)
    assert len(group.mesh.faces) == 2
    assert hist.undo()                             # undoes the INSIDE rect
    assert len(group.mesh.faces) == 1              # group restored
    assert len(scene.mesh.faces) == loose_faces    # loose mesh untouched
    assert hist.redo()
    assert len(group.mesh.faces) == 2


def test_render_views_do_not_duplicate_while_editing():
    scene, hist, group = _grouped_scene()
    _rect(scene, hist, 10, 10, 12, 12)             # loose slab
    total = len(list(scene.render_faces()))
    scene.begin_group_edit(group)
    assert len(list(scene.render_faces())) == total   # same faces, no dupes
    scene.end_group_edit()


def test_clear_scene_exits_the_context():
    scene, hist, group = _grouped_scene()
    scene.begin_group_edit(group)
    scene.clear()
    assert scene.edit_group is None
    assert len(scene.mesh.faces) == 0
