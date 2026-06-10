"""Consistent outward orientation of closed solids (core.orient).

Root-fix groundwork: every face of a closed solid wound so its normal points
out of the enclosed volume, decided per face by parity ray casting (robust to
the non-manifold edges of architecture). Validated on a cube and the irregular
triangle-prism bench from the engine notes; no-op on open/flat meshes.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.mesh import Mesh
from core.orient import is_closed, orient_outward, signed_volume


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(float(x), float(y), float(z))


def _cube(mesh: Mesh) -> None:
    """Add a unit cube with deliberately *inconsistent* face windings — each
    face is added in whatever vertex order, so several normals point inward."""
    mesh.add_face([V(0, 0, 0), V(1, 0, 0), V(1, 1, 0), V(0, 1, 0)])  # bottom
    mesh.add_face([V(0, 0, 1), V(1, 0, 1), V(1, 1, 1), V(0, 1, 1)])  # top
    mesh.add_face([V(0, 0, 0), V(1, 0, 0), V(1, 0, 1), V(0, 0, 1)])  # front y=0
    mesh.add_face([V(0, 1, 0), V(1, 1, 0), V(1, 1, 1), V(0, 1, 1)])  # back y=1
    mesh.add_face([V(0, 0, 0), V(0, 1, 0), V(0, 1, 1), V(0, 0, 1)])  # left x=0
    mesh.add_face([V(1, 0, 0), V(1, 1, 0), V(1, 1, 1), V(1, 0, 1)])  # right x=1


def _prism(mesh: Mesh, h: float = 3.0) -> None:
    """The irregular-triangle prism bench from the engine notes, with arbitrary
    windings on every face (caps + three side quads)."""
    a, b, c = (-0.2, -2.8), (2.7, -7.2), (4.1, -2.7)
    base = [V(*a, 0.0), V(*b, 0.0), V(*c, 0.0)]
    top = [V(*a, h), V(*b, h), V(*c, h)]
    mesh.add_face(base)
    mesh.add_face(top)
    for i in range(3):
        j = (i + 1) % 3
        mesh.add_face([base[i], base[j], top[j], top[i]])


def _all_outward(mesh: Mesh, center: QVector3D) -> bool:
    """Every face normal points away from the solid's interior point."""
    return all(
        QVector3D.dotProduct(f.normal(), f.centroid() - center) > 0
        for f in mesh.faces
    )


# ---- closedness ------------------------------------------------------------

def test_single_face_is_open():
    m = Mesh()
    m.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    assert is_closed(m) is False


def test_two_faces_sharing_one_edge_is_open():
    m = Mesh()
    m.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    m.add_face([V(1, 0), V(2, 0), V(2, 1), V(1, 1)])
    assert is_closed(m) is False


def test_cube_is_closed():
    m = Mesh()
    _cube(m)
    assert is_closed(m) is True


# ---- orientation: cube -----------------------------------------------------

def test_cube_oriented_all_outward():
    m = Mesh()
    _cube(m)
    orient_outward(m)
    assert _all_outward(m, V(0.5, 0.5, 0.5))


def test_cube_signed_volume_positive_and_correct():
    m = Mesh()
    _cube(m)
    orient_outward(m)
    assert abs(signed_volume(m) - 1.0) < 1e-6


def test_cube_orientation_is_idempotent():
    m = Mesh()
    _cube(m)
    orient_outward(m)
    again = orient_outward(m)
    assert again == []  # already consistent → nothing to flip


def test_cube_restores_a_deliberate_flip():
    m = Mesh()
    _cube(m)
    orient_outward(m)            # now consistent outward
    # Deliberately flip one face inward.
    f = m.faces[0]
    outer = [QVector3D(v) for v in f.vertices][::-1]
    m.remove_face(f)
    m.add_face(outer)
    flipped = orient_outward(m)
    assert len(flipped) == 1
    assert _all_outward(m, V(0.5, 0.5, 0.5))


# ---- orientation: irregular prism bench ------------------------------------

def test_prism_oriented_all_outward():
    m = Mesh()
    _prism(m)
    orient_outward(m)
    # Interior point: centroid of the prism's bounding behaviour.
    cx = (-0.2 + 2.7 + 4.1) / 3
    cy = (-2.8 - 7.2 - 2.7) / 3
    center = V(cx, cy, 1.5)
    assert _all_outward(m, center)


def test_prism_signed_volume_positive():
    m = Mesh()
    _prism(m)
    orient_outward(m)
    assert signed_volume(m) > 0.0


# ---- interior partitions are left as is -------------------------------------
# Fuzz-bench find (cube seed=0): an interior face — the slab a Ctrl-push keeps,
# a wall shared by two rooms — is inside the solid on *both* sides, so no
# winding is outward. orient_outward used to flip it on every call (the +normal
# side reads "inside" → flip → same again), so each commit toggled it and the
# pass was not idempotent.

def _two_storey_box(m: Mesh) -> None:
    """A 1×1×2 box with an interior slab at z=1: bottom, top, slab, and the
    four walls split into lower/upper quads (so every slab edge carries three
    faces and the mesh stays closed)."""
    lo = [V(0, 0, 0), V(1, 0, 0), V(1, 1, 0), V(0, 1, 0)]
    mid = [V(0, 0, 1), V(1, 0, 1), V(1, 1, 1), V(0, 1, 1)]
    hi = [V(0, 0, 2), V(1, 0, 2), V(1, 1, 2), V(0, 1, 2)]
    m.add_face(lo)
    m.add_face(mid)   # the interior slab
    m.add_face(hi)
    for i in range(4):
        j = (i + 1) % 4
        m.add_face([lo[i], lo[j], mid[j], mid[i]])
        m.add_face([mid[i], mid[j], hi[j], hi[i]])


def test_interior_partition_is_not_flipped():
    m = Mesh()
    _two_storey_box(m)
    slab = next(f for f in m.faces
                if all(abs(v.z() - 1) < 1e-9 for v in f.vertices))
    before = [QVector3D(v) for v in slab.vertices]
    orient_outward(m)
    assert [(_v.x(), _v.y(), _v.z()) for _v in slab.vertices] == \
        [(_v.x(), _v.y(), _v.z()) for _v in before]


def test_orientation_idempotent_with_interior_partition():
    m = Mesh()
    _two_storey_box(m)
    orient_outward(m)
    assert orient_outward(m) == []
    assert orient_outward(m) == []


# ---- no-op on open / flat geometry -----------------------------------------

def test_open_sheet_is_noop():
    m = Mesh()
    m.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    assert orient_outward(m) == []


def test_flat_plan_is_noop():
    m = Mesh()
    m.add_face([V(0, 0), V(2, 0), V(2, 2), V(0, 2)])
    m.add_face([V(2, 0), V(4, 0), V(4, 2), V(2, 2)])
    assert orient_outward(m) == []
