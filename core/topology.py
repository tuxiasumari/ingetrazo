"""Topology helpers — graph queries over the scene's edge network.

Used by tools (today: ``LineTool``) to find polygons that close when a new
edge is added. Modeled after SketchUp's behaviour: as soon as a new edge
completes a planar cycle in the edge graph — using any combination of
existing edges — that cycle becomes a face automatically.

Position equality is tolerant: two vertices within ``_KEY_DECIMALS``
decimal places (≈ 0.1 mm at metric scale) are treated as the same node.
"""
from __future__ import annotations

from collections import deque
from typing import Iterable, Optional

from PySide6.QtGui import QVector3D

from core.geometry import Edge, Face


_KEY_DECIMALS = 4
_PLANAR_TOLERANCE = 1e-3
# Two points closer than this weld together; also the gate for deciding a
# crossing is real (skew lines whose closest approach exceeds it don't touch).
_SPLIT_TOLERANCE = 1e-4


def _key(p: QVector3D) -> tuple[float, float, float]:
    return (round(p.x(), _KEY_DECIMALS),
            round(p.y(), _KEY_DECIMALS),
            round(p.z(), _KEY_DECIMALS))


def same_position(p: QVector3D, q: QVector3D) -> bool:
    """Whether two points coincide within the welding tolerance (≈ 0.1 mm)."""
    return _key(p) == _key(q)


def find_duplicate_edge(
    edges: Iterable[Edge], a: QVector3D, b: QVector3D
) -> Optional[Edge]:
    """Return an existing edge whose endpoints coincide with segment ``a``–``b``.

    Orientation-independent: an edge stored as ``b``–``a`` still matches.
    Coincidence uses the same tolerant position key as the cycle finder, so
    two endpoints within ≈ 0.1 mm weld to the same node. A degenerate
    (zero-length) query never matches. Returns ``None`` if no duplicate
    exists. This is the primitive behind SketchUp-style auto-merge: drawing
    an edge that already exists reuses it instead of stacking a duplicate.
    """
    ka, kb = _key(a), _key(b)
    if ka == kb:
        return None
    target = frozenset((ka, kb))
    for edge in edges:
        ea, eb = _key(edge.a), _key(edge.b)
        if ea == eb:
            continue
        if frozenset((ea, eb)) == target:
            return edge
    return None


def find_smallest_cycle_through(
    edges: Iterable[Edge],
    a: QVector3D,
    b: QVector3D,
    max_len: int = 32,
) -> Optional[list[QVector3D]]:
    """Smallest simple cycle in the edge graph that contains segment ``a-b``.

    The segment is *virtual*: it does not need to exist in ``edges`` yet.
    Returns the cycle as an ordered list of vertices starting at ``a`` and
    walking back to ``b`` through existing edges (so the full polygon loop
    is the returned list with the implicit closing segment back to ``a``).
    Returns ``None`` if no cycle exists or it would exceed ``max_len`` nodes.
    """
    ka, kb = _key(a), _key(b)
    if ka == kb:
        return None

    # adj[u] -> [(v_key, v_pos), ...]
    adj: dict[tuple, list[tuple[tuple, QVector3D]]] = {}
    for edge in edges:
        ea, eb = _key(edge.a), _key(edge.b)
        if ea == eb:
            continue
        # Skip an existing copy of the same edge, otherwise the cycle just
        # finds itself (length-2 loop a→b→a).
        if {ea, eb} == {ka, kb}:
            continue
        adj.setdefault(ea, []).append((eb, edge.b))
        adj.setdefault(eb, []).append((ea, edge.a))

    if ka not in adj or kb not in adj:
        return None

    parent: dict = {kb: None}
    parent_pos: dict = {kb: b}
    q = deque([kb])
    found = False
    while q:
        u = q.popleft()
        if u == ka:
            found = True
            break
        for v_key, v_pos in adj.get(u, ()):
            if v_key not in parent:
                parent[v_key] = u
                parent_pos[v_key] = v_pos
                q.append(v_key)

    if not found:
        return None

    path: list[QVector3D] = []
    cur = ka
    while cur is not None:
        path.append(parent_pos[cur])
        cur = parent[cur]
    # path is [a, ..., b]. Cycle = path + implicit closing a–b.
    if len(path) < 3 or len(path) > max_len:
        return None
    return path


def is_planar(vertices: list[QVector3D], tolerance: float = _PLANAR_TOLERANCE) -> bool:
    """Whether ``vertices`` all lie on a common plane within ``tolerance``."""
    n = len(vertices)
    if n < 3:
        return False
    if n == 3:
        # Any 3 distinct points are coplanar by definition. Reject degenerate
        # (collinear) triangles so we don't try to face them.
        e1 = vertices[1] - vertices[0]
        e2 = vertices[2] - vertices[0]
        return QVector3D.crossProduct(e1, e2).length() > 1e-6

    v0 = vertices[0]
    plane_normal: Optional[QVector3D] = None
    for i in range(1, n - 1):
        for j in range(i + 1, n):
            cross = QVector3D.crossProduct(vertices[i] - v0, vertices[j] - v0)
            if cross.length() > 1e-6:
                plane_normal = cross.normalized()
                break
        if plane_normal is not None:
            break
    if plane_normal is None:
        return False
    for v in vertices:
        if abs(QVector3D.dotProduct(plane_normal, v - v0)) > tolerance:
            return False
    return True


def segment_intersection(
    p1: QVector3D,
    p2: QVector3D,
    p3: QVector3D,
    p4: QVector3D,
    tol: float = _SPLIT_TOLERANCE,
) -> Optional[QVector3D]:
    """Where segment ``p1-p2`` meets segment ``p3-p4`` in 3D, or ``None``.

    Uses the closest-points-between-two-lines solution and accepts the hit
    only when (a) the lines are not parallel, (b) their closest approach is
    within ``tol`` (so genuinely skew segments that merely *look* crossed in
    a 2D projection are rejected), and (c) both parameters land on their
    segment (endpoints included). The returned point is the midpoint of the
    closest approach, so an X-crossing yields one shared vertex for both
    edges. Collinear overlaps return ``None`` — those are a merge problem,
    handled separately, not a crossing.
    """
    d1 = p2 - p1
    d2 = p4 - p3
    len1 = d1.length()
    len2 = d2.length()
    if len1 < tol or len2 < tol:
        return None

    a = QVector3D.dotProduct(d1, d1)
    b = QVector3D.dotProduct(d1, d2)
    c = QVector3D.dotProduct(d2, d2)
    w0 = p1 - p3
    d = QVector3D.dotProduct(d1, w0)
    e = QVector3D.dotProduct(d2, w0)
    denom = a * c - b * b
    if denom < 1e-12:
        return None  # parallel or collinear

    s = (b * e - c * d) / denom
    t = (a * e - b * d) / denom

    # Allow a hair past the endpoints (proportional to length) so a touch
    # exactly at a vertex still registers; same_position decides interior
    # vs endpoint later.
    margin1 = tol / len1
    margin2 = tol / len2
    if not (-margin1 <= s <= 1.0 + margin1):
        return None
    if not (-margin2 <= t <= 1.0 + margin2):
        return None

    point_on_1 = p1 + d1 * s
    point_on_2 = p3 + d2 * t
    if (point_on_1 - point_on_2).length() > tol:
        return None  # skew: lines pass without meeting
    return (point_on_1 + point_on_2) * 0.5


def _order_along(a: QVector3D, b: QVector3D, points: list[QVector3D]) -> list[QVector3D]:
    """Deduplicate ``points`` and order them by their projection along a→b,
    dropping any that coincide with an endpoint."""
    d = b - a
    uniq: list[QVector3D] = []
    for p in points:
        if same_position(p, a) or same_position(p, b):
            continue
        if not any(same_position(p, q) for q in uniq):
            uniq.append(p)
    uniq.sort(key=lambda p: QVector3D.dotProduct(p - a, d))
    return uniq


def plan_edge_split(
    edges: Iterable[Edge], a: QVector3D, b: QVector3D
) -> tuple[list[tuple[QVector3D, QVector3D]], dict[Edge, QVector3D]]:
    """Plan the splits caused by adding segment ``a-b`` to ``edges``.

    Returns a pair:

    - ``new_segments`` — the new edge broken at every interior crossing,
      ordered from ``a`` to ``b`` (just ``[(a, b)]`` when nothing is crossed);
    - ``edge_cuts`` — existing edge → the interior point where the new edge
      crosses it (those edges must be replaced by two sub-edges).

    A crossing at a shared *endpoint* produces no split on that side (the
    weld already shares that vertex). A straight segment meets another at
    most once, so each existing edge maps to a single cut point.
    """
    new_cuts: list[QVector3D] = []
    edge_cuts: dict[Edge, QVector3D] = {}
    for e in edges:
        point = segment_intersection(a, b, e.a, e.b)
        if point is None:
            continue
        if not (same_position(point, a) or same_position(point, b)):
            new_cuts.append(point)
        if not (same_position(point, e.a) or same_position(point, e.b)):
            edge_cuts[e] = point

    ordered = _order_along(a, b, new_cuts)
    chain = [a, *ordered, b]
    new_segments = [(chain[i], chain[i + 1]) for i in range(len(chain) - 1)]
    return new_segments, edge_cuts


def face_exists(faces: Iterable[Face], cycle: list[QVector3D]) -> bool:
    """Whether a face with the same vertex set as ``cycle`` already exists."""
    cycle_keys = frozenset(_key(v) for v in cycle)
    for face in faces:
        if frozenset(_key(v) for v in face.vertices) == cycle_keys:
            return True
    return False


# ---- Containment (face split / hole punching) ------------------------------

def _on_segment_2d(p, a, b, tol: float = 1e-7) -> bool:
    """Whether 2D point ``p`` lies on segment ``a``–``b`` within ``tol``."""
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    if abs(cross) > tol:
        return False
    dot = (p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])
    if dot < -tol:
        return False
    sqlen = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
    return dot <= sqlen + tol


def _strictly_inside_2d(p, poly: list[tuple[float, float]]) -> bool:
    """Ray-cast point-in-polygon, strict: points on the boundary are *not*
    inside (they signal a shared-edge case, which is a chord split, not a
    hole)."""
    n = len(poly)
    j = n - 1
    inside = False
    for i in range(n):
        if _on_segment_2d(p, poly[i], poly[j]):
            return False
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > p[1]) != (yj > p[1]):
            xint = xi + (xj - xi) * (p[1] - yi) / (yj - yi)
            if p[0] < xint:
                inside = not inside
        j = i
    return inside


def loop_inside_face(mother: Face, loop: list[QVector3D]) -> bool:
    """Whether ``loop`` lies entirely, strictly inside coplanar face ``mother``.

    Requires the loop to be coplanar with the mother and every loop vertex to
    fall strictly inside the mother's outer polygon (no vertex on its
    boundary). A loop that shares any boundary point is a chord split, handled
    elsewhere, and returns ``False`` here.
    """
    if len(mother.vertices) < 3 or len(loop) < 3:
        return False
    normal = mother.normal()
    origin = mother.vertices[0]
    # Coplanarity: every loop vertex on the mother's plane.
    for v in loop:
        if abs(QVector3D.dotProduct(normal, v - origin)) > _PLANAR_TOLERANCE:
            return False

    from core.triangulate import plane_axes

    u, w = plane_axes(normal)

    def proj(p):
        rel = p - origin
        return (QVector3D.dotProduct(rel, u), QVector3D.dotProduct(rel, w))

    poly2 = [proj(p) for p in mother.vertices]
    return all(_strictly_inside_2d(proj(v), poly2) for v in loop)


def find_containing_face(
    faces: Iterable[Face], loop: list[QVector3D], exclude: Optional[Face] = None
) -> Optional[Face]:
    """Smallest existing face that strictly contains ``loop`` (or ``None``).

    Smallest by vertex count is a cheap, good-enough proxy for the immediate
    mother when faces are nested. ``exclude`` skips the face being added.
    """
    best: Optional[Face] = None
    for face in faces:
        if face is exclude:
            continue
        if loop_inside_face(face, loop):
            if best is None or len(face.vertices) < len(best.vertices):
                best = face
    return best


def _loop_edges(loop: list[QVector3D]) -> list[frozenset]:
    n = len(loop)
    return [
        frozenset((_key(loop[i]), _key(loop[(i + 1) % n]))) for i in range(n)
    ]


def orphaned_edges_at(
    edges: Iterable[Edge], faces: Iterable[Face], vertices: Iterable[QVector3D]
) -> list[Edge]:
    """Edges incident to any of ``vertices`` that border no face.

    Used after push/pull consumes a face to sweep up the dangling lines left
    where geometry was carved away, without disturbing standalone edges
    elsewhere (only those touching the operation's vertices are considered).
    """
    vkeys = {_key(v) for v in vertices}
    face_edges: set = set()
    for f in faces:
        face_edges.update(_loop_edges(f.vertices))
        for hole in f.holes:
            face_edges.update(_loop_edges(hole))
    out: list[Edge] = []
    for e in edges:
        ek = frozenset((_key(e.a), _key(e.b)))
        if (_key(e.a) in vkeys or _key(e.b) in vkeys) and ek not in face_edges:
            out.append(e)
    return out


def _faces_coplanar(n1: QVector3D, n2: QVector3D) -> bool:
    return abs(QVector3D.dotProduct(n1.normalized(), n2.normalized())) > 0.999


def _face_all_edges(face: Face) -> set:
    edges = set(_loop_edges(face.vertices))
    for hole in face.holes:
        edges.update(_loop_edges(hole))
    return edges


def _point_on_seg_incl(pt: QVector3D, p: QVector3D, q: QVector3D,
                       tol: float = _SPLIT_TOLERANCE) -> bool:
    """Whether ``pt`` lies on segment ``p``–``q``, endpoints included."""
    pq = q - p
    length = pq.length()
    if length < tol:
        return same_position(pt, p)
    t = QVector3D.dotProduct(pt - p, pq) / (length * length)
    if t < -tol or t > 1.0 + tol:
        return False
    return (pt - (p + pq * t)).length() < tol


def _segment_on_face_boundary(a: QVector3D, b: QVector3D, face: Face) -> bool:
    """Whether segment ``a``–``b`` lies on a boundary edge of ``face`` (a
    possibly-shorter sub-segment of one of its edges).

    Checks the outer loop *and* every hole: a face stacked on another sits with
    its base edges on the host's *hole* boundary (the opening the stack punched),
    so a perpendicular neighbour must be found there too — otherwise pushing the
    stack's side wall is misread as a free extrusion."""
    for loop in (face.vertices, *face.holes):
        n = len(loop)
        for i in range(n):
            p = loop[i]
            q = loop[(i + 1) % n]
            if _point_on_seg_incl(a, p, q) and _point_on_seg_incl(b, p, q):
                return True
    return False


def refine_loop_with_points(
    loop: list[QVector3D], points: Iterable[QVector3D], tol: float = _SPLIT_TOLERANCE
) -> list[QVector3D]:
    """Insert any ``points`` that lie on an edge's interior into ``loop``.

    A pushed wall whose edge runs along a *T-junction* — where the host wall ends
    and an earlier overhang's floor begins — has that edge covered piecewise by
    two faces, but no single one, so it reads as ``free``. Splitting the edge at
    the existing vertices sitting on it makes each sub-segment carried by one
    face, so push/pull classifies (and consumes) it correctly. Returns a new loop
    with the collinear points inserted in order; the originals are untouched."""
    out: list[QVector3D] = []
    n = len(loop)
    pts = list(points)
    for i in range(n):
        a = loop[i]
        b = loop[(i + 1) % n]
        out.append(a)
        ab = b - a
        length = ab.length()
        if length < tol:
            continue
        on: list[tuple[float, QVector3D]] = []
        for p in pts:
            if same_position(p, a) or same_position(p, b):
                continue
            t = QVector3D.dotProduct(p - a, ab) / (length * length)
            if t <= tol / length or t >= 1.0 - tol / length:
                continue
            if (p - (a + ab * t)).length() < tol:
                on.append((t, p))
        on.sort(key=lambda it: it[0])
        for _, p in on:
            out.append(QVector3D(p))
    return out


def classify_push_edge(
    face: Face, a: QVector3D, b: QVector3D, faces: Iterable[Face]
) -> tuple[str, Optional[Face]]:
    """How a push/pull side edge ``a``–``b`` of ``face`` attaches to the model.

    - ``("coplanar", g)`` — a face on the same plane shares this edge (its
      boundary or a hole); the push raises an inner wall here.
    - ``("perp", s)`` — a non-coplanar face carries this edge on its boundary
      (the solid's side wall); pushing in notches that wall.
    - ``("free", None)`` — nothing adjacent; a free extrusion edge.

    A coplanar neighbour is matched even when it carries the edge only as a
    *sub-segment* of a wider boundary edge (its own edge was never split at this
    vertex — the colinear-overlap case). Without this, a block flush against a
    wider wall reads its shared edge as ``free`` and the push becomes a stray
    free extrusion that leaves the base as an internal partition.
    """
    fn = face.normal()
    eset = frozenset((_key(a), _key(b)))
    faces = list(faces)
    for g in faces:
        if g is face:
            continue
        if _faces_coplanar(fn, g.normal()) and (
            eset in _face_all_edges(g) or _segment_on_face_boundary(a, b, g)
        ):
            return ("coplanar", g)
    for s in faces:
        if s is face or _faces_coplanar(fn, s.normal()):
            continue
        if _segment_on_face_boundary(a, b, s):
            return ("perp", s)
    return ("free", None)


def face_is_bordered(face: Face, faces: Iterable[Face]) -> bool:
    """Whether every boundary edge of ``face`` is also an edge of some other
    face (its boundary or a hole).

    A bordered face is embedded in a surface or solid — a cube's top, or a
    rectangle drawn inside another face — so push/pull *moves* it and the base
    is consumed (carving a recess, or extending/shortening a solid without
    leaving an internal cap). A free-standing face (free edges) is *extruded*,
    keeping the base as a cap. This is orientation-independent, so it works
    regardless of how the face happens to be wound.
    """
    base_edges = _loop_edges(face.vertices)
    if not base_edges:
        return False
    others: set = set()
    for f in faces:
        if f is face:
            continue
        others.update(_loop_edges(f.vertices))
        for hole in f.holes:
            others.update(_loop_edges(hole))
    return all(e in others for e in base_edges)


# ---- Chord split (a new edge divides an existing face) ---------------------

def _point_on_segment_3d(
    p: QVector3D, a: QVector3D, b: QVector3D, tol: float = _SPLIT_TOLERANCE
) -> bool:
    """Whether ``p`` lies on the *interior* of segment ``a``–``b`` (endpoints
    excluded — those are handled as vertex hits)."""
    ab = b - a
    length = ab.length()
    if length < tol:
        return False
    t = QVector3D.dotProduct(p - a, ab) / (length * length)
    if t < tol or t > 1.0 - tol:
        return False
    return (p - (a + ab * t)).length() < tol


def _locate_on_loop(vertices: list[QVector3D], p: QVector3D):
    """Where ``p`` sits on a face's boundary loop: ``("vertex", i)`` if it is
    vertex ``i``; ``("edge", i)`` if it lies on edge ``i → i+1``; else ``None``."""
    kp = _key(p)
    for i, v in enumerate(vertices):
        if _key(v) == kp:
            return ("vertex", i)
    n = len(vertices)
    for i in range(n):
        if _point_on_segment_3d(p, vertices[i], vertices[(i + 1) % n]):
            return ("edge", i)
    return None


def split_face_by_chord(
    face: Face, a: QVector3D, b: QVector3D
) -> Optional[tuple[list[QVector3D], list[QVector3D]]]:
    """If segment ``a``–``b`` is a chord of ``face`` (both ends on its
    boundary, the segment running through its interior), return the two
    sub-loops it divides the face into; otherwise ``None``.

    Handles ends that are existing vertices *or* points on a boundary edge
    (the latter get inserted into the loop). Faces with holes are skipped —
    chord-splitting a holed face is a harder case left for later. The two
    returned loops inherit the mother's winding, so neither comes out
    inverted.
    """
    if face.holes or len(face.vertices) < 3:
        return None
    la = _locate_on_loop(face.vertices, a)
    lb = _locate_on_loop(face.vertices, b)
    if la is None or lb is None:
        return None

    # Build an augmented loop with any on-edge endpoints inserted in order.
    on_edge: dict[int, list[QVector3D]] = {}
    if la[0] == "edge":
        on_edge.setdefault(la[1], []).append(QVector3D(a))
    if lb[0] == "edge":
        on_edge.setdefault(lb[1], []).append(QVector3D(b))

    aug: list[QVector3D] = []
    for i, v in enumerate(face.vertices):
        aug.append(v)
        if i in on_edge:
            base = v
            for p in sorted(on_edge[i], key=lambda q: (q - base).length()):
                aug.append(p)

    keys = [_key(v) for v in aug]
    ia = keys.index(_key(a))
    ib = keys.index(_key(b))
    if ia > ib:
        ia, ib = ib, ia
    m = len(aug)
    # Adjacent positions mean the "chord" is just a boundary edge.
    if ib - ia <= 1 or (ia == 0 and ib == m - 1):
        return None

    # The chord must run through the interior, not outside a concave face.
    normal = face.normal()
    origin = face.vertices[0]
    from core.triangulate import plane_axes

    u, w = plane_axes(normal)

    def proj(p):
        rel = p - origin
        return (QVector3D.dotProduct(rel, u), QVector3D.dotProduct(rel, w))

    mid = (a + b) * 0.5
    poly2 = [proj(v) for v in face.vertices]
    if not _strictly_inside_2d(proj(mid), poly2):
        return None

    loop_a = aug[ia : ib + 1]
    loop_b = aug[ib:] + aug[: ia + 1]
    if len(loop_a) < 3 or len(loop_b) < 3:
        return None
    return loop_a, loop_b


def find_chord_split(
    faces: Iterable[Face], a: QVector3D, b: QVector3D
) -> Optional[tuple[Face, list[QVector3D], list[QVector3D]]]:
    """First face that segment ``a``–``b`` chord-splits, with its two halves."""
    for face in faces:
        result = split_face_by_chord(face, a, b)
        if result is not None:
            return face, result[0], result[1]
    return None


def _loop_with_point(
    loop: list[QVector3D], ka: tuple, kb: tuple, point: QVector3D
) -> Optional[list[QVector3D]]:
    """Copy of ``loop`` with ``point`` inserted between the first consecutive
    vertex pair whose keys are ``ka``/``kb`` (either order). ``None`` if no such
    boundary edge exists or ``point`` already is one of those vertices."""
    kp = _key(point)
    n = len(loop)
    for i in range(n):
        j = (i + 1) % n
        ki, kj = _key(loop[i]), _key(loop[j])
        if {ki, kj} == {ka, kb}:
            if kp in (ki, kj):
                return None
            new = list(loop)
            new.insert(i + 1, QVector3D(point))
            return new
    return None


def split_edge_in_faces(
    faces: Iterable[Face],
    edge_a: QVector3D,
    edge_b: QVector3D,
    point: QVector3D,
    skip_endpoints: Iterable[QVector3D] = (),
) -> list[tuple[Face, list[QVector3D]]]:
    """Propagate an edge split into the faces that share that edge.

    When a drawn segment cuts an existing edge ``edge_a``–``edge_b`` at
    ``point``, every face carrying that edge on its outer boundary should gain
    ``point`` as a (collinear) vertex — otherwise the face stays detached from
    the split and won't follow when that vertex is later moved. This is what
    lets a gable wall pick up the ridge apex and deform into a pentagon when the
    ridge is raised, instead of leaving an open triangular gap.

    Returns ``[(face, new_vertices), ...]`` for the faces that changed. A face
    carrying *all* of ``skip_endpoints`` on its boundary is left out: that face
    is chord-split by the drawn segment, which already inserts the point, so
    handling it here would double-process it. Holes are left untouched.
    """
    ka, kb = _key(edge_a), _key(edge_b)
    skip = list(skip_endpoints)
    out: list[tuple[Face, list[QVector3D]]] = []
    for f in faces:
        if skip and all(_locate_on_loop(f.vertices, p) is not None for p in skip):
            continue
        new_verts = _loop_with_point(f.vertices, ka, kb, point)
        if new_verts is not None:
            out.append((f, new_verts))
    return out


# ---- Loop subtraction (a face drawn against another's boundary) -------------

def _face_plane_proj(face: Face):
    """Return ``(proj, poly2)`` — a projector to the face's 2D plane and the
    face's boundary projected with it."""
    from core.triangulate import plane_axes

    normal = face.normal()
    origin = face.vertices[0]
    u, w = plane_axes(normal)

    def proj(p):
        rel = p - origin
        return (QVector3D.dotProduct(rel, u), QVector3D.dotProduct(rel, w))

    return proj, [proj(v) for v in face.vertices]


def find_subdividing_chain(
    face: Face, loop: list[QVector3D]
) -> Optional[list[QVector3D]]:
    """If ``loop`` shares a contiguous boundary arc with ``face`` and pushes a
    single run of vertices through its interior, return that interior chain
    ``[P, ...interior..., Q]`` (P, Q on the face boundary). Otherwise ``None``.

    This is the "rectangle drawn in a corner / along an edge" case: the loop
    neither sits strictly inside the face (a hole) nor is a single straight
    chord — it carves a connected sub-region. Loops that poke outside the face,
    and loops touching the boundary in more than one place are out of scope and
    return ``None``.

    Holes on ``face`` are allowed and ignored here (the chain only concerns the
    outer boundary): drawing a door on a wall that already has a window must
    still subdivide. The caller is responsible for re-assigning each hole to the
    region that contains it.
    """
    if len(face.vertices) < 3 or len(loop) < 3:
        return None
    proj, poly2 = _face_plane_proj(face)

    labels: list[str] = []
    for v in loop:
        if _locate_on_loop(face.vertices, v) is not None:
            labels.append("bdry")
        elif _strictly_inside_2d(proj(v), poly2):
            labels.append("in")
        else:
            return None  # loop pokes outside the face → not a clean subdivision
    if "in" not in labels or "bdry" not in labels:
        return None

    n = len(loop)
    start = labels.index("bdry")
    runs: list[list[int]] = []
    cur: list[int] = []
    for k in range(n):
        idx = (start + k) % n
        if labels[idx] == "in":
            cur.append(idx)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    if len(runs) != 1:
        return None

    run = runs[0]
    p = loop[(run[0] - 1) % n]
    q = loop[(run[-1] + 1) % n]
    if same_position(p, q):
        return None
    return [p, *(loop[i] for i in run), q]


def _arc(seq: list, i: int, j: int) -> list:
    """Cyclic slice of ``seq`` from index ``i`` to ``j`` inclusive, forward."""
    if i <= j:
        return seq[i : j + 1]
    return seq[i:] + seq[: j + 1]


def split_face_by_chain(
    face: Face, chain: list[QVector3D]
) -> Optional[tuple[list[QVector3D], list[QVector3D]]]:
    """Split ``face`` along ``chain`` (``[P, ...interior..., Q]``, ends on the
    boundary) into its two sub-loops. On-edge ends are inserted into the
    boundary. Returns ``None`` if the ends can't be located."""
    p, q = chain[0], chain[-1]
    middles = list(chain[1:-1])
    on_edge: dict[int, list[QVector3D]] = {}
    for pt in (p, q):
        loc = _locate_on_loop(face.vertices, pt)
        if loc is None:
            return None
        if loc[0] == "edge":
            on_edge.setdefault(loc[1], []).append(QVector3D(pt))

    aug: list[QVector3D] = []
    for i, v in enumerate(face.vertices):
        aug.append(v)
        if i in on_edge:
            for pp in sorted(on_edge[i], key=lambda r: (r - v).length()):
                aug.append(pp)

    keys = [_key(x) for x in aug]
    ip = keys.index(_key(p))
    iq = keys.index(_key(q))
    region_pq = _arc(aug, ip, iq) + list(reversed(middles))
    region_qp = _arc(aug, iq, ip) + middles
    if len(region_pq) < 3 or len(region_qp) < 3:
        return None
    return region_pq, region_qp


def extend_wall_edge(
    face: Face,
    a: QVector3D,
    b: QVector3D,
    a2: QVector3D,
    b2: QVector3D,
) -> Optional[list[QVector3D]]:
    """Grow (or shrink) ``face`` by moving its boundary edge ``a``–``b`` to
    ``a2``–``b2``, returning the new vertex loop. ``None`` if it doesn't apply.

    This is the push/pull "extend a prism" case: when a cap is pushed further
    along its normal, each adjacent wall just gets taller in its own plane.
    Replacing the wall's shared edge keeps it a single face — otherwise a
    second coplanar strip would be stacked on top, leaving a visible seam at
    the old cap level.

    Applies only when ``a`` and ``b`` are *both* full vertices of ``face`` and
    adjacent (so ``a``–``b`` is a real boundary edge, not a sub-segment of a
    wider wall — that's the notch case, handled elsewhere) and the move keeps
    the outer loop planar (the extrusion stays in the wall's plane). Any holes
    the wall carries (a window / door opening) are untouched and must be
    re-attached by the caller to the extended face."""
    if len(face.vertices) < 3:
        return None
    ka, kb = _key(a), _key(b)
    ia = ib = None
    for i, v in enumerate(face.vertices):
        kv = _key(v)
        if kv == ka:
            ia = i
        elif kv == kb:
            ib = i
    if ia is None or ib is None:
        return None
    n = len(face.vertices)
    if not (abs(ia - ib) == 1 or {ia, ib} == {0, n - 1}):
        return None  # a, b not adjacent → not a single boundary edge
    # Only a *true prism extend* applies here: the whole wall grows, so both
    # endpoints must be real corners. If the wall continues straight past an
    # endpoint (a T-junction — the host is wider than the pushed cap, so the
    # wall carries on at the old level), moving that vertex up would collapse
    # the step into a diagonal slant. Bail so the caller raises a coplanar strip
    # instead, which coplanar-merge then fuses into a clean L (step), not a wedge.
    verts = face.vertices
    for idx, other_idx in ((ia, ib), (ib, ia)):
        outer = verts[(idx - 1) % n]
        if (idx - 1) % n == other_idx:
            outer = verts[(idx + 1) % n]
        d_into = verts[idx] - outer            # wall edge arriving at the vertex
        d_along = verts[other_idx] - verts[idx]  # the edge being moved
        if d_into.length() < 1e-9 or d_along.length() < 1e-9:
            continue
        if QVector3D.dotProduct(d_into.normalized(), d_along.normalized()) > 0.999:
            return None  # collinear continuation → not a corner; not a prism extend
    new_loop = list(face.vertices)
    new_loop[ia] = QVector3D(a2)
    new_loop[ib] = QVector3D(b2)
    if not is_planar(new_loop):
        return None  # the move left the wall's plane → not a coplanar extend
    return new_loop


def subtract_loop_from_face(
    face: Face, loop: list[QVector3D]
) -> Optional[list[QVector3D]]:
    """The remainder of ``face`` after ``loop`` is carved out of it, when the
    loop shares a contiguous boundary arc (corner / edge rectangle). ``None``
    if it isn't that case. The ``loop`` itself stays a separate face."""
    chain = find_subdividing_chain(face, loop)
    if chain is None:
        return None
    split = split_face_by_chain(face, chain)
    if split is None:
        return None
    r1, r2 = split
    loop_keys = frozenset(_key(v) for v in loop)
    if frozenset(_key(v) for v in r1) == loop_keys:
        return r2
    if frozenset(_key(v) for v in r2) == loop_keys:
        return r1
    return None  # neither half is the drawn loop → ambiguous, leave it alone


# ---- Multiple-cycle detection ----------------------------------------------

def _same_cycle(c1: list[QVector3D], c2: list[QVector3D]) -> bool:
    return frozenset(_key(v) for v in c1) == frozenset(_key(v) for v in c2)


def find_cycles_through(
    edges: Iterable[Edge], a: QVector3D, b: QVector3D, max_results: int = 2
) -> list[list[QVector3D]]:
    """Up to ``max_results`` distinct minimal cycles through segment ``a``–``b``.

    A single new edge can close more than one face — the classic case being a
    diagonal across a square, which bounds a triangle on each side. The first
    cycle is the smallest; the second is the smallest found after removing the
    first's interior nodes, which routes the search to the other side. This is
    what stops auto-facing from creating only one of the two triangles.
    """
    edges = list(edges)
    first = find_smallest_cycle_through(edges, a, b)
    if first is None:
        return []
    cycles = [first]
    if max_results >= 2:
        interior = {_key(v) for v in first} - {_key(a), _key(b)}
        if interior:
            filtered = [
                e for e in edges
                if _key(e.a) not in interior and _key(e.b) not in interior
            ]
            second = find_smallest_cycle_through(filtered, a, b)
            if second is not None and not _same_cycle(second, first):
                cycles.append(second)
    return cycles


# ---- Polygon offset (Offset tool: walls with thickness) --------------------

def _offset_line_intersection(
    p0: QVector3D, u: QVector3D, p1: QVector3D, v: QVector3D, n: QVector3D
) -> Optional[QVector3D]:
    """Intersection of two coplanar lines ``p0+s·u`` and ``p1+t·v`` (in the plane
    with normal ``n``). For (near-)parallel lines — collinear consecutive edges —
    return ``p1`` so the shared vertex survives without a spurious corner."""
    denom = QVector3D.dotProduct(QVector3D.crossProduct(u, v), n)
    if abs(denom) < 1e-9:
        return QVector3D(p1)
    s = QVector3D.dotProduct(QVector3D.crossProduct(p1 - p0, v), n) / denom
    return p0 + u * s


def offset_loop(
    loop: list[QVector3D], normal: QVector3D, d: float
) -> Optional[list[QVector3D]]:
    """Offset a planar polygon ``loop`` by ``d`` in its plane: ``d > 0`` moves the
    boundary *inward* (toward the interior), ``d < 0`` outward. Each edge slides
    along its in-plane normal and consecutive offset edges are re-intersected for
    the new corners. ``None`` if the loop degenerates (too few points, a zero-
    length edge, or the offset collapses/inverts it — e.g. inward by more than
    the polygon's half-width).

    Inward is ``cross(n, edge_dir)``: the Newell ``normal`` winds the loop CCW
    around itself, so that points to the interior. Convex and mild concave loops
    work; a deep concavity can self-intersect (not handled — returns None on
    inversion). Enough for rectangular footprints (walls with thickness)."""
    count = len(loop)
    if count < 3:
        return None
    n = normal.normalized()
    offset_lines: list[tuple[QVector3D, QVector3D]] = []
    for i in range(count):
        a = loop[i]
        b = loop[(i + 1) % count]
        edge = b - a
        if edge.length() < _SPLIT_TOLERANCE:
            return None
        e = edge.normalized()
        inward = QVector3D.crossProduct(n, e).normalized()
        offset_lines.append((a + inward * d, e))
    new_loop: list[QVector3D] = []
    for i in range(count):
        p0, u = offset_lines[(i - 1) % count]
        p1, v = offset_lines[i]
        pt = _offset_line_intersection(p0, u, p1, v, n)
        if pt is None:
            return None
        new_loop.append(pt)
    # Reject an overshot inward offset: if any edge reversed direction, the
    # offset edges crossed (the wall is thicker than the polygon's half-width).
    for i in range(count):
        orig = loop[(i + 1) % count] - loop[i]
        new = new_loop[(i + 1) % count] - new_loop[i]
        if new.length() < _SPLIT_TOLERANCE:
            return None
        if QVector3D.dotProduct(orig.normalized(), new.normalized()) <= 0.0:
            return None
    return new_loop


# ---- Heal overlapping coplanar faces (spurious mother) ---------------------

def _loop_inside_loop(inner: list[QVector3D], outer: list[QVector3D],
                      normal: QVector3D) -> bool:
    """Whether ``inner`` lies inside ``outer`` (both coplanar loops, by centroid)."""
    if len(inner) < 3 or len(outer) < 3:
        return False
    from core.triangulate import plane_axes
    u, w = plane_axes(normal)
    origin = outer[0]

    def proj(p):
        rel = p - origin
        return (QVector3D.dotProduct(rel, u), QVector3D.dotProduct(rel, w))

    poly = [proj(p) for p in outer]
    cx = sum(p.x() for p in inner) / len(inner)
    cy = sum(p.y() for p in inner) / len(inner)
    cz = sum(p.z() for p in inner) / len(inner)
    return _point_inside_2d(proj(QVector3D(cx, cy, cz)), poly)


def _point_inside_2d(pt, poly) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _maximal_holes(holes: list) -> list:
    """Drop holes nested inside a larger hole of the same face — redundant
    overlapping holes that incremental subdivision can leave behind."""
    def area(loop):
        a = 0.0
        n = len(loop)
        for i in range(n):
            a += loop[i].x() * loop[(i + 1) % n].y()
            a -= loop[(i + 1) % n].x() * loop[i].y()
        return abs(a) / 2.0

    areas = [area(h) for h in holes]
    keep = []
    for i, h in enumerate(holes):
        # The plane normal from the hole itself (any 3 non-collinear points).
        nrm = QVector3D.crossProduct(h[1] - h[0], h[2] - h[0])
        if nrm.length() < 1e-9:
            keep.append(h)
            continue
        nrm = nrm.normalized()
        nested = any(
            j != i and areas[j] > areas[i] + 1e-6
            and _loop_inside_loop(h, holes[j], nrm)
            for j in range(len(holes))
        )
        if not nested:
            keep.append(h)
    return keep


def _g_inside_a_hole(face: Face, loop: list[QVector3D]) -> bool:
    """Whether ``loop`` sits inside one of ``face``'s holes (so it doesn't
    actually overlap the face's solid region — it fills a hole)."""
    n = face.normal()
    return any(_loop_inside_loop(loop, h, n) for h in face.holes if len(h) >= 3)


def _point_in_face_solid(face: Face, point: QVector3D) -> bool:
    """Whether ``point`` lies in ``face``'s solid region — inside its outer loop
    and outside every hole."""
    from core.triangulate import plane_axes
    n = face.normal()
    u, w = plane_axes(n)
    origin = face.vertices[0]

    def proj(p):
        rel = p - origin
        return (QVector3D.dotProduct(rel, u), QVector3D.dotProduct(rel, w))

    pt = proj(point)
    if not _point_inside_2d(pt, [proj(v) for v in face.vertices]):
        return False
    return not any(_point_inside_2d(pt, [proj(v) for v in h]) for h in face.holes)


def orient_coplanar_faces(mesh) -> list:
    """Flip faces whose winding came out reversed, so coplanar faces face the
    same way.

    Auto-faced cycles can close with the opposite orientation (a normal pointing
    the wrong way), and push/pull then extrudes that face *into* the model
    instead of out — it looks like "I can't push this one". A valid solid never
    has two anti-parallel faces on the same plane, so flipping any face whose
    normal opposes the area-weighted majority of its plane only ever fixes that
    anomaly. Returns the flipped faces.
    """
    groups: dict = {}
    for f in mesh.faces:
        n = f.normal()
        axis = (round(abs(n.x()), 2), round(abs(n.y()), 2), round(abs(n.z()), 2))
        dist = round(abs(QVector3D.dotProduct(n, f.centroid())), 2)
        groups.setdefault((axis, dist), []).append(f)

    flipped: list = []
    for fs in groups.values():
        if len(fs) < 2:
            continue
        dominant = QVector3D(0.0, 0.0, 0.0)
        for f in fs:
            dominant += f.normal() * f.area()
        for f in fs:
            if QVector3D.dotProduct(f.normal(), dominant) < 0:
                outer = [QVector3D(v) for v in f.vertices][::-1]
                holes = [[QVector3D(v) for v in h] for h in f.holes]
                mesh.remove_face(f)
                mesh.add_face(outer, holes or None)
                flipped.append(f)
    return flipped


def _collinear_overlapping(e1, e2) -> bool:
    """Whether edge ``e1`` lies collinearly over ``e2`` with overlapping span."""
    a, b, c, d = e1.a, e1.b, e2.a, e2.b
    d1, d2 = b - a, d - c
    if d1.length() < 1e-9 or d2.length() < 1e-9:
        return False
    u = d2.normalized()
    if QVector3D.crossProduct(d1.normalized(), u).length() > 1e-4:
        return False  # not parallel
    rel = a - c
    if (rel - u * QVector3D.dotProduct(rel, u)).length() > _PLANAR_TOLERANCE:
        return False  # parallel but offset (different line)
    ta = QVector3D.dotProduct(a - c, u)
    tb = QVector3D.dotProduct(b - c, u)
    lo1, hi1 = min(ta, tb), max(ta, tb)
    return min(hi1, d2.length()) - max(lo1, 0.0) > 1e-4


def resolve_tjunctions(mesh, max_iter: int = 1000) -> None:
    """Split edges at T-junction vertices so faces with mismatched subdivisions
    share connectivity instead of a naked collinear seam.

    Two walls meeting can leave one face's long edge running past the vertices
    where the other face is split (the door, a perpendicular wall). Their shared
    boundary is then two separate naked edges, not one border-2 edge — so erasing
    the dividing line cascades and deletes a wall instead of merging. Splitting at
    each interior vertex welds the seam; erase-merge then reunites the walls."""
    for _ in range(max_iter):
        target = None
        for e in mesh.edges:
            v = mesh.interior_vertex_on(e)
            if v is not None:
                target = (e, v)
                break
        if target is None:
            return
        mesh.split_edge_at(*target)


def prune_collinear_orphan_edges(mesh) -> list:
    """Remove edges that bound no face and lie collinearly over another edge — the
    unwelded collinear overlaps that leave a duplicate 'division line'. Returns
    the removed edges."""
    removed = []
    for e in [edge for edge in mesh.edges if len(edge.faces) == 0]:
        if any(other is not e and _collinear_overlapping(e, other)
               for other in mesh.edges):
            mesh.remove_edge(e)
            removed.append(e)
    return removed


def _mesh_is_flat(mesh) -> bool:
    """Whether every face lies on one common plane — a flat 2D drawing, where the
    aggressive partial-overlap cleanup is safe (in 3D, coplanar faces inside each
    other are legitimate)."""
    faces = mesh.faces
    if len(faces) < 2:
        return True
    n0 = faces[0].normal()
    d0 = QVector3D.dotProduct(n0, faces[0].vertices[0])
    for f in faces[1:]:
        if not _faces_coplanar(n0, f.normal()):
            return False
        if abs(QVector3D.dotProduct(n0, f.vertices[0]) - d0) > _PLANAR_TOLERANCE:
            return False
    return True


def heal_overlapping_faces(mesh, coverage: float = 0.5, partial=None) -> list:
    """Clean up coplanar face overlaps that draw/delete sequences can leave:

    1. **Redundant nested holes** — incrementally subdividing a face can punch
       overlapping holes (a hole inside a hole). Keep only the outermost ones.
    2. **A redundant mother** — a big enclosing face left on top of the smaller
       coplanar faces inside it (not merely filling its holes). When the inside
       faces cover most (> ``coverage``) of its area, that mother is spurious;
       remove it so the real subdivision stays. A ring (face with holes) whose
       inside faces only *fill its holes* is legitimate and kept.

    3. **Reversed faces** — a face auto-faced with the wrong winding (so it would
       push the wrong way) is flipped to match its plane.

    The partial-overlap pass also removes a small face whose body lies in
    another's solid region — a partial overlap the auto-divide missed (e.g. a
    door-corner sliver). It's unsafe in 3D (stacked blocks, through-holes
    legitimately nest coplanar faces), so ``partial`` defaults to *auto*: on only
    when the whole model is one flat plane (a 2D drawing). ``True``/``False``
    force it.

    Returns the faces removed (the rebuilt/flipped ones don't count).
    """
    if partial is None:
        partial = _mesh_is_flat(mesh)

    # 0a. Prune duplicate division lines: an edge bounding no face that lies
    #     collinearly over another edge (the unwelded collinear-overlap that
    #     leaves a "doubled line" you can't merge away).
    prune_collinear_orphan_edges(mesh)

    # 0b. Weld T-junction seams so two faces share their dividing edge — erasing
    #     it then merges the walls instead of deleting one.
    resolve_tjunctions(mesh)

    # 0c. Flip any reversed face so coplanar faces face the same way.
    orient_coplanar_faces(mesh)

    # 1. Dedupe nested holes by rebuilding the face with the outermost holes.
    for face in list(mesh.faces):
        if len(face.holes) < 2:
            continue
        keep = _maximal_holes(face.holes)
        if len(keep) != len(face.holes):
            outer = [QVector3D(v) for v in face.vertices]
            mesh.remove_face(face)
            mesh.add_face(outer, [[QVector3D(v) for v in h] for h in keep] or None)

    # 2. Remove a redundant mother covered by faces that aren't in its holes.
    removed: list = []
    for face in list(mesh.faces):
        if face.area() < _SPLIT_TOLERANCE:
            continue
        fn = face.normal()
        covered = 0.0
        for g in mesh.faces:
            if g is face or g in removed:
                continue
            if not _faces_coplanar(fn, g.normal()):
                continue
            if (g.area() < face.area() and loop_inside_face(face, g.vertices)
                    and not _g_inside_a_hole(face, g.vertices)):
                covered += g.area()
        if covered > coverage * face.area():
            mesh.remove_face(face)
            removed.append(face)

    # 3. (Flat plans) A small face whose body lies in a bigger coplanar face's
    #    solid region is a partial overlap the auto-divide missed (a door piece
    #    drawn over the wall). Don't delete it — the user wants it as its own
    #    selectable face — instead punch a matching hole in the bigger face so
    #    they no longer overlap. The small face fills that hole.
    if partial:
        from collections import defaultdict
        punch: dict = defaultdict(list)
        for face in mesh.faces:
            if face in removed:
                continue
            centre = face.centroid()
            fn = face.normal()
            for g in mesh.faces:
                if g is face or g in removed or g.area() <= face.area():
                    continue
                if not _faces_coplanar(fn, g.normal()):
                    continue
                if _g_inside_a_hole(g, face.vertices):
                    continue  # already filling a hole — no overlap
                if _point_in_face_solid(g, centre):
                    punch[g].append(face)
                    break
        for g, smalls in punch.items():
            outer = [QVector3D(v) for v in g.vertices]
            holes = [[QVector3D(v) for v in h] for h in g.holes]
            holes += [[QVector3D(v) for v in s.vertices] for s in smalls]
            mesh.remove_face(g)
            try:
                mesh.add_face(outer, holes)
            except Exception:
                # Degenerate hole arrangement — fall back to the face as it was.
                mesh.add_face(outer, [[QVector3D(v) for v in h]
                                      for h in g.holes] or None)
    return removed


