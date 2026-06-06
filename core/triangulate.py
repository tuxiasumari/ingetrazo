"""Polygon triangulation, including polygons with holes.

The renderer and the face picker both need triangles, and a face that has
been divided by an inner loop (a "donut") cannot be fan-triangulated. This
module turns an outer loop plus zero or more hole loops into a triangle
list, working in the face's own plane.

Algorithm: project to 2D using the face normal, bridge each hole into the
outer boundary with a zero-width slit (Eberly's mutually-visible-vertex
method), then ear-clip the resulting simple polygon. Robust enough for the
nested convex loops IngeTrazo produces today; it also handles concave outer
loops, which is why ear-clipping (Phase 1, sub-step 5) will reuse it.

All math is via ``QVector3D`` / plain tuples — no numpy.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QVector3D

Point2 = tuple[float, float]
Tri3 = tuple[QVector3D, QVector3D, QVector3D]

_EPS = 1e-9


def plane_axes(normal: QVector3D) -> tuple[QVector3D, QVector3D]:
    """Two orthonormal in-plane axes derived from a plane normal.

    ``u`` is world +X projected onto the plane (or +Y when the normal is
    nearly +X, to avoid the degenerate projection); ``v = normal × u``.
    """
    n = normal.normalized()
    ref = QVector3D(1.0, 0.0, 0.0)
    u = ref - n * QVector3D.dotProduct(ref, n)
    if u.length() < 0.1:
        ref = QVector3D(0.0, 1.0, 0.0)
        u = ref - n * QVector3D.dotProduct(ref, n)
    u = u.normalized()
    v = QVector3D.crossProduct(n, u).normalized()
    return u, v


def is_convex(poly: list[Point2]) -> bool:
    """Whether a simple 2D polygon is convex — every turn goes the same way.

    Collinear vertices (a split point left on a straight edge) are ignored, so
    a rectangle that gained a mid-edge vertex still counts as convex and keeps
    the cheap fan path. Triangles are trivially convex.
    """
    n = len(poly)
    if n < 4:
        return True
    sign = 0
    for i in range(n):
        cr = _cross(poly[i], poly[(i + 1) % n], poly[(i + 2) % n])
        if cr > _EPS:
            if sign < 0:
                return False
            sign = 1
        elif cr < -_EPS:
            if sign > 0:
                return False
            sign = -1
    return True


def _signed_area(poly: list[Point2]) -> float:
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def _cross(o: Point2, a: Point2, b: Point2) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _point_in_tri(p: Point2, a: Point2, b: Point2, c: Point2) -> bool:
    """Inside-or-on test (used by the bridge-visibility search)."""
    d1 = _cross(a, b, p)
    d2 = _cross(b, c, p)
    d3 = _cross(c, a, p)
    has_neg = (d1 < -_EPS) or (d2 < -_EPS) or (d3 < -_EPS)
    has_pos = (d1 > _EPS) or (d2 > _EPS) or (d3 > _EPS)
    return not (has_neg and has_pos)


def _same2(p: Point2, q: Point2) -> bool:
    return abs(p[0] - q[0]) < _EPS and abs(p[1] - q[1]) < _EPS


def _blocks_ear(p: Point2, a: Point2, b: Point2, c: Point2) -> bool:
    """Whether vertex ``p`` prevents triangle ``a,b,c`` from being clipped.

    Inside-or-on the triangle blocks (so a reflex vertex resting on an ear's
    edge — the concave case — is respected), *except* when ``p`` coincides
    with one of the triangle's corners. That exception matters for the
    zero-width bridge slit of a hole, where a corner vertex appears twice
    under different indices and must not block its own ear.
    """
    if _same2(p, a) or _same2(p, b) or _same2(p, c):
        return False
    return _point_in_tri(p, a, b, c)


def _ear_clip(poly: list[Point2]) -> list[tuple[int, int, int]]:
    """Triangulate a simple polygon (CCW) → triangles as index triples into
    ``poly``. O(n²); bails out gracefully on degeneracy rather than looping."""
    n = len(poly)
    if n < 3:
        return []
    idx = list(range(n))
    if _signed_area(poly) < 0.0:
        idx.reverse()

    tris: list[tuple[int, int, int]] = []
    guard = 0
    limit = 2 * len(idx) * len(idx) + 8
    while len(idx) > 3 and guard < limit:
        guard += 1
        m = len(idx)
        ear_found = False
        for i in range(m):
            i0 = idx[(i - 1) % m]
            i1 = idx[i]
            i2 = idx[(i + 1) % m]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if _cross(a, b, c) <= _EPS:  # reflex or collinear → not an ear tip
                continue
            blocked = False
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                if _blocks_ear(poly[j], a, b, c):
                    blocked = True
                    break
            if not blocked:
                tris.append((i0, i1, i2))
                del idx[i]
                ear_found = True
                break
        if not ear_found:
            break
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


def _find_bridge(merged: list[int], pts2: list[Point2], hole: list[int]) -> Optional[int]:
    """Eberly's mutually-visible vertex: returns the position in ``merged``
    of the outer-loop vertex visible from the hole's rightmost vertex.

    ``merged`` holds master indices into ``pts2``; ``hole`` likewise. Returns
    ``None`` if no visible vertex is found (caller skips that hole)."""
    # M = hole vertex with max x.
    m_local = max(range(len(hole)), key=lambda i: pts2[hole[i]][0])
    M = pts2[hole[m_local]]

    best_x = float("inf")
    best_pos: Optional[int] = None  # position in merged of the larger-x endpoint
    inter: Optional[Point2] = None
    k = len(merged)
    for i in range(k):
        a = pts2[merged[i]]
        b = pts2[merged[(i + 1) % k]]
        # Edge must straddle M's y for a +x ray hit.
        if (a[1] > M[1]) == (b[1] > M[1]):
            continue
        if abs(b[1] - a[1]) < _EPS:
            continue
        ix = a[0] + (b[0] - a[0]) * (M[1] - a[1]) / (b[1] - a[1])
        if ix < M[0] - _EPS:
            continue
        if ix < best_x:
            best_x = ix
            inter = (ix, M[1])
            best_pos = i if a[0] > b[0] else (i + 1) % k

    if best_pos is None or inter is None:
        return None

    P = pts2[merged[best_pos]]
    # If the intersection is essentially at P, P is directly visible.
    if abs(P[0] - inter[0]) < _EPS and abs(P[1] - inter[1]) < _EPS:
        return best_pos

    # Otherwise check reflex vertices inside triangle (M, inter, P): the one
    # with the smallest angle to +x from M wins visibility.
    tri = (M, inter, P)
    best_angle = float("inf")
    chosen = best_pos
    for pos in range(k):
        cand = pts2[merged[pos]]
        if cand == P:
            continue
        if not _point_in_tri(cand, *tri):
            continue
        dx = cand[0] - M[0]
        dy = cand[1] - M[1]
        if dx <= _EPS:
            continue
        angle = abs(dy) / dx
        if angle < best_angle:
            best_angle = angle
            chosen = pos
    return chosen


def triangulate(
    outer: list[QVector3D],
    holes: Optional[list[list[QVector3D]]] = None,
    normal: Optional[QVector3D] = None,
) -> list[Tri3]:
    """Triangulate ``outer`` (a planar loop) minus ``holes``.

    Returns a list of 3D triangles. With no holes and a convex-or-simple
    outer loop this is equivalent to ear-clipping the projected polygon.
    """
    if len(outer) < 3:
        return []
    holes = [h for h in (holes or []) if len(h) >= 3]
    if normal is None:
        normal = _newell(outer)

    u, v = plane_axes(normal)
    origin = outer[0]

    def proj(p: QVector3D) -> Point2:
        rel = p - origin
        return (QVector3D.dotProduct(rel, u), QVector3D.dotProduct(rel, v))

    # Fast path: a convex outer loop with no holes fans trivially in O(n).
    # Concave loops fall through to ear-clipping below.
    if not holes and is_convex([proj(p) for p in outer]):
        return [(outer[0], outer[i], outer[i + 1]) for i in range(1, len(outer) - 1)]

    # Master vertex tables (3D + 2D), parallel.
    master3: list[QVector3D] = list(outer)
    pts2: list[Point2] = [proj(p) for p in outer]
    outer_idx = list(range(len(outer)))

    # Outer loop CCW.
    if _signed_area([pts2[i] for i in outer_idx]) < 0.0:
        outer_idx.reverse()

    hole_index_loops: list[list[int]] = []
    for h in holes:
        start = len(master3)
        master3.extend(h)
        pts2.extend(proj(p) for p in h)
        h_idx = list(range(start, start + len(h)))
        # Holes wound CW (opposite the outer loop).
        if _signed_area([pts2[i] for i in h_idx]) > 0.0:
            h_idx.reverse()
        hole_index_loops.append(h_idx)

    merged = list(outer_idx)
    # Bridge holes in order of decreasing rightmost x (Eberly).
    hole_index_loops.sort(
        key=lambda loop: max(pts2[i][0] for i in loop), reverse=True
    )
    for hole in hole_index_loops:
        pos = _find_bridge(merged, pts2, hole)
        if pos is None:
            continue  # un-bridgeable hole; skip it rather than corrupt output
        m_local = max(range(len(hole)), key=lambda i: pts2[hole[i]][0])
        hole_rot = hole[m_local:] + hole[:m_local]  # starts at the bridge vertex
        bridge_vertex = merged[pos]
        insert = hole_rot + [hole_rot[0], bridge_vertex]
        merged = merged[: pos + 1] + insert + merged[pos + 1 :]

    poly2 = [pts2[i] for i in merged]
    tris = _ear_clip(poly2)
    return [
        (master3[merged[t[0]]], master3[merged[t[1]]], master3[merged[t[2]]])
        for t in tris
    ]


def _newell(vertices: list[QVector3D]) -> QVector3D:
    n = QVector3D(0.0, 0.0, 0.0)
    count = len(vertices)
    for i in range(count):
        c = vertices[i]
        nx = vertices[(i + 1) % count]
        n = n + QVector3D(
            (c.y() - nx.y()) * (c.z() + nx.z()),
            (c.z() - nx.z()) * (c.x() + nx.x()),
            (c.x() - nx.x()) * (c.y() + nx.y()),
        )
    if n.length() < 1e-9:
        return QVector3D(0.0, 0.0, 1.0)
    return n.normalized()
