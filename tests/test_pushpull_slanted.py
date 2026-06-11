"""A.2 — the slanted-faces bench (next-session item).

The directed push/pull benches were all axis-aligned plus the irregular
triangle prism (vertical walls). This locks in the slanted cases the casita
actually uses: thickening a gable roof plane, extending the gable pentagon,
a recess carved into a slanted roof, and a wedge (slanted cap) pushed on
every face in every order — each commit must be watertight, outward and
seam-free (the fuzz invariants, applied to directed slanted geometry).
"""
from __future__ import annotations

import itertools

import pytest
from PySide6.QtGui import QVector3D

from core.edits import build_add_edge
from core.history import History, MoveVerticesCommand
from core.orient import is_closed, orient_outward, signed_volume
from core.scene import Scene
from core.triangulate import plane_axes
from tests.test_fuzz_engine import V, _draw_rect, _push, _up


def _gable_house():
    """4×4×3 box with a ridge lifted to z=4: floor, two side walls, two
    pentagon gables, two slanted roof planes — the casita's hito 3."""
    scene = Scene()
    hist = History(scene)
    _draw_rect(scene, hist, [V(0, 0), V(4, 0), V(4, 4), V(0, 4)], [])
    f = scene.mesh.faces[0]
    _push(scene, hist, f, _up(f, 3.0))
    hist.execute(build_add_edge(scene, V(2, 0, 3), V(2, 4, 3)))
    hist.execute(MoveVerticesCommand([V(2, 0, 3), V(2, 4, 3)],
                                     QVector3D(0, 0, 1)))
    return scene, hist


def _wedge():
    """4×4 box whose cap is slanted (one top edge dropped to z=1): a ramp."""
    scene = Scene()
    hist = History(scene)
    _draw_rect(scene, hist, [V(0, 0), V(4, 0), V(4, 4), V(0, 4)], [])
    f = scene.mesh.faces[0]
    _push(scene, hist, f, _up(f, 3.0))
    hist.execute(MoveVerticesCommand([V(0, 0, 3), V(0, 4, 3)],
                                     QVector3D(0, 0, -2)))
    return scene, hist


def _assert_clean(scene, ctx: str = "") -> None:
    m = scene.mesh
    assert is_closed(m), f"{ctx}: solid left open"
    assert signed_volume(m) > 0.0, f"{ctx}: bad signed volume"
    assert orient_outward(m) == [], f"{ctx}: inconsistent winding"
    for e in m.edges:
        if len(e.faces) != 2:
            continue
        d = QVector3D.dotProduct(e.faces[0].normal().normalized(),
                                 e.faces[1].normal().normalized())
        assert abs(d) <= 0.999, f"{ctx}: unmerged coplanar seam"


def _roof(scene):
    return next(f for f in scene.mesh.faces
                if f.normal().z() > 0.5 and f.normal().x() > 0.1)


def _gable_face(scene):
    return next(f for f in scene.mesh.faces
                if len(f.vertices) == 5 and f.normal().y() < -0.5)


# ---- the gable house itself --------------------------------------------------

def test_gable_house_builds_clean():
    scene, _hist = _gable_house()
    _assert_clean(scene, "gable build")
    assert len(scene.mesh.faces) == 7
    assert abs(signed_volume(scene.mesh) - 56.0) < 1e-6  # 4·4·3 + ridge 8


# ---- thicken a slanted roof plane ---------------------------------------------

def test_thicken_roof_plane():
    scene, hist = _gable_house()
    roof = _roof(scene)
    area = roof.area()
    _push(scene, hist, roof, 0.3)
    _assert_clean(scene, "thicken roof")
    assert abs(signed_volume(scene.mesh) - (56.0 + 0.3 * area)) < 1e-3


def test_roof_pushed_back_flush_restores_house():
    scene, hist = _gable_house()
    _push(scene, hist, _roof(scene), 0.3)
    raised = next(f for f in scene.mesh.faces
                  if f.normal().z() > 0.5 and f.normal().x() > 0.1)
    _push(scene, hist, raised, -0.3)
    _assert_clean(scene, "roof flush back")
    assert abs(signed_volume(scene.mesh) - 56.0) < 1e-3
    assert len(scene.mesh.faces) == 7


# ---- extend the gable pentagon -------------------------------------------------

def test_extend_gable_pentagon():
    scene, hist = _gable_house()
    gab = _gable_face(scene)
    _push(scene, hist, gab, 1.0)
    _assert_clean(scene, "extend gable")
    # Pentagon area = 4·3 + ½·4·1 = 14 → house grows by exactly 14·1.
    assert abs(signed_volume(scene.mesh) - 70.0) < 1e-6
    assert len(scene.mesh.faces) == 7        # clean prism extension


# ---- recess carved into a slanted roof -----------------------------------------

def _roof_rect(scene):
    roof = _roof(scene)
    n = roof.normal().normalized()
    u, w = plane_axes(n)
    o = roof.vertices[0]
    return n, [o + u * 0.5 + w * 0.5, o + u * 1.5 + w * 0.5,
               o + u * 1.5 + w * 1.5, o + u * 0.5 + w * 1.5]


def test_recess_on_slanted_roof():
    scene, hist = _gable_house()
    n, rect = _roof_rect(scene)
    _draw_rect(scene, hist, rect, [])
    sub = next(f for f in scene.mesh.faces
               if len(f.vertices) == 4 and not f.holes and f.area() < 1.1
               and abs(QVector3D.dotProduct(f.normal().normalized(), n)) > 0.999)
    _push(scene, hist, sub, -0.2)
    _assert_clean(scene, "recess on roof")
    assert abs(signed_volume(scene.mesh) - (56.0 - 0.2)) < 1e-3


def test_recess_attrs_survive_on_slanted_roof():
    # A.3 meets A.2: the roof's material survives being subdivided + carved.
    scene, hist = _gable_house()
    roof = _roof(scene)
    roof.attrs = {"color": "teja"}
    n, rect = _roof_rect(scene)
    _draw_rect(scene, hist, rect, [])
    host = next(f for f in scene.mesh.faces
                if f.holes and abs(QVector3D.dotProduct(
                    f.normal().normalized(), n)) > 0.999)
    assert host.attrs == {"color": "teja"}


# ---- wedge: slanted cap, every push order ---------------------------------------

def _wedge_faces(scene):
    """Stable order: the slanted cap, then the three full walls (x=4, y=0,
    y=4 — the x=0 side is the wedge's thin edge, height 1)."""
    cap = next(f for f in scene.mesh.faces
               if abs(f.normal().z()) > 0.3 and f.centroid().z() > 1.0)
    walls = sorted(
        (f for f in scene.mesh.faces
         if abs(f.normal().z()) < 0.1 and f.centroid().z() > 0.0
         and f is not cap),
        key=lambda f: (round(f.centroid().x(), 3), round(f.centroid().y(), 3)),
    )
    return [cap] + walls


def test_wedge_builds_clean():
    scene, _hist = _wedge()
    _assert_clean(scene, "wedge build")
    assert abs(signed_volume(scene.mesh) - 32.0) < 1e-6  # 4·4·(3+1)/2


_DISTS = [0.7, 1.1, 0.5]


@pytest.mark.parametrize("perm", list(itertools.permutations(range(3))))
def test_wedge_push_orders(perm):
    scene, hist = _wedge()
    for k, fi in enumerate(perm):
        faces = _wedge_faces(scene)
        _push(scene, hist, faces[fi], _DISTS[k])
        _assert_clean(scene, f"wedge perm={perm} step={k}")


@pytest.mark.parametrize("fi", [0, 1, 2, 3])
def test_wedge_single_face_in_and_out(fi):
    scene, hist = _wedge()
    face = _wedge_faces(scene)[fi]
    _push(scene, hist, face, 0.6)
    _assert_clean(scene, f"wedge out fi={fi}")
    scene, hist = _wedge()
    face = _wedge_faces(scene)[fi]
    _push(scene, hist, face, -0.4)
    _assert_clean(scene, f"wedge in fi={fi}")


# ---- undo/redo round-trips slanted commits --------------------------------------

def test_slanted_undo_redo_roundtrip():
    scene, hist = _gable_house()
    _push(scene, hist, _roof(scene), 0.3)
    after = len(scene.mesh.faces)
    assert hist.undo() is True
    assert len(scene.mesh.faces) == 7
    assert hist.redo() is True
    assert len(scene.mesh.faces) == after
    _assert_clean(scene, "undo/redo roundtrip")
