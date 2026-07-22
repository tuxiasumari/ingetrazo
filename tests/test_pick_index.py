# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The vectorised pick index (viewport picks batched over NumPy arrays).

Every mouse-move pick used to walk the mesh in Python re-running earcut per
face — ~1–2 s per move against an imported 17k-triangle building. These pin
the batched replacements to the same answers headless (no GL needed)."""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.history import History, MakeGroupCommand
from core.scene import Scene
from views.viewport import Viewport


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


class _VP:
    pick_threshold_px = 10.0

    def __init__(self, scene):
        self.scene = scene

    def width(self):
        return 100

    def height(self):
        return 100


def _bind(vp):
    vp.DEFAULT_FACE_COLOR = Viewport.DEFAULT_FACE_COLOR
    vp._LIGHT = Viewport._LIGHT
    vp.active_tool = None
    vp._mesh_fingerprint = Viewport._mesh_fingerprint  # staticmethod
    vp._translation_probe = Viewport._translation_probe
    vp._samples_match = Viewport._samples_match
    for name in ("_pick_index", "_ray_hits", "_hover_face_t", "pick_face",
                 "pick_face_any", "pick_edge", "pick_vertex", "_project_px",
                 "_np_mvp", "_group_chunk", "_append_textured_face",
                 "_shaded_color", "_shade_factor", "_group_fp", "_gedge_screen",
                 "_nearby_group_edges", "_snap_scene",
                 "_billboard_snap_edges", "_billboard_quad",
                 "_instance_chunk", "_shift_instance_entry"):
        setattr(vp, name, getattr(Viewport, name).__get__(vp))
    return vp


def _ray_down(x=0.0, y=0.0):
    return (QVector3D(x, y, 10.0), QVector3D(0.0, 0.0, -1.0))


def test_pick_face_prefers_smallest_coplanar():
    scene = Scene()
    big = scene.mesh.add_face([V(-5, -5), V(5, -5), V(5, 5), V(-5, 5)])
    small = scene.mesh.add_face([V(-1, -1), V(1, -1), V(1, 1), V(-1, 1)])
    assert big is not small
    vp = _bind(_VP(scene))
    vp._pixel_to_ray = lambda sx, sy: _ray_down(0, 0)
    hit = vp.pick_face(0, 0)
    # Both faces overlap at the origin at the same depth: the smallest wins.
    assert hit is small or (hit.area() < 5)


def test_pick_face_any_reaches_group_faces():
    scene = Scene()
    hist = History(scene)
    f = scene.mesh.add_face([V(-2, -2), V(2, -2), V(2, 2), V(-2, 2)])
    hist.execute(MakeGroupCommand([f], []))
    vp = _bind(_VP(scene))
    vp._pixel_to_ray = lambda sx, sy: _ray_down(0, 0)
    face, grp = vp.pick_face_any(0, 0)
    assert grp is scene.groups[0]
    assert face in scene.groups[0].mesh.faces
    # pick_face (loose only) sees nothing.
    assert vp.pick_face(0, 0) is None


def test_pick_index_refreshes_on_scene_change():
    scene = Scene()
    vp = _bind(_VP(scene))
    vp._pixel_to_ray = lambda sx, sy: _ray_down(0, 0)
    assert vp.pick_face(0, 0) is None
    scene.mesh.add_face([V(-2, -2), V(2, -2), V(2, 2), V(-2, 2)])
    scene.version += 1
    assert vp.pick_face(0, 0) is not None


def test_group_chunk_fingerprint_invalidation():
    # The cached group chunk must refresh when the group's content changes —
    # paint (attrs) and move (positions) both produce a new fingerprint;
    # an untouched group keeps the same one (that reuse is what makes
    # drawing beside a 17k-face import instant).
    scene = Scene()
    hist = History(scene)
    f = scene.mesh.add_face([V(-2, -2), V(2, -2), V(2, 2), V(-2, 2)])
    hist.execute(MakeGroupCommand([f], []))
    g = scene.groups[0]
    fp0 = Viewport._mesh_fingerprint(g.mesh)
    assert Viewport._mesh_fingerprint(g.mesh) == fp0     # stable when untouched
    gf = g.mesh.faces[0]
    gf.attrs["color"] = [0.5, 0.2, 0.2]
    fp_paint = Viewport._mesh_fingerprint(g.mesh)
    assert fp_paint != fp0                               # paint invalidates
    for v in g.mesh.vertices:
        v.position += QVector3D(1.0, 0.0, 0.0)
    assert Viewport._mesh_fingerprint(g.mesh) != fp_paint  # move invalidates


def test_snap_reaches_group_geometry():
    """Dimensioning/drawing over an imported reference model must snap to the
    group's corners and edges: pick_vertex sees group endpoints, and the snap
    scene carries the group edges near the cursor as pseudo-edges."""
    from core.camera import OrbitCamera

    scene = Scene()
    hist = History(scene)
    f = scene.mesh.add_face([V(-2, -2), V(2, -2), V(2, 2), V(-2, 2)])
    hist.execute(MakeGroupCommand([f], []))
    vp = _bind(_VP(scene))
    vp.camera = OrbitCamera()
    vp.camera.set_view("top")
    vp.camera.fit_to(V(-2, -2, 0), V(2, 2, 0))
    vp.snap_threshold_px = 9.0
    vp._is_occluded = lambda world: False

    corner = V(2, 2, 0)
    px, py, ok = vp._project_px(__import__("numpy").array([[2.0, 2.0, 0.0]]))
    assert ok[0]
    v = vp.pick_vertex(float(px[0]), float(py[0]))
    assert v is not None and (v - corner).length() < 1e-4

    pseudo = vp._nearby_group_edges(float(px[0]), float(py[0]))
    assert pseudo, "group edges near the cursor must feed the snap engine"
    snap_scene = vp._snap_scene(float(px[0]), float(py[0]))
    assert len(list(snap_scene.edges)) >= len(pseudo)
    # far from the model: no pseudo-edges, plain scene comes back
    far = vp._nearby_group_edges(-10_000.0, -10_000.0)
    assert far == []


def test_instance_groups_pick_through_transformed_chunks():
    """Two component instances share ONE prototype mesh; picking hits each
    at its transformed world spot, and updating one instance's transform
    moves only that copy."""
    from PySide6.QtGui import QMatrix4x4

    scene = Scene()
    proto = __import__("core.mesh", fromlist=["Mesh"]).Mesh()
    proto.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    from core.group import Group
    g1, g2 = Group(proto, name="i1"), Group(proto, name="i2")
    m1 = QMatrix4x4()
    m2 = QMatrix4x4(); m2.translate(10, 0, 0)
    g1.xform, g2.xform = m1, m2
    scene.groups.extend([g1, g2])
    scene.version += 1

    vp = _bind(_VP(scene))
    vp._pixel_to_ray = lambda sx, sy: _ray_down(sx, sy)
    f, grp = vp.pick_face_any(0.5, 0.5)
    assert grp is g1 and f in proto.faces
    f, grp = vp.pick_face_any(10.5, 0.5)
    assert grp is g2
    assert vp.pick_face_any(5.0, 0.5) == (None, None)   # empty gap between

    # move instance 2: pick follows the new transform, sibling untouched
    m2b = QMatrix4x4(); m2b.translate(20, 0, 0)
    g2.xform = m2b
    scene.version += 1
    assert vp.pick_face_any(10.5, 0.5) == (None, None)
    f, grp = vp.pick_face_any(20.5, 0.5)
    assert grp is g2
    f, grp = vp.pick_face_any(0.5, 0.5)
    assert grp is g1
