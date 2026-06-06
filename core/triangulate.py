"""Polygon triangulation, including polygons with holes.

The renderer and the face picker both need triangles, and a face that has
been divided by inner loops (a "donut", or a wall with a window *and* a door)
cannot be fan-triangulated. This module turns an outer loop plus zero or more
hole loops into a triangle list, working in the face's own plane.

The holed / concave path is a faithful port of the **earcut** ear-clipping
algorithm (Mapbox, ISC license), which is robust to the cases a hand-rolled
bridge is not: several holes, holes at the same height, axis-aligned edges,
and coincident points. Convex hole-free faces keep a trivial O(n) fan.

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


def _cross3(o: Point2, a: Point2, b: Point2) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


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
        cr = _cross3(poly[i], poly[(i + 1) % n], poly[(i + 2) % n])
        if cr > _EPS:
            if sign < 0:
                return False
            sign = 1
        elif cr < -_EPS:
            if sign > 0:
                return False
            sign = -1
    return True


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


# ---- earcut (Mapbox earcut port, no z-order hashing) -----------------------

class _Node:
    __slots__ = ("i", "x", "y", "prev", "next", "steiner")

    def __init__(self, i: int, x: float, y: float) -> None:
        self.i = i
        self.x = x
        self.y = y
        self.prev: Optional[_Node] = None
        self.next: Optional[_Node] = None
        self.steiner = False


def _insert_node(i: int, x: float, y: float, last: Optional[_Node]) -> _Node:
    node = _Node(i, x, y)
    if last is None:
        node.prev = node
        node.next = node
    else:
        node.next = last.next
        node.prev = last
        last.next.prev = node
        last.next = node
    return node


def _remove_node(node: _Node) -> None:
    node.next.prev = node.prev
    node.prev.next = node.next


def _signed_area_idx(points: list[Point2], idxs: list[int]) -> float:
    s = 0.0
    n = len(idxs)
    for i in range(n):
        a = points[idxs[i]]
        b = points[idxs[(i + 1) % n]]
        s += (b[0] - a[0]) * (a[1] + b[1])
    return s


def _build_ring(points: list[Point2], idxs: list[int], want_ccw: bool) -> Optional[_Node]:
    # _signed_area_idx > 0 means clockwise here (shoelace with (x2-x1)(y1+y2)).
    clockwise = _signed_area_idx(points, idxs) > 0
    seq = idxs if (clockwise != want_ccw) else list(reversed(idxs))
    last: Optional[_Node] = None
    for i in seq:
        last = _insert_node(i, points[i][0], points[i][1], last)
    if last is not None and _equals(last, last.next):
        _remove_node(last)
        last = last.next
    return last


def _area(p: _Node, q: _Node, r: _Node) -> float:
    return (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y)


def _equals(p1: _Node, p2: _Node) -> bool:
    return p1.x == p2.x and p1.y == p2.y


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _filter_points(start: Optional[_Node], end: Optional[_Node] = None) -> Optional[_Node]:
    if start is None:
        return start
    if end is None:
        end = start
    p = start
    while True:
        again = False
        if not p.steiner and (_equals(p, p.next) or _area(p.prev, p, p.next) == 0):
            _remove_node(p)
            p = end = p.prev
            if p is p.next:
                break
            again = True
        else:
            p = p.next
        if not again and p is end:
            break
    return end


def _point_in_triangle(ax, ay, bx, by, cx, cy, px, py) -> bool:
    return (
        (cx - px) * (ay - py) - (ax - px) * (cy - py) >= 0
        and (ax - px) * (by - py) - (bx - px) * (ay - py) >= 0
        and (bx - px) * (cy - py) - (cx - px) * (by - py) >= 0
    )


def _is_ear(ear: _Node) -> bool:
    a, b, c = ear.prev, ear, ear.next
    if _area(a, b, c) >= 0:
        return False  # reflex, not an ear
    ax, bx, cx = a.x, b.x, c.x
    ay, by, cy = a.y, b.y, c.y
    x0 = min(ax, bx, cx)
    y0 = min(ay, by, cy)
    x1 = max(ax, bx, cx)
    y1 = max(ay, by, cy)
    p = c.next
    while p is not a:
        if (
            x0 <= p.x <= x1
            and y0 <= p.y <= y1
            and _point_in_triangle(ax, ay, bx, by, cx, cy, p.x, p.y)
            and _area(p.prev, p, p.next) >= 0
        ):
            return False
        p = p.next
    return True


def _earcut_linked(ear: Optional[_Node], triangles: list, pass_: int = 0) -> None:
    if ear is None:
        return
    stop = ear
    while ear.prev is not ear.next:
        prev = ear.prev
        nxt = ear.next
        if _is_ear(ear):
            triangles.append((prev.i, ear.i, nxt.i))
            _remove_node(ear)
            ear = nxt.next
            stop = nxt.next
            continue
        ear = nxt
        if ear is stop:
            # No ear found — recover from degeneracies.
            if pass_ == 0:
                _earcut_linked(_filter_points(ear), triangles, 1)
            elif pass_ == 1:
                ear = _cure_local_intersections(_filter_points(ear), triangles)
                _earcut_linked(ear, triangles, 2)
            elif pass_ == 2:
                _split_earcut(ear, triangles)
            return


def _cure_local_intersections(start: _Node, triangles: list) -> _Node:
    p = start
    while True:
        a = p.prev
        b = p.next.next
        if (
            not _equals(a, b)
            and _intersects(a, p, p.next, b)
            and _locally_inside(a, b)
            and _locally_inside(b, a)
        ):
            triangles.append((a.i, p.i, b.i))
            _remove_node(p)
            _remove_node(p.next)
            p = start = b
        p = p.next
        if p is start:
            break
    return _filter_points(p)


def _split_earcut(start: _Node, triangles: list) -> None:
    a = start
    while True:
        b = a.next.next
        while b is not a.prev:
            if a.i != b.i and _is_valid_diagonal(a, b):
                c = _split_polygon(a, b)
                a = _filter_points(a, a.next)
                c = _filter_points(c, c.next)
                _earcut_linked(a, triangles, 0)
                _earcut_linked(c, triangles, 0)
                return
            b = b.next
        a = a.next
        if a is start:
            break


def _eliminate_holes(points, hole_indices, outer_node):
    queue = []
    total = len(points)
    for k in range(len(hole_indices)):
        start = hole_indices[k]
        end = hole_indices[k + 1] if k + 1 < len(hole_indices) else total
        idxs = list(range(start, end))
        h = _build_ring(points, idxs, want_ccw=False)
        if h is h.next:
            h.steiner = True
        queue.append(_get_leftmost(h))
    queue.sort(key=lambda n: (n.x, n.y))
    for hole in queue:
        outer_node = _eliminate_hole(hole, outer_node)
    return outer_node


def _eliminate_hole(hole, outer_node):
    bridge = _find_hole_bridge(hole, outer_node)
    if bridge is None:
        return outer_node
    bridge_reverse = _split_polygon(bridge, hole)
    _filter_points(bridge_reverse, bridge_reverse.next)
    return _filter_points(bridge, bridge.next)


def _get_leftmost(start: _Node) -> _Node:
    p = start
    leftmost = start
    while True:
        if p.x < leftmost.x or (p.x == leftmost.x and p.y < leftmost.y):
            leftmost = p
        p = p.next
        if p is start:
            break
    return leftmost


def _find_hole_bridge(hole: _Node, outer_node: _Node) -> Optional[_Node]:
    p = outer_node
    hx, hy = hole.x, hole.y
    qx = -float("inf")
    m: Optional[_Node] = None
    # Find the edge whose intersection with a +x ray from the hole is closest.
    while True:
        if hy <= p.y and hy >= p.next.y and p.next.y != p.y:
            x = p.x + (hy - p.y) * (p.next.x - p.x) / (p.next.y - p.y)
            if hx >= x > qx:
                qx = x
                m = p if p.x < p.next.x else p.next
                if x == hx:
                    return m
        p = p.next
        if p is outer_node:
            break
    if m is None:
        return None
    # Look for a reflex vertex inside the candidate triangle, closer in angle.
    stop = m
    mx, my = m.x, m.y
    tan_min = float("inf")
    p = m
    while True:
        if (
            hx >= p.x >= mx
            and hx != p.x
            and _point_in_triangle(
                hx if hy < my else qx, hy,
                mx, my,
                qx if hy < my else hx, hy,
                p.x, p.y,
            )
        ):
            tan = abs(hy - p.y) / (hx - p.x)
            if _locally_inside(p, hole) and (
                tan < tan_min
                or (tan == tan_min and (p.x > m.x or (p.x == m.x and _sector_contains(m, p))))
            ):
                m = p
                tan_min = tan
        p = p.next
        if p is stop:
            break
    return m


def _sector_contains(m: _Node, p: _Node) -> bool:
    return _area(m.prev, m, p.prev) < 0 and _area(p.next, m, m.next) < 0


def _locally_inside(a: _Node, b: _Node) -> bool:
    if _area(a.prev, a, a.next) < 0:
        return _area(a, b, a.next) >= 0 and _area(a, a.prev, b) >= 0
    return _area(a, b, a.prev) < 0 or _area(a, a.next, b) < 0


def _middle_inside(a: _Node, b: _Node) -> bool:
    p = a
    inside = False
    px = (a.x + b.x) / 2
    py = (a.y + b.y) / 2
    while True:
        if ((p.y > py) != (p.next.y > py)) and p.next.y != p.y and (
            px < (p.next.x - p.x) * (py - p.y) / (p.next.y - p.y) + p.x
        ):
            inside = not inside
        p = p.next
        if p is a:
            break
    return inside


def _is_valid_diagonal(a: _Node, b: _Node) -> bool:
    return (
        a.next.i != b.i
        and a.prev.i != b.i
        and not _intersects_polygon(a, b)
        and (
            _locally_inside(a, b)
            and _locally_inside(b, a)
            and _middle_inside(a, b)
            and (_area(a.prev, a, b.prev) != 0 or _area(a, b.prev, b) != 0)
            or _equals(a, b)
            and _area(a.prev, a, a.next) > 0
            and _area(b.prev, b, b.next) > 0
        )
    )


def _on_segment(p: _Node, q: _Node, r: _Node) -> bool:
    return (
        max(p.x, r.x) >= q.x >= min(p.x, r.x)
        and max(p.y, r.y) >= q.y >= min(p.y, r.y)
    )


def _intersects(p1: _Node, q1: _Node, p2: _Node, q2: _Node) -> bool:
    o1 = _sign(_area(p1, q1, p2))
    o2 = _sign(_area(p1, q1, q2))
    o3 = _sign(_area(p2, q2, p1))
    o4 = _sign(_area(p2, q2, q1))
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(p1, p2, q1):
        return True
    if o2 == 0 and _on_segment(p1, q2, q1):
        return True
    if o3 == 0 and _on_segment(p2, p1, q2):
        return True
    if o4 == 0 and _on_segment(p2, q1, q2):
        return True
    return False


def _intersects_polygon(a: _Node, b: _Node) -> bool:
    p = a
    while True:
        if (
            p.i != a.i
            and p.next.i != a.i
            and p.i != b.i
            and p.next.i != b.i
            and _intersects(p, p.next, a, b)
        ):
            return True
        p = p.next
        if p is a:
            break
    return False


def _split_polygon(a: _Node, b: _Node) -> _Node:
    a2 = _Node(a.i, a.x, a.y)
    b2 = _Node(b.i, b.x, b.y)
    an = a.next
    bp = b.prev
    a.next = b
    b.prev = a
    a2.next = an
    an.prev = a2
    b2.next = a2
    a2.prev = b2
    bp.next = b2
    b2.prev = bp
    return b2


def earcut(points: list[Point2], hole_indices: Optional[list[int]] = None) -> list[tuple[int, int, int]]:
    """Triangulate the polygon described by ``points`` + ``hole_indices``.

    ``points`` is the outer ring followed by each hole's vertices; each entry
    of ``hole_indices`` is the start offset of a hole. Returns triangles as
    triples of indices into ``points``.
    """
    hole_indices = hole_indices or []
    has_holes = len(hole_indices) > 0
    outer_len = hole_indices[0] if has_holes else len(points)
    outer_node = _build_ring(points, list(range(outer_len)), want_ccw=True)
    triangles: list = []
    if outer_node is None or outer_node.next is outer_node.prev:
        return triangles
    if has_holes:
        outer_node = _eliminate_holes(points, hole_indices, outer_node)
    _earcut_linked(outer_node, triangles)
    return triangles


# ---- public entry point ----------------------------------------------------

def triangulate(
    outer: list[QVector3D],
    holes: Optional[list[list[QVector3D]]] = None,
    normal: Optional[QVector3D] = None,
) -> list[Tri3]:
    """Triangulate ``outer`` (a planar loop) minus ``holes``.

    Returns a list of 3D triangles. A convex hole-free loop fans in O(n);
    anything concave or holed goes through earcut.
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

    if not holes and is_convex([proj(p) for p in outer]):
        return [(outer[0], outer[i], outer[i + 1]) for i in range(1, len(outer) - 1)]

    master3: list[QVector3D] = list(outer)
    pts2: list[Point2] = [proj(p) for p in outer]
    hole_indices: list[int] = []
    for h in holes:
        hole_indices.append(len(master3))
        master3.extend(h)
        pts2.extend(proj(p) for p in h)

    tris = earcut(pts2, hole_indices)
    return [(master3[a], master3[b], master3[c]) for a, b, c in tris]
