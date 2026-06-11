"""A.4 — hand-drawn subdivisions survive the plane rebuild.

``apply_rebuild`` used to union every region of a touched plane, dissolving a
diagonal the user drew on a wall whenever a push landed anything on that
plane. Now the push's own rims (captured by position at fixpoint entry) are
the only edges allowed to dissolve: any other face-bearing plane edge is the
user's structure and survives as a union boundary. The op's seams — a stacked
strip's belt, a flush landing's contact line — still merge away.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.edits import build_add_edge
from core.history import History
from core.orient import is_closed, orient_outward
from core.scene import Scene
from tests.test_fuzz_engine import V, _draw_rect, _push, _up


def _key(p):
    return (round(p.x(), 6), round(p.y(), 6), round(p.z(), 6))


def _cube_with_chord():
    """Cube with a hand-drawn chord splitting the y=0 wall in two."""
    scene = Scene()
    hist = History(scene)
    _draw_rect(scene, hist, [V(0, 0), V(4, 0), V(4, 4), V(0, 4)], [])
    f = scene.mesh.faces[0]
    _push(scene, hist, f, _up(f, 3.0))
    hist.execute(build_add_edge(scene, V(0, 0, 1), V(4, 0, 2)))
    return scene, hist


def _chord_edges(mesh):
    return [e for e in mesh.edges
            if {_key(e.a), _key(e.b)} == {(0.0, 0.0, 1.0), (4.0, 0.0, 2.0)}]


def test_chord_splits_the_wall():
    scene, _hist = _cube_with_chord()
    walls = [f for f in scene.mesh.faces
             if all(abs(v.y()) < 1e-9 for v in f.vertices)]
    assert len(walls) == 2
    assert len(_chord_edges(scene.mesh)) == 1


def test_chord_survives_bump_on_same_wall():
    # A bump pushed out elsewhere on the wall rebuilds the wall plane; the
    # chord must survive it (the DoD's "diagonal sobrevive al re-push").
    scene, hist = _cube_with_chord()
    m = scene.mesh
    _draw_rect(scene, hist,
               [V(1, 0, 0.2), V(2, 0, 0.2), V(2, 0, 0.8), V(1, 0, 0.8)], [])
    rect = next(f for f in m.faces if len(f.vertices) == 4 and not f.holes
                and all(abs(v.y()) < 1e-9 for v in f.vertices)
                and f.area() < 0.7)
    n = rect.normal()
    _push(scene, hist, rect, 0.5 if n.y() < 0 else -0.5)
    assert len(_chord_edges(m)) == 1, "user chord dissolved by the rebuild"
    assert is_closed(m)
    assert orient_outward(m) == []


def test_chord_survives_ctrl_stack_on_top():
    # A Ctrl-stack on the cube top lands strips on the wall planes; the chord
    # below must survive while the stack's own belt machinery works as usual.
    scene, hist = _cube_with_chord()
    m = scene.mesh
    top = next(f for f in m.faces
               if all(abs(v.z() - 3) < 1e-9 for v in f.vertices))
    _push(scene, hist, top, 1.0, keep_base=True)
    assert len(_chord_edges(m)) == 1, "user chord dissolved by the Ctrl stack"
    assert is_closed(m)
    assert orient_outward(m) == []


def test_stacked_strip_seam_still_dissolves():
    # The op's own seams keep merging: extending a bump leaves its flank one
    # face, not a strip-stacked pair (the DoD's "el seam del strip sí se
    # disuelve").
    scene = Scene()
    hist = History(scene)
    _draw_rect(scene, hist, [V(0, 0), V(4, 0), V(4, 4), V(0, 4)], [])
    f = scene.mesh.faces[0]
    _push(scene, hist, f, _up(f, 3.0))
    m = scene.mesh
    _draw_rect(scene, hist, [V(1, 0, 1), V(2, 0, 1), V(2, 0, 2), V(1, 0, 2)],
               [])
    rect = next(fc for fc in m.faces if len(fc.vertices) == 4 and not fc.holes
                and all(abs(v.y()) < 1e-9 for v in fc.vertices)
                and 0.9 < fc.centroid().x() < 2.1)
    n = rect.normal()
    _push(scene, hist, rect, 0.5 if n.y() < 0 else -0.5)
    cap = next(fc for fc in m.faces
               if all(abs(v.y() + 0.5) < 1e-6 for v in fc.vertices))
    n = cap.normal()
    _push(scene, hist, cap, 0.5 if n.y() < 0 else -0.5)   # extend the bump
    flanks = [fc for fc in m.faces
              if all(abs(v.x() - 2) < 1e-9 for v in fc.vertices)
              and fc.centroid().y() < -0.01]
    assert len(flanks) == 1, "the stacked strip seam did not dissolve"
    assert is_closed(m)


def test_chord_attrs_partition_survives_push():
    # A.3 meets A.4: each side of the chord can carry its own material and
    # both survive a push that rebuilds the wall plane.
    scene, hist = _cube_with_chord()
    m = scene.mesh
    lower = next(f for f in m.faces
                 if all(abs(v.y()) < 1e-9 for v in f.vertices)
                 and f.centroid().z() < 1.4)
    upper = next(f for f in m.faces
                 if all(abs(v.y()) < 1e-9 for v in f.vertices)
                 and f is not lower)
    lower.attrs = {"color": "zocalo"}
    upper.attrs = {"color": "muro"}
    top = next(f for f in m.faces
               if all(abs(v.z() - 3) < 1e-9 for v in f.vertices))
    _push(scene, hist, top, 1.0, keep_base=True)  # rebuilds the wall plane
    colors = {f.attrs.get("color") for f in m.faces
              if all(abs(v.y()) < 1e-9 for v in f.vertices)}
    assert {"zocalo", "muro"} <= colors
