# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""IFC4 export: structure, classes, geometry kind, quantities, encoding."""
from __future__ import annotations

import re

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QVector3D

from core.bim import next_object_id, tag_faces
from core.edits import build_add_edges
from core.history import AddFaceCommand, History
from core.scene import Scene
from formats.ifc import ifc_guid, save_ifc
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


def _tagged_wall_scene():
    scene = Scene()
    vp = _Vp(scene)
    pts = [V(0, 0), V(4, 0), V(4, 0.25), V(0, 0.25)]
    vp.history.execute(build_add_edges(
        scene, [(pts[i], pts[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(pts))]))
    pp = PushPullTool()
    pp.hovered_face = scene.mesh.faces[0]
    pp._hover_group = None
    pp.on_click(ToolContext(viewport=vp, world=V(0, 0), screen=QPointF(0, 0),
                            modifiers=Qt.NoModifier, snap=None))
    pp.extrusion = 2.6
    pp._commit(vp)
    tag_faces(scene.mesh.faces, "IfcWall", "Muro eje Ñ",
              next_object_id(scene))
    return scene


def test_watertight_wall_exports_brep_with_quantities(tmp_path):
    scene = _tagged_wall_scene()
    p = tmp_path / "wall.ifc"
    assert save_ifc(scene, p) == 1
    text = p.read_text()
    assert text.startswith("ISO-10303-21;")
    assert "FILE_SCHEMA(('IFC4'));" in text
    assert text.rstrip().endswith("END-ISO-10303-21;")
    assert "IFCWALL(" in text
    assert "IFCFACETEDBREP(" in text                # watertight → closed Brep
    assert "IFCCLOSEDSHELL(" in text
    # BaseQuantities carry the exact takeoff: 4.0*0.25*2.6 = 2.6 m³
    m = re.search(r"IFCQUANTITYVOLUME\('GrossVolume',\$,\$,([\d.]+),", text)
    assert m and abs(float(m.group(1)) - 2.6) < 1e-4
    assert "IFCELEMENTQUANTITY(" in text
    assert "IFCRELCONTAINEDINSPATIALSTRUCTURE(" in text
    # ñ is escaped, the file stays ASCII
    assert "\\X2\\00D1\\X0\\" in text
    text.encode("ascii")                            # would raise otherwise


def test_open_sheet_exports_surface_model_without_volume(tmp_path):
    scene = Scene()
    f = scene.mesh.add_face([V(0, 0), V(3, 0), V(3, 2), V(0, 2)])
    tag_faces([f], "IfcSlab", "Losa", 1)
    p = tmp_path / "slab.ifc"
    save_ifc(scene, p)
    text = p.read_text()
    assert "IFCSLAB(" in text
    assert "IFCSHELLBASEDSURFACEMODEL(" in text     # open → surface model
    assert "IFCQUANTITYVOLUME" not in text          # honest: no volume
    assert "IFCQUANTITYAREA('GrossArea',$,$,6.000000" in text


def test_quirky_class_padding_and_unknown_class(tmp_path):
    scene = Scene()
    f = scene.mesh.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    tag_faces([f], "IfcDoor", "P-1", 1)
    g = scene.mesh.add_face([V(5, 0), V(6, 0), V(6, 1), V(5, 1)])
    tag_faces([g], "IfcInvento", "X", 2)            # not in the curated list
    p = tmp_path / "door.ifc"
    save_ifc(scene, p)
    text = p.read_text()
    door = re.search(r"IFCDOOR\((.*)\);", text).group(1)
    assert door.count(",") == 12                    # 13 attributes (IFC4)
    assert "IFCBUILDINGELEMENTPROXY(" in text       # unknown class falls back


def test_wall_emits_standard_qto_set_with_lengths(tmp_path):
    scene = _tagged_wall_scene()
    p = tmp_path / "wall.ifc"
    save_ifc(scene, p)
    text = p.read_text()
    assert "'Qto_WallBaseQuantities'" in text
    q = {m.group(1): float(m.group(2)) for m in re.finditer(
        r"IFCQUANTITYLENGTH\('(\w+)',\$,\$,([\d.]+),", text)}
    assert abs(q["Height"] - 2.6) < 1e-4
    assert abs(q["Length"] - 4.0) < 1e-4
    assert abs(q["Width"] - 0.25) < 1e-4
    m = re.search(r"IFCQUANTITYAREA\('NetSideArea',\$,\$,([\d.]+),", text)
    assert m and abs(float(m.group(1)) - 10.4) < 1e-4
    # the misleading whole-shell area is NOT offered as a takeoff quantity
    assert "IFCQUANTITYAREA('GrossArea'" not in text


def test_door_carries_overall_height_and_width(tmp_path):
    scene = Scene()
    f = scene.mesh.add_face([V(0, 0, 0), V(0.9, 0, 0),
                             V(0.9, 0, 2.1), V(0, 0, 2.1)])
    tag_faces([f], "IfcDoor", "P-1", 1)
    p = tmp_path / "door.ifc"
    save_ifc(scene, p)
    text = p.read_text()
    door = re.search(r"IFCDOOR\((.*)\);", text).group(1)
    parts = door.split(",")
    assert abs(float(parts[8]) - 2.1) < 1e-4        # OverallHeight
    assert abs(float(parts[9]) - 0.9) < 1e-4        # OverallWidth
    assert "'Qto_DoorBaseQuantities'" in text


def test_nothing_tagged_raises(tmp_path):
    scene = Scene()
    scene.mesh.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    with pytest.raises(ValueError):
        save_ifc(scene, tmp_path / "x.ifc")


def test_guid_format():
    g = ifc_guid()
    assert len(g) == 22
    allowed = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                  "abcdefghijklmnopqrstuvwxyz_$")
    assert set(g) <= allowed
    assert g[0] in "0123"                           # 128 bits → top char < 4
