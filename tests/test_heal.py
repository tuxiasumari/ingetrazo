"""Heal: drop a redundant 'mother' face left overlapping its own subdivisions."""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.mesh import Mesh
from core.topology import heal_overlapping_faces


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(float(x), float(y), float(z))


def test_heal_removes_mother_covered_by_inset_subdivisions():
    # The planta bug: a big slab left on top of the inset rooms drawn inside it.
    m = Mesh()
    m.add_face([V(0, 0), V(8, 0), V(8, 8), V(0, 8)])               # big slab (64)
    m.add_face([V(0.2, 0.2), V(3.8, 0.2), V(3.8, 7.8), V(0.2, 7.8)])  # room (~27)
    m.add_face([V(4.2, 0.2), V(7.8, 0.2), V(7.8, 7.8), V(4.2, 7.8)])  # room (~27)
    removed = heal_overlapping_faces(m)
    assert len(removed) == 1                # only the big slab
    assert len(m.faces) == 2                # the two rooms stay
    assert all(f.area() < 30 for f in m.faces)


def test_heal_keeps_a_face_with_a_small_island_inside():
    # A big face with a small square inside it is NOT a redundant mother (4% «
    # 50%). Isolate the mother rule with partial=False (the flat partial pass
    # would treat the island as an overlap and remove it — covered elsewhere).
    m = Mesh()
    m.add_face([V(0, 0), V(10, 0), V(10, 10), V(0, 10)])   # big (100)
    m.add_face([V(4, 4), V(6, 4), V(6, 6), V(4, 6)])       # small island (4%)
    removed = heal_overlapping_faces(m, partial=False)
    assert removed == []
    assert len(m.faces) == 2


def test_heal_no_op_on_clean_subdivision():
    # A cleanly split face (two halves sharing a chord) has no overlap to heal.
    m = Mesh()
    m.add_face([V(0, 0), V(4, 0), V(4, 4), V(0, 4)])
    m.add_face([V(4, 0), V(8, 0), V(8, 4), V(4, 4)])
    assert heal_overlapping_faces(m) == []


def test_heal_dedupes_nested_holes_keeps_the_ring():
    # The planta2 bug: a wall ring whose interior was subdivided accumulated
    # overlapping holes (a hole inside a hole). Heal keeps the outermost hole and
    # the ring itself — it must NOT delete the ring or leave nested holes.
    m = Mesh()
    outer = [V(0, 0), V(10, 0), V(10, 10), V(0, 10)]
    big_hole = [V(2, 2), V(8, 2), V(8, 8), V(2, 8)]          # the real interior
    nested = [V(2, 2), V(8, 2), V(8, 5), V(2, 5)]            # redundant, inside it
    m.add_face(list(outer), [list(big_hole), list(nested)])  # ring, 2 holes
    m.add_face(list(big_hole))                               # fills the interior
    removed = heal_overlapping_faces(m)
    assert removed == []                                     # ring kept
    ring = next(f for f in m.faces if f.holes)
    assert len(ring.holes) == 1                              # nested hole dropped


def test_heal_keeps_a_legit_ring_with_an_inner_face():
    # A face with one hole, filled by an inner face (an offset wall + room), is
    # valid: the inner only fills the hole, so the ring is not "covered".
    m = Mesh()
    m.add_face([V(1, 1), V(3, 1), V(3, 3), V(1, 3)])                 # inner room
    m.add_face([V(0, 0), V(4, 0), V(4, 4), V(0, 4)],
               [[V(1, 1), V(3, 1), V(3, 3), V(1, 3)]])               # ring
    assert heal_overlapping_faces(m) == []
    assert any(f.holes for f in m.faces)                            # ring intact


def test_heal_flips_a_reversed_face():
    # A face auto-faced with reversed winding (normal pointing the wrong way)
    # would push into the model. Heal flips it to match its coplanar neighbours.
    m = Mesh()
    m.add_face([V(0, 0), V(10, 0), V(10, 10), V(0, 10)])     # big, +Z
    # a small strip wound clockwise -> normal -Z (reversed)
    m.add_face([V(2, 2), V(2, 4), V(4, 4), V(4, 2)])
    normals_before = sorted(round(f.normal().z(), 1) for f in m.faces)
    assert normals_before == [-1.0, 1.0]
    heal_overlapping_faces(m)
    assert all(f.normal().z() > 0 for f in m.faces)          # both face +Z now


def _solid_overlap(m):
    from core.topology import _faces_coplanar, _point_in_face_solid
    import itertools
    n = 0
    for a, b in itertools.combinations(m.faces, 2):
        if not _faces_coplanar(a.normal(), b.normal()):
            continue
        if a.area() > b.area() and _point_in_face_solid(a, b.centroid()):
            n += 1
        if b.area() > a.area() and _point_in_face_solid(b, a.centroid()):
            n += 1
    return n


def test_partial_overlap_resolved_by_holing_on_flat_off_in_3d():
    # A sliver in a bigger face's solid (the door piece over the wall). On a flat
    # plan it's resolved by punching a hole in the bigger face — the sliver stays
    # as its own selectable face, no overlap. In 3D the pass is gated off.
    flat = Mesh()
    flat.add_face([V(0, 0), V(10, 0), V(10, 10), V(0, 10)])
    flat.add_face([V(2, 2), V(4, 2), V(4, 4), V(2, 4)])      # the door piece
    assert _solid_overlap(flat) == 1
    heal_overlapping_faces(flat)
    assert len(flat.faces) == 2                              # both faces kept
    assert _solid_overlap(flat) == 0                         # no longer overlapping
    assert any(f.holes for f in flat.faces)                 # big face gained a hole
    assert any(not f.holes and abs(f.area() - 4.0) < 0.1 for f in flat.faces)

    d3 = Mesh()
    d3.add_face([V(0, 0), V(10, 0), V(10, 10), V(0, 10)])
    d3.add_face([V(2, 2), V(4, 2), V(4, 4), V(2, 4)])
    d3.add_face([V(0, 0, 3), V(1, 0, 3), V(1, 1, 3), V(0, 1, 3)])  # 3D
    heal_overlapping_faces(d3)
    assert not any(f.holes for f in d3.faces)               # not flat -> untouched
