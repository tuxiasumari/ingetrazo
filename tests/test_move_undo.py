# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Move undo/redo exactness: moving geometry onto another vertex's position and
undoing must not drag the innocent coincident vertex along (snapshot restore)."""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.edits import build_add_edges
from core.history import History, MoveVerticesCommand
from core.scene import Scene


def V(x, y):
    return QVector3D(x, y, 0)


def _positions(scene):
    return sorted((round(v.position.x(), 4), round(v.position.y(), 4))
                  for v in scene.mesh.vertices)


def _two_squares(scene, hist):
    a = [V(0, 0), V(2, 0), V(2, 2), V(0, 2)]
    b = [V(5, 0), V(7, 0), V(7, 2), V(5, 2)]
    for sq in (a, b):
        hist.execute(build_add_edges(
            scene, [(sq[i], sq[(i + 1) % 4]) for i in range(4)],
            detect_faces=True))


def test_move_onto_other_vertex_then_undo_is_exact():
    # Square A moved so two corners land exactly on square B's corners (what an
    # endpoint snap produces). Undo used to translate BY POSITION, dragging B's
    # never-moved corners along — warping the drawing. Must restore exactly.
    scene = Scene()
    hist = History(scene)
    _two_squares(scene, hist)
    before = _positions(scene)
    hist.execute(MoveVerticesCommand(
        [V(0, 0), V(2, 0), V(2, 2), V(0, 2)], QVector3D(3, 0, 0)))
    hist.undo()
    assert _positions(scene) == before


def test_move_undo_redo_cycle_stable():
    scene = Scene()
    hist = History(scene)
    _two_squares(scene, hist)
    before = _positions(scene)
    hist.execute(MoveVerticesCommand(
        [V(0, 0), V(2, 0), V(2, 2), V(0, 2)], QVector3D(3, 0, 0)))
    moved = _positions(scene)
    hist.undo()
    hist.redo()
    assert _positions(scene) == moved      # redo reproduces the move
    hist.undo()
    assert _positions(scene) == before     # and undo is still exact


def test_plain_move_still_works():
    scene = Scene()
    hist = History(scene)
    _two_squares(scene, hist)
    hist.execute(MoveVerticesCommand(
        [V(0, 0), V(2, 0), V(2, 2), V(0, 2)], QVector3D(0, 10, 0)))
    assert (0.0, 10.0) in _positions(scene)
    hist.undo()
    assert (0.0, 0.0) in _positions(scene)
