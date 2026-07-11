# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Tape Measure guides + Eraser strokes: entities, commands, .igz, erase flow."""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.guide import Guide
from core.history import (
    AddGuideCommand,
    DeleteGuidesCommand,
    History,
)
from core.scene import Scene
from formats import igz


def V(x, y, z=0.0):
    return QVector3D(x, y, z)


# ---- Guide entity ---------------------------------------------------------------

def test_guide_line_segment_spans_both_ways():
    g = Guide(V(1, 2), V(1, 0, 0))
    a, b = g.segment()
    assert a.x() < 1 < b.x() and abs(a.y() - 2) < 1e-9 and abs(b.y() - 2) < 1e-9
    assert g.is_line
    # Snap duck-typing: .a/.b exist and differ.
    assert (g.b - g.a).length() > 1


def test_guide_point():
    g = Guide(V(3, 4))
    assert not g.is_line
    a, b = g.segment()
    assert (a - b).length() < 1e-9


def test_guide_commands_undo():
    scene = Scene()
    hist = History(scene)
    g = Guide(V(0, 0), V(0, 1, 0))
    hist.execute(AddGuideCommand(g))
    assert scene.guides == [g]
    hist.undo()
    assert scene.guides == []
    hist.redo()
    hist.execute(DeleteGuidesCommand([g]))
    assert scene.guides == []
    hist.undo()
    assert scene.guides == [g]


def test_guides_igz_round_trip(tmp_path):
    scene = Scene()
    scene.guides.append(Guide(V(1, 2, 0), V(0, 1, 0)))
    scene.guides.append(Guide(V(5, 5, 0)))            # a guide point
    path = tmp_path / "guides.igz"
    igz.save_scene(scene, path)
    loaded = Scene()
    igz.load_into(loaded, path)
    assert len(loaded.guides) == 2
    assert loaded.guides[0].is_line and not loaded.guides[1].is_line


def test_clear_resets_guides():
    scene = Scene()
    scene.guides.append(Guide(V(0, 0), V(1, 0, 0)))
    scene.clear()
    assert scene.guides == []


# ---- Eraser stroke (headless: mark + release) ------------------------------------

class _FakeViewport:
    """Just enough viewport for the eraser's release path."""

    def __init__(self, scene):
        self.scene = scene
        self.history = History(scene)

    def update(self):
        pass


def test_eraser_release_erases_marked_edges_one_undo():
    from tools.eraser import EraserTool
    from core.edits import build_add_edges
    scene = Scene()
    hist = History(scene)
    sq = [V(0, 0), V(2, 0), V(2, 2), V(0, 2)]
    hist.execute(build_add_edges(
        scene, [(sq[i], sq[(i + 1) % 4]) for i in range(4)], detect_faces=True))
    vp = _FakeViewport(scene)
    tool = EraserTool()
    tool._stroke = True
    tool.marked = set(scene.mesh.edges[:2])           # swept over two edges
    tool.on_release(vp)
    assert len(scene.mesh.edges) == 2                 # two gone in one step
    vp.history.undo()
    assert len(scene.mesh.edges) == 4                 # single undo restores


def test_eraser_release_erases_guides():
    from tools.eraser import EraserTool
    scene = Scene()
    g = Guide(V(0, 0), V(1, 0, 0))
    scene.guides.append(g)
    vp = _FakeViewport(scene)
    tool = EraserTool()
    tool._stroke = True
    tool.marked = {g}
    tool.on_release(vp)
    assert scene.guides == []
