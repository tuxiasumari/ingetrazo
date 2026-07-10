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


def test_plain_edge_has_no_curve():
    scene = Scene()
    e = scene.mesh.add_edge(QVector3D(0, 0, 0), QVector3D(1, 0, 0))
    assert e.curve is None
    assert scene.mesh.curve_edges(e) == [e]
