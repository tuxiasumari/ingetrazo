"""Subtractive push/pull — recess / pocket.

Push/pull consumes the base face when that face is *bordered* (its edges are
shared with other faces — embedded in a surface or solid), and keeps it when
the face is free-standing. This is orientation-independent, so it does not
depend on the (inconsistent) sign of the face normal:

- a face drawn inside another (embedded) → pushing it in opens a recess: the
  surrounding hole becomes the mouth, plus floor + walls; the base is consumed
  either direction;
- a free-standing face is extruded into a box, keeping its base as a cap.

Headless: ``QVector3D`` values + a stub viewport for the tool.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.history import AddFaceCommand, History
from core.scene import Scene
from tools.pushpull import PushPullTool


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(x, y, z)


BIG = [V(0, 0), V(4, 0), V(4, 4), V(0, 4)]
SMALL = [V(1, 1), V(3, 1), V(3, 3), V(1, 3)]


class _StubViewport:
    def __init__(self, scene):
        self.scene = scene
        self.history = History(scene)

    def update(self):
        pass


def _embedded_scene():
    """Big face with a small face drawn inside it (so big is holed)."""
    scene = Scene()
    hist = History(scene)
    hist.execute(AddFaceCommand(BIG))
    hist.execute(AddFaceCommand(SMALL))
    return scene, scene.faces[0], scene.faces[1]  # scene, big, small


def _push(scene, base_face, distance):
    vp = _StubViewport(scene)
    tool = PushPullTool()
    tool.base_face = base_face
    tool.extrusion = distance
    tool.dragging = True
    tool._commit(vp)
    return vp


def _has_face_at_z(scene, z, nverts):
    return any(
        len(f.vertices) == nverts and all(abs(v.z() - z) < 1e-9 for v in f.vertices)
        for f in scene.faces
    )


# ---- recess (embedded, inward) ---------------------------------------------

def test_inward_push_consumes_base_face():
    scene, big, small = _embedded_scene()
    _push(scene, small, -1.0)
    assert small not in scene.faces            # base consumed
    assert big in scene.faces                  # surrounding face stays
    assert len(big.holes) == 1                 # mouth stays open


def test_inward_push_builds_floor_and_walls():
    scene, big, small = _embedded_scene()
    _push(scene, small, -1.0)
    # big (holed) + floor at z=-1 + 4 vertical wall quads.
    assert _has_face_at_z(scene, -1.0, 4)              # floor (all verts at z=-1)
    quads = [f for f in scene.faces if len(f.vertices) == 4]
    # Walls span the mouth (z=0) and the floor (z=-1); the floor sits wholly
    # at z=-1, the surrounding face wholly at z=0.
    walls = [
        f for f in quads
        if any(v.z() > -1e-9 for v in f.vertices)
        and any(v.z() < -1e-9 for v in f.vertices)
    ]
    assert len(walls) == 4
    assert len(scene.faces) == 6                       # big + floor + 4 walls


def test_inward_push_undo_restores_base():
    scene, big, small = _embedded_scene()
    vp = _push(scene, small, -1.0)
    assert vp.history.undo() is True
    assert small in scene.faces
    assert sorted(len(f.vertices) for f in scene.faces) == [4, 4]
    assert len(big.holes) == 1


# ---- bordered face is moved either direction -------------------------------

def test_outward_push_embedded_also_consumes_base():
    # A bordered face is moved by the push regardless of direction, so even
    # pushing out consumes the base (the result is a hollow raised block).
    scene, big, small = _embedded_scene()
    _push(scene, small, 1.0)
    assert small not in scene.faces
    assert len(big.holes) == 1                 # mouth stays open


# ---- standalone (free-edged) face keeps its base ---------------------------

def test_standalone_face_keeps_base():
    # No other face shares its edges → not bordered → extruded into a box with
    # the original face kept as a cap.
    scene = Scene()
    hist = History(scene)
    hist.execute(AddFaceCommand([V(0, 0), V(2, 0), V(2, 2), V(0, 2)]))
    square = scene.faces[0]
    _push(scene, square, -1.0)
    assert square in scene.faces               # base kept (it's free-standing)
    assert _has_face_at_z(scene, -1.0, 4)      # moved face
    assert len(scene.faces) == 6               # base + moved + 4 walls


# ---- face_is_bordered -------------------------------------------------------

def test_face_is_bordered_detects_embedded():
    from core.topology import face_is_bordered
    scene, big, small = _embedded_scene()
    assert face_is_bordered(small, scene.faces) is True
    assert face_is_bordered(big, scene.faces) is False  # has free outer edges


def test_face_is_bordered_standalone_false():
    from core.topology import face_is_bordered
    from core.geometry import Face
    f = Face([V(0, 0), V(2, 0), V(2, 2), V(0, 2)])
    assert face_is_bordered(f, [f]) is False


# ---- faithful end to end: cube → rect on a face → push in = recess ---------

def test_recess_on_real_cube_end_to_end():
    from core.edits import build_add_edges

    scene = Scene()
    hist = History(scene)

    # Ground rectangle, extruded into a cube with the real tool.
    ground = [V(0, 0), V(4, 0), V(4, 4), V(0, 4)]
    hist.execute(build_add_edges(
        scene, [(ground[i], ground[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(ground))]))
    _push(scene, scene.faces[0], 2.0)  # extrude up → cube

    top = next(
        f for f in scene.faces
        if len(f.vertices) == 4 and all(abs(v.z() - 2) < 1e-9 for v in f.vertices)
    )

    # Small rectangle drawn on the top face → embeds (top gains a hole).
    small_loop = [V(1, 1, 2), V(3, 1, 2), V(3, 3, 2), V(1, 3, 2)]
    hist.execute(build_add_edges(
        scene, [(small_loop[i], small_loop[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(small_loop))]))
    small = next(
        f for f in scene.faces
        if len(f.vertices) == 4 and all(abs(v.z() - 2) < 1e-9 for v in f.vertices)
        and f is not top
    )
    assert len(top.holes) == 1  # mouth punched

    # Push the small face inward → recess.
    _push(scene, small, -1.0)
    assert small not in scene.faces            # base consumed
    assert len(top.holes) == 1                 # mouth still open
    assert _has_face_at_z(scene, 1.0, 4)       # recess floor at z=1


# ---- solid-aware: a corner step carves the cube ----------------------------

def test_corner_step_notches_adjacent_walls():
    from core.edits import build_add_edges

    scene = Scene()
    hist = History(scene)
    ground = [V(0, 0), V(10, 0), V(10, 10), V(0, 10)]
    hist.execute(build_add_edges(
        scene, [(ground[i], ground[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(ground))]))
    _push(scene, scene.faces[0], 3.0)  # cube, height 3

    top = next(
        f for f in scene.faces
        if len(f.vertices) == 4 and all(abs(v.z() - 3) < 1e-9 for v in f.vertices)
    )
    # Corner rectangle on the top, sharing parts of two top edges.
    corner_loop = [V(0, 0, 3), V(4, 0, 3), V(4, 4, 3), V(0, 4, 3)]
    hist.execute(build_add_edges(
        scene, [(corner_loop[i], corner_loop[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(corner_loop))]))
    corner = next(
        f for f in scene.faces
        if all(abs(v.z() - 3) < 1e-9 for v in f.vertices) and len(f.vertices) == 4
        and max(v.x() for v in f.vertices) <= 4.001
        and max(v.y() for v in f.vertices) <= 4.001
    )

    vp = _push(scene, corner, -1.5)  # push the corner down → step

    assert corner not in scene.faces                       # base consumed
    assert _has_face_at_z(scene, 1.5, 4)                   # step tread
    # The two side walls the corner touched are notched into L-shapes.
    front = [f for f in scene.faces if all(abs(v.y()) < 1e-9 for v in f.vertices)]
    left = [f for f in scene.faces if all(abs(v.x()) < 1e-9 for v in f.vertices)]
    assert any(len(f.vertices) == 6 for f in front)        # front wall notched
    assert any(len(f.vertices) == 6 for f in left)         # left wall notched

    assert vp.history.undo() is True
    assert corner in scene.faces                           # fully restored
    assert any(len(f.vertices) == 4 and all(abs(v.y()) < 1e-9 for v in f.vertices)
               for f in scene.faces)                       # walls back to rects
