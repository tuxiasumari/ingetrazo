# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Text annotations (leader labels) and 3D Text geometry."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication, QVector3D

_app = QGuiApplication.instance() or QGuiApplication([])

from core.history import (AddTextLabelCommand, DeleteTextLabelsCommand,
                          History)
from core.scene import Scene
from core.text3d import build_text_mesh
from core.textlabel import TextLabel
from formats import igz


def _closed(mesh) -> bool:
    counts: dict = {}
    for f in mesh.faces:
        for lp in (f.loop, *f.hole_loops):
            n = len(lp)
            for i in range(n):
                k = frozenset((id(lp[i]), id(lp[(i + 1) % n])))
                counts[k] = counts.get(k, 0) + 1
    return bool(counts) and all(c == 2 for c in counts.values())


# ---- 3D Text ----------------------------------------------------------------

def test_3d_text_builds_watertight_letters_with_holes():
    mesh = build_text_mesh("O", "Sans", height=0.5, thickness=0.1)
    assert mesh.faces
    assert _closed(mesh)                            # solid ring, hole intact
    from core.orient import signed_volume
    assert signed_volume(mesh) > 0
    # the front face carries the counter of the O as a hole
    assert any(f.hole_loops for f in mesh.faces)


def test_3d_text_height_is_the_real_block_height():
    mesh = build_text_mesh("AB", "Sans", height=0.5, thickness=0.05)
    zs = [v.position.z() for v in mesh.vertices]
    assert abs((max(zs) - min(zs)) - 0.5) < 1e-6    # exactly what was asked
    assert abs(min(zs)) < 1e-9                      # base ON the ground


def test_3d_text_flat_when_thickness_zero():
    mesh = build_text_mesh("I", "Sans", height=0.3, thickness=0.0)
    assert mesh.faces
    ys = [v.position.y() for v in mesh.vertices]
    assert abs(max(ys) - min(ys)) < 1e-9            # a flat sheet


def test_3d_text_empty_string_builds_nothing():
    assert not build_text_mesh("", "Sans").faces
    assert not build_text_mesh("   ", "Sans").faces


# ---- Leader text labels -------------------------------------------------------

def test_label_commands_undo_redo():
    scene = Scene()
    history = History(scene)
    lab = TextLabel(QVector3D(1, 2, 0), QVector3D(0.5, 0, 1), "Muro eje A")
    history.execute(AddTextLabelCommand(lab))
    assert scene.text_labels == [lab]
    history.undo()
    assert scene.text_labels == []
    history.redo()
    assert scene.text_labels == [lab]
    history.execute(DeleteTextLabelsCommand([lab]))
    assert scene.text_labels == []
    history.undo()
    assert scene.text_labels == [lab]


def test_label_igz_round_trip(tmp_path):
    scene = Scene()
    scene.text_labels.append(TextLabel(
        QVector3D(1, 2, 3), QVector3D(0, 0, 1), "Cisterna\n10 m³"))
    p = tmp_path / "texto.igz"
    igz.save_scene(scene, p)
    scene2 = Scene()
    igz.load_into(scene2, p)
    assert len(scene2.text_labels) == 1
    lab = scene2.text_labels[0]
    assert lab.text == "Cisterna\n10 m³"
    assert (lab.position() - QVector3D(1, 2, 4)).length() < 1e-6
    scene2.clear()
    assert scene2.text_labels == []
