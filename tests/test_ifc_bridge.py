# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The actual bridge: IngeTrazo .ifc → IngePresupuestos importer → partidas.

Loads the sibling repo's real ``parse_ifc`` (regex STEP parser) by file path
and asserts the budget quantities arrive EXACT in the units the estimator
bills by. Skips when the sibling checkout isn't present (CI, other machines).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QVector3D

from core.bim import next_object_id, tag_faces
from core.edits import build_add_edges
from core.history import AddFaceCommand, History
from core.scene import Scene
from formats.ifc import save_ifc
from tools.base import ToolContext
from tools.pushpull import PushPullTool

_IMPORTER = Path("~/ingepresupuestos-pyside6/core/ifc_importer.py").expanduser()

pytestmark = pytest.mark.skipif(
    not _IMPORTER.exists(),
    reason="IngePresupuestos checkout not present")


def _parse_ifc():
    spec = importlib.util.spec_from_file_location("ip_ifc_importer",
                                                  _IMPORTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse_ifc


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
    n0 = len(scene.mesh.faces)
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
    return scene.mesh.faces[n0:]


def test_casita_quantities_arrive_exact_in_ingepresupuestos(tmp_path):
    scene = Scene()
    vp = _Vp(scene)
    w1 = _extrude(scene, vp, [V(0, 0), V(4, 0), V(4, 0.15), V(0, 0.15)], 2.6)
    tag_faces(w1, "IfcWall", "Muro frontal", next_object_id(scene))
    w2 = _extrude(scene, vp,
                  [V(0, 5), V(4, 5), V(4, 5.15), V(0, 5.15)], 2.6)
    tag_faces(w2, "IfcWall", "Muro posterior", next_object_id(scene))
    losa = _extrude(scene, vp, [V(8, 0), V(12, 0), V(12, 3), V(8, 3)], 0.20)
    tag_faces(losa, "IfcSlab", "Losa aligerada", next_object_id(scene))
    col = _extrude(scene, vp,
                   [V(15, 0), V(15.25, 0), V(15.25, 0.25), V(15, 0.25)], 3.0)
    tag_faces(col, "IfcColumn", "C-1", next_object_id(scene))
    pil = _extrude(scene, vp,
                   [V(24, 0), V(24.3, 0), V(24.3, 0.3), V(24, 0.3)], 8.0)
    tag_faces(pil, "IfcPile", "P-1", next_object_id(scene))
    door = scene.mesh.add_face([V(30, 0, 0), V(30.9, 0, 0),
                                V(30.9, 0, 2.1), V(30, 0, 2.1)])
    tag_faces([door], "IfcDoor", "Puerta principal", next_object_id(scene))

    path = tmp_path / "casita.ifc"
    save_ifc(scene, path, project_name="Casita puente")

    info, partidas = _parse_ifc()(str(path))
    assert info["nombre"] == "Casita puente"
    rows = {p["descripcion"].split(" (")[0]: p
            for p in partidas if not p["es_titulo"]}
    # wall metrado = side face m², NOT the whole shell area
    assert rows["MURO"]["unidad"] == "m2"
    assert abs(rows["MURO"]["metrado"] - 2 * 4.0 * 2.6) < 1e-3
    assert abs(rows["LOSA"]["metrado"] - 12.0) < 1e-3
    assert rows["COLUMNA"]["unidad"] == "m3"
    assert abs(rows["COLUMNA"]["metrado"] - 0.25 * 0.25 * 3.0) < 1e-3
    # pile bills by the metre — carried by the new IfcQuantityLength
    assert rows["PILOTE"]["unidad"] == "m"
    assert abs(rows["PILOTE"]["metrado"] - 8.0) < 1e-3
    assert rows["PUERTA"]["unidad"] == "und"
    assert rows["PUERTA"]["metrado"] == 1.0
