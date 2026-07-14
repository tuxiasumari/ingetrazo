# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""BIM tagging layer: tag → live quantities → CSV takeoff → persistence."""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QVector3D

from core.bim import (
    class_quantities,
    collect_objects,
    face_set_volume,
    next_object_id,
    quantities_csv,
    tag_faces,
    tag_group,
)
from core.edits import build_add_edges
from core.history import AddFaceCommand, History, MakeGroupCommand
from core.scene import Scene
from formats import igz
from tools.base import ToolContext
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


def V(x, y, z=0.0):
    return QVector3D(x, y, z)


def _wall(scene, vp, x0=0.0, length=4.0, thick=0.25, height=2.6):
    pts = [V(x0, 0), V(x0 + length, 0), V(x0 + length, thick), V(x0, thick)]
    vp.history.execute(build_add_edges(
        scene, [(pts[i], pts[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(pts))]))
    pp = PushPullTool()
    pp.hovered_face = scene.mesh.faces[-1]
    pp._hover_group = None
    pp.on_click(ToolContext(viewport=vp, world=V(0, 0), screen=QPointF(0, 0),
                            modifiers=Qt.NoModifier, snap=None))
    pp.extrusion = height
    pp._commit(vp)


def test_tag_solid_reports_area_and_exact_volume():
    scene = Scene()
    vp = _Vp(scene)
    _wall(scene, vp)                                # 4.0 × 0.25 × 2.6 wall
    tag_faces(scene.mesh.faces, "IfcWall", "Muro eje A", next_object_id(scene))
    objs = collect_objects(scene)
    assert len(objs) == 1
    obj = objs[0]
    assert obj["class"] == "IfcWall" and obj["name"] == "Muro eje A"
    assert abs(obj["volume"] - 4.0 * 0.25 * 2.6) < 1e-6   # exact takeoff
    assert obj["area"] > 2 * 4.0 * 2.6                     # shell area


def test_open_face_set_has_area_but_no_volume():
    scene = Scene()
    f = scene.mesh.add_face([V(0, 0), V(3, 0), V(3, 2), V(0, 2)])
    tag_faces([f], "IfcSlab", "Losa", 1)
    obj = collect_objects(scene)[0]
    assert abs(obj["area"] - 6.0) < 1e-9
    assert obj["volume"] is None                    # honest: not watertight
    assert face_set_volume([f]) is None


def test_two_objects_and_csv_takeoff():
    scene = Scene()
    vp = _Vp(scene)
    _wall(scene, vp, x0=0.0)
    tag_faces(scene.mesh.faces, "IfcWall", "Muro 1", next_object_id(scene))
    slab = scene.mesh.add_face([V(10, 0), V(12, 0), V(12, 2), V(10, 2)])
    tag_faces([slab], "IfcSlab", "Losa 1", next_object_id(scene))
    objs = collect_objects(scene)
    assert {o["class"] for o in objs} == {"IfcWall", "IfcSlab"}
    csv = quantities_csv(scene)
    lines = csv.strip().splitlines()
    assert lines[0] == "class,name,metrado,unit,area_m2,volume_m3"
    assert len(lines) == 3
    # wall metrado = net side area (4.0 × 2.6), volume = 2.6 m³
    wall = next(ln for ln in lines if "IfcWall" in ln)
    assert ",10.4000,m2," in wall and wall.endswith(",2.6000")
    slab = next(ln for ln in lines if "IfcSlab" in ln)
    assert ",4.0000,m2," in slab                    # 2×2 sheet, net area


def test_group_tag_and_igz_round_trip(tmp_path):
    scene = Scene()
    vp = _Vp(scene)
    _wall(scene, vp)
    vp.history.execute(MakeGroupCommand(list(scene.mesh.faces),
                                        list(scene.mesh.edges)))
    tag_group(scene.groups[0], "IfcColumn", "C-1")
    # loose tagged slab too
    slab = scene.mesh.add_face([V(10, 0), V(12, 0), V(12, 2), V(10, 2)])
    tag_faces([slab], "IfcSlab", "Losa", next_object_id(scene))

    p = tmp_path / "bim.igz"
    igz.save_scene(scene, p)
    scene2 = Scene()
    igz.load_into(scene2, p)
    objs = collect_objects(scene2)
    classes = {o["class"]: o for o in objs}
    assert classes["IfcColumn"]["name"] == "C-1"
    assert classes["IfcColumn"]["volume"] is not None      # group still solid
    assert classes["IfcSlab"]["volume"] is None


def test_wall_class_quantities_are_the_budget_measures():
    scene = Scene()
    vp = _Vp(scene)
    _wall(scene, vp)                                # 4.0 × 0.25 × 2.6
    qset, entries, (metrado, unit) = class_quantities(
        "IfcWall", scene.mesh.faces)
    assert qset == "Qto_WallBaseQuantities"
    q = {(kind, name): val for kind, name, val in entries}
    assert abs(q[("length", "Height")] - 2.6) < 1e-6
    assert abs(q[("length", "Length")] - 4.0) < 1e-6
    assert abs(q[("length", "Width")] - 0.25) < 1e-6
    assert abs(q[("area", "NetSideArea")] - 10.4) < 1e-6   # 4.0 × 2.6
    assert abs(q[("volume", "GrossVolume")] - 2.6) < 1e-6
    assert unit == "m2" and abs(metrado - 10.4) < 1e-6     # NOT the 24 m²
                                                           # shell area


def test_column_and_pile_quantities():
    scene = Scene()
    vp = _Vp(scene)
    _wall(scene, vp, length=0.3, thick=0.3, height=3.0)    # 0.3×0.3×3.0
    qset, entries, (metrado, unit) = class_quantities(
        "IfcColumn", scene.mesh.faces)
    q = {name: val for _, name, val in entries}
    assert abs(q["Length"] - 3.0) < 1e-6
    assert abs(q["CrossSectionArea"] - 0.09) < 1e-6
    assert unit == "m3" and abs(metrado - 0.27) < 1e-6
    # same solid tagged as pile bills by the metre
    _, _, (metrado, unit) = class_quantities("IfcPile", scene.mesh.faces)
    assert unit == "m" and abs(metrado - 3.0) < 1e-6


def test_door_dimensions_in_rotated_wall_plane():
    import math
    scene = Scene()
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    f = scene.mesh.add_face([V(0, 0, 0), V(0.9 * c, 0.9 * s, 0),
                             V(0.9 * c, 0.9 * s, 2.1), V(0, 0, 2.1)])
    qset, entries, (metrado, unit) = class_quantities("IfcDoor", [f])
    q = {name: val for _, name, val in entries}
    assert abs(q["Width"] - 0.9) < 1e-6            # true leaf width, not bbox
    assert abs(q["Height"] - 2.1) < 1e-6
    assert unit == "und" and metrado == 1.0


def test_slab_net_area_discounts_holes():
    scene = Scene()
    f = scene.mesh.add_face(
        [V(0, 0), V(4, 0), V(4, 3), V(0, 3)],
        hole_loops=[[V(1, 1), V(2, 1), V(2, 2), V(1, 2)]])
    _, entries, (metrado, unit) = class_quantities("IfcSlab", [f])
    q = {name: val for _, name, val in entries}
    assert abs(q["GrossArea"] - 12.0) < 1e-6
    assert abs(q["NetArea"] - 11.0) < 1e-6
    assert abs(metrado - 11.0) < 1e-6 and unit == "m2"


def test_tag_survives_pushpull_churn():
    # The reason tags live in attrs: the engine's rebuilds replace face
    # objects, and the tag must ride the identity-inheritance machinery.
    scene = Scene()
    vp = _Vp(scene)
    f = scene.mesh.add_face([V(0, 0), V(4, 0), V(4, 4), V(0, 4)])
    tag_faces([f], "IfcSlab", "Piso", 1)
    pp = PushPullTool()
    pp.hovered_face = f
    pp._hover_group = None
    pp.on_click(ToolContext(viewport=vp, world=V(0, 0), screen=QPointF(0, 0),
                            modifiers=Qt.NoModifier, snap=None))
    pp.extrusion = 0.3
    pp._commit(vp)
    tagged = [g for g in scene.mesh.faces if g.attrs.get("ifc")]
    assert tagged                                   # the tag survived the push
