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

    def set_hover(self, entity):
        pass

    def set_suppressed_faces(self, faces):
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


def _push_real(scene, base_face, distance):
    """Push mimicking the tool's ``on_click`` classification, so prism-cap
    commits go through the translation path the real app uses (not just the
    extrude/extend path the bare ``_push`` exercises)."""
    vp = _StubViewport(scene)
    tool = PushPullTool()
    tool.base_face = base_face
    tool.extrusion = distance
    tool.dragging = True
    tool._anchor = base_face.centroid()
    tool._normal = base_face.normal()
    tool._attached, tool._prism_cap = tool._classify_base(scene)
    prism_cap = tool._prism_cap  # _commit resets the tool, so capture it first
    tool._cap_positions = [QVector3D(v) for v in base_face.vertices]
    tool._commit(vp)
    return prism_cap


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


# ---- extend a prism: re-pushing a cap leaves no seam -----------------------

def test_repush_top_extends_walls_no_seam():
    """Extrude a square into a box, then push the top up again. The box should
    just get taller — the walls extend in place, leaving no ring of edges at
    the old cap level (the seam the naive 'stack a coplanar strip' produced)."""
    from core.edits import build_add_edges

    scene = Scene()
    hist = History(scene)
    ground = [V(0, 0), V(4, 0), V(4, 4), V(0, 4)]
    hist.execute(build_add_edges(
        scene, [(ground[i], ground[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(ground))]))
    _push(scene, scene.faces[0], 2.0)  # cube, height 2

    top = next(
        f for f in scene.faces
        if len(f.vertices) == 4 and all(abs(v.z() - 2) < 1e-9 for v in f.vertices)
    )
    vp = _push(scene, top, 2.0)  # push the top up again → height 4

    # A clean taller box: same 6 faces / 12 edges as a single-extrusion cube.
    assert len(scene.faces) == 6
    assert all(len(f.vertices) == 4 for f in scene.faces)
    assert len(scene.edges) == 12
    # No seam: nothing left at the old cap level z=2.
    assert not any(abs(e.a.z() - 2) < 1e-9 or abs(e.b.z() - 2) < 1e-9
                   for e in scene.edges)
    # Walls span the full new height.
    assert _has_face_at_z(scene, 4.0, 4)   # new top
    assert _has_face_at_z(scene, 0.0, 4)   # original base kept

    # Undo brings the cap back to the old level.
    assert vp.history.undo() is True
    assert any(len(f.vertices) == 4 and all(abs(v.z() - 2) < 1e-9 for v in f.vertices)
               for f in scene.faces)


def test_repush_top_extends_holed_wall_keeping_opening():
    """Cube with a window opening on one wall, then push the roof up. The wall
    carrying the window must extend with the rest — keeping its hole and
    leaving no seam at the old cap level (the bug: a holed wall fell back to
    stacking a coplanar strip)."""
    from core.edits import build_add_edges

    scene = Scene()
    hist = History(scene)
    ground = [V(0, 0), V(4, 0), V(4, 4), V(0, 4)]
    hist.execute(build_add_edges(
        scene, [(ground[i], ground[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(ground))]))
    _push(scene, scene.faces[0], 3.0)  # cube, front wall at y=0

    front = next(
        f for f in scene.faces
        if all(abs(v.y()) < 1e-9 for v in f.vertices) and len(f.vertices) == 4
    )
    # Window strictly inside the front wall → punches a hole.
    win = [V(1, 0, 1), V(3, 0, 1), V(3, 0, 2), V(1, 0, 2)]
    hist.execute(build_add_edges(
        scene, [(win[i], win[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(win))]))
    assert len(front.holes) == 1
    winface = next(
        f for f in scene.faces
        if all(abs(v.y()) < 1e-9 for v in f.vertices) and len(f.vertices) == 4
        and f is not front
    )
    _push(scene, winface, 0.5)  # recess the window
    assert len(front.holes) == 1

    top = next(
        f for f in scene.faces
        if len(f.vertices) == 4 and all(abs(v.z() - 3) < 1e-9 for v in f.vertices)
    )
    _push(scene, top, 2.0)  # raise the roof to z=5

    fronts = [f for f in scene.faces if all(abs(v.y()) < 1e-9 for v in f.vertices)]
    # Exactly one face on the front plane: the wall, extended, still holed.
    assert len(fronts) == 1
    assert len(fronts[0].holes) == 1
    assert max(v.z() for v in fronts[0].vertices) == 5.0
    # No seam ring at the old cap level on the front plane.
    seam = [
        e for e in scene.edges
        if abs(e.a.y()) < 1e-9 and abs(e.b.y()) < 1e-9
        and abs(e.a.z() - 3) < 1e-9 and abs(e.b.z() - 3) < 1e-9
    ]
    assert seam == []


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


def test_corner_step_leaves_no_orphan_edges():
    from core.edits import build_add_edges
    from core.topology import _key, _loop_edges

    scene = Scene()
    hist = History(scene)
    ground = [V(0, 0), V(10, 0), V(10, 10), V(0, 10)]
    hist.execute(build_add_edges(
        scene, [(ground[i], ground[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(ground))]))
    _push(scene, scene.faces[0], 3.0)
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
    _push(scene, corner, -1.5)

    face_edges = set()
    for f in scene.faces:
        face_edges.update(_loop_edges(f.vertices))
        for hole in f.holes:
            face_edges.update(_loop_edges(hole))
    orphans = [e for e in scene.edges
               if frozenset((_key(e.a), _key(e.b))) not in face_edges]
    assert orphans == []   # no dangling lines where faces were carved away


def test_corner_step_splits_corner_vertical_not_deletes_it():
    from core.edits import build_add_edges
    from core.topology import same_position

    scene = Scene()
    hist = History(scene)
    ground = [V(0, 0), V(10, 0), V(10, 10), V(0, 10)]
    hist.execute(build_add_edges(
        scene, [(ground[i], ground[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(ground))]))
    _push(scene, scene.faces[0], 3.0)
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
    _push(scene, corner, -1.5)

    def has_edge(a, b):
        return any(
            (same_position(e.a, a) and same_position(e.b, b))
            or (same_position(e.a, b) and same_position(e.b, a))
            for e in scene.edges
        )

    # The cube's corner vertical is split at the step level, not erased.
    assert has_edge(V(0, 0, 0), V(0, 0, 1.5))      # lower segment survives
    assert not has_edge(V(0, 0, 0), V(0, 0, 3))    # cut-away part gone


def test_stacked_block_side_pushes_stay_clean():
    # Cube, then a block stacked on its top (the block's footprint is a hole in
    # the cube's top). Pushing the block's side walls must recognise that their
    # base edge sits on that hole (a perpendicular neighbour) and deform the
    # solid cleanly — no leftover strips, no dangling edges — so a second push
    # on the adjacent wall is just as clean.
    from core.edits import build_add_edges
    from core.topology import _key, _loop_edges

    scene = Scene()
    hist = History(scene)
    ground = [V(0, 0), V(4, 0), V(4, 4), V(0, 4)]
    hist.execute(build_add_edges(
        scene, [(ground[i], ground[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(ground))]))
    _push(scene, scene.faces[0], 3.0)  # cube, top at z=3

    inner = [V(1, 1, 3), V(3, 1, 3), V(3, 3, 3), V(1, 3, 3)]
    hist.execute(build_add_edges(
        scene, [(inner[i], inner[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(inner), auto=True)]))
    inner_face = next(
        f for f in scene.faces if len(f.vertices) == 4
        and all(abs(v.z() - 3) < 1e-9 for v in f.vertices)
        and max(v.x() for v in f.vertices) <= 3.001
        and min(v.x() for v in f.vertices) >= 0.999
    )
    _push(scene, inner_face, 2.0)  # block 1..3 x 1..3 x 3..5

    def block_wall(axis, val):
        return next(
            f for f in scene.faces
            if all(abs((v.x() if axis == "x" else v.y()) - val) < 1e-9 for v in f.vertices)
            and any(v.z() > 3.5 for v in f.vertices)
        )

    # Both side pushes must be recognised as clean prism walls.
    assert _push_real(scene, block_wall("x", 1.0), 1.0), \
        "wall on a host hole misread as a free extrusion"
    assert _push_real(scene, block_wall("y", 1.0), 1.0)

    face_edges = set()
    for f in scene.faces:
        face_edges.update(_loop_edges(f.vertices))
        for hole in f.holes:
            face_edges.update(_loop_edges(hole))
    orphans = [e for e in scene.edges
               if frozenset((_key(e.a), _key(e.b))) not in face_edges]
    assert orphans == []


def test_push_through_wall_makes_a_real_hole():
    # A wall (thin box), a window on its front face, pushed past the thickness:
    # the opening punches clean through — the back face gains the same hole and
    # tunnel walls join the two, with no blind floor left inside.
    from core.edits import build_add_edges

    scene = Scene()
    hist = History(scene)
    # Wall footprint x0..4, y0..0.3 (thickness), extruded up to z=3.
    floor = [V(0, 0, 0), V(4, 0, 0), V(4, 0.3, 0), V(0, 0.3, 0)]
    hist.execute(build_add_edges(
        scene, [(floor[i], floor[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(floor))]))
    _push(scene, scene.faces[0], 3.0)

    # Window on the front face (y=0).
    window = [V(1, 0, 1), V(3, 0, 1), V(3, 0, 2), V(1, 0, 2)]
    hist.execute(build_add_edges(
        scene, [(window[i], window[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(window), auto=True)]))
    winface = next(
        f for f in scene.faces if len(f.vertices) == 4
        and all(abs(v.y()) < 1e-9 for v in f.vertices)
        and max(v.x() for v in f.vertices) <= 3.001
        and min(v.x() for v in f.vertices) >= 0.999
    )

    # Push inward past the 0.3 wall (the normal points out, so inward is -).
    _push_real(scene, winface, -0.4)

    def big_face_at_y(y):
        # 0.3 isn't exact in QVector3D's float32 storage, so use a loose tol.
        return [
            f for f in scene.faces if len(f.vertices) == 4
            and all(abs(v.y() - y) < 1e-4 for v in f.vertices)
            and {round(v.z()) for v in f.vertices} == {0, 3}
        ]

    front = big_face_at_y(0.0)
    back = big_face_at_y(0.3)
    assert len(front) == 1 and len(front[0].holes) == 1   # mouth on the front
    assert len(back) == 1 and len(back[0].holes) == 1     # punched clean through
    # No blind floor cap left inside the wall thickness.
    assert [f for f in scene.faces if all(0.01 < v.y() < 0.29 for v in f.vertices)] == []
    # The window pane itself is consumed.
    assert [
        f for f in scene.faces if all(abs(v.y()) < 1e-9 for v in f.vertices)
        and {round(v.z()) for v in f.vertices} == {1, 2}
    ] == []
