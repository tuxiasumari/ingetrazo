"""Circle, Polygon, Rotated Rectangle and Arc tools — the geometry they commit."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QVector3D

from core.history import History
from core.scene import Scene
from tools.arc import ArcTool, ThreePointArcTool
from tools.base import ToolContext
from tools.circle import CircleTool, PolygonTool
from tools.rotated_rectangle import RotatedRectangleTool


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


class _VP:
    def __init__(self, scene):
        self.scene = scene
        self.history = History(scene)

    def update(self):
        pass

    def flash_status(self, text, msec=2500):
        pass


def _ctx(vp, world):
    return ToolContext(viewport=vp, world=world, screen=QPointF(0, 0),
                       modifiers=Qt.NoModifier, snap=None)


# ---- Circle / Polygon ----------------------------------------------------------

def test_circle_commits_24gon_at_radius():
    scene = Scene()
    vp = _VP(scene)
    tool = CircleTool()
    tool.on_click(_ctx(vp, V(0, 0, 0)))     # centre
    tool.on_hover(_ctx(vp, V(2, 0, 0)))     # radius 2
    tool.on_click(_ctx(vp, V(2, 0, 0)))     # commit
    faces = scene.mesh.faces
    assert len(faces) == 1
    assert len(faces[0].vertices) == 24
    for v in faces[0].vertices:
        assert abs(v.length() - 2.0) < 1e-4   # all on the radius-2 circle (z=0)


def test_polygon_commits_hexagon():
    scene = Scene()
    vp = _VP(scene)
    tool = PolygonTool()
    tool.on_click(_ctx(vp, V(1, 1, 0)))
    tool.on_hover(_ctx(vp, V(3, 1, 0)))     # radius 2 toward +X
    tool.on_click(_ctx(vp, V(3, 1, 0)))
    f = scene.mesh.faces[0]
    assert len(f.vertices) == 6
    # one vertex points toward the cursor (+X from the centre).
    assert any((v - V(1, 1, 0)).x() > 1.99 for v in f.vertices)


def test_circle_radius_via_vcb():
    scene = Scene()
    vp = _VP(scene)
    tool = CircleTool()
    tool.on_click(_ctx(vp, V(0, 0, 0)))
    tool.on_hover(_ctx(vp, V(1, 0, 0)))     # direction set, radius will be typed
    assert tool.on_value(vp, 5.0) is True
    f = scene.mesh.faces[0]
    assert all(abs(v.length() - 5.0) < 1e-4 for v in f.vertices)


# ---- Rotated rectangle ---------------------------------------------------------

def _has_corner(face, p, tol=1e-4):
    return any((v - p).length() < tol for v in face.vertices)


def test_rotated_rectangle_axis_aligned_case():
    scene = Scene()
    vp = _VP(scene)
    tool = RotatedRectangleTool()
    tool.on_click(_ctx(vp, V(0, 0, 0)))     # corner 1
    tool.on_click(_ctx(vp, V(4, 0, 0)))     # base edge +X, length 4
    tool.on_hover(_ctx(vp, V(2, 3, 0)))     # width 3 toward +Y
    tool.on_click(_ctx(vp, V(2, 3, 0)))     # commit
    f = scene.mesh.faces[0]
    assert len(f.vertices) == 4
    for c in (V(0, 0, 0), V(4, 0, 0), V(4, 3, 0), V(0, 3, 0)):
        assert _has_corner(f, c)


def test_rotated_rectangle_diagonal_base_is_rotated():
    scene = Scene()
    vp = _VP(scene)
    tool = RotatedRectangleTool()
    tool.on_click(_ctx(vp, V(0, 0, 0)))
    tool.on_click(_ctx(vp, V(3, 3, 0)))     # base at 45°, length 3√2
    # width 1 perpendicular; perp = Z × (1,1,0)/√2 = (-1,1,0)/√2
    perp = V(-1, 1, 0)
    perp = perp / perp.length()
    cursor = V(3, 3, 0) + perp * 1.0
    tool.on_hover(_ctx(vp, cursor))
    tool.on_click(_ctx(vp, cursor))
    f = scene.mesh.faces[0]
    assert len(f.vertices) == 4
    assert _has_corner(f, V(0, 0, 0))
    assert _has_corner(f, V(3, 3, 0))
    assert _has_corner(f, V(3, 3, 0) + perp)        # base corner + width
    assert _has_corner(f, perp)                      # start corner + width


# ---- Arc -----------------------------------------------------------------------

def test_arc_points_span_chord_and_bulge():
    tool = ArcTool()
    tool.start_point = V(0, 0, 0)
    tool.end_point = V(4, 0, 0)
    tool.work_plane = None
    pts = tool._points(V(2, 1, 0))              # bulge 1 toward +Y
    assert len(pts) == 17                        # _SEGMENTS + 1
    assert (pts[0] - V(0, 0, 0)).length() < 1e-6
    assert (pts[-1] - V(4, 0, 0)).length() < 1e-6
    # max bulge (perpendicular extent) reaches ~1 at the apex.
    assert abs(max(p.y() for p in pts) - 1.0) < 1e-4


def test_arc_flat_bulge_is_straight_chord():
    tool = ArcTool()
    tool.start_point = V(0, 0, 0)
    tool.end_point = V(4, 0, 0)
    tool.work_plane = None
    pts = tool._points(V(2, 0, 0))               # ~zero bulge
    assert pts == [V(0, 0, 0), V(4, 0, 0)]


def test_arc_commits_polyline_edges():
    scene = Scene()
    vp = _VP(scene)
    tool = ArcTool()
    tool.on_click(_ctx(vp, V(0, 0, 0)))
    tool.on_click(_ctx(vp, V(4, 0, 0)))
    tool.on_hover(_ctx(vp, V(2, 1, 0)))
    tool.on_click(_ctx(vp, V(2, 1, 0)))
    assert len(scene.mesh.edges) == 16           # _SEGMENTS edges
    assert all(e.soft for e in scene.mesh.edges)  # an arc is a smooth curve


def test_3point_arc_passes_through_all_three():
    scene = Scene()
    vp = _VP(scene)
    tool = ThreePointArcTool()
    tool.on_click(_ctx(vp, V(0, 0, 0)))          # start
    tool.on_click(_ctx(vp, V(2, 2, 0)))          # through
    tool.on_hover(_ctx(vp, V(4, 0, 0)))
    pts = tool._points(V(4, 0, 0))
    assert (pts[0] - V(0, 0, 0)).length() < 1e-6
    assert (pts[-1] - V(4, 0, 0)).length() < 1e-6
    # the arc passes through the mid point (some sample lands on it)
    assert any((p - V(2, 2, 0)).length() < 1e-4 for p in pts)


# ---- Side count + soft (clean) edges ------------------------------------------

def test_typed_number_before_centre_sets_sides():
    scene = Scene()
    vp = _VP(scene)
    tool = PolygonTool()
    assert tool.on_value(vp, 8.0) is True        # 8 sides, before any click
    assert tool.sides == 8
    tool.on_click(_ctx(vp, V(0, 0, 0)))
    tool.on_hover(_ctx(vp, V(2, 0, 0)))
    tool.on_click(_ctx(vp, V(2, 0, 0)))
    assert len(scene.mesh.faces[0].vertices) == 8   # octagon


def test_circle_edges_are_soft_polygon_edges_are_hard():
    scene = Scene()
    vp = _VP(scene)
    c = CircleTool()
    c.on_click(_ctx(vp, V(0, 0, 0)))
    c.on_hover(_ctx(vp, V(2, 0, 0)))
    c.on_click(_ctx(vp, V(2, 0, 0)))
    assert all(e.soft for e in scene.mesh.edges)     # circle hides its segments

    scene2 = Scene()
    vp2 = _VP(scene2)
    p = PolygonTool()
    p.on_click(_ctx(vp2, V(0, 0, 0)))
    p.on_hover(_ctx(vp2, V(2, 0, 0)))
    p.on_click(_ctx(vp2, V(2, 0, 0)))
    assert not any(e.soft for e in scene2.mesh.edges)  # polygon shows its sides


def test_soft_survives_snapshot_and_igz(tmp_path):
    from formats import igz
    scene = Scene()
    vp = _VP(scene)
    c = CircleTool()
    c.on_click(_ctx(vp, V(0, 0, 0)))
    c.on_hover(_ctx(vp, V(2, 0, 0)))
    c.on_click(_ctx(vp, V(2, 0, 0)))
    # undo then redo keeps the soft flags (CompoundCommand re-softens on redo).
    vp.history.undo()
    vp.history.redo()
    assert all(e.soft for e in scene.mesh.edges)
    # round-trip through .igz
    path = tmp_path / "circle.igz"
    igz.save_scene(scene, path)
    loaded = Scene()
    igz.load_into(loaded, path)
    assert all(e.soft for e in loaded.mesh.edges)
