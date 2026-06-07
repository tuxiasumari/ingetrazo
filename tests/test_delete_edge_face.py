"""Deleting an edge takes its faces with it (SketchUp behaviour).

A face can't exist without its bounding edges, so erasing a boundary edge erases
the face too — but the face's *other* edges stay, now free. Undo restores both.
Edge *splitting* (an internal subdivision) must NOT drop faces: the collinear
sub-edges keep the boundary intact.

Headless: ``QVector3D`` values + commands against a ``Scene``.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.edits import build_add_edges
from core.history import AddFaceCommand, DeleteEdgesCommand, History
from core.scene import Scene


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(float(x), float(y), float(z))


def _square_with_face():
    scene = Scene()
    hist = History(scene)
    corners = [V(0, 0), V(2, 0), V(2, 2), V(0, 2)]
    hist.execute(build_add_edges(
        scene, [(corners[i], corners[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(corners))]))
    return scene, hist


def test_deleting_boundary_edge_removes_the_face():
    scene, hist = _square_with_face()
    assert len(scene.faces) == 1
    assert len(scene.edges) == 4

    edge = scene.edges[0]
    hist.execute(DeleteEdgesCommand([edge]))

    assert edge not in scene.edges       # edge erased
    assert len(scene.faces) == 0         # face went with it
    assert len(scene.edges) == 3         # the other three edges stay (now free)


def test_undo_restores_edge_and_face():
    scene, hist = _square_with_face()
    edge = scene.edges[0]
    hist.execute(DeleteEdgesCommand([edge]))
    assert hist.undo() is True
    assert edge in scene.edges
    assert len(scene.faces) == 1
    assert len(scene.edges) == 4


def test_edge_split_keeps_the_face():
    # Drawing an edge that crosses a face's boundary splits that boundary edge
    # internally (cascade_faces=False). The face must survive the split.
    scene, hist = _square_with_face()
    # A segment from the midpoint of the bottom edge up into the square; its
    # foot lands on the bottom edge, splitting it — but the face stays.
    hist.execute(build_add_edges(scene, [(V(1, 0), V(1, 1))], detect_faces=False))
    assert len(scene.faces) == 1         # face not dropped by the split
