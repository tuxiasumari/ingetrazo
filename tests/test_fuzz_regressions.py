"""Named regressions for engine bugs the fuzz bench (test_fuzz_engine) found.

Each test is the minimized form of a failing fuzz sequence, kept permanent so
the root fix can't silently regress. The fuzz file holds the generator; this
file holds the stories.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.history import History
from core.orient import is_closed, orient_outward, signed_volume
from core.scene import Scene
from core.topology import is_planar
from tests.test_fuzz_engine import V, _draw_rect, _push, _up


def _cube_scene(size=4.0, height=3.0):
    scene = Scene()
    hist = History(scene)
    user: list = []
    _draw_rect(scene, hist, [V(0, 0), V(size, 0), V(size, size), V(0, size)],
               user)
    f = scene.mesh.faces[0]
    _push(scene, hist, f, _up(f, height))
    return scene, hist, user


def _wall_rect(scene, hist, user):
    """Draw a 1×1 rect on the cube's y=0 wall and return its face."""
    _draw_rect(scene, hist, [V(1, 0, 1), V(2, 0, 1), V(2, 0, 2), V(1, 0, 2)],
               user)
    return next(
        fc for fc in scene.mesh.faces
        if len(fc.vertices) == 4 and not fc.holes
        and all(abs(v.y()) < 1e-9 for v in fc.vertices)
        and 0.9 < fc.centroid().x() < 2.1 and 0.9 < fc.centroid().z() < 2.1)


# ---- pushing a face with holes (fuzz: prism seed 0) --------------------------

def test_push_holed_wall_extrudes_hole_rim_too():
    # The prism-translate path used to classify/move only the *outer* loop:
    # the wall slid away while its hole rim (and the panel inside) stayed,
    # leaving a non-planar holed face. The hole edges must count in the
    # classification and the rim must travel with the cap.
    scene, hist, user = _cube_scene()
    _wall_rect(scene, hist, user)
    wall = next(fc for fc in scene.mesh.faces if fc.holes)
    n = wall.normal().normalized()
    assert _push(scene, hist, wall, 1.0 if n.y() < 0 else -1.0)
    m = scene.mesh
    assert is_closed(m)
    assert all(is_planar(list(fc.vertices)) for fc in m.faces)
    assert signed_volume(m) > 0
    assert orient_outward(m) == []


# ---- flush side-collapse of a bump (fuzz: prism seed 0, step 7) --------------

def test_bump_side_flank_pushed_flush_dissolves_bump():
    # Push a wall rect out into a bump, then push the bump's side flank clear
    # across its width. The moved flank lands on the opposite flank: the
    # coincident pair must vanish (not survive as a zero-thickness fin — the
    # old ``if not rebuilt`` guard in apply_rebuild kept it), the degenerate
    # mouth hole must close, and the cube comes back pristine.
    scene, hist, user = _cube_scene()
    rect = _wall_rect(scene, hist, user)
    n = rect.normal().normalized()
    _push(scene, hist, rect, 1.0 if n.y() < 0 else -1.0)
    m = scene.mesh
    flank = next(fc for fc in m.faces
                 if all(abs(v.x() - 2) < 1e-9 for v in fc.vertices)
                 and fc.centroid().y() < -0.01)
    assert _push(scene, hist, flank, -1.0)
    assert (len(m.faces), len(m.edges), len(m.vertices)) == (6, 12, 8)
    assert is_closed(m) and abs(signed_volume(m) - 48.0) < 1e-6
    assert hist.undo() and hist.redo()  # snapshot round-trips


# ---- sub-weld-tolerance pushes are no-ops (fuzz: crash class) -----------------

def test_push_below_weld_tolerance_is_noop():
    # An extrusion under the mesh's weld resolution used to crash add_edge
    # ("degenerate edge: endpoints weld to one vertex").
    scene, hist, _user = _cube_scene()
    top = next(f for f in scene.mesh.faces
               if all(abs(v.z() - 3) < 1e-9 for v in f.vertices))
    before = len(hist.undo_stack)
    assert _push(scene, hist, top, 5e-5) is False or \
        len(hist.undo_stack) == before
    assert len(scene.mesh.faces) == 6


# ---- drawing on back-to-back solids keeps both outwards (fuzz: plan 16) ------

def test_draw_on_shared_plane_does_not_flip_the_other_solid():
    # Two boxes back to back share a plane with *opposite* outwards. The draw
    # heal used to run orient_coplanar_faces ungated and align them all,
    # flipping one wall inward. Winding in 3D is orient_outward's job.
    scene = Scene()
    hist = History(scene)
    user: list = []
    _draw_rect(scene, hist, [V(0, 0), V(3, 0), V(3, 4), V(0, 4)], user)
    _draw_rect(scene, hist, [V(3, 0), V(6, 0), V(6, 4), V(3, 4)], user)
    room_a = min(scene.mesh.faces, key=lambda f: f.centroid().x())
    _push(scene, hist, room_a, _up(room_a, 2.7))
    room_b = next(f for f in scene.mesh.faces
                  if all(abs(v.z()) < 1e-9 for v in f.vertices)
                  and f.centroid().x() > 3)
    _push(scene, hist, room_b, _up(room_b, 2.7))
    # Draw a rect on the now-interior shared wall's plane region of room B's
    # outer wall (x=6) and check nothing got flipped anywhere.
    _draw_rect(scene, hist,
               [V(6, 1, 1), V(6, 2, 1), V(6, 2, 2), V(6, 1, 2)], user)
    assert orient_outward(scene.mesh) == []


# ---- interior partitions survive later pushes (fuzz: cube seed 26) -----------

def test_partition_survives_push_on_another_plane():
    # Ctrl-push a wall outward (kept base = interior division), then raise the
    # floor. The rebuild used to garbage-collect the division ("material both
    # sides → no face") and leave the result cracked/pinched.
    scene, hist, _user = _cube_scene()
    wall = next(f for f in scene.mesh.faces
                if all(abs(v.x()) < 1e-9 for v in f.vertices))
    _push(scene, hist, wall, 1.5, keep_base=True)
    bottom = next(f for f in scene.mesh.faces
                  if all(abs(v.z()) < 1e-9 for v in f.vertices)
                  and f.centroid().x() > 0)
    n = bottom.normal()
    _push(scene, hist, bottom, -1.0 if n.z() < 0 else 1.0)  # floor up 1.0
    m = scene.mesh
    assert is_closed(m), "crack: partition cleanup broke the shell"
    division = [f for f in m.faces
                if all(abs(v.x()) < 1e-9 for v in f.vertices) and f.interior]
    assert division, "the kept slab division was dissolved"


# ---- inward push past a slanted wall clamps (fuzz: prism seed 21) ------------

def test_inward_push_clamps_at_lateral_exit():
    # Pushing the long wall of a triangular prism inward sweeps out through
    # the slanted walls almost immediately; uncapped it committed an
    # inside-out solid (negative volume). The lateral-exit clamp keeps the
    # solid a solid (here: effectively no-op).
    scene = Scene()
    hist = History(scene)
    tri = [V(-0.2, -2.8), V(2.7, -7.2), V(4.1, -2.7)]
    scene.mesh.add_face(tri)
    f = scene.mesh.faces[0]
    _push(scene, hist, f, _up(f, 3.0))
    wall = next(fc for fc in scene.mesh.faces
                if abs(fc.normal().z()) < 0.1 and fc.centroid().y() > -3.0)
    _push(scene, hist, wall, -2.48)
    m = scene.mesh
    assert is_closed(m)
    assert signed_volume(m) > 0
    assert orient_outward(m) == []
