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


def test_bundled_components_import_closed_and_insert_undoably():
    # The starter components (SketchUp-style props) must import clean and
    # insert as selectable groups with exact undo.
    from pathlib import Path

    from core.group import Group
    from core.history import InsertGroupCommand
    from core.orient import is_closed
    from formats.obj import load_obj

    comp_dir = Path(__file__).resolve().parent.parent / "resources" / "components"
    scene = Scene()
    hist = History(scene)
    for name, must_close in (("person", True), ("tree", True),
                             ("bush", True), ("car", False)):
        temp = Scene()
        load_obj(temp, comp_dir / f"{name}.obj")
        assert temp.mesh.faces, name
        assert is_closed(temp.mesh) == must_close, name
        hist.execute(InsertGroupCommand(Group(temp.mesh, name=name)))
    assert len(scene.groups) == 4
    # scale figure is actually 1.75 m
    person = next(g for g in scene.groups if g.name == "person")
    zs = [v.position.z() for v in person.mesh.vertices]
    assert abs(max(zs) - 1.75) < 1e-6 and abs(min(zs)) < 1e-6
    assert hist.undo() and len(scene.groups) == 3
    assert hist.redo() and len(scene.groups) == 4


def test_billboard_group_round_trips_and_faces_camera(tmp_path):
    # Face-me billboards: flag + textured quad persist in .igz; the quad the
    # viewport computes always faces the camera around the vertical axis.
    from core.group import make_billboard_group
    from formats import igz

    scene = Scene()
    g = make_billboard_group("person_billboard.png", 1.75, "Persona", 0.28)
    scene.groups.append(g)
    assert g.billboard
    # excluded from the static render views (drawn per-frame instead)
    assert list(scene.render_faces()) == []
    p = tmp_path / "bb.igz"
    igz.save_scene(scene, p)
    scene2 = Scene()
    igz.load_into(scene2, p)
    g2 = scene2.groups[0]
    assert g2.billboard
    tex = g2.mesh.faces[0].attrs["texture"]
    assert tex["path"].endswith("person_billboard.png")

    # quad math: a stub viewport with a camera at two azimuths
    class _Cam:
        def __init__(self, eye):
            self._eye = eye

        def eye(self):
            return self._eye

    class _VpB:
        def __init__(self, scene, eye):
            self.scene = scene
            self.camera = _Cam(eye)

    from views.viewport import Viewport
    for eye, expect_normal in ((QVector3D(10, 0, 1), QVector3D(1, 0, 0)),
                               (QVector3D(0, -8, 1), QVector3D(0, -1, 0))):
        vp = _VpB(scene2, eye)
        corners, _path = Viewport._billboard_quad(vp, g2)
        n = QVector3D.crossProduct(corners[1] - corners[0],
                                   corners[3] - corners[0]).normalized()
        assert QVector3D.dotProduct(n, expect_normal) > 0.99 or \
            QVector3D.dotProduct(-n, expect_normal) > 0.99
        assert abs((corners[3] - corners[0]).z() - 1.75) < 1e-6
