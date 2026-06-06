"""Phase 1 Definition of Done — the gate for the topology engine.

One test per DoD bullet, exercised through the same command paths the tools
use, plus a real Push/Pull over a chord-split triangle:

    - diagonal across a square      → two triangles
    - a square inside a face        → divides the mother (hole)
    - two rectangles sharing a side → one shared edge, not duplicated
    - a concave "L" face            → fills correctly
    - push/pull works on the result → extrudes a split triangle into a prism

Headless: ``QVector3D`` value types + a stub viewport for the Push/Pull tool.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.edits import build_add_edge, build_add_edges
from core.geometry import Face
from core.history import AddFaceCommand, History
from core.scene import Scene
from tools.pushpull import PushPullTool


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(x, y, z)


def _face_sizes(scene):
    return sorted(len(f.vertices) for f in scene.faces)


def _rectangle(scene, hist, corners):
    segments = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    hist.execute(
        build_add_edges(scene, segments, detect_faces=False,
                        extra=[AddFaceCommand(list(corners))])
    )


class _StubViewport:
    """Just enough surface for PushPullTool._commit."""

    def __init__(self, scene):
        self.scene = scene
        self.history = History(scene)

    def update(self):
        pass


def test_dod_diagonal_makes_two_triangles():
    scene = Scene()
    hist = History(scene)
    _rectangle(scene, hist, [V(0, 0), V(2, 0), V(2, 2), V(0, 2)])
    hist.execute(build_add_edge(scene, V(0, 0), V(2, 2)))
    assert _face_sizes(scene) == [3, 3]
    assert len(scene.edges) == 5


def test_dod_square_inside_face_divides_mother():
    scene = Scene()
    hist = History(scene)
    _rectangle(scene, hist, [V(0, 0), V(4, 0), V(4, 4), V(0, 4)])
    _rectangle(scene, hist, [V(1, 1), V(3, 1), V(3, 3), V(1, 3)])
    mother = scene.faces[0]
    assert len(mother.holes) == 1
    area = sum(
        QVector3D.crossProduct(t[1] - t[0], t[2] - t[0]).length() * 0.5
        for t in mother.triangulate()
    )
    assert abs(area - 12.0) < 1e-6   # 16 - 4


def test_dod_shared_edge_not_duplicated():
    scene = Scene()
    hist = History(scene)
    _rectangle(scene, hist, [V(0, 0), V(2, 0), V(2, 2), V(0, 2)])
    _rectangle(scene, hist, [V(2, 0), V(4, 0), V(4, 2), V(2, 2)])
    assert len(scene.edges) == 7     # 4 + 4 - 1 shared


def test_dod_concave_l_fills():
    el = [V(0, 0), V(2, 0), V(2, 1), V(1, 1), V(1, 2), V(0, 2)]
    area = sum(
        QVector3D.crossProduct(t[1] - t[0], t[2] - t[0]).length() * 0.5
        for t in Face(el).triangulate()
    )
    assert abs(area - 3.0) < 1e-6


def test_dod_pushpull_on_split_triangle():
    scene = Scene()
    hist = History(scene)
    _rectangle(scene, hist, [V(0, 0), V(2, 0), V(2, 2), V(0, 2)])
    hist.execute(build_add_edge(scene, V(0, 0), V(2, 2)))  # → two triangles
    triangle = scene.faces[0]
    assert len(triangle.vertices) == 3

    vp = _StubViewport(scene)
    tool = PushPullTool()
    tool.base_face = triangle
    tool.extrusion = 1.0
    tool.dragging = True
    tool._commit(vp)

    # Prism: the base triangle plus a top face and three side quads.
    assert sum(1 for f in scene.faces if len(f.vertices) == 3) >= 2  # base + top
    assert any(len(f.vertices) == 4 for f in scene.faces)            # side quads
