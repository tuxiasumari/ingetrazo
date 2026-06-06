"""Face split / hole punching — Phase 1, sub-step 3.

When a new loop is drawn strictly inside an existing coplanar face, the
mother face must be divided so it no longer overlaps the new one — a hole.
Covers:

- the hole-aware triangulator (area conservation, on any plane, concave OK);
- containment detection (``loop_inside_face`` / ``find_containing_face``);
- ``AddFaceCommand`` punching/un-punching the mother, end to end;
- the canonical DoD case: a small rectangle inside a big one divides it.

Headless: ``QVector3D`` value types only.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.edits import build_add_edges
from core.geometry import Face
from core.history import AddFaceCommand, History
from core.scene import Scene
from core.topology import find_containing_face, loop_inside_face
from core.triangulate import triangulate


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(x, y, z)


def _tri_area(a, b, c) -> float:
    return QVector3D.crossProduct(b - a, c - a).length() * 0.5


def _total_area(tris) -> float:
    return sum(_tri_area(*t) for t in tris)


SQUARE_4 = [V(0, 0), V(4, 0), V(4, 4), V(0, 4)]
HOLE_2 = [V(1, 1), V(3, 1), V(3, 3), V(1, 3)]


# ---- triangulate ------------------------------------------------------------

def test_triangulate_simple_square():
    tris = triangulate(SQUARE_4)
    assert len(tris) == 2
    assert abs(_total_area(tris) - 16.0) < 1e-6


def test_triangulate_square_with_hole_area():
    tris = triangulate(SQUARE_4, [HOLE_2])
    # Donut area = 16 - 4 = 12, regardless of how it tessellates.
    assert abs(_total_area(tris) - 12.0) < 1e-6


def test_triangulate_hole_on_vertical_plane():
    outer = [V(0, 0, 0), V(4, 0, 0), V(4, 0, 4), V(0, 0, 4)]
    hole = [V(1, 0, 1), V(3, 0, 1), V(3, 0, 3), V(1, 0, 3)]
    tris = triangulate(outer, [hole])
    assert abs(_total_area(tris) - 12.0) < 1e-6


def test_triangulate_concave_outer():
    # An L-shape (area 3); exercises ear-clipping on a non-convex loop.
    el = [V(0, 0), V(2, 0), V(2, 1), V(1, 1), V(1, 2), V(0, 2)]
    tris = triangulate(el)
    assert abs(_total_area(tris) - 3.0) < 1e-6


def test_triangulate_hole_winding_independent():
    # Reversed hole winding must give the same donut area.
    tris = triangulate(SQUARE_4, [list(reversed(HOLE_2))])
    assert abs(_total_area(tris) - 12.0) < 1e-6


# ---- containment ------------------------------------------------------------

def test_loop_inside_face_true():
    assert loop_inside_face(Face(list(SQUARE_4)), HOLE_2) is True


def test_loop_sharing_boundary_is_not_inside():
    # Shares the corner (0,0) and edges with the mother → chord split, not hole.
    touching = [V(0, 0), V(2, 0), V(2, 2), V(0, 2)]
    assert loop_inside_face(Face(list(SQUARE_4)), touching) is False


def test_loop_outside_is_not_inside():
    outside = [V(5, 5), V(6, 5), V(6, 6), V(5, 6)]
    assert loop_inside_face(Face(list(SQUARE_4)), outside) is False


def test_loop_not_coplanar_is_not_inside():
    raised = [V(1, 1, 1), V(3, 1, 1), V(3, 3, 1), V(1, 3, 1)]
    assert loop_inside_face(Face(list(SQUARE_4)), raised) is False


def test_find_containing_face_picks_mother():
    big = Face(list(SQUARE_4))
    elsewhere = Face([V(10, 10), V(11, 10), V(11, 11)])
    mother = find_containing_face([big, elsewhere], HOLE_2)
    assert mother is big


# ---- AddFaceCommand hole punching ------------------------------------------

def _history():
    scene = Scene()
    return scene, History(scene)


def test_add_inner_face_punches_mother():
    scene, hist = _history()
    hist.execute(AddFaceCommand(SQUARE_4))
    mother = scene.faces[0]
    hist.execute(AddFaceCommand(HOLE_2))
    assert len(scene.faces) == 2          # mother + inner are both faces
    assert len(mother.holes) == 1          # mother divided
    assert abs(_total_area(mother.triangulate()) - 12.0) < 1e-6


def test_undo_inner_face_restores_mother():
    scene, hist = _history()
    hist.execute(AddFaceCommand(SQUARE_4))
    mother = scene.faces[0]
    hist.execute(AddFaceCommand(HOLE_2))
    assert hist.undo() is True
    assert mother.holes == []
    assert len(scene.faces) == 1
    assert abs(_total_area(mother.triangulate()) - 16.0) < 1e-6


def test_redo_inner_face_repunches():
    scene, hist = _history()
    hist.execute(AddFaceCommand(SQUARE_4))
    mother = scene.faces[0]
    hist.execute(AddFaceCommand(HOLE_2))
    hist.undo()
    assert hist.redo() is True
    assert len(mother.holes) == 1


def test_outer_face_added_after_inner_is_not_punched():
    # Order independence: adding the small one first, then the big one, must
    # still punch the big one (it now contains the existing small loop).
    scene, hist = _history()
    hist.execute(AddFaceCommand(HOLE_2))
    hist.execute(AddFaceCommand(SQUARE_4))
    big = scene.faces[1]
    assert len(big.holes) == 1
    assert abs(_total_area(big.triangulate()) - 12.0) < 1e-6


# ---- canonical DoD via the rectangle tool's command shape ------------------

def _draw_rectangle(scene, hist, corners):
    segments = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    hist.execute(
        build_add_edges(
            scene, segments, detect_faces=False,
            extra=[AddFaceCommand(list(corners))],
        )
    )


def test_small_rect_inside_big_rect_divides_mother():
    scene, hist = _history()
    _draw_rectangle(scene, hist, SQUARE_4)
    _draw_rectangle(scene, hist, HOLE_2)
    mother = scene.faces[0]
    assert len(scene.faces) == 2
    assert len(mother.holes) == 1
    assert abs(_total_area(mother.triangulate()) - 12.0) < 1e-6


# ---- robustness -------------------------------------------------------------

def test_triangulate_rotated_donut():
    import math
    # Outer + hole rotated 30° about Z (not axis-aligned) → same areas.
    ang = math.radians(30)
    cos, sin = math.cos(ang), math.sin(ang)

    def rot(p):
        return V(p.x() * cos - p.y() * sin, p.x() * sin + p.y() * cos, 0)

    outer = [rot(p) for p in SQUARE_4]
    hole = [rot(p) for p in HOLE_2]
    assert abs(_total_area(triangulate(outer, [hole])) - 12.0) < 1e-6


def test_triangulate_two_aligned_holes():
    # Window + door at the same height — the case a hand-rolled bridge bungled.
    outer = [V(0, 0), V(10, 0), V(10, 5), V(0, 5)]
    window = [V(1, 1), V(3, 1), V(3, 3), V(1, 3)]
    door = [V(5, 1), V(7, 1), V(7, 3), V(5, 3)]
    assert abs(_total_area(triangulate(outer, [window, door])) - (50 - 4 - 4)) < 1e-6


def test_triangulate_three_holes():
    outer = [V(0, 0), V(10, 0), V(10, 5), V(0, 5)]
    holes = [
        [V(1, 1), V(3, 1), V(3, 3), V(1, 3)],
        [V(5, 1), V(7, 1), V(7, 3), V(5, 3)],
        [V(8, 0.5), V(9, 0.5), V(9, 4.5), V(8, 4.5)],
    ]
    assert abs(_total_area(triangulate(outer, holes)) - (50 - 4 - 4 - 4)) < 1e-6


def test_two_rectangles_inside_face_punch_two_holes():
    # End to end: a wall face gains a window hole and a door hole; it must
    # still triangulate (not vanish).
    scene, hist = _history()
    hist.execute(AddFaceCommand([V(0, 0), V(10, 0), V(10, 5), V(0, 5)]))
    wall = scene.faces[0]
    hist.execute(AddFaceCommand([V(1, 1), V(3, 1), V(3, 3), V(1, 3)]))   # window
    hist.execute(AddFaceCommand([V(5, 1), V(7, 1), V(7, 3), V(5, 3)]))   # door
    assert len(wall.holes) == 2
    assert abs(_total_area(wall.triangulate()) - 42.0) < 1e-6


def test_igz_roundtrip_preserves_holes(tmp_path):
    from formats import igz

    scene, hist = _history()
    hist.execute(AddFaceCommand(SQUARE_4))
    hist.execute(AddFaceCommand(HOLE_2))
    path = tmp_path / "donut.igz"
    igz.save_scene(scene, path)

    loaded = Scene()
    igz.load_into(loaded, path)
    mother = loaded.faces[0]
    assert len(mother.holes) == 1
    assert abs(_total_area(mother.triangulate()) - 12.0) < 1e-6
