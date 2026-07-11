# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Curve entity: a drawn circle/arc's segments share a curve id so selecting one
selects the whole curve (SketchUp), and it survives undo/redo + .igz."""
from __future__ import annotations

import math

from PySide6.QtGui import QVector3D

from core.history import History, TagCurveCommand
from core.scene import Scene
from formats import igz


def _circle_pts(cx, cy, r, n=24):
    return [QVector3D(cx + r * math.cos(2 * math.pi * i / n),
                      cy + r * math.sin(2 * math.pi * i / n), 0.0)
            for i in range(n)]


def _draw_circle(scene):
    from core.edits import build_add_edges
    from core.history import AddFaceCommand
    pts = _circle_pts(0, 0, 5, 24)
    segs = [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
    return build_add_edges(scene, segs, detect_faces=False,
                           extra=[AddFaceCommand(list(pts)),
                                  TagCurveCommand(list(pts), closed=True)]), pts


def test_circle_segments_share_one_curve_id():
    scene = Scene()
    hist = History(scene)
    cmd, pts = _draw_circle(scene)
    hist.execute(cmd)
    ids = {e.curve for e in scene.mesh.edges}
    assert None not in ids            # every segment tagged
    assert len(ids) == 1              # all share one id


def test_curve_edges_selects_whole_circle():
    scene = Scene()
    hist = History(scene)
    cmd, pts = _draw_circle(scene)
    hist.execute(cmd)
    one = scene.mesh.edges[0]
    whole = scene.mesh.curve_edges(one)
    assert len(whole) == 24           # clicking one segment → all 24


def test_curve_id_survives_undo_redo():
    scene = Scene()
    hist = History(scene)
    cmd, pts = _draw_circle(scene)
    hist.execute(cmd)
    cid = scene.mesh.edges[0].curve
    hist.undo()
    assert scene.mesh.edges == []     # gone
    hist.redo()
    ids = {e.curve for e in scene.mesh.edges}
    assert ids == {cid}               # redo keeps the same curve id


def test_curve_id_survives_igz(tmp_path):
    scene = Scene()
    hist = History(scene)
    cmd, pts = _draw_circle(scene)
    hist.execute(cmd)
    path = tmp_path / "circle.igz"
    igz.save_scene(scene, path)

    loaded = Scene()
    igz.load_into(loaded, path)
    ids = {e.curve for e in loaded.mesh.edges}
    assert len(ids) == 1 and None not in ids
    # curve_edges still groups the loaded circle.
    assert len(loaded.mesh.curve_edges(loaded.mesh.edges[0])) == 24


def test_two_circles_get_distinct_ids():
    scene = Scene()
    hist = History(scene)
    c1, _ = _draw_circle(scene)
    hist.execute(c1)
    from core.edits import build_add_edges
    from core.history import AddFaceCommand
    pts2 = _circle_pts(100, 100, 5, 24)
    segs2 = [(pts2[i], pts2[(i + 1) % 24]) for i in range(24)]
    hist.execute(build_add_edges(scene, segs2, detect_faces=False,
                                 extra=[AddFaceCommand(list(pts2)),
                                        TagCurveCommand(list(pts2), closed=True)]))
    ids = {e.curve for e in scene.mesh.edges}
    assert len(ids) == 2              # two circles → two distinct curve ids


def test_tag_curve_robust_to_split_segments():
    # A loop segment split into two pieces: both must be tagged (this is what
    # was breaking selection when a circle crossed existing geometry).
    from core.mesh import Mesh
    m = Mesh()
    loop = [QVector3D(0, 0, 0), QVector3D(10, 0, 0),
            QVector3D(10, 10, 0), QVector3D(0, 10, 0)]
    m.add_edge(QVector3D(0, 0, 0), QVector3D(5, 0, 0))    # split first segment
    m.add_edge(QVector3D(5, 0, 0), QVector3D(10, 0, 0))
    m.add_edge(QVector3D(10, 0, 0), QVector3D(10, 10, 0))
    m.add_edge(QVector3D(10, 10, 0), QVector3D(0, 10, 0))
    m.add_edge(QVector3D(0, 10, 0), QVector3D(0, 0, 0))
    cid = m.tag_curve(loop, closed=True)
    assert cid is not None
    assert all(e.curve == cid for e in m.edges)          # every piece tagged
    assert len(m.curve_edges(m.edges[0])) == 5


def _draw_user_scenario(scene, hist):
    """The reported case: rectangle → corner square → circle crossing it."""
    from core.edits import build_add_edges
    from core.history import AddFaceCommand, RebuildPlanarFacesCommand

    def V(x, y):
        return QVector3D(x, y, 0)

    rect = [V(0, 0), V(12, 0), V(12, 8), V(0, 8)]
    hist.execute(build_add_edges(
        scene, [(rect[i], rect[(i + 1) % 4]) for i in range(4)],
        detect_faces=True, extra=[AddFaceCommand(rect)]))
    sq = [V(0, 0), V(3.6, 0), V(3.6, 3.6), V(0, 3.6)]
    hist.execute(build_add_edges(
        scene, [(sq[i], sq[(i + 1) % 4]) for i in range(4)], detect_faces=True))
    c, r = V(3.6, 3.6), 3.6
    pts = [V(c.x() + r * math.cos(2 * math.pi * i / 24),
             c.y() + r * math.sin(2 * math.pi * i / 24)) for i in range(24)]
    segs = [(pts[i], pts[(i + 1) % 24]) for i in range(24)]
    hist.execute(build_add_edges(
        scene, segs, detect_faces=False,
        extra=[TagCurveCommand(list(pts), closed=True),
               RebuildPlanarFacesCommand()]))
    return r


def test_circle_crossing_square_two_contours_and_sector_face():
    # SketchUp: the crossed circle becomes TWO separate contours, and the
    # quarter-circle sector inside the square is recognised as a face.
    scene = Scene()
    hist = History(scene)
    r = _draw_user_scenario(scene, hist)
    ids = sorted({e.curve for e in scene.mesh.edges if e.curve is not None})
    assert len(ids) == 2                              # two contours
    sizes = sorted(len([e for e in scene.mesh.edges if e.curve == cid])
                   for cid in ids)
    assert sizes == [6, 18]                           # quarter inside + rest
    target = math.pi * r * r / 4                       # sector area (~10.18)
    assert any(abs(f.area() - target) < 0.2 for f in scene.mesh.faces)


def test_square_drawn_after_circle_splits_three_areas():
    # The reverse order of the scenario above: the circle exists FIRST and the
    # square lands over it. The straight-edge planner can't form the lens, so
    # build_add_edges must run the planar arrangement itself (flat + curves
    # gate) — otherwise a duplicate square face stacks over the lens
    # (z-fighting stripes on screen). SketchUp: three areas.
    from core.edits import build_add_edges
    from core.history import AddFaceCommand, RebuildPlanarFacesCommand

    scene = Scene()
    hist = History(scene)
    pts = _circle_pts(0, -2, 4)
    segs = [(pts[i], pts[(i + 1) % 24]) for i in range(24)]
    hist.execute(build_add_edges(
        scene, segs, detect_faces=False,
        extra=[TagCurveCommand(list(pts), closed=True),
               RebuildPlanarFacesCommand()]))
    sq = [QVector3D(-5, 0, 0), QVector3D(5, 0, 0),
          QVector3D(5, 10, 0), QVector3D(-5, 10, 0)]
    hist.execute(build_add_edges(
        scene, [(sq[i], sq[(i + 1) % 4]) for i in range(4)],
        detect_faces=True, extra=[AddFaceCommand(list(sq))]))
    faces = list(scene.mesh.faces)
    assert len(faces) == 3                              # lens + disc rest + square rest
    areas = sorted(f.area() for f in faces)
    assert areas[0] < areas[1] < areas[2]
    # lens + (disc − lens) + (square − lens) = 24-gon disc + square − lens
    disc = 0.5 * 24 * 16 * math.sin(2 * math.pi / 24)
    assert abs(sum(areas) - (disc + 100 - areas[0])) < 1e-3
    # no doubled region: no two faces share (almost) the same area ≈ 100
    assert sum(1 for a in areas if a > 85) == 1
    # the crossed circle split into two contours
    ids = {e.curve for e in scene.mesh.edges if e.curve is not None}
    assert len(ids) == 2
    # undo restores the pristine pre-square state
    hist.undo()
    assert len(scene.mesh.faces) == 1
    assert len(scene.mesh.edges) == 24


def _click(vp, tool, x, y):
    from PySide6.QtCore import QPointF, Qt
    from tools.base import ToolContext
    tool.on_click(ToolContext(viewport=vp, world=QVector3D(x, y, 0),
                              screen=QPointF(0, 0), modifiers=Qt.NoModifier,
                              snap=None))


class _Vp:
    def __init__(self, scene):
        self.scene = scene
        self.history = History(scene)

    def update(self):
        pass

    def flash_status(self, *a, **k):
        pass


def test_two_circles_intersect_next_to_a_solid():
    # As soon as ANY 3D geometry exists, the whole-mesh flat gate goes dark and
    # curves used to fall back to naive stacked discs (no lens, z-fighting).
    # The scoped per-plane arrangement must still split into three areas and
    # leave the solid untouched.
    from tools.circle import CircleTool

    scene = Scene()
    vp = _Vp(scene)
    marker = scene.mesh.add_face([QVector3D(20, 20, 5), QVector3D(24, 20, 5),
                                  QVector3D(24, 24, 5)])
    for cx in (0, 5):
        t = CircleTool()
        t.work_plane = None
        _click(vp, t, cx, 0)
        _click(vp, t, cx + 4, 0)
    ground = [f for f in scene.mesh.faces
              if all(abs(v.position.z()) < 1e-6 for v in f.loop)]
    assert len(ground) == 3                      # lens + two crescents
    areas = sorted(round(f.area(), 1) for f in ground)
    assert areas[0] < areas[1] == areas[2]
    assert marker in scene.mesh.faces            # 3D geometry untouched
    # contours: each circle split in two at the crossings — and no fragment
    # contours from AddFace's full-length duplicate chords (pruned)
    sizes = sorted(len([e for e in scene.mesh.edges if e.curve == cid])
                   for cid in {e.curve for e in scene.mesh.edges
                               if e.curve is not None})
    assert sizes == [8, 8, 18, 18]
    vp.history.undo()
    assert len(scene.mesh.faces) == 2            # circle 1 disc + marker
    assert len({e.curve for e in scene.mesh.edges
                if e.curve is not None}) == 1


def test_square_over_circle_next_to_a_solid():
    # Same 3D-scene gate for STRAIGHT edges over curves: the per-plane rebuild
    # in build_add_edges must split square+circle into three areas.
    from core.edits import build_add_edges
    from core.history import AddFaceCommand

    scene = Scene()
    hist = History(scene)
    scene.mesh.add_face([QVector3D(20, 20, 5), QVector3D(24, 20, 5),
                         QVector3D(24, 24, 5)])
    vp = _Vp(scene)
    vp.history = hist
    from tools.circle import CircleTool
    t = CircleTool()
    t.work_plane = None
    _click(vp, t, 0, -2)
    _click(vp, t, 4, -2)
    sq = [QVector3D(-5, 0, 0), QVector3D(5, 0, 0),
          QVector3D(5, 10, 0), QVector3D(-5, 10, 0)]
    hist.execute(build_add_edges(
        scene, [(sq[i], sq[(i + 1) % 4]) for i in range(4)],
        detect_faces=True, extra=[AddFaceCommand(list(sq))]))
    ground = [f for f in scene.mesh.faces
              if all(abs(v.position.z()) < 1e-6 for v in f.loop)]
    assert len(ground) == 3
    assert sum(1 for f in ground if f.area() > 85) == 1   # no doubled square


def test_paste_keeps_curve_identity():
    # Copy/paste re-created edges with no soft/curve flags, silently degrading
    # a pasted circle to 24 loose segments (per-segment selection — the
    # circo.igz report). The clipboard must carry the flags and paste must
    # remap curve ids to fresh ones.
    from tools.circle import CircleTool
    from tools.paste import PasteTool
    from views.viewport import Viewport

    scene = Scene()
    vp = _Vp(scene)
    vp.clipboard = None
    t = CircleTool()
    t.work_plane = None
    _click(vp, t, 0, 0)
    _click(vp, t, 3, 0)
    arc = next(e for e in scene.mesh.edges if e.curve is not None)
    scene.selection.update(scene.mesh.curve_edges(arc))
    assert Viewport.copy_selection(vp)          # unbound: stub viewport
    p = PasteTool()
    p.on_activate(vp)
    _click(vp, p, 10, 0)
    ids = {e.curve for e in scene.mesh.edges if e.curve is not None}
    assert len(ids) == 2                        # original + pasted, own ids
    assert sum(1 for e in scene.mesh.edges if e.curve is None) == 0
    sizes = sorted(len([e for e in scene.mesh.edges if e.curve == cid])
                   for cid in ids)
    assert sizes == [24, 24]


def test_offset_of_circle_tags_ring_and_undoes_cleanly():
    # The offset of a circle face must select as ONE contour (SketchUp), and
    # undo must remove the ring exactly (the hole edges used to leak).
    from tools.circle import CircleTool
    from tools.offset import OffsetTool

    scene = Scene()
    vp = _Vp(scene)
    t = CircleTool()
    t.work_plane = None
    _click(vp, t, 0, 0)
    _click(vp, t, 3, 0)
    edges0 = len(scene.mesh.edges)
    disc = scene.mesh.faces[0]
    o = OffsetTool()
    o.base_face = disc
    o._loop = [QVector3D(v) for v in disc.vertices]
    o._normal = disc.normal()
    o.distance = 1.0
    o._commit(vp)
    ids = {e.curve for e in scene.mesh.edges if e.curve is not None}
    assert len(ids) == 2                        # source circle + offset ring
    assert len(scene.mesh.faces) == 2           # ring + inner
    vp.history.undo()
    assert len(scene.mesh.edges) == edges0      # no leaked ring edges
    assert len(scene.mesh.faces) == 1
    vp.history.redo()
    assert len(scene.mesh.faces) == 2


def test_deleting_one_contour_leaves_the_other():
    from core.history import EraseSelectionCommand
    from core.mesh import Edge
    scene = Scene()
    hist = History(scene)
    _draw_user_scenario(scene, hist)
    outer_id = max({e.curve for e in scene.mesh.edges if e.curve is not None},
                   key=lambda cid: len([e for e in scene.mesh.edges
                                        if e.curve == cid]))
    one = next(e for e in scene.mesh.edges if e.curve == outer_id)
    selected = scene.mesh.curve_edges(one)
    assert len(selected) == 18                        # whole outer contour
    hist.execute(EraseSelectionCommand(
        [e for e in selected if isinstance(e, Edge)], []))
    left = [e for e in scene.mesh.edges if e.curve is not None]
    assert len(left) == 6                             # inner arc intact
    assert all(e not in scene.mesh.edges for e in selected)


def test_line_crossing_circle_splits_into_contours():
    # Drawing a line across a circle must keep every split piece tagged (the
    # planner propagates curve ids) and re-split into separate contours.
    from core.edits import build_add_edges
    from core.history import AddFaceCommand
    scene = Scene()
    hist = History(scene)
    cmd, pts = _draw_circle(scene)
    hist.execute(cmd)
    hist.execute(build_add_edges(
        scene, [(QVector3D(-10, 0, 0), QVector3D(10, 0, 0))], detect_faces=True))
    curve_edges = [e for e in scene.mesh.edges if e.curve is not None]
    ids = {e.curve for e in curve_edges}
    assert len(ids) == 2                              # split into two arcs
    # No piece of the circle lost its tag (each side spans half the circle).
    per = sorted(len([e for e in curve_edges if e.curve == cid]) for cid in ids)
    assert sum(per) >= 24                             # splits may add pieces


def test_deleting_big_rectangle_leaves_clean_faces():
    # Erasing the outer rectangle used to punch a bogus hole into the sector
    # face (the heal's partial-overlap test used the polygon centroid, which
    # for the concave square-minus-sector face falls inside the sector) —
    # producing a giant garbled face on screen. Faces must stay hole-free.
    from core.history import EraseSelectionCommand
    scene = Scene()
    hist = History(scene)
    _draw_user_scenario(scene, hist)

    def on_rect_boundary(e):
        for p in (e.a, e.b):
            if not (abs(p.x()) < 1e-6 or abs(p.x() - 12) < 1e-6
                    or abs(p.y()) < 1e-6 or abs(p.y() - 8) < 1e-6):
                return False
        return True

    big = [e for e in scene.mesh.edges
           if e.curve is None and on_rect_boundary(e)]
    hist.execute(EraseSelectionCommand(big, []))
    assert len(scene.mesh.faces) == 2
    for f in scene.mesh.faces:
        assert not f.hole_loops                     # no spurious holes
        assert f.triangulate()                      # triangulates cleanly
    areas = sorted(round(f.area(), 1) for f in scene.mesh.faces)
    assert areas == [10.1, 30.2]                    # sector + rest of disc


def test_tangent_lines_split_circle_into_arcs():
    # Two lines merely TOUCHING the circle at vertices (endpoint snap — no
    # crossing, no edge splits) must still break the curve there, SketchUp-
    # style: deleting the arc between the tangent points leaves the rest.
    from core.edits import build_add_edge
    from core.history import EraseSelectionCommand
    scene = Scene()
    hist = History(scene)
    cmd, pts = _draw_circle(scene)
    hist.execute(cmd)
    hist.execute(build_add_edge(scene, QVector3D(9, 2, 0), pts[0]))
    hist.execute(build_add_edge(scene, QVector3D(2, 9, 0), pts[6]))
    ids = sorted({e.curve for e in scene.mesh.edges if e.curve is not None})
    sizes = sorted(len([e for e in scene.mesh.edges if e.curve == c])
                   for c in ids)
    assert sizes == [6, 18]                     # two arcs at the touch points
    short = min(ids, key=lambda c: len([e for e in scene.mesh.edges
                                        if e.curve == c]))
    sel = scene.mesh.curve_edges(
        next(e for e in scene.mesh.edges if e.curve == short))
    hist.execute(EraseSelectionCommand(sel, []))
    left = [e for e in scene.mesh.edges if e.curve is not None]
    assert len(left) == 18                      # only the short arc died
    hist.undo(), hist.undo(), hist.undo()
    reunited = {e.curve for e in scene.mesh.edges if e.curve is not None}
    assert len(reunited) == 1                   # undoing reunites the circle


def test_plain_edge_has_no_curve():
    scene = Scene()
    e = scene.mesh.add_edge(QVector3D(0, 0, 0), QVector3D(1, 0, 0))
    assert e.curve is None
    assert scene.mesh.curve_edges(e) == [e]
