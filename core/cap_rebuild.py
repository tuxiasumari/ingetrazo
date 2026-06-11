"""Deterministic plane rebuild for push/pull (the root-fix, "path C").

This is what replaced the fragile post-extrude heal chain — the old
``_extrude_commands`` case tree (extend-wall / notch / strip), the
``cap_boundary_loops`` crack patcher, and the winding-tolerant coplanar merge
on solids — with one deterministic recompute per touched plane:

1. Gather the mesh edges lying on the plane.
2. Run the planar arrangement (:mod:`core.arrangement`) to get every minimal
   bounded region.
3. Classify each region by **which side of the plane holds material**,
   volumetrically: parity ray-casting (the same primitive as
   :func:`core.orient.orient_outward`) from a sample point just off the
   region's interior on each side. Reading the volume keeps the answer
   independent of the order planes are rebuilt in — the overlapping coplanar
   faces a naive extrude leaves mid-cleanup cancel in crossing-parity pairs.
   Material on exactly **one** side → a boundary face (wound toward the empty
   side); on neither → a phantom outside the solid; on both → solid interior
   (e.g. the mouth ring where a pushed-out pane meets its wall — facing it
   would create an internal partition and swallow a window hole).
4. Union each side's regions (dropping the edges interior to the union) into
   the final face loops — outer boundary plus holes.

The headline win: a push that touched a plane just rebuilds that plane's faces
from its edges, instead of patching geometry with a growing tree of special
cases.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Optional

from PySide6.QtGui import QVector3D

from core.arrangement import (
    _TOL,
    _interior_point,
    _point_in_polygon,
    _signed_area,
    planar_arrangement,
    plane_basis,
)
from core.mesh import _key as _vkey
from core.orient import _face_triangles, ray_parity_outside

# A point this close to the plane (along its normal) counts as on it.
_ON_PLANE = 1e-4


def _key2(p) -> tuple[int, int]:
    return (round(p[0] / _TOL), round(p[1] / _TOL))


def _on_plane(pos: QVector3D, origin: QVector3D, normal: QVector3D) -> bool:
    return abs(QVector3D.dotProduct(pos - origin, normal)) < _ON_PLANE


# Normal offset of the material sample points: clear of the welding tolerance
# (1e-4) yet far smaller than any feature the engine can represent.
_SAMPLE_OFF = 1e-3


def _region_test_point(outer_xy, holes_xy):
    """A point inside ``outer_xy`` but outside every hole — so the classifier
    samples the region's *material*, not a void. The plain centroid of an annular
    region (a cap with a skylight) falls in the hole, which would misread it as
    outside the solid; this nudges in from an outer edge instead when needed."""
    p = _interior_point(outer_xy)
    if not any(_point_in_polygon(p, h) for h in holes_xy):
        return p
    n = len(outer_xy)
    for i in range(n):
        ax, ay = outer_xy[i]
        bx, by = outer_xy[(i + 1) % n]
        mx, my = (ax + bx) / 2, (ay + by) / 2
        dx, dy = bx - ax, by - ay
        ln = math.hypot(dx, dy)
        if ln < _TOL:
            continue
        # Interior of a CCW loop is to the left of a→b: nudge along (-dy, dx).
        q = (mx - dy / ln * 1e-3, my + dx / ln * 1e-3)
        if _point_in_polygon(q, outer_xy) and not any(
            _point_in_polygon(q, h) for h in holes_xy
        ):
            return q
    return p


def _on_seg2(p, a, b, tol: float = _TOL / 2) -> bool:
    """Whether 2D point ``p`` lies on segment ``a``–``b``. Tolerance is tied
    to the arrangement's (its split points and float32-projected mesh
    coordinates can deviate well past 1e-6 on slanted planes)."""
    abx, aby = b[0] - a[0], b[1] - a[1]
    apx, apy = p[0] - a[0], p[1] - a[1]
    if abs(abx * apy - aby * apx) > tol * max(1.0, abs(abx) + abs(aby)):
        return False
    dot = apx * abx + apy * aby
    return -tol <= dot <= abx * abx + aby * aby + tol


def _union_outline(solid_regions_xy, keep_segs=None) -> list:
    """Union a set of 2D regions (each ``(outer, [holes])`` wound as the
    arrangement winds them — outer CCW, holes CW) into ``[(outer, [holes])]``,
    dropping every edge interior to the union (it appears in two solid regions,
    once in each direction, so the directions cancel).

    ``keep_segs`` (2D segments) are **creases**: edges a perpendicular face
    stands on. Union edges *lying on* one never cancel, so the union keeps a
    face boundary there — two roof slabs over a dividing wall stay two faces
    with a visible ridge, SketchUp-style. The test is geometric (midpoint on
    segment): the arrangement splits edges at crossings, so an endpoint-pair
    match would lose the crease on the split-off pieces."""
    dir_count: dict = defaultdict(int)
    coords: dict = {}
    for outer, holes in solid_regions_xy:
        for loop in (outer, *holes):
            n = len(loop)
            for i in range(n):
                a, b = loop[i], loop[(i + 1) % n]
                ka, kb = _key2(a), _key2(b)
                coords[ka] = a
                coords[kb] = b
                dir_count[(ka, kb)] += 1

    def _is_crease(ka, kb) -> bool:
        if not keep_segs:
            return False
        pa, pb = coords[ka], coords[kb]
        mid = ((pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0)
        return any(_on_seg2(mid, a, b) and _on_seg2(pa, a, b)
                   and _on_seg2(pb, a, b) for a, b in keep_segs)

    # Net direction per undirected edge: interior edges cancel (a→b and b→a),
    # boundary edges survive in the direction that keeps the solid on the left.
    # Crease edges skip the cancellation — both directed copies survive, so the
    # trace closes one loop on each side of the crease.
    survivors: dict = defaultdict(int)
    for (ka, kb), c in dir_count.items():
        if _is_crease(ka, kb):
            survivors[(ka, kb)] += c
            continue
        net = c - dir_count.get((kb, ka), 0)
        if net > 0:
            survivors[(ka, kb)] += net

    # Trace the surviving directed edges into closed loops, choosing at each
    # junction the next edge *clockwise* from the arrival direction — the same
    # DCEL rule as ``core.arrangement._trace_faces``. An arbitrary choice can
    # thread straight through a crease junction and pinch the two faces it
    # should separate into one self-touching outline (the Ctrl-stack belt bug).
    nbrs: dict = defaultdict(set)
    for (ka, kb), c in survivors.items():
        if c > 0:
            nbrs[ka].add(kb)
            nbrs[kb].add(ka)
    order: dict = {}
    for k, ns in nbrs.items():
        kx, ky = coords[k]
        order[k] = sorted(ns, key=lambda m2: math.atan2(coords[m2][1] - ky,
                                                        coords[m2][0] - kx))

    loops: list = []
    for start in sorted(survivors):
        while survivors[start] > 0:
            u, v = start
            survivors[start] -= 1
            loop = []
            cu, cv = u, v
            ok = True
            while True:
                loop.append(cu)
                ring = order[cv]
                idx = ring.index(cu)
                m = len(ring)
                w = None
                for s in range(1, m + 1):
                    cand = ring[(idx - s) % m]
                    if (survivors.get((cv, cand), 0) > 0
                            or (cv, cand) == start):
                        w = cand
                        break
                if w is None or len(loop) > 100000:
                    ok = False
                    break
                cu, cv = cv, w
                if (cu, cv) == start:
                    break
                survivors[(cu, cv)] -= 1
            if ok and len(loop) >= 3:
                loops.append([coords[k] for k in loop])

    # Classify: CCW loops are outer faces, CW loops are holes; nest each hole in
    # the smallest outer that contains it.
    outers = [lp for lp in loops if _signed_area(lp) > _TOL]
    holes = [lp for lp in loops if _signed_area(lp) < -_TOL]
    outers.sort(key=lambda lp: _signed_area(lp))  # smallest first
    result = [(o, []) for o in outers]
    for h in holes:
        hp = _interior_point(h)
        for outer, hl in result:
            if _point_in_polygon(hp, outer):
                hl.append(h)
                break
    return result


def rebuild_plane(mesh, origin: QVector3D, normal: QVector3D,
                  fresh=(), keep_mode: bool = False,
                  removing: bool = True) -> Optional[list]:
    """Recompute the solid *boundary* faces of ``mesh`` on the plane
    ``(origin, normal)``.

    A region of the plane carries a face exactly when material sits on **one**
    side of it: none → it is outside the solid (a phantom); both → it is
    interior. A both-sides region is dropped when fresh (the mouth ring where
    a pushed-out pane meets its wall — facing it would swallow a window hole),
    **kept as a partition** when an existing interior face covers it (the slab
    a Ctrl-push keeps, a wall two rooms share — deliberate structure), and
    **declared boundary by the push** when one of this push's ``fresh`` faces
    covers it: parity can't see a void still walled in by an untrimmed
    neighbouring plane, but the naive build's deterministic winding already
    says which side emptied (the fresh normal points to the empty side) — that
    is what keeps the per-plane rebuild order-free next to partitions.

    Returns ``[(outer_loop, [hole_loops], is_partition), …]`` with every loop a
    list of 3D ``QVector3D`` on the plane, boundary loops *wound outward*
    (toward the empty side) — or ``None`` when the plane carries too little to
    face (fewer than three usable edges). Pure: it reads the mesh, it does not
    mutate it.
    """
    normal = normal.normalized()
    u, v = plane_basis(normal)

    def to2d(p):
        d = p - origin
        return (QVector3D.dotProduct(d, u), QVector3D.dotProduct(d, v))

    def to3d(xy):
        return origin + u * xy[0] + v * xy[1]

    segs = [
        (e.a, e.b)
        for e in mesh.edges
        if _on_plane(e.a, origin, normal) and _on_plane(e.b, origin, normal)
    ]
    if len(segs) < 3:
        return None

    regions_3d = planar_arrangement(segs, origin, normal)
    if not regions_3d:
        return None

    # Material presence per side is volumetric: parity ray-casting from a point
    # just off the region's interior, away from the plane (so the ray never
    # looks back through a crack being capped). It reads the whole mesh — the
    # one source that is consistent mid-cleanup, because the overlapping
    # coplanar faces a naive extrude leaves cancel in crossing-parity pairs.
    # This is what makes the per-plane rebuild independent of plane order.
    # Two exclusions keep the counts honest:
    # - interior partitions are not boundary: a ray crossing one would flip
    #   parity without leaving the solid;
    # - in **keep mode** (Ctrl: the push stacks/divides, it removes nothing),
    #   a fresh face shadowed by a counted old face on its own plane (an
    #   inward Ctrl-stack's tube quad lying on the boundary it overlaps)
    #   double-counts a boundary that *persists* — exclude it. Shadowed by an
    #   interior partition, the quad is itself a partition-to-be (material on
    #   both sides; keep removes nothing) — exclude it too. A consumed-base
    #   push is the opposite: its sweep empties the overlapped region, and the
    #   pair must keep cancelling (two crossings = no boundary) — the original
    #   "cancel in pairs" doctrine — while a quad over an interior partner is
    #   the only crossing left there and must count.
    fresh_set = {f for f in fresh if f in mesh.faces}
    shadowed: set = set()
    for f in (fresh_set if keep_mode else ()):
        if f.interior:
            continue
        fn = f.normal().normalized()
        fc = f.centroid()
        fu, fv = plane_basis(fn)

        def fto2d(p, fc=fc, fu=fu, fv=fv):
            d = p - fc
            return (QVector3D.dotProduct(d, fu), QVector3D.dotProduct(d, fv))

        for g in mesh.faces:
            if g is f or g in fresh_set:
                continue
            if abs(QVector3D.dotProduct(g.normal().normalized(), fn)) < 0.999:
                continue
            if abs(QVector3D.dotProduct(g.centroid() - fc, fn)) > _ON_PLANE:
                continue
            if _point_in_polygon((0.0, 0.0), [fto2d(p) for p in g.vertices]) \
                    and not any(_point_in_polygon((0.0, 0.0),
                                                  [fto2d(p) for p in h])
                                for h in g.holes):
                shadowed.add(f)
                break
    tris = [t for f, t in _face_triangles(mesh).items()
            if not f.interior and f not in shadowed]
    rng = random.Random(54321)

    def _proj_polys(faces):
        return [
            (([to2d(p) for p in f.vertices],
              [[to2d(p) for p in h] for h in f.holes]),
             QVector3D.dotProduct(f.normal().normalized(), normal) > 0)
            for f in faces if _coplanar_on(f, origin, normal)
        ]

    def _poly_covers(poly, pt_xy) -> bool:
        outer_xy, holes_xy = poly
        return _point_in_polygon(pt_xy, outer_xy) and not any(
            _point_in_polygon(pt_xy, h) for h in holes_xy)

    # A fresh face *marked interior* (the cap of an inward Ctrl-stack) is a
    # deliberate division, not a boundary declaration — it counts as existing
    # structure instead. Declarations only exist when the push *removes*
    # material (an inward carve): they testify that a still-walled-in region
    # emptied. A Ctrl push removes nothing, and an outward push only adds —
    # its quad landing on an existing wall means the spot became a legitimate
    # partition (a raised room touching its neighbour), not a boundary.
    fresh_cover_polys = _proj_polys(f for f in fresh_set if not f.interior)
    fresh_polys = ([] if keep_mode or not removing else fresh_cover_polys)
    old_polys = _proj_polys(f for f in mesh.faces
                            if f not in fresh_set or f.interior)

    # Group boundary regions by which side holds the material — each group is
    # unioned separately because its faces wind the other way (outward points
    # toward the empty side). Partition and sheet regions form extra groups.
    #
    # When material reads the *same* on both sides, parity is blind there (a
    # void still walled in by an untrimmed neighbouring plane, or coincident
    # face pairs cancelling its crossings) and coverage decides:
    # - covered by this push's ``fresh`` faces in a *single* winding → the op
    #   declares the region boundary; the fresh normal points to the emptied
    #   side. Two opposite fresh windings = a collapsed coincident pair (a
    #   flush-landed fin) — debris, drop.
    # - otherwise covered by a pre-existing face → the user's structure
    #   survives: an interior partition (Ctrl-slab, shared wall) when material
    #   reads both sides, a free sheet (a plan's flat floor) when neither.
    # - covered by nothing → phantom; no face.
    solid_by_side: dict = {True: [], False: []}  # material on +side / on -side
    partitions: list = []
    sheets: list = []
    for outer, holes in regions_3d:
        outer_xy = [to2d(p) for p in outer]
        holes_xy = [[to2d(p) for p in h] for h in holes]
        ip_xy = _region_test_point(outer_xy, holes_xy)
        ip = to3d(ip_xy)
        mat_plus = ray_parity_outside(
            ip + normal * _SAMPLE_OFF, normal, tris, rng) is False
        mat_minus = ray_parity_outside(
            ip - normal * _SAMPLE_OFF, -normal, tris, rng) is False
        if mat_plus != mat_minus:
            solid_by_side[mat_plus].append((outer_xy, holes_xy))
            continue
        decl = {p for poly, p in fresh_polys if _poly_covers(poly, ip_xy)}
        if mat_plus:  # material on both sides
            if len(decl) == 1:
                # One fresh winding covers the spot: the push declares which
                # side emptied (parity can be blind to a void still walled in
                # by an untrimmed neighbouring plane).
                solid_by_side[not decl.pop()].append((outer_xy, holes_xy))
            elif not decl and (
                any(_poly_covers(poly, ip_xy) for poly, _ in old_polys)
                or (keep_mode and any(_poly_covers(poly, ip_xy)
                                      for poly, _ in fresh_cover_polys))
            ):
                # Existing structure — or, in keep mode, the stack's own fresh
                # quad cutting through solid interior: a Ctrl-stack grown into
                # the body lays brand-new divisions where no face existed.
                partitions.append((outer_xy, holes_xy))
        else:         # material on neither side
            if not decl and any(
                    _poly_covers(poly, ip_xy) for poly, _ in old_polys):
                sheets.append((outer_xy, holes_xy))
            # fresh-covered: op debris (a flush-landed cap or collapsed fin)

    # Creases: plane edges a non-coplanar face stands on. The union must keep a
    # boundary there (a roof stays split over its dividing wall). Stored as 2D
    # segments — the union tests lie-on geometrically, since the arrangement
    # may have split these edges at crossings.
    keep_segs: list = []
    for e in mesh.edges:
        if not (_on_plane(e.a, origin, normal) and _on_plane(e.b, origin, normal)):
            continue
        if any(abs(QVector3D.dotProduct(f.normal().normalized(), normal)) < 0.999
               for f in e.faces):
            keep_segs.append((to2d(e.a), to2d(e.b)))

    result = []
    for mat_plus, regions in solid_by_side.items():
        if not regions:
            continue
        for outer, holes in _union_outline(regions, keep_segs):
            # CCW in (u, v) gives a +normal face; flip when the material is on
            # the +side so the face points outward (toward the empty side).
            if mat_plus:
                outer = list(reversed(outer))
                holes = [list(reversed(h)) for h in holes]
            result.append(
                ([to3d(p) for p in outer],
                 [[to3d(p) for p in h] for h in holes], False)
            )
    if partitions:
        # Interior partitions have no outward side; keep the arrangement's
        # winding (orientation leaves them as-is anyway).
        for outer, holes in _union_outline(partitions, keep_segs):
            result.append(
                ([to3d(p) for p in outer],
                 [[to3d(p) for p in h] for h in holes], True)
            )
    if sheets:
        # Free sheets (flat drawing attached to the solid) — not boundary, not
        # partition; they survive the rebuild as plain faces.
        for outer, holes in _union_outline(sheets, keep_segs):
            result.append(
                ([to3d(p) for p in outer],
                 [[to3d(p) for p in h] for h in holes], False)
            )
    return result


def _coplanar_on(face, origin, normal) -> bool:
    return (
        abs(QVector3D.dotProduct(face.normal().normalized(), normal)) > 0.999
        and _on_plane(face.centroid(), origin, normal)
    )


def _canon_loop(loop) -> tuple:
    """Canonical form of a vertex-position cycle: rotated so the smallest key
    leads. Winding is preserved (an orientation flip is a real change)."""
    keys = [(round(p.x() / 1e-4), round(p.y() / 1e-4), round(p.z() / 1e-4))
            for p in loop]
    i = min(range(len(keys)), key=lambda j: keys[j])
    return tuple(keys[i:] + keys[:i])


def _canon_faces(faces_as_loops) -> frozenset:
    """Order-free fingerprint of a set of faces given as ``(outer, holes,
    is_partition)``."""
    return frozenset(
        (_canon_loop(outer), frozenset(_canon_loop(h) for h in holes), interior)
        for outer, holes, interior in faces_as_loops
    )


def apply_rebuild(mesh, origin: QVector3D, normal: QVector3D,
                  fresh=(), keep_mode: bool = False,
                  removing: bool = True) -> bool:
    """Rebuild one plane of ``mesh`` in place: replace its coplanar faces with the
    deterministic solid faces from :func:`rebuild_plane`, and prune the edges left
    interior to the plane (a dissolved seam) that now border nothing. Returns
    whether anything changed — **False when the plane already matches** the
    rebuilt result, so callers can iterate rebuilds to a fixpoint and know it
    terminates. No-op when the rebuild can't trace the plane (``None``); an
    *empty* rebuild is a real answer — every region is phantom or interior
    (a zero-thickness fin left by a flush collapse) — and removes the plane's
    faces. ``fresh`` (this push's new faces) lets the rebuild resolve regions
    a partition used to cover (see :func:`rebuild_plane`).

    The caller snapshots for undo (the push wraps the whole mutation), so this
    keeps no inverse of its own."""
    normal = normal.normalized()
    rebuilt = rebuild_plane(mesh, origin, normal, fresh, keep_mode, removing)
    if rebuilt is None:
        return False
    old = [f for f in mesh.faces if _coplanar_on(f, origin, normal)]
    if _canon_faces(rebuilt) == _canon_faces(
        [(list(f.vertices), [list(h) for h in f.holes], f.interior)
         for f in old]
    ):
        return False  # plane already in its rebuilt form — stable
    for f in old:
        mesh.remove_face(f)
    for outer, holes, interior in rebuilt:
        mesh.add_face(outer, holes or None).interior = interior
    # Drop edges that lie fully on the plane and ended up facing nothing — the
    # interior seams the union dissolved. Perimeter edges still border a wall, so
    # they keep a face and survive. Vertices those edges leave behind with no
    # incidence at all go too (they would otherwise haunt snapping).
    for e in list(mesh.edges):
        if (not e.faces
                and _on_plane(e.a, origin, normal)
                and _on_plane(e.b, origin, normal)):
            mesh.remove_edge(e)
    # Only vertices no face loop references either — deregistering a vertex a
    # loop still holds would make later welds mint a duplicate object at the
    # same position, silently severing that face's adjacency.
    referenced = {
        lv for f in mesh.faces for lp in (f.loop, *f.hole_loops) for lv in lp
    }
    for v in list(mesh.vertices):
        if not v.edges and v not in referenced:
            mesh.vertices.remove(v)
            if mesh._registry.get(_vkey(v.position)) is v:
                del mesh._registry[_vkey(v.position)]
    return True


def plane_key(point: QVector3D, normal: QVector3D):
    """Canonical ``(key, origin, normal)`` for the plane through ``point`` with
    ``normal``: the normal's largest-magnitude component is made positive, so a
    face and its flip map to one plane while the two sides of a slab stay
    distinct (their offsets ``d`` differ)."""
    n = normal.normalized()
    comps = (n.x(), n.y(), n.z())
    if comps[max(range(3), key=lambda i: abs(comps[i]))] < 0:
        n = -n
    d = QVector3D.dotProduct(n, point)
    return (round(n.x(), 3), round(n.y(), 3), round(n.z(), 3), round(d, 3)), point, n


def crack_planes(mesh) -> list:
    """Representative ``(origin, normal)`` for each distinct plane that carries a
    crack — a boundary edge bordering a single face. These are the planes a
    nested push can leave unclosed, the ones :func:`apply_rebuild` should
    recompute (the deterministic replacement for ``cap_boundary_loops``)."""
    seen: dict = {}
    for e in mesh.edges:
        if len(e.faces) != 1:
            continue
        f = e.faces[0]
        key, origin, n = plane_key(f.centroid(), f.normal())
        if key not in seen:
            seen[key] = (origin, n)
    return list(seen.values())


def seam_planes(mesh, new_faces) -> list:
    """Representative ``(origin, normal)`` for each plane where one of this
    operation's ``new_faces`` ended up coplanar-edge-adjacent to another face —
    the seams and overlaps a naive extrude leaves behind: a strip stacked on an
    existing wall, a phantom quad over a wall about to be notched, a fresh cap
    flush with an old roof, or two fresh quads split at a T-junction. These are
    exactly the planes :func:`apply_rebuild` must recompute so the deterministic
    union dissolves them (the replacement for the winding-tolerant coplanar
    merge of ``run_stitch`` phase 3)."""
    seen: dict = {}
    for f in new_faces:
        if f not in mesh.faces:
            continue
        nf = f.normal().normalized()
        for lp in (f.loop, *f.hole_loops):
            n = len(lp)
            for i in range(n):
                e = mesh.find_edge(lp[i], lp[(i + 1) % n])
                if e is None:
                    continue
                for g in e.faces:
                    if g is f:
                        continue
                    if abs(QVector3D.dotProduct(
                            nf, g.normal().normalized())) > 0.999:
                        key, origin, cn = plane_key(f.centroid(), nf)
                        if key not in seen:
                            seen[key] = (origin, cn)
    return list(seen.values())
