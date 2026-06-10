"""Consistent outward orientation for closed solids in the non-manifold mesh.

The root-fix invariant: every face of a closed solid is wound so its normal
points *outward* from the enclosed volume. The solid push/pull pipeline relies
on it at both ends — it orients **on entry** (hand-built or loaded meshes can
arrive with mixed winding) so the naive extrude's deterministic quad winding
and the per-plane rebuild's material-side classification (:mod:`core.
cap_rebuild`) can trust face normals, and **on exit** so every committed solid
upholds the invariant for the next edit.

The mesh is **shared-vertex, non-manifold** (an interior wall is shared by two
rooms; an edge can border three faces), so winding does *not* propagate cleanly
through half-edges — there is no single "the other face across this edge". So
orientation is decided **per face, independently**, by parity ray casting: a
point just off a face along its normal is *outside* the solid iff a ray from it
crosses the rest of the mesh an even number of times. That is robust to
non-manifold edges and needs no seed face.

Only meaningful for a **closed** component (no boundary edges). Flat drawings and
open sheets have no inside/outside, so the entry points no-op on them.
"""
from __future__ import annotations

import math
import random
from typing import Optional

from PySide6.QtGui import QVector3D

# Ray/triangle hit tolerance and the small offset used to lift the sample point
# off the face it sits on.
_EPS = 1e-7
_T_MIN = 1e-6
# Number of jittered rays voted over to step around degenerate hits (a ray that
# grazes a shared edge or vertex would be counted 0 or 2 times). The face normal
# is one of them; the rest are small angular perturbations.
_RAYS = 7
_JITTER = 0.08


Triangle = tuple[QVector3D, QVector3D, QVector3D]


# ---- Ray / triangle --------------------------------------------------------

def _ray_triangle(origin: QVector3D, direction: QVector3D, tri: Triangle) -> Optional[float]:
    """Möller–Trumbore. Returns the ray parameter ``t > 0`` of the intersection
    with ``tri``, or ``None`` (miss / parallel / behind)."""
    a, b, c = tri
    e1 = b - a
    e2 = c - a
    p = QVector3D.crossProduct(direction, e2)
    det = QVector3D.dotProduct(e1, p)
    if abs(det) < _EPS:
        return None  # parallel
    inv = 1.0 / det
    tvec = origin - a
    u = QVector3D.dotProduct(tvec, p) * inv
    if u < -_EPS or u > 1.0 + _EPS:
        return None
    q = QVector3D.crossProduct(tvec, e1)
    v = QVector3D.dotProduct(direction, q) * inv
    if v < -_EPS or u + v > 1.0 + _EPS:
        return None
    t = QVector3D.dotProduct(e2, q) * inv
    return t if t > _T_MIN else None


def _basis(n: QVector3D) -> tuple[QVector3D, QVector3D]:
    """Two unit vectors perpendicular to ``n`` (for jittering a ray direction)."""
    ref = QVector3D(1.0, 0.0, 0.0)
    if abs(QVector3D.dotProduct(ref, n)) > 0.9:
        ref = QVector3D(0.0, 1.0, 0.0)
    u = QVector3D.crossProduct(n, ref).normalized()
    v = QVector3D.crossProduct(n, u).normalized()
    return u, v


def _face_triangles(mesh) -> dict:
    """Triangulate every face once. Returns ``{face: [Triangle, …]}`` in world
    space (holes included via the face's own earcut triangulation)."""
    tris: dict = {}
    for f in mesh.faces:
        tris[f] = f.triangulate()
    return tris


def ray_parity_outside(origin: QVector3D, direction: QVector3D,
                       triangle_lists, rng: random.Random) -> Optional[bool]:
    """Whether ``origin`` sits *outside* the volume bounded by the triangles, by
    crossing parity along jittered rays around ``direction`` (majority vote —
    a single ray can graze a shared edge and miscount). Even crossings ahead →
    outside (infinity is outside; each crossing flips). ``None`` on a tie.

    This is the engine's one volumetric primitive: orientation asks it with a
    face's centroid and normal, and the per-plane rebuild asks it from sample
    points just off a plane ("is there material on this side?") — the dirty
    overlapping coplanar faces a naive extrude leaves cancel in pairs, so the
    answer is right even mid-cleanup, in any plane order."""
    n = direction.normalized()
    u, v = _basis(n)
    outside_votes = 0
    inside_votes = 0
    for r in range(_RAYS):
        if r == 0:
            d = n
        else:
            d = (n
                 + u * rng.uniform(-_JITTER, _JITTER)
                 + v * rng.uniform(-_JITTER, _JITTER)).normalized()
        crossings = 0
        for tlist in triangle_lists:
            for tri in tlist:
                if _ray_triangle(origin, d, tri) is not None:
                    crossings += 1
        if crossings % 2 == 0:
            outside_votes += 1
        else:
            inside_votes += 1
    if outside_votes == inside_votes:
        return None
    return outside_votes > inside_votes


def _face_side_state(face, tris_by_face: dict, rng: random.Random) -> Optional[str]:
    """Classify ``face`` against the volume bounded by ``tris_by_face`` (the
    current *boundary* faces): ``"outward"`` / ``"inward"`` for a boundary face
    (by which side is empty), ``"interior"`` for a partition with material on
    both sides (the slab a Ctrl-push keeps, a wall two rooms share), ``None``
    when undecidable (degenerate face, tied votes)."""
    n = face.normal()
    if n.length() < 1e-9:
        return None
    others = [t for f, t in tris_by_face.items() if f is not face]
    # The region just past the centroid along the normal being outside is
    # exactly the normal pointing outward.
    ahead = ray_parity_outside(face.centroid(), n, others, rng)
    if ahead is not False:
        return "outward" if ahead else None
    # The +normal side is inside. An inward-wound boundary face has its *other*
    # side outside; an interior partition is inside both ways.
    behind = ray_parity_outside(face.centroid(), -n, others, rng)
    if behind is True:
        return "inward"
    return "interior" if behind is False else None


# ---- Public API ------------------------------------------------------------

def _all_coplanar(mesh) -> bool:
    """Cheap flatness test: every face on one plane (a 2D drawing)."""
    first = mesh.faces[0]
    n = first.normal()
    o = first.centroid()
    for f in mesh.faces:
        fn = f.normal()
        if abs(QVector3D.dotProduct(fn, n)) < 0.999:
            return False
        if abs(QVector3D.dotProduct(f.centroid() - o, n)) > 1e-4:
            return False
    return True


def is_closed(mesh) -> bool:
    """Whether the mesh has no boundary: every edge borders at least two faces.
    A necessary condition for inside/outside (and thus orientation) to mean
    anything. Open sheets and flat drawings return ``False``."""
    if not mesh.faces:
        return False
    return all(len(e.faces) >= 2 for e in mesh.edges)


def signed_volume(mesh) -> float:
    """Signed volume of the mesh as currently wound (sum of tetrahedra from the
    origin over each triangulated face). For a consistently-oriented closed
    solid its magnitude is the real volume and its sign reports the global
    winding (positive == outward by the right-hand rule). Mixed winding gives a
    meaningless value — use it only to confirm consistency. Interior partitions
    (marked by :func:`orient_outward`) are not boundary and are skipped — their
    arbitrary winding would bias the sum."""
    total = 0.0
    for f in mesh.faces:
        if f.interior:
            continue
        for a, b, c in f.triangulate():
            total += QVector3D.dotProduct(a, QVector3D.crossProduct(b, c)) / 6.0
    return total


def orient_outward(mesh, seed: int = 12345) -> list:
    """Flip the faces of a closed solid so every boundary normal points
    outward, and mark interior partitions (``face.interior``). Returns the
    faces that were flipped.

    No-op (returns ``[]``) on a mesh that isn't closed — an open sheet or flat
    drawing has no outside, so there is nothing to orient.

    Parity ray-casting only means anything against the *boundary* faces, but an
    interior partition (the slab a Ctrl-push keeps, a wall two rooms share) is
    not boundary — a ray crossing it would flip parity without leaving the
    solid. So the partitions are peeled off iteratively: classify every face
    against the current boundary set, drop the ones that read interior,
    reclassify — to a fixpoint (typically 1 extra round; capped). Boundary
    faces judged inward are then flipped; partitions keep their winding (no
    winding of theirs is outward)."""
    if not mesh.faces:
        return []
    if _all_coplanar(mesh):
        # A flat drawing has no volume at all: no partitions, nothing to flip.
        for f in mesh.faces:
            f.interior = False
        return []
    all_tris = _face_triangles(mesh)
    boundary = dict(all_tris)
    for _ in range(4):
        rng = random.Random(seed)
        interior_now = {
            f for f in mesh.faces
            if _face_side_state(f, boundary, rng) == "interior"
        }
        if interior_now == {f for f in mesh.faces if f not in boundary}:
            break
        boundary = {f: t for f, t in all_tris.items() if f not in interior_now}
    for f in mesh.faces:
        f.interior = f not in boundary

    # Flipping needs a real outside, which only a closed mesh has. The marks
    # above are still computed for open meshes (a plan mixing raised rooms
    # with flat floors is open as a whole, yet its interior walls are real
    # partitions the volumetric queries must skip).
    if not is_closed(mesh):
        return []
    rng = random.Random(seed)
    to_flip = [
        f for f in boundary
        if _face_side_state(f, boundary, rng) == "inward"
    ]
    for f in to_flip:
        # Flip in place: reversing the loops reverses the winding (so the normal
        # flips) while keeping the *same* Face object and its shared edges/
        # incidence. Identity is preserved — a freshly extruded box keeps its
        # base face object, and snapshot undo stays valid.
        f.loop.reverse()
        for h in f.hole_loops:
            h.reverse()
    return to_flip
