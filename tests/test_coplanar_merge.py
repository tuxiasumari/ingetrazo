"""Coplanar-merge — dissolve the seam a flush push/pull leaves behind.

SketchUp's model: an edge bordering exactly two faces in the same plane carries
no silhouette, so it is dissolved and the two faces merge into one (the "L").
This is the fix for the phantom line left when a stacked block's wall is pushed
flush with the wall it sits against — easy on the shared-vertex engine because
the two incident faces are right there on ``edge.faces``.

Headless: ``QVector3D`` values + ``Mesh`` / ``Scene`` directly.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.edits import build_add_edges
from core.history import CoplanarMergeCommand, History
from core.mesh import Mesh
from core.scene import Scene
from tools.pushpull import PushPullTool


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(float(x), float(y), float(z))


def _key3(v):
    return (round(v.x(), 3), round(v.y(), 3), round(v.z(), 3))


class _StubVP:
    def __init__(self, scene, history):
        self.scene = scene
        self.history = history

    def set_hover(self, *a):
        pass

    def set_suppressed_faces(self, *a):
        pass

    def update(self):
        pass


def _shared_edge(mesh, a, b):
    va, vb = mesh.vertex_at(a), mesh.vertex_at(b)
    return mesh.find_edge(va, vb)


# ---- dissolve_edge (mesh level) ---------------------------------------------

def test_two_coplanar_squares_merge_into_one_face():
    m = Mesh()
    m.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])      # left, +Z
    m.add_face([V(1, 0), V(2, 0), V(2, 1), V(1, 1)])      # right, +Z
    seam = _shared_edge(m, V(1, 0), V(1, 1))
    assert seam is not None and len(seam.faces) == 2

    merged = m.dissolve_edge(seam)

    assert merged is not None
    assert len(m.faces) == 1
    assert abs(merged.area() - 2.0) < 1e-9          # the full 2×1 rectangle
    assert _shared_edge(m, V(1, 0), V(1, 1)) is None  # seam edge gone


def test_perpendicular_faces_do_not_merge():
    # Floor and wall of a box share an edge but are not coplanar — keep the edge.
    m = Mesh()
    m.add_face([V(0, 0, 0), V(1, 0, 0), V(1, 1, 0), V(0, 1, 0)])  # floor, +Z
    m.add_face([V(0, 0, 0), V(1, 0, 0), V(1, 0, 1), V(0, 0, 1)])  # wall, +Y
    seam = _shared_edge(m, V(0, 0, 0), V(1, 0, 0))

    assert m.dissolve_edge(seam) is None
    assert len(m.faces) == 2


def test_non_manifold_edge_is_not_dissolved():
    # Three faces meet at one edge (two walls + a floor): not a redundant seam.
    m = Mesh()
    m.add_face([V(0, 0, 0), V(1, 0, 0), V(1, 1, 0), V(0, 1, 0)])
    m.add_face([V(0, 0, 0), V(1, 0, 0), V(1, 0, 1), V(0, 0, 1)])
    m.add_face([V(0, 0, 0), V(1, 0, 0), V(1, 0, -1), V(0, 0, -1)])
    seam = _shared_edge(m, V(0, 0, 0), V(1, 0, 0))
    assert len(seam.faces) == 3

    assert m.dissolve_edge(seam) is None
    assert len(m.faces) == 3


# ---- CoplanarMergeCommand (scene level, with undo) --------------------------

def test_command_merges_then_undo_restores():
    scene = Scene()
    hist = History(scene)
    scene.mesh.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    scene.mesh.add_face([V(1, 0), V(2, 0), V(2, 1), V(1, 1)])

    hist.execute(CoplanarMergeCommand([V(1, 0), V(1, 1)]))
    assert len(scene.mesh.faces) == 1
    assert _shared_edge(scene.mesh, V(1, 0), V(1, 1)) is None

    assert hist.undo() is True
    assert len(scene.mesh.faces) == 2
    assert _shared_edge(scene.mesh, V(1, 0), V(1, 1)) is not None


def test_command_leaves_unseeded_seam_alone():
    # A coplanar seam not touched by the operation's vertices is intentional
    # geometry — the seeded command must not dissolve it.
    scene = Scene()
    hist = History(scene)
    scene.mesh.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    scene.mesh.add_face([V(1, 0), V(2, 0), V(2, 1), V(1, 1)])

    hist.execute(CoplanarMergeCommand([V(9, 9)]))  # seed far away

    assert len(scene.mesh.faces) == 2
    assert _shared_edge(scene.mesh, V(1, 0), V(1, 1)) is not None


# ---- integration: push a partial flush wall up → clean L, not a slant -------

def _cube_with_top_strip():
    """A 2×2×2 cube whose top carries a drawn rectangle (left strip x[0,1]),
    flush with the front/back/left walls. Returns (scene, history, rect_face)."""
    scene = Scene()
    hist = History(scene)
    m = scene.mesh
    m.add_face([V(0, 0, 0), V(2, 0, 0), V(2, 2, 0), V(0, 2, 0)])  # bottom
    m.add_face([V(0, 0, 2), V(2, 0, 2), V(2, 2, 2), V(0, 2, 2)])  # top
    m.add_face([V(0, 0, 0), V(2, 0, 0), V(2, 0, 2), V(0, 0, 2)])  # front
    m.add_face([V(2, 0, 0), V(2, 2, 0), V(2, 2, 2), V(2, 0, 2)])  # right
    m.add_face([V(0, 2, 0), V(2, 2, 0), V(2, 2, 2), V(0, 2, 2)])  # back
    m.add_face([V(0, 0, 0), V(0, 2, 0), V(0, 2, 2), V(0, 0, 2)])  # left
    corners = [V(0, 0, 2), V(1, 0, 2), V(1, 2, 2), V(0, 2, 2)]
    segs = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    hist.execute(build_add_edges(scene, segs))
    rect = next(
        f for f in m.faces
        if frozenset(_key3(v) for v in f.vertices)
        == frozenset({(0, 0, 2), (1, 0, 2), (1, 2, 2), (0, 2, 2)})
    )
    return scene, hist, rect


def _push(scene, hist, face, dist):
    tool = PushPullTool()
    tool.base_face = face
    tool.extrusion = dist
    tool._normal = face.normal()
    tool._anchor = face.centroid()
    tool._attached = True
    tool._prism_cap = False
    tool._cap_positions = tool._cap_loop_positions(face)
    tool._preview_delta = QVector3D(0, 0, 0)
    tool._commit(_StubVP(scene, hist))


def test_partial_flush_push_makes_a_step_not_a_slant():
    # Pushing the left strip up should leave the front wall a clean L with the
    # T-junction vertex (1,0,2) kept and a vertical step up to (1,0,3) — the
    # old extend_wall_edge collapsed this into a diagonal (2,0,2)->(1,0,3).
    scene, hist, rect = _cube_with_top_strip()
    _push(scene, hist, rect, 1.0)

    front = next(
        f for f in scene.mesh.faces
        if all(abs(v.y()) < 1e-6 for v in f.vertices) and f.area() > 1.0
    )
    keys = {_key3(v) for v in front.vertices}
    assert (1.0, 0.0, 2.0) in keys      # T-junction kept (the step's inner corner)
    assert (1.0, 0.0, 3.0) in keys      # raised over the strip only
    # No phantom seam left on the front plane.
    for e in scene.mesh.edges:
        if len(e.faces) == 2:
            d = QVector3D.dotProduct(e.faces[0].normal(), e.faces[1].normal())
            assert d <= 0.999, f"phantom coplanar seam survived at {_key3(e.a)}-{_key3(e.b)}"


def test_partial_flush_push_undo_restores():
    scene, hist, rect = _cube_with_top_strip()
    faces_before = len(scene.mesh.faces)
    _push(scene, hist, rect, 1.0)
    assert hist.undo() is True
    assert len(scene.mesh.faces) == faces_before


def test_overhang_push_consumes_base_despite_unsplit_host_wall():
    # A cubito sits flush against a cube whose front/left walls were never split
    # at the cubito's interior corner (the colinear-overlap-no-split case).
    # Pushing the cubito's front wall outward must still consume the base (treat
    # the coplanar host wall as carrying the edge as a sub-segment) — otherwise a
    # free extrusion leaves the base as an internal partition wall.
    from core.topology import classify_push_edge

    X0, X1, XC = 0.0, 4.0, 2.0
    Y0, Y1, YC = 0.0, 6.0, 1.5
    ZT, ZC = 3.0, 5.0
    scene = Scene()
    hist = History(scene)
    m = scene.mesh
    m.add_face([V(X0, Y0, 0), V(X1, Y0, 0), V(X1, Y1, 0), V(X0, Y1, 0)])
    m.add_face([V(X0, Y0, 0), V(X1, Y0, 0), V(X1, Y0, ZT), V(X0, Y0, ZT)])   # front UNSPLIT
    m.add_face([V(X1, Y0, 0), V(X1, Y1, 0), V(X1, Y1, ZT), V(X1, Y0, ZT)])
    m.add_face([V(X0, Y1, 0), V(X1, Y1, 0), V(X1, Y1, ZT), V(X0, Y1, ZT)])
    m.add_face([V(X0, Y0, 0), V(X0, Y1, 0), V(X0, Y1, ZT), V(X0, Y0, ZT)])   # left UNSPLIT
    m.add_face([V(XC, Y0, ZT), V(X1, Y0, ZT), V(X1, Y1, ZT),
                V(X0, Y1, ZT), V(X0, YC, ZT), V(XC, YC, ZT)])                 # top remainder (L)
    m.add_face([V(X0, Y0, ZC), V(XC, Y0, ZC), V(XC, YC, ZC), V(X0, YC, ZC)])  # cubito top
    fw = m.add_face([V(X0, Y0, ZT), V(XC, Y0, ZT), V(XC, Y0, ZC), V(X0, Y0, ZC)])  # cubito front
    m.add_face([V(XC, Y0, ZT), V(XC, YC, ZT), V(XC, YC, ZC), V(XC, Y0, ZC)])
    m.add_face([V(X0, YC, ZT), V(XC, YC, ZT), V(XC, YC, ZC), V(X0, YC, ZC)])
    m.add_face([V(X0, Y0, ZT), V(X0, YC, ZT), V(X0, YC, ZC), V(X0, Y0, ZC)])

    kind, _ = classify_push_edge(fw, V(X0, Y0, ZT), V(XC, Y0, ZT), m.faces)
    assert kind == "coplanar"      # sub-segment of the unsplit cube front wall

    _push(scene, hist, fw, -2.0)   # push the front wall outward (overhang)

    partition = [
        f for f in m.faces
        if {round(v.y(), 2) for v in f.vertices} == {Y0}
        and sorted({round(v.z(), 2) for v in f.vertices}) == [ZT, ZC]
    ]
    assert not partition, "internal partition wall survived the overhang push"
    for e in m.edges:
        if len(e.faces) == 2:
            d = QVector3D.dotProduct(e.faces[0].normal(), e.faces[1].normal())
            assert d <= 0.999, "coplanar seam left after overhang merge"


def test_stitch_resolves_t_junction_then_merges_coplanar():
    from core.history import StitchSolidCommand

    # Three coplanar quads tile a 4×2 rectangle, but the left quad carries the
    # shared boundary as one edge while the right side splits it at a midpoint —
    # a T-junction the bare merge can't bridge (the edge is naked, the faces
    # don't share it). The stitch must resolve it and fuse all three into one.
    scene = Scene()
    hist = History(scene)
    m = scene.mesh
    m.add_face([V(0, 0, 0), V(2, 0, 0), V(2, 2, 0), V(0, 2, 0)])     # left quad
    m.add_face([V(2, 0, 0), V(4, 0, 0), V(4, 1, 0), V(2, 1, 0)])     # right-bottom
    m.add_face([V(2, 1, 0), V(4, 1, 0), V(4, 2, 0), V(2, 2, 0)])     # right-top
    seam = _shared_edge(m, V(2, 0, 0), V(2, 2, 0))
    assert seam is not None and len(seam.faces) == 1   # naked (T-junction)

    seed = [QVector3D(v.position) for v in list(m.vertices)]
    hist.execute(StitchSolidCommand(seed))
    assert len(m.faces) == 1                            # all three fused
    assert _shared_edge(m, V(2, 0, 0), V(2, 2, 0)) is None

    assert hist.undo() is True
    assert len(m.faces) == 3
    assert _shared_edge(m, V(2, 0, 0), V(2, 2, 0)) is not None


def test_refine_loop_inserts_collinear_t_junction():
    from core.topology import refine_loop_with_points

    loop = [V(0, -2, 3), V(0, 2, 3), V(0, 2, 5), V(0, -2, 5)]
    refined = refine_loop_with_points(loop, [V(0, 0, 3), V(9, 9, 9)])
    keys = [(round(p.x(), 2), round(p.y(), 2), round(p.z(), 2)) for p in refined]
    # (0,0,3) sits on the first edge and is inserted; the off-loop point is not.
    assert (0.0, 0.0, 3.0) in keys
    assert (9.0, 9.0, 9.0) not in keys
    assert keys.index((0.0, 0.0, 3.0)) == 1   # between (0,-2,3) and (0,2,3)


def test_push_wall_whose_base_edge_spans_a_t_junction():
    # The cubito has already been pushed to overhang the front; now its LEFT wall
    # is pushed out. That wall's bottom edge runs partly over the cube's left wall
    # (coplanar) and partly over the front overhang's floor (perpendicular), with
    # a T-junction vertex (0,0,3) between them. Without splitting the edge there it
    # reads as ``free`` and the push leaves an internal partition.
    scene = Scene()
    hist = History(scene)
    m = scene.mesh
    m.add_face([V(0, 0, 0), V(4, 0, 0), V(4, 6, 0), V(0, 6, 0)])          # cube bottom
    m.add_face([V(0, 0, 0), V(4, 0, 0), V(4, 0, 3), V(0, 0, 3)])          # cube front
    m.add_face([V(4, 0, 0), V(4, 6, 0), V(4, 6, 3), V(4, 0, 3)])          # cube right
    m.add_face([V(0, 6, 0), V(4, 6, 0), V(4, 6, 3), V(0, 6, 3)])          # cube back
    m.add_face([V(0, 0, 0), V(0, 6, 0), V(0, 6, 3), V(0, 0, 3)])          # cube left
    m.add_face([V(2, 0, 3), V(4, 0, 3), V(4, 6, 3), V(0, 6, 3),
                V(0, 2, 3), V(2, 2, 3)])                                   # cube top (L)
    m.add_face([V(0, -2, 5), V(2, -2, 5), V(2, 2, 5), V(0, 2, 5)])        # cubito top
    m.add_face([V(0, -2, 3), V(2, -2, 3), V(2, -2, 5), V(0, -2, 5)])      # cubito front
    m.add_face([V(2, -2, 3), V(2, 2, 3), V(2, 2, 5), V(2, -2, 5)])        # cubito right
    m.add_face([V(0, 2, 3), V(2, 2, 3), V(2, 2, 5), V(0, 2, 5)])          # cubito back
    left = m.add_face([V(0, -2, 3), V(0, 2, 3), V(0, 2, 5), V(0, -2, 5)])  # cubito LEFT
    m.add_face([V(0, -2, 3), V(2, -2, 3), V(2, 0, 3), V(0, 0, 3)])        # front overhang floor

    _push(scene, hist, left, -2.0)   # push the left wall outward (along -x)

    partition = [
        f for f in m.faces
        if abs(f.normal().x()) > 0.9
        and {round(v.x(), 2) for v in f.vertices} == {0.0}
        and sorted({round(v.z(), 2) for v in f.vertices}) == [3.0, 5.0]
    ]
    assert not partition, "internal partition survived the T-junction overhang push"


# ---- erase a coplanar divider edge → faces merge (SketchUp) ------------------

def test_erase_coplanar_divider_merges_faces():
    from core.history import EraseSelectionCommand

    scene = Scene()
    hist = History(scene)
    m = scene.mesh
    # A square split by a diagonal into two coplanar triangles.
    m.add_face([V(0, 0, 0), V(2, 0, 0), V(2, 2, 0)])
    m.add_face([V(0, 0, 0), V(2, 2, 0), V(0, 2, 0)])
    diagonal = _shared_edge(m, V(0, 0, 0), V(2, 2, 0))
    assert diagonal is not None and len(diagonal.faces) == 2

    hist.execute(EraseSelectionCommand([diagonal]))
    assert len(m.faces) == 1                                  # reunited
    assert _shared_edge(m, V(0, 0, 0), V(2, 2, 0)) is None
    assert abs(m.faces[0].area() - 4.0) < 1e-9

    assert hist.undo() is True
    assert len(m.faces) == 2
    assert _shared_edge(m, V(0, 0, 0), V(2, 2, 0)) is not None


def test_erase_non_coplanar_edge_cascades_faces():
    from core.history import EraseSelectionCommand

    scene = Scene()
    hist = History(scene)
    m = scene.mesh
    # Two perpendicular faces sharing an edge (a box corner): erasing it can't
    # merge them, so both go.
    m.add_face([V(0, 0, 0), V(2, 0, 0), V(2, 2, 0), V(0, 2, 0)])  # floor
    m.add_face([V(0, 0, 0), V(2, 0, 0), V(2, 0, 2), V(0, 0, 2)])  # wall
    shared = _shared_edge(m, V(0, 0, 0), V(2, 0, 0))

    hist.execute(EraseSelectionCommand([shared]))
    assert len(m.faces) == 0                                      # both removed
    assert _shared_edge(m, V(0, 0, 0), V(2, 0, 0)) is None
    assert hist.undo() is True
    assert len(m.faces) == 2
