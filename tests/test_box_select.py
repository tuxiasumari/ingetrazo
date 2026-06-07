"""Rubber-band box selection — window vs crossing (SketchUp-style).

- Left-to-right drag (``crossing=False``, "window"): selects only entities whose
  screen projection is *entirely* inside the box.
- Right-to-left drag (``crossing=True``, "crossing"): selects anything the box
  touches (a vertex inside, or an edge crossing the box).

Headless: a stub viewport projects world (x, y) straight to pixels so the box
maths is exercised without a GL context.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.geometry import Edge, Face
from core.scene import Scene
from tools.select import SelectTool


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(float(x), float(y), float(z))


class _StubViewport:
    def __init__(self, scene):
        self.scene = scene
    def _world_to_pixel(self, v):
        return (v.x(), v.y())
    def update(self):
        pass


def _scene():
    s = Scene()
    s.edges.append(Edge(V(1, 1), V(2, 2)))      # fully inside (0,0,5,5)
    s.edges.append(Edge(V(4, 4), V(10, 10)))    # one end inside, crosses out
    s.edges.append(Edge(V(20, 20), V(30, 30)))  # fully outside
    s.faces.append(Face([V(1, 1), V(2, 1), V(2, 2), V(1, 2)]))      # fully inside
    s.faces.append(Face([V(4, 4), V(10, 4), V(10, 10), V(4, 10)]))  # partly inside
    return s


RECT = (0.0, 0.0, 5.0, 5.0)


def test_window_selects_only_fully_enclosed():
    scene = _scene()
    vp = _StubViewport(scene)
    SelectTool().on_box_select(vp, RECT, crossing=False, additive=False)
    assert scene.edges[0] in scene.selection          # fully inside edge
    assert scene.edges[1] not in scene.selection      # crosses out → excluded
    assert scene.edges[2] not in scene.selection
    assert scene.faces[0] in scene.selection          # fully inside face
    assert scene.faces[1] not in scene.selection      # partly inside → excluded


def test_crossing_selects_anything_touched():
    scene = _scene()
    vp = _StubViewport(scene)
    SelectTool().on_box_select(vp, RECT, crossing=True, additive=False)
    assert scene.edges[0] in scene.selection          # inside
    assert scene.edges[1] in scene.selection          # touches the box
    assert scene.edges[2] not in scene.selection      # fully outside
    assert scene.faces[0] in scene.selection
    assert scene.faces[1] in scene.selection          # a vertex is inside


def test_box_select_additive_keeps_previous():
    scene = _scene()
    vp = _StubViewport(scene)
    scene.select([scene.edges[2]])                    # pre-select the far edge
    SelectTool().on_box_select(vp, RECT, crossing=False, additive=True)
    assert scene.edges[2] in scene.selection          # kept
    assert scene.edges[0] in scene.selection          # added by the box
