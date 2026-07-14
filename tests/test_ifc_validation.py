# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""IFC export validated against a REAL consumer (ifcopenshell).

The structural tests in test_ifc_export.py check what we wrote; these check
that an independent IFC implementation can actually read it — parse, schema
validation, spatial containment, quantity sets and geometry tessellation.
ifcopenshell is a dev-only dependency (deliberately not in requirements.txt);
the whole module skips when it isn't installed.
"""
from __future__ import annotations

import logging

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QVector3D

ifcopenshell = pytest.importorskip("ifcopenshell")
import ifcopenshell.geom  # noqa: E402
import ifcopenshell.util.element  # noqa: E402
import ifcopenshell.validate  # noqa: E402

from core.bim import next_object_id, tag_faces, tag_group  # noqa: E402
from core.edits import build_add_edges  # noqa: E402
from core.history import AddFaceCommand, History, MakeGroupCommand  # noqa: E402
from core.scene import Scene  # noqa: E402
from formats.ifc import save_ifc  # noqa: E402
from tools.base import ToolContext  # noqa: E402
from tools.pushpull import PushPullTool  # noqa: E402


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


def _extrude(scene, vp, pts, dist):
    vp.history.execute(build_add_edges(
        scene, [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))],
        detect_faces=False, extra=[AddFaceCommand(list(pts))]))
    pp = PushPullTool()
    pp.hovered_face = scene.mesh.faces[-1]
    pp._hover_group = None
    pp.on_click(ToolContext(viewport=vp, world=pts[0], screen=QPointF(0, 0),
                            modifiers=Qt.NoModifier, snap=None))
    pp.extrusion = dist
    pp._commit(vp)


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    """A representative tagged model — watertight wall, open slab sheet,
    column tagged as a group, door face — exported once for the module."""
    scene = Scene()
    vp = _Vp(scene)
    _extrude(scene, vp, [V(0, 0), V(4, 0), V(4, 0.25), V(0, 0.25)], 2.6)
    tag_faces(scene.mesh.faces, "IfcWall", "Muro eje Ñ",
              next_object_id(scene))
    slab = scene.mesh.add_face([V(6, 0), V(9, 0), V(9, 2), V(6, 2)])
    tag_faces([slab], "IfcSlab", "Losa", next_object_id(scene))
    n0 = len(scene.mesh.faces)
    _extrude(scene, vp, [V(12, 0), V(12.3, 0), V(12.3, 0.3), V(12, 0.3)], 3.0)
    vp.history.execute(MakeGroupCommand(scene.mesh.faces[n0:], []))
    tag_group(scene.groups[-1], "IfcColumn", "C-1")
    door = scene.mesh.add_face([V(20, 0, 0), V(20.9, 0, 0),
                                V(20.9, 0, 2.1), V(20, 0, 2.1)])
    tag_faces([door], "IfcDoor", "P-1", next_object_id(scene))

    path = tmp_path_factory.mktemp("ifc") / "modelo.ifc"
    assert save_ifc(scene, path) == 4
    return ifcopenshell.open(str(path)), path


def test_schema_validates_clean(exported):
    _, path = exported
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(str(path), logger)
    assert logger.statements == []


def test_spatial_skeleton_and_containment(exported):
    f, _ = exported
    assert f.schema == "IFC4"
    for cls in ("IfcProject", "IfcSite", "IfcBuilding", "IfcBuildingStorey"):
        assert len(f.by_type(cls)) == 1
    for cls in ("IfcWall", "IfcSlab", "IfcColumn", "IfcDoor"):
        (elem,) = f.by_type(cls)
        container = ifcopenshell.util.element.get_container(elem)
        assert container.is_a("IfcBuildingStorey")
    (wall,) = f.by_type("IfcWall")
    assert wall.Name == "Muro eje Ñ"                # \X2\ escape round-trips


def test_quantities_read_back_by_consumer(exported):
    f, _ = exported
    (wall,) = f.by_type("IfcWall")
    qtos = ifcopenshell.util.element.get_psets(wall, qtos_only=True)
    q = qtos["Qto_WallBaseQuantities"]
    assert abs(q["NetSideArea"] - 10.4) < 1e-4
    assert abs(q["GrossVolume"] - 2.6) < 1e-4
    assert abs(q["Width"] - 0.25) < 1e-4
    (col,) = f.by_type("IfcColumn")
    qc = ifcopenshell.util.element.get_psets(
        col, qtos_only=True)["Qto_ColumnBaseQuantities"]
    assert abs(qc["GrossVolume"] - 0.27) < 1e-4
    (door,) = f.by_type("IfcDoor")
    assert abs(door.OverallHeight - 2.1) < 1e-4
    assert abs(door.OverallWidth - 0.9) < 1e-4


def test_geometry_tessellates_with_real_kernel(exported):
    f, _ = exported
    settings = ifcopenshell.geom.settings()
    elems = [e for e in f.by_type("IfcProduct") if e.Representation]
    assert len(elems) == 4
    for e in elems:
        shape = ifcopenshell.geom.create_shape(settings, e)
        verts = shape.geometry.verts
        assert len(verts) >= 3 * 4                  # at least a quad
        zs = verts[2::3]
        if e.is_a("IfcWall"):
            assert abs(max(zs) - 2.6) < 1e-6        # world metres survive
        if e.is_a("IfcColumn"):
            assert abs(max(zs) - 3.0) < 1e-6
