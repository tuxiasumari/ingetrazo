"""Push/Pull UX parity with SketchUp: Ctrl = push/pull a copy (keep the base
face as a slab division), double-click = repeat the last distance, VCB accepts
negatives (reverse) and unit suffixes.

Headless: stub viewport + direct tool calls.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QVector3D

from core.edits import build_add_edges
from core.history import AddFaceCommand, History
from core.orient import signed_volume
from core.scene import Scene
from tools.base import ToolContext
from tools.pushpull import PushPullTool


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(x, y, z)


class _StubViewport:
    def __init__(self, scene, pick=None):
        self.scene = scene
        self.history = History(scene)
        self._pick = pick

    def update(self):
        pass

    def set_hover(self, entity):
        pass

    def set_suppressed_faces(self, faces):
        pass

    def pick_face(self, x, y):
        return self._pick

    def pick_face_any(self, x, y):
        return self._pick, None


def _ctx(vp, modifiers=Qt.NoModifier):
    return ToolContext(viewport=vp, world=QVector3D(), screen=QPointF(0, 0),
                       modifiers=modifiers, snap=None)


def _cube(scene, hist, size=4.0, height=3.0):
    ground = [V(0, 0), V(size, 0), V(size, size), V(0, size)]
    hist.execute(build_add_edges(
        scene, [(ground[i], ground[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(ground))]))
    _push(scene, scene.faces[0], height)


def _push(scene, face, dist, keep_base=False):
    vp = _StubViewport(scene)
    tool = PushPullTool()
    tool.base_face = face
    tool.extrusion = dist
    tool.dragging = True
    tool._anchor = face.centroid()
    tool._normal = face.normal()
    tool._attached, tool._prism_cap = tool._classify_base(scene)
    tool._cap_positions = tool._cap_loop_positions(face)
    tool._keep_base = keep_base
    tool._commit(vp)
    return vp


def _top(scene, z):
    return next(
        f for f in scene.faces
        if len(f.vertices) == 4 and all(abs(v.z() - z) < 1e-9 for v in f.vertices)
    )


# ---- Ctrl: push/pull a copy --------------------------------------------------

def test_ctrl_push_keeps_base_as_slab_division():
    scene = Scene()
    hist = History(scene)
    _cube(scene, hist, height=3.0)
    top = _top(scene, 3.0)
    vp = _push(scene, top, 2.0, keep_base=True)

    assert top in scene.faces                  # the start face stays
    assert _top(scene, 5.0) is not None        # new cap above it
    # 6 cube faces + 4 stacked strips + the new cap = 11; the walls are NOT
    # merged into tall faces (the belt at z=3 divides them, SketchUp-style).
    assert len(scene.faces) == 11
    belt = [e for e in scene.mesh.edges
            if abs(e.a.z() - 3) < 1e-9 and abs(e.b.z() - 3) < 1e-9]
    assert belt and all(len(e.faces) == 3 for e in belt)  # wall + strip + slab face

    assert vp.history.undo() is True
    assert len(scene.faces) == 6               # back to the plain cube


def test_ctrl_push_overrides_prism_translation():
    # Without Ctrl this cap push is a prism translate (cube just gets taller,
    # 6 faces). With Ctrl it must stack a segment instead.
    scene = Scene()
    hist = History(scene)
    _cube(scene, hist, height=3.0)
    _push(scene, _top(scene, 3.0), 2.0, keep_base=False)
    assert len(scene.faces) == 6               # translate path: no division
    scene2 = Scene()
    hist2 = History(scene2)
    _cube(scene2, hist2, height=3.0)
    _push(scene2, _top(scene2, 3.0), 2.0, keep_base=True)
    assert len(scene2.faces) == 11             # copy path: belt + strips


# ---- double-click repeats the last distance -----------------------------------

def test_double_click_repeats_last_distance():
    scene = Scene()
    hist = History(scene)
    _cube(scene, hist, height=3.0)
    PushPullTool.last_distance = None

    # First push: normal commit records the distance.
    inner = [V(1, 1, 3), V(2, 1, 3), V(2, 2, 3), V(1, 2, 3)]
    hist.execute(build_add_edges(
        scene, [(inner[i], inner[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(inner))]))
    block = next(
        f for f in scene.faces
        if all(abs(v.z() - 3) < 1e-9 for v in f.vertices) and len(f.vertices) == 4
        and max(v.x() for v in f.vertices) <= 2.001
    )
    _push(scene, block, 1.5)
    assert PushPullTool.last_distance == 1.5

    # Second block: double-click pushes it by the same 1.5 without dragging.
    inner2 = [V(2.5, 2.5, 3), V(3.5, 2.5, 3), V(3.5, 3.5, 3), V(2.5, 3.5, 3)]
    hist.execute(build_add_edges(
        scene, [(inner2[i], inner2[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(inner2))]))
    block2 = next(
        f for f in scene.faces
        if all(abs(v.z() - 3) < 1e-9 for v in f.vertices) and len(f.vertices) == 4
        and min(v.x() for v in f.vertices) >= 2.499
    )
    vp = _StubViewport(scene, pick=block2)
    tool = PushPullTool()
    tool.on_double_click(_ctx(vp))
    tops = [f for f in scene.faces
            if len(f.vertices) == 4 and all(abs(v.z() - 4.5) < 1e-9 for v in f.vertices)]
    assert len(tops) == 2                      # both blocks now at z=4.5


def test_double_click_without_history_is_plain_click():
    scene = Scene()
    hist = History(scene)
    _cube(scene, hist, height=3.0)
    PushPullTool.last_distance = None
    top = _top(scene, 3.0)
    vp = _StubViewport(scene, pick=top)
    tool = PushPullTool()
    tool.hovered_face = top
    tool.on_double_click(_ctx(vp))             # falls back to on_click
    assert tool.dragging is True               # started a drag, no commit
    assert len(scene.faces) == 6


# ---- VCB: negative reverses the direction -------------------------------------

def test_vcb_negative_value_reverses_direction():
    scene = Scene()
    hist = History(scene)
    _cube(scene, hist, height=3.0)
    inner = [V(1, 1, 3), V(2, 1, 3), V(2, 2, 3), V(1, 2, 3)]
    hist.execute(build_add_edges(
        scene, [(inner[i], inner[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(inner))]))
    block = next(
        f for f in scene.faces
        if all(abs(v.z() - 3) < 1e-9 for v in f.vertices) and len(f.vertices) == 4
        and max(v.x() for v in f.vertices) <= 2.001
    )
    vp = _StubViewport(scene)
    tool = PushPullTool()
    tool.base_face = block
    tool.dragging = True
    tool.extrusion = 0.4   # user is dragging upward (+normal, outward)
    tool._anchor = block.centroid()
    tool._normal = block.normal()
    tool._attached, tool._prism_cap = tool._classify_base(scene)
    tool._cap_positions = tool._cap_loop_positions(block)
    assert tool.on_value(vp, -1.0) is True     # typed "-1" → carve down instead
    assert any(
        len(f.vertices) == 4 and all(abs(v.z() - 2.0) < 1e-9 for v in f.vertices)
        for f in scene.faces
    )                                          # recess floor at z=2
    assert signed_volume(scene.mesh) > 0


def test_vcb_zero_rejected():
    tool = PushPullTool()
    tool.dragging = True
    tool.base_face = object()
    assert tool.on_value(None, 0.0) is False


# ---- VCB parser: units + sign --------------------------------------------------

def test_parse_value_buffer_units_and_sign():
    from views.viewport import Viewport
    parse = Viewport._parse_value_buffer
    assert parse("2") == 2.0
    assert parse("-2") == -2.0
    assert parse("30cm") == 0.3
    assert parse("1500mm") == 1.5
    assert parse("2m") == 2.0
    assert parse("2,5") == 2.5
    assert parse("1;2;50cm") == (1.0, 2.0, 0.5)
    assert parse("-30cm") == -0.3
    assert parse("abc") is None
    assert parse("2x") is None


# ---- clamp: "Offset limited to ..." -------------------------------------------

def _locked_tool(scene, face, dist):
    """Build the tool exactly as a real drag-lock click would, then set the
    extrusion (as if dragged/typed) and return it ready to clamp/commit."""
    tool = PushPullTool()
    tool.base_face = face
    tool.dragging = True
    tool._anchor = face.centroid()
    tool._normal = face.normal()
    tool._attached, tool._prism_cap = tool._classify_base(scene)
    tool._cap_positions = tool._cap_loop_positions(face)
    tool._compute_inward_limit(scene)
    tool.extrusion = dist
    tool._clamp_extrusion()
    return tool


def test_inward_limit_detected_on_prism_cap():
    scene = Scene()
    hist = History(scene)
    _cube(scene, hist, height=3.0)
    tool = _locked_tool(scene, _top(scene, 3.0), -1.0)
    assert tool._limit_in is not None and abs(tool._limit_in - 3.0) < 1e-6


def test_shrink_beyond_height_clamps():
    scene = Scene()
    hist = History(scene)
    _cube(scene, hist, height=3.0)
    tool = _locked_tool(scene, _top(scene, 3.0), -99.0)
    assert tool.extrusion == -3.0          # clamped to the solid's extent


def test_shrink_to_exact_limit_collapses_to_single_face():
    # Pushing the top all the way down flattens the box to one face — how
    # SketchUp deletes a volume with Push/Pull.
    scene = Scene()
    hist = History(scene)
    _cube(scene, hist, height=3.0)
    tool = _locked_tool(scene, _top(scene, 3.0), -99.0)
    tool._commit(_StubViewport(scene))
    m = scene.mesh
    assert len(m.faces) == 1
    assert all(abs(v.z()) < 1e-9 for v in m.faces[0].vertices)
    assert len(m.edges) == 4 and len(m.vertices) == 4


def test_corner_step_clamped_opens_notch_through_floor():
    # A corner rect pushed deeper than the cube is tall: clamp to the height,
    # landing flush on the floor plane — the notch opens clear through and the
    # result is a watertight L-section solid.
    from core.orient import signed_volume

    scene = Scene()
    hist = History(scene)
    _cube(scene, hist, size=10.0, height=3.0)
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
    tool = _locked_tool(scene, corner, -99.0)
    assert tool.extrusion == -3.0
    tool._commit(_StubViewport(scene))
    m = scene.mesh
    assert all(len(e.faces) == 2 for e in m.edges), "not watertight"
    assert signed_volume(m) > 0
    # The floor lost the corner region: it is an L (6 vertices), not a square.
    floors = [f for f in m.faces if all(abs(v.z()) < 1e-9 for v in f.vertices)]
    assert len(floors) == 1 and len(floors[0].vertices) == 6


def test_through_target_does_not_clamp():
    # A window inside a thin wall: the far face is a punch target, not a
    # blocker — the push past it must stay a through-hole, never a clamp.
    scene = Scene()
    hist = History(scene)
    floor = [V(0, 0, 0), V(4, 0, 0), V(4, 0.3, 0), V(0, 0.3, 0)]
    hist.execute(build_add_edges(
        scene, [(floor[i], floor[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(floor))]))
    _push(scene, scene.faces[0], 3.0)
    window = [V(1, 0, 1), V(3, 0, 1), V(3, 0, 2), V(1, 0, 2)]
    hist.execute(build_add_edges(
        scene, [(window[i], window[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(window))]))
    winface = next(
        f for f in scene.faces if len(f.vertices) == 4
        and all(abs(v.y()) < 1e-9 for v in f.vertices)
        and max(v.x() for v in f.vertices) <= 3.001
        and min(v.x() for v in f.vertices) >= 0.999
    )
    tool = _locked_tool(scene, winface, -0.4)
    assert tool._limit_in is None          # nothing blocks: far face is punchable
    assert tool.extrusion == -0.4
    tool._commit(_StubViewport(scene))
    backs = [f for f in scene.faces
             if all(abs(v.y() - 0.3) < 1e-4 for v in f.vertices) and f.holes]
    assert len(backs) == 1                 # punched clean through


def test_outward_pull_never_clamped():
    scene = Scene()
    hist = History(scene)
    _cube(scene, hist, height=3.0)
    tool = _locked_tool(scene, _top(scene, 3.0), 50.0)
    assert tool.extrusion == 50.0


# ---- distance inference (hover a vertex mid-push) ------------------------------

class _InferViewport(_StubViewport):
    """Viewport stub with a trivial top-view projection: world (x, z) → pixel
    (x*10, -z*10), so vertices land at predictable screen spots."""

    snap_threshold_px = 9.0

    def _world_to_pixel(self, world):
        return (world.x() * 10.0, -world.z() * 10.0)


def test_hovering_vertex_infers_distance():
    from PySide6.QtCore import QPointF
    from tools.base import ToolContext

    scene = Scene()
    hist = History(scene)
    _cube(scene, hist, height=3.0)                      # cube A
    other = [V(6, 0, 0), V(8, 0, 0), V(8, 2, 0), V(6, 2, 0)]
    hist.execute(build_add_edges(
        scene, [(other[i], other[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(other))]))
    ref = next(f for f in scene.faces
               if all(abs(v.z()) < 1e-9 for v in f.vertices)
               and min(v.x() for v in f.vertices) >= 5.999)
    # Extrude the block upward regardless of the drawn sheet's winding.
    _push(scene, ref, 5.0 if ref.normal().z() > 0 else -5.0)   # block top z=5

    vp = _InferViewport(scene)
    tool = PushPullTool()
    top = _top(scene, 3.0)
    tool.base_face = top
    tool.dragging = True
    tool._anchor = top.centroid()
    tool._normal = top.normal()
    tool._attached, tool._prism_cap = tool._classify_base(scene)
    tool._cap_positions = tool._cap_loop_positions(top)

    # Cursor over the reference block's top corner (6, 0, 5) → pixel (60, -50).
    ctx = ToolContext(viewport=vp, world=QVector3D(), screen=QPointF(60.0, -50.0),
                      modifiers=Qt.NoModifier, snap=None)
    d = tool._infer_reference_distance(ctx)
    # Anchor is the cube top (z=3); the hovered corner is at z=5 → push +2.
    assert d is not None and abs(d - 2.0) < 1e-6

    # Cursor over empty space → no inference.
    ctx2 = ToolContext(viewport=vp, world=QVector3D(), screen=QPointF(500.0, 500.0),
                       modifiers=Qt.NoModifier, snap=None)
    assert tool._infer_reference_distance(ctx2) is None

    # The base face's own corners never pin the drag.
    ctx3 = ToolContext(viewport=vp, world=QVector3D(), screen=QPointF(0.0, -30.0),
                       modifiers=Qt.NoModifier, snap=None)
    assert tool._infer_reference_distance(ctx3) is None


# ---- autofold: a move that warps a face splits it into planar pieces ----------

def test_move_lifting_a_corner_autofolds_quad():
    from core.history import MoveVerticesCommand
    from core.topology import is_planar

    scene = Scene()
    hist = History(scene)
    scene.mesh.add_face([V(0, 0, 0), V(2, 0, 0), V(2, 2, 0), V(0, 2, 0)])
    hist.execute(MoveVerticesCommand([V(2, 2, 0)], QVector3D(0, 0, 1)))

    m = scene.mesh
    assert len(m.faces) == 2                       # quad folded into 2 triangles
    assert all(len(f.vertices) == 3 for f in m.faces)
    assert all(is_planar(list(f.vertices)) for f in m.faces)
    fold = [e for e in m.edges if len(e.faces) == 2]
    assert len(fold) == 1                          # exactly one fold edge

    assert hist.undo() is True                     # snapshot undo: quad restored
    assert len(m.faces) == 1 and len(m.faces[0].vertices) == 4
    assert hist.redo() is True
    assert len(m.faces) == 2


def test_move_in_plane_does_not_fold():
    from core.history import MoveVerticesCommand

    scene = Scene()
    hist = History(scene)
    scene.mesh.add_face([V(0, 0, 0), V(2, 0, 0), V(2, 2, 0), V(0, 2, 0)])
    hist.execute(MoveVerticesCommand([V(2, 2, 0)], QVector3D(1, 1, 0)))
    assert len(scene.mesh.faces) == 1              # still one planar quad
    assert len(scene.mesh.faces[0].vertices) == 4
    assert hist.undo() is True                     # cheap inverse-translate undo
    assert abs(scene.mesh.faces[0].vertices[2].x() - 2.0) < 1e-6


def test_autofold_pentagon_folds_minimally():
    # Lift one vertex of a planar pentagon: the planar remainder must merge
    # back into one piece — pieces stay minimal, not a full triangle fan.
    from core.history import MoveVerticesCommand
    from core.topology import is_planar

    scene = Scene()
    hist = History(scene)
    scene.mesh.add_face(
        [V(0, 0, 0), V(4, 0, 0), V(4, 4, 0), V(2, 6, 0), V(0, 4, 0)])
    hist.execute(MoveVerticesCommand([V(2, 6, 0)], QVector3D(0, 0, 1.5)))
    m = scene.mesh
    assert all(is_planar(list(f.vertices)) for f in m.faces)
    assert len(m.faces) <= 3                       # folded, not fanned to bits


# ---- push/pull directly on a group's face --------------------------------------

def _boxed_group(scene, x0=0.0):
    """A 2×2×2 closed box living in its own Group, appended to the scene."""
    from core.group import Group
    from core.orient import orient_outward

    g = Group()
    m = g.mesh
    p = [V(x0, 0, 0), V(x0 + 2, 0, 0), V(x0 + 2, 2, 0), V(x0, 2, 0)]
    q = [V(x0, 0, 2), V(x0 + 2, 0, 2), V(x0 + 2, 2, 2), V(x0, 2, 2)]
    m.add_face(p)
    m.add_face(q)
    for i in range(4):
        j = (i + 1) % 4
        m.add_face([p[i], p[j], q[j], q[i]])
    orient_outward(m)
    scene.groups.append(g)
    return g


def _push_group(scene, group, face, dist):
    vp = _StubViewport(scene)
    tool = PushPullTool()
    tool.base_face = face
    tool.extrusion = dist
    tool.dragging = True
    tool._group = group
    target = tool._target_scene(scene)
    tool._anchor = face.centroid()
    tool._normal = face.normal()
    tool._attached, tool._prism_cap = tool._classify_base(target)
    tool._cap_positions = tool._cap_loop_positions(face)
    tool._compute_inward_limit(target)
    tool._commit(vp)
    return vp


def test_push_on_group_face_edits_only_the_group():
    from core.orient import signed_volume

    scene = Scene()
    hist = History(scene)
    _cube(scene, hist, height=3.0)                 # loose cube in the scene
    g = _boxed_group(scene, x0=10.0)
    loose_faces = len(scene.mesh.faces)

    top = next(f for f in g.mesh.faces
               if all(abs(v.z() - 2) < 1e-9 for v in f.vertices))
    vp = _push_group(scene, g, top, 1.0)           # raise the group's box to z=3

    assert len(scene.mesh.faces) == loose_faces    # loose mesh untouched
    assert any(all(abs(v.z() - 3) < 1e-9 for v in f.vertices)
               for f in g.mesh.faces)              # group cap moved up
    assert all(len(e.faces) == 2 for e in g.mesh.edges)
    assert signed_volume(g.mesh) > 0

    assert vp.history.undo() is True               # snapshot lands on the group
    assert any(all(abs(v.z() - 2) < 1e-9 for v in f.vertices)
               for f in g.mesh.faces)
    assert not any(v.position.z() > 2.5 for v in g.mesh.vertices)


def test_group_recess_and_clamp_use_group_geometry():
    scene = Scene()
    hist = History(scene)
    g = _boxed_group(scene)

    top = next(f for f in g.mesh.faces
               if all(abs(v.z() - 2) < 1e-9 for v in f.vertices))
    vp = _StubViewport(scene)
    tool = PushPullTool()
    tool.base_face = top
    tool.dragging = True
    tool._group = g
    target = tool._target_scene(scene)
    tool._anchor = top.centroid()
    tool._normal = top.normal()
    tool._attached, tool._prism_cap = tool._classify_base(target)
    tool._cap_positions = tool._cap_loop_positions(top)
    tool._compute_inward_limit(target)
    assert tool._limit_in is not None and abs(tool._limit_in - 2.0) < 1e-6
    tool.extrusion = -99.0
    tool._clamp_extrusion()
    assert tool.extrusion == -2.0                  # clamped by the group's box
    tool._commit(vp)
    assert len(g.mesh.faces) == 1                  # collapsed flat, group-local
    assert scene.mesh.faces == []                  # loose mesh untouched


# ---- floor-plan workflow: raising adjacent rooms --------------------------------

def test_two_room_plan_raises_cleanly():
    """The casita core loop: a plan with two rooms sharing a wall, both raised
    to the same height. The second push must not crash on the already-built
    shared wall (it deduplicates), the roofs stay two faces split by a ridge
    over the divider (SketchUp's crease rule — no slab floating over the
    wall), and nothing is left orphaned."""
    scene = Scene()
    hist = History(scene)

    def draw_rect(loop):
        hist.execute(build_add_edges(
            scene, [(loop[i], loop[(i + 1) % 4]) for i in range(4)],
            detect_faces=False, extra=[AddFaceCommand(list(loop))]))

    draw_rect([V(0, 0), V(3, 0), V(3, 4), V(0, 4)])
    draw_rect([V(3, 0), V(6, 0), V(6, 4), V(3, 4)])
    room_a = min(scene.mesh.faces, key=lambda f: f.centroid().x())
    _push(scene, room_a, 2.7)
    room_b = next(f for f in scene.mesh.faces
                  if all(abs(v.z()) < 1e-9 for v in f.vertices)
                  and f.centroid().x() > 3)
    _push(scene, room_b, 2.7)

    m = scene.mesh
    tops = [f for f in m.faces if all(abs(v.z() - 2.7) < 1e-6 for v in f.vertices)]
    shared = [f for f in m.faces if all(abs(v.x() - 3) < 1e-6 for v in f.vertices)]
    assert len(tops) == 2                      # roofs split by the ridge
    assert len(shared) == 1                    # one shared wall, deduplicated
    ridge = [e for e in m.edges
             if abs(e.a.z() - 2.7) < 1e-6 and abs(e.b.z() - 2.7) < 1e-6
             and abs(e.a.x() - 3) < 1e-6 and abs(e.b.x() - 3) < 1e-6]
    assert len(ridge) == 1 and len(ridge[0].faces) == 3   # 2 roofs + the wall
    assert sum(1 for e in m.edges if not e.faces) == 0
    seams = [e for e in m.edges if len(e.faces) == 2 and
             QVector3D.dotProduct(e.faces[0].normal().normalized(),
                                  e.faces[1].normal().normalized()) > 0.999]
    assert seams == []
