# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Shared-vertex, non-manifold connectivity mesh.

Level B+C of the topology-engine migration (see
``docs/halfedge-migration-plan.md``). Unlike the legacy ``core.geometry`` model
— where every ``Edge``/``Face`` stores its own *copies* of points and
connectivity is rediscovered by matching rounded positions — here a
:class:`Vertex` is a first-class **shared** object:

- an :class:`Edge` references two vertices and knows its incident faces (a
  *radial* list that may hold more than two — non-manifold is fine: three faces
  meet where two walls and a floor join);
- a :class:`Face` is bounded by loops of shared vertices, with optional holes;
- the :class:`Mesh` welds coincident positions to a single vertex on insert
  ("sticky" geometry) and maintains the incidence as geometry is added/removed.

This is SketchUp's model. Moving a vertex moves every edge and face that
references it for free — no position-matching, no float tolerance.

Built in parallel with the legacy model; the app is migrated onto it
incrementally (phase M2) behind the ``Command`` facade. Nothing here is wired
into the running app yet.
"""
from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtGui import QVector3D


# Weld tolerance — two positions within this many decimals (~0.1 mm at metric
# scale) are the same vertex. Same value as legacy ``core.topology``.
_KEY_DECIMALS = 4

# Monotonic id for drawn curves (circle/arc). Segments of one curve share it so
# selection groups them, SketchUp-style. Session-scoped; ``.igz`` load bumps it
# past any stored ids so new curves stay unique.
_CURVE_COUNTER = 1

# Distance tolerance for the stitch pass (a point this close to an edge counts
# as on it). Matches the weld key resolution.
_STITCH_TOL = 1e-4


def _key(p: QVector3D) -> tuple[float, float, float]:
    return (round(p.x(), _KEY_DECIMALS),
            round(p.y(), _KEY_DECIMALS),
            round(p.z(), _KEY_DECIMALS))


class Vertex:
    """A shared point in the mesh.

    Coincident positions weld to one ``Vertex`` (the :class:`Mesh` registry
    guarantees this), so moving it moves every edge and face that references it.
    ``edges`` holds the incident edges, maintained by the mesh.
    """

    __slots__ = ("position", "edges")

    def __init__(self, position: QVector3D) -> None:
        self.position = QVector3D(position)
        self.edges: set[Edge] = set()

    def faces(self) -> set[Face]:
        """Faces touching this vertex (via its incident edges)."""
        out: set[Face] = set()
        for e in self.edges:
            out.update(e.faces)
        return out

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        p = self.position
        return f"Vertex({p.x():.3f}, {p.y():.3f}, {p.z():.3f})"


class Edge:
    """A segment between two shared vertices.

    ``faces`` is the *radial* list of incident faces — 0, 1, 2, or more. More
    than two is non-manifold and fully supported (it is the common case in
    architecture). Maintained by the mesh.
    """

    __slots__ = ("v0", "v1", "faces", "soft", "curve", "layer")

    def __init__(self, v0: Vertex, v1: Vertex) -> None:
        self.v0 = v0
        self.v1 = v1
        self.faces: list[Face] = []
        # A "soft" edge is a curve segment (circle/arc): kept in the topology but
        # hidden from the edge render, so the curve reads smooth, SketchUp-style.
        self.soft: bool = False
        # Layer / tag name (None = default layer).
        self.layer = None
        # Segments of one drawn curve (circle/arc) share a ``curve`` id, so
        # selecting one segment selects the whole curve (SketchUp curve entity).
        # ``None`` for a plain edge. A split just leaves each side's segments with
        # the same id → the pieces select as separate arcs, like SketchUp.
        self.curve: int | None = None

    # Position accessors keep parity with the legacy ``Edge.a`` / ``Edge.b`` so
    # read-only consumers (render, bounds) port with minimal churn.
    @property
    def a(self) -> QVector3D:
        return self.v0.position

    @property
    def b(self) -> QVector3D:
        return self.v1.position

    def other(self, v: Vertex) -> Vertex:
        """The endpoint that is not ``v``."""
        return self.v1 if v is self.v0 else self.v0

    def has(self, v: Vertex) -> bool:
        return v is self.v0 or v is self.v1

    def length(self) -> float:
        return (self.v1.position - self.v0.position).length()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Edge({self.v0!r} -> {self.v1!r})"


class Face:
    """A planar polygon: an outer ``loop`` of shared vertices, plus optional
    inner ``hole_loops``. Geometry is derived from the vertices' positions, so
    it follows automatically when a shared vertex moves.

    Connectivity is kept as vertex loops (``loop`` / ``hole_loops``). The
    ``vertices`` and ``holes`` properties return the same loops as plain
    positions, matching the legacy ``core.geometry.Face`` read interface — so
    rendering, bounds and ``.igz`` save consume a ``mesh.Face`` unchanged (M1).
    """

    __slots__ = ("loop", "hole_loops", "interior", "attrs")

    def __init__(
        self, loop: list[Vertex], hole_loops: Optional[list[list[Vertex]]] = None
    ) -> None:
        self.loop = list(loop)
        self.hole_loops = [list(h) for h in hole_loops] if hole_loops else []
        # Interior partition: a face *inside* a solid (the slab a Ctrl-push
        # keeps, a wall two rooms share) rather than on its boundary. Marked by
        # ``core.orient.orient_outward`` (and the plane rebuild) so volumetric
        # queries — parity ray-casting, signed volume — skip it: an interior
        # face is not part of the boundary, and counting a ray crossing
        # through it would corrupt every inside/outside answer.
        self.interior = False
        # User-facing attributes that must survive the engine's face churn
        # (materials, future BIM tags). The rebuild/dissolve/dedupe/fold paths
        # replace face objects, so each inheritance point carries this dict to
        # the surviving/covering face — see tests/test_face_attrs.py.
        self.attrs: dict = {}

    # ---- Legacy-compatible read interface (positions) -----------------------
    @property
    def vertices(self) -> list[QVector3D]:
        return [v.position for v in self.loop]

    @property
    def holes(self) -> list[list[QVector3D]]:
        return [[v.position for v in h] for h in self.hole_loops]

    # ---- Geometry (ported from legacy Face, over shared positions) ----------
    def _newell(self) -> QVector3D:
        n = QVector3D(0.0, 0.0, 0.0)
        loop = self.loop
        count = len(loop)
        for i in range(count):
            curr = loop[i].position
            nxt = loop[(i + 1) % count].position
            n = n + QVector3D(
                (curr.y() - nxt.y()) * (curr.z() + nxt.z()),
                (curr.z() - nxt.z()) * (curr.x() + nxt.x()),
                (curr.x() - nxt.x()) * (curr.y() + nxt.y()),
            )
        return n

    def normal(self) -> QVector3D:
        if len(self.loop) < 3:
            return QVector3D(0.0, 0.0, 1.0)
        n = self._newell()
        if n.length() < 1e-9:
            return QVector3D(0.0, 0.0, 1.0)
        return n.normalized()

    def area(self) -> float:
        if len(self.loop) < 3:
            return 0.0
        return 0.5 * self._newell().length()

    def centroid(self) -> QVector3D:
        count = len(self.loop)
        if count == 0:
            return QVector3D(0.0, 0.0, 0.0)
        cx = sum(v.position.x() for v in self.loop) / count
        cy = sum(v.position.y() for v in self.loop) / count
        cz = sum(v.position.z() for v in self.loop) / count
        return QVector3D(cx, cy, cz)

    def triangulate(self) -> list[tuple[QVector3D, QVector3D, QVector3D]]:
        if len(self.loop) < 3:
            return []
        from core.triangulate import triangulate

        return triangulate(self.vertices, self.holes, self.normal())

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Face({len(self.loop)} verts, {len(self.holes)} holes)"


class Mesh:
    """Owns the shared vertices, edges and faces and keeps their incidence in
    sync. The single place geometry is mutated; all welding happens here.
    """

    def __init__(self) -> None:
        self._registry: dict[tuple, Vertex] = {}
        #: Set by every mutation primitive; the viewport's cached group chunk
        #: clears it after (re)validating — an O(1) dirty signal so render
        #: caches never content-hash a 131k-vertex mesh per scene change.
        self._chunk_dirty = True
        self.vertices: list[Vertex] = []
        self.edges: list[Edge] = []
        self.faces: list[Face] = []

    # ---- Vertices -----------------------------------------------------------
    def _lookup(self, position: QVector3D) -> Optional[Vertex]:
        """The registered vertex within weld tolerance of ``position``, or
        ``None``. Rounding alone has a boundary hole: two points 2e-6 apart
        can straddle a 0.5e-4 rounding edge and land in different cells (a
        slanted plane's corner computed via two float paths) — so a miss in
        the exact cell probes the neighbouring cells and checks real
        distance."""
        k = _key(position)
        v = self._registry.get(k)
        if v is not None:
            return v
        step = 10.0 ** -_KEY_DECIMALS
        tol2 = step * step
        for dx in (-step, 0.0, step):
            for dy in (-step, 0.0, step):
                for dz in (-step, 0.0, step):
                    if dx == 0.0 and dy == 0.0 and dz == 0.0:
                        continue
                    n = self._registry.get((round(k[0] + dx, _KEY_DECIMALS),
                                            round(k[1] + dy, _KEY_DECIMALS),
                                            round(k[2] + dz, _KEY_DECIMALS)))
                    if n is not None and (
                            (n.position - position).lengthSquared() < tol2):
                        return n
        return None

    def vertex(self, position: QVector3D) -> Vertex:
        """Get-or-create the shared vertex at ``position`` (welds coincident
        points to one object)."""
        v = self._lookup(position)
        if v is None:
            v = Vertex(position)
            self._registry[_key(position)] = v
            self.vertices.append(v)
        return v

    def vertex_at(self, position: QVector3D) -> Optional[Vertex]:
        """The existing vertex at ``position``, or ``None``."""
        return self._lookup(position)

    # ---- Edges --------------------------------------------------------------
    def find_edge(self, v0: Vertex, v1: Vertex) -> Optional[Edge]:
        """The edge between two vertices (either orientation), or ``None``.
        O(degree) via the smaller vertex's incidence — no list scan."""
        small = v0 if len(v0.edges) <= len(v1.edges) else v1
        target = v1 if small is v0 else v0
        for e in small.edges:
            if e.other(small) is target:
                return e
        return None

    @staticmethod
    def next_curve_id() -> int:
        """A fresh curve id — for tools that re-create curve edges (paste,
        offset) and must give the copy its own contour identity."""
        global _CURVE_COUNTER
        cid = _CURVE_COUNTER
        _CURVE_COUNTER += 1
        return cid

    def tag_curve(self, loop_points, closed: bool = True) -> int | None:
        """Mark every edge lying along the drawn ``loop_points`` path as one
        curve (a fresh id), so selecting any segment selects the whole circle/arc.
        Robust to splits: an edge is tagged when both its endpoints fall on one
        of the loop's segments, so a segment split by crossing geometry still has
        all its pieces tagged. Returns the id, or ``None`` if nothing matched."""
        self._chunk_dirty = True
        from core.topology import _point_on_seg_incl
        global _CURVE_COUNTER
        pts = list(loop_points)
        if len(pts) < 2:
            return None
        cid = _CURVE_COUNTER
        pairs = list(zip(pts, pts[1:]))
        if closed:
            pairs.append((pts[-1], pts[0]))
        tagged = False
        for e in self.edges:
            if e.curve is not None:
                continue
            a, b = e.a, e.b
            for pa, pb in pairs:
                if _point_on_seg_incl(a, pa, pb) and _point_on_seg_incl(b, pa, pb):
                    e.curve = cid
                    tagged = True
                    break
        if tagged:
            _CURVE_COUNTER += 1
            return cid
        return None

    def curve_edges(self, edge) -> list:
        """All edges sharing ``edge``'s curve id (``[edge]`` if it has none)."""
        cid = getattr(edge, "curve", None)
        if cid is None:
            return [edge]
        return [e for e in self.edges if e.curve == cid]

    def resplit_curves(self) -> None:
        """Re-partition curve ids into contiguous contours (SketchUp).

        A curve breaks at any vertex where other geometry attaches (a non-curve
        edge is incident) or where its own connectivity isn't a simple chain
        (curve-degree ≠ 2). Each resulting chain keeps/gets its own id — so a
        circle crossed by a square's edges selects as two separate arcs, exactly
        SketchUp's 'two contours'. A pristine circle stays one curve."""
        self._chunk_dirty = True
        global _CURVE_COUNTER
        by_id: dict[int, list[Edge]] = {}
        for e in self.edges:
            if e.curve is not None:
                by_id.setdefault(e.curve, []).append(e)
        for cid, edges in by_id.items():
            eset = set(edges)

            def is_break(v) -> bool:
                cdeg = sum(1 for e in v.edges if e in eset)
                return cdeg != 2 or len(v.edges) != cdeg

            # Walk chains between break vertices; a break-free component is a
            # closed loop (one chain).
            unvisited = set(edges)
            chains: list[list[Edge]] = []
            # Seed from break vertices first so open chains are walked end-to-end.
            seeds = [e for e in edges if is_break(e.v0) or is_break(e.v1)]
            for seed in seeds + edges:
                if seed not in unvisited:
                    continue
                start_v = seed.v0 if is_break(seed.v0) else (
                    seed.v1 if is_break(seed.v1) else seed.v0)
                chain, v, e = [], start_v, seed
                while e in unvisited:
                    unvisited.discard(e)
                    chain.append(e)
                    v = e.other(v)
                    if is_break(v):
                        break
                    nxt = next((n for n in v.edges if n in unvisited), None)
                    if nxt is None:
                        break
                    e = nxt
                chains.append(chain)
            if len(chains) <= 1:
                continue  # still one contour — keep the id stable
            for i, chain in enumerate(chains):
                new_id = cid if i == 0 else _CURVE_COUNTER
                if i > 0:
                    _CURVE_COUNTER += 1
                for e in chain:
                    e.curve = new_id

    def _link_edge(self, v0: Vertex, v1: Vertex) -> Edge:
        self._chunk_dirty = True
        e = self.find_edge(v0, v1)
        if e is not None:
            return e
        e = Edge(v0, v1)
        v0.edges.add(e)
        v1.edges.add(e)
        self.edges.append(e)
        return e

    def add_edge(self, a: QVector3D, b: QVector3D) -> Edge:
        """Add a free edge between two positions (welding endpoints, deduping
        the edge). Returns the existing edge if it is already there."""
        v0 = self.vertex(a)
        v1 = self.vertex(b)
        if v0 is v1:
            raise ValueError("degenerate edge: endpoints weld to one vertex")
        return self._link_edge(v0, v1)

    def remove_edge(self, e: Edge) -> None:
        """Detach and drop an edge. Callers remove any incident faces first when
        needed; this does not cascade."""
        self._chunk_dirty = True
        e.v0.edges.discard(e)
        e.v1.edges.discard(e)
        if e in self.edges:
            self.edges.remove(e)

    # ---- Faces --------------------------------------------------------------
    def add_face(
        self,
        loop_positions: Iterable[QVector3D],
        hole_loops: Optional[Iterable[Iterable[QVector3D]]] = None,
    ) -> Face:
        """Add a face from position loops. Vertices weld, the boundary edges are
        created if missing (and the face is registered on each as an incident
        face — radial, so an edge can carry several).

        Nested holes are dropped here, at the single choke point every face
        creation path goes through (draw, push rebuild, heal, ``.igz`` load): a
        hole inside another hole of the same face is geometric nonsense — its
        region is already void — yet it corrupts earcut (phantom wedge
        triangles across the opening) and registers the face on rim edges it
        does not geometrically touch, which lets ``is_closed`` report a broken
        solid as watertight (defeating the BIM push guard)."""
        self._chunk_dirty = True
        loop = [self.vertex(p) for p in loop_positions]
        raw_holes = [list(h) for h in (hole_loops or [])]
        if len(raw_holes) > 1:
            from core.topology import _maximal_holes

            raw_holes = _maximal_holes(raw_holes)
        holes = [[self.vertex(p) for p in h] for h in raw_holes]
        face = Face(loop, holes)  # Face stores them as hole_loops (vertices)
        for lp in (loop, *holes):
            n = len(lp)
            for i in range(n):
                edge = self._link_edge(lp[i], lp[(i + 1) % n])
                edge.faces.append(face)
        self.faces.append(face)
        return face

    def surface_of(self, face: "Face") -> list:
        """The faces forming the same **curved surface** as ``face`` — the
        connected component reached by crossing **soft** edges (a circle/arc
        sweep's hidden seams). SketchUp groups faces joined by softened edges
        into one surface, so clicking/painting one acts on the whole curved
        side. Returns just ``[face]`` for ordinary (hard-edged) geometry."""
        seen = {face}
        stack = [face]
        while stack:
            f = stack.pop()
            for lp in (f.loop, *f.hole_loops):
                n = len(lp)
                for i in range(n):
                    e = self.find_edge(lp[i], lp[(i + 1) % n])
                    if e is None or not e.soft:
                        continue
                    for g in e.faces:
                        if g is not f and g not in seen:
                            seen.add(g)
                            stack.append(g)
        return list(seen)

    def remove_face(self, face: Face) -> None:
        """Drop a face and detach it from its boundary edges' radial lists. The
        edges themselves stay (they may border other faces or stand alone)."""
        self._chunk_dirty = True
        for lp in (face.loop, *face.hole_loops):
            n = len(lp)
            for i in range(n):
                edge = self.find_edge(lp[i], lp[(i + 1) % n])
                if edge is not None and face in edge.faces:
                    edge.faces.remove(face)
        if face in self.faces:
            self.faces.remove(face)

    # ---- Re-link (undo support) --------------------------------------------
    def relink_edge(self, edge: Edge) -> None:
        """Re-attach a previously removed edge object (undo), preserving its
        identity so other commands' references stay valid."""
        if edge in self.edges:
            return
        edge.v0.edges.add(edge)
        edge.v1.edges.add(edge)
        self.edges.append(edge)
        self._registry.setdefault(_key(edge.v0.position), edge.v0)
        self._registry.setdefault(_key(edge.v1.position), edge.v1)

    def relink_face(self, face: Face) -> None:
        """Re-attach a previously removed face object (undo), recreating any
        missing boundary edges and re-registering incidence."""
        if face in self.faces:
            return
        for lp in (face.loop, *face.hole_loops):
            n = len(lp)
            for i in range(n):
                edge = self._link_edge(lp[i], lp[(i + 1) % n])
                if face not in edge.faces:
                    edge.faces.append(face)
        self.faces.append(face)

    # ---- Holes --------------------------------------------------------------
    def add_hole(self, face: Face, loop_positions: Iterable[QVector3D]) -> list[Vertex]:
        """Punch a hole into ``face`` (a window/door drawn inside it). Welds the
        loop's vertices, ensures its boundary edges, and registers the face on
        them. Returns the vertex loop so undo can remove exactly this hole."""
        loop = [self.vertex(p) for p in loop_positions]
        face.hole_loops.append(loop)
        n = len(loop)
        for i in range(n):
            edge = self._link_edge(loop[i], loop[(i + 1) % n])
            if face not in edge.faces:
                edge.faces.append(face)
        return loop

    def remove_hole(self, face: Face, loop: list[Vertex]) -> None:
        if loop in face.hole_loops:
            face.hole_loops.remove(loop)
        n = len(loop)
        for i in range(n):
            edge = self.find_edge(loop[i], loop[(i + 1) % n])
            if edge is not None and face in edge.faces:
                edge.faces.remove(face)

    # ---- State snapshot (identity-preserving) -------------------------------
    def capture_state(self) -> dict:
        """Snapshot the mesh keeping *object identity*: the same vertex/edge/face
        objects, plus a copy of every mutable field. :meth:`restore_state` brings
        the exact objects back, so delta commands holding references stay valid.

        Used by the stitch pass, whose splits/merges/collapses interact too much
        for a clean per-op inverse — restoring the whole touched mesh is robust."""
        return {
            "vertices": list(self.vertices),
            "edges": list(self.edges),
            "faces": list(self.faces),
            "registry": dict(self._registry),
            "vpos": {v: QVector3D(v.position) for v in self.vertices},
            "vedges": {v: set(v.edges) for v in self.vertices},
            "efaces": {e: list(e.faces) for e in self.edges},
            "esoft": {e: e.soft for e in self.edges},
            "elayer": {e: e.layer for e in self.edges},
            "ecurve": {e: e.curve for e in self.edges},
            "floop": {f: list(f.loop) for f in self.faces},
            "fholes": {f: [list(h) for h in f.hole_loops] for f in self.faces},
            "fattrs": {f: dict(f.attrs) for f in self.faces},
        }

    def restore_state(self, snap: dict) -> None:
        """Restore a :meth:`capture_state` snapshot. Objects created since are
        dropped; captured ones are re-listed with their fields reset."""
        self._chunk_dirty = True
        self.vertices[:] = snap["vertices"]
        self.edges[:] = snap["edges"]
        self.faces[:] = snap["faces"]
        self._registry.clear()
        self._registry.update(snap["registry"])
        for v, p in snap["vpos"].items():
            v.position = QVector3D(p)
        for v, es in snap["vedges"].items():
            v.edges = set(es)
        for e, fs in snap["efaces"].items():
            e.faces = list(fs)
        for e, soft in snap.get("esoft", {}).items():
            e.soft = soft
        for e, layer in snap.get("elayer", {}).items():
            e.layer = layer
        for e, cid in snap.get("ecurve", {}).items():
            e.curve = cid
        for f, loop in snap["floop"].items():
            f.loop = list(loop)
        for f, holes in snap["fholes"].items():
            f.hole_loops = [list(h) for h in holes]
        for f, attrs in snap.get("fattrs", {}).items():
            f.attrs = dict(attrs)

    # ---- Reset --------------------------------------------------------------
    def clear(self) -> None:
        self._chunk_dirty = True
        self._registry.clear()
        self.vertices.clear()
        self.edges.clear()
        self.faces.clear()

    # ---- Incidence queries --------------------------------------------------
    def edges_at(self, v: Vertex) -> set[Edge]:
        return set(v.edges)

    def faces_at(self, v: Vertex) -> set[Face]:
        return v.faces()

    # ---- Mutation -----------------------------------------------------------
    def move_vertex(self, v: Vertex, delta: QVector3D) -> None:
        """Translate a vertex. Every edge and face referencing it follows for
        free (they hold the same object) — the headline win over the legacy
        position-matching move.

        The registry is re-keyed so later welds find it at the new spot. Merging
        when it lands exactly on another vertex is deliberately *not* done here;
        that topological merge is a separate operation (migration phase M2).
        """
        self._chunk_dirty = True
        old = _key(v.position)
        v.position = v.position + delta
        new = _key(v.position)
        if old == new:
            return
        if self._registry.get(old) is v:
            del self._registry[old]
        self._registry.setdefault(new, v)

    def split_edge(self, edge: Edge, position: QVector3D) -> tuple[Edge, Edge]:
        """Split ``edge`` at ``position``, inserting a shared vertex, and return
        the two sub-edges.

        Every incident face gains the new vertex in its loop, between the edge's
        endpoints — for *any* number of faces (non-manifold). This is the
        operation that, in the legacy model, needed position-matching plus a
        special ``split_edge_in_faces`` pass plus the holes patch; here it falls
        straight out of shared connectivity. The new vertex lands collinearly,
        so faces stay flat until it is later moved (a gable ridge, a T-junction).
        """
        v0, v1 = edge.v0, edge.v1
        mid = self.vertex(position)
        if mid is v0 or mid is v1:
            # position coincides with an endpoint → nothing to split
            return edge, edge
        incident = list(edge.faces)
        self.remove_edge(edge)
        e0 = self._link_edge(v0, mid)
        e1 = self._link_edge(mid, v1)
        for face in incident:
            self._insert_into_face_loops(face, v0, v1, mid)
            if face not in e0.faces:
                e0.faces.append(face)
            if face not in e1.faces:
                e1.faces.append(face)
        return e0, e1

    @staticmethod
    def _insert_into_face_loops(
        face: Face, v0: Vertex, v1: Vertex, mid: Vertex
    ) -> None:
        """Insert ``mid`` between the consecutive ``v0``/``v1`` pair in the
        face's outer loop or one of its holes (whichever carries that edge)."""
        for loop in (face.loop, *face.hole_loops):
            n = len(loop)
            for i in range(n):
                j = (i + 1) % n
                if (loop[i] is v0 and loop[j] is v1) or (
                    loop[i] is v1 and loop[j] is v0
                ):
                    loop.insert(i + 1, mid)  # i+1 == n wraps to an append
                    return

    # ---- Stitch primitives (watertight cleanup, all reversible) -------------
    def weld_coincident(self) -> bool:
        """Merge vertices occupying the same position — the debris a translation
        leaves when geometry lands exactly on existing geometry (a cap pushed
        flush onto the ring it came from). Edges and face loops are re-pointed
        onto the kept vertex; zero-length edges vanish, duplicate edges merge
        their incidence, and faces degenerated below three distinct vertices
        are dropped. Returns whether anything merged. No undo bookkeeping —
        callers run under a snapshot, like the rest of the stitch."""
        self._chunk_dirty = True
        groups: dict = {}
        for v in self.vertices:
            groups.setdefault(_key(v.position), []).append(v)
        changed = False
        for vs in groups.values():
            if len(vs) < 2:
                continue
            keep = vs[0]
            for dup in vs[1:]:
                self._weld_into(keep, dup)
            changed = True
        # Cross-cell pass: two coincident points can straddle a rounding
        # boundary into *different* cells (a slanted corner computed via two
        # float paths, 2e-6 apart across a 0.5e-4 edge). Probe neighbouring
        # cells and weld into the lexicographically smaller key — a
        # deterministic survivor.
        step = 10.0 ** -_KEY_DECIMALS
        tol2 = step * step
        merged = True
        while merged:
            merged = False
            for v in list(self.vertices):
                k = _key(v.position)
                if self._registry.get(k) is not v:
                    continue
                for dx in (-step, 0.0, step):
                    for dy in (-step, 0.0, step):
                        for dz in (-step, 0.0, step):
                            nk = (round(k[0] + dx, _KEY_DECIMALS),
                                  round(k[1] + dy, _KEY_DECIMALS),
                                  round(k[2] + dz, _KEY_DECIMALS))
                            if nk == k:
                                continue
                            w = self._registry.get(nk)
                            if (w is None or w is v or
                                    (w.position - v.position)
                                    .lengthSquared() >= tol2):
                                continue
                            kp, dp = (v, w) if k < nk else (w, v)
                            self._weld_into(kp, dp)
                            changed = True
                            merged = True
                            break
                        if merged:
                            break
                    if merged:
                        break
                if merged:
                    break
        return changed

    def dedupe_faces(self) -> int:
        """Drop faces occupying the same *outer* edge cycle as another — a box
        pushed flat (top welded onto bottom), or a room raised whose shared
        wall the neighbour's push already built. Two faces over one region are
        always junk in a surface model; SketchUp keeps one. When the pair
        differs in holes (a plain cap collapsed onto a subdivided base), the
        one with more holes survives — it carries the user's subdivision, and
        its holes are filled by their own faces. Returns how many were
        removed."""
        self._chunk_dirty = True
        seen: dict = {}
        removed = 0
        for f in list(self.faces):
            sig = frozenset(
                frozenset((id(f.loop[i]), id(f.loop[(i + 1) % len(f.loop)])))
                for i in range(len(f.loop))
            )
            other = seen.get(sig)
            if other is None:
                seen[sig] = f
                continue
            drop, keep = ((other, f) if len(f.hole_loops) > len(other.hole_loops)
                          else (f, other))
            if drop.attrs and not keep.attrs:
                keep.attrs = dict(drop.attrs)
            self.remove_face(drop)
            seen[sig] = keep
            removed += 1
        return removed

    def _weld_into(self, keep: Vertex, dup: Vertex) -> None:
        affected = {f for e in dup.edges for f in e.faces}
        for e in list(dup.edges):
            other = e.other(dup)
            incident = list(e.faces)
            self.remove_edge(e)
            if other is keep:
                continue  # zero-length edge vanishes
            kept_edge = self._link_edge(keep, other)
            for f in incident:
                if f not in kept_edge.faces:
                    kept_edge.faces.append(f)
        for f in affected:
            for loop in (f.loop, *f.hole_loops):
                for i, lv in enumerate(loop):
                    if lv is dup:
                        loop[i] = keep
                i = 0
                while len(loop) > 1 and i < len(loop):
                    if loop[i] is loop[(i + 1) % len(loop)]:
                        loop.pop(i if i + 1 < len(loop) else i + 1 - len(loop))
                    else:
                        i += 1
            if len({id(x) for x in f.loop}) < 3:
                self.remove_face(f)  # collapsed to a sliver
        if dup in self.vertices:
            self.vertices.remove(dup)
        if self._registry.get(_key(dup.position)) is dup:
            self._registry[_key(dup.position)] = keep

    def interior_vertex_on(self, edge: Edge) -> Optional[Vertex]:
        """An existing vertex lying strictly on ``edge``'s interior (a
        T-junction: ``edge`` runs past a vertex that belongs to another face),
        or ``None``. Splitting there is what makes mismatched subdivisions share
        connectivity instead of leaving a naked seam."""
        a = edge.v0.position
        b = edge.v1.position
        ab = b - a
        length = ab.length()
        if length < _STITCH_TOL:
            return None
        for v in self.vertices:
            if v is edge.v0 or v is edge.v1:
                continue
            t = QVector3D.dotProduct(v.position - a, ab) / (length * length)
            if t <= _STITCH_TOL / length or t >= 1.0 - _STITCH_TOL / length:
                continue
            if (v.position - (a + ab * t)).length() < _STITCH_TOL:
                return v
        return None

    def split_edge_at(self, edge: Edge, mid: Vertex) -> dict:
        """Split ``edge`` at the *existing* vertex ``mid`` on its interior.
        Returns a reversible record consumed by :meth:`revert_stitch`.

        A sub-edge may already exist — the T-junction's other face owns it — in
        which case :meth:`_link_edge` reuses it. The record tracks which sub-edges
        this split *created* so undo removes only those and merely detaches the
        face from the reused ones (never deleting a neighbour's edge)."""
        v0, v1 = edge.v0, edge.v1
        incident = list(edge.faces)
        self.remove_edge(edge)
        e0_new = self.find_edge(v0, mid) is None
        e0 = self._link_edge(v0, mid)
        e1_new = self.find_edge(mid, v1) is None
        e1 = self._link_edge(mid, v1)
        # Sub-edges continue the split edge's curve/soft/layer identity.
        for sub, was_new in ((e0, e0_new), (e1, e1_new)):
            if was_new:
                sub.soft = edge.soft
                sub.layer = edge.layer
                sub.curve = edge.curve
        for face in incident:
            self._insert_into_face_loops(face, v0, v1, mid)
            if face not in e0.faces:
                e0.faces.append(face)
            if face not in e1.faces:
                e1.faces.append(face)
        return {"kind": "split", "edge": edge, "e0": e0, "e1": e1,
                "e0_new": e0_new, "e1_new": e1_new, "mid": mid, "faces": incident}

    def collapsible_vertex(self, v: Vertex) -> bool:
        """Whether ``v`` is a redundant valence-2 collinear vertex: its two edges
        are collinear and border the same faces, so it is a spurious subdivision
        of a straight boundary (no edge or face needs it). SketchUp keeps no such
        vertex. Removing it lets two coplanar faces share a single edge again."""
        if len(v.edges) != 2:
            return False
        e1, e2 = list(v.edges)
        d1 = v.position - e1.other(v).position
        d2 = e2.other(v).position - v.position
        if d1.length() < _STITCH_TOL or d2.length() < _STITCH_TOL:
            return False
        if QVector3D.dotProduct(d1.normalized(), d2.normalized()) < 0.999:
            return False
        return set(e1.faces) == set(e2.faces)

    def collapse_vertex(self, v: Vertex) -> dict:
        """Remove the redundant valence-2 collinear vertex ``v`` (see
        :meth:`collapsible_vertex`), merging its two edges into one. Returns a
        reversible record."""
        e1, e2 = list(v.edges)
        n1, n2 = e1.other(v), e2.other(v)
        faces = list(set(e1.faces))
        self.remove_edge(e1)
        self.remove_edge(e2)
        new_edge_existed = self.find_edge(n1, n2) is not None
        new_edge = self._link_edge(n1, n2)
        for face in faces:
            for loop in (face.loop, *face.hole_loops):
                if v in loop:
                    loop.remove(v)
            if face not in new_edge.faces:
                new_edge.faces.append(face)
        if v in self.vertices:
            self.vertices.remove(v)
        if self._registry.get(_key(v.position)) is v:
            del self._registry[_key(v.position)]
        return {"kind": "collapse", "v": v, "e1": e1, "e2": e2, "n1": n1,
                "n2": n2, "new_edge": new_edge, "new_edge_new": not new_edge_existed,
                "faces": faces}

    def dissolve_edge_recorded(self, edge: Edge) -> Optional[dict]:
        """:meth:`dissolve_edge`, but returning a reversible record (the removed
        faces, the edge, and the merged face) instead of just the new face."""
        if len(edge.faces) != 2:
            return None
        face_a, face_b = edge.faces[0], edge.faces[1]
        if face_a is face_b:
            return None
        if QVector3D.dotProduct(face_a.normal(), face_b.normal()) < 0.999:
            return None
        merged = _splice_loops(face_a.loop, face_b.loop, edge.v0, edge.v1)
        if merged is None:
            return None
        loop_positions = [v.position for v in merged]
        hole_positions = [[v.position for v in h]
                          for h in (*face_a.hole_loops, *face_b.hole_loops)]
        self.remove_face(face_a)
        self.remove_face(face_b)
        self.remove_edge(edge)
        new_face = self.add_face(loop_positions, hole_positions or None)
        new_face.attrs = dict(_dominant_attrs((face_a, face_b)))
        return {"kind": "merge", "edge": edge, "face_a": face_a,
                "face_b": face_b, "merged": new_face}

    def dissolve_coplanar_pair(self, face_a: "Face", face_b: "Face") -> Optional[dict]:
        """Merge two coplanar faces into the boundary of their union, removing
        *every* edge they share — not just one. T-junction resolution can leave
        two coplanar faces sharing several edges (a footprint notch), which the
        single-edge splice can't fuse. Traces the union outline instead. Returns
        a reversible record, or ``None`` if they aren't a coplanar adjacent pair
        with a simple union outline."""
        if face_a is face_b:
            return None
        if QVector3D.dotProduct(face_a.normal(), face_b.normal()) < 0.999:
            return None
        a_loops = [face_a.loop, *face_a.hole_loops]
        b_loops = [face_b.loop, *face_b.hole_loops]
        counts: dict = {}
        for loops in (a_loops, b_loops):
            for loop in loops:
                n = len(loop)
                for i in range(n):
                    key = frozenset((id(loop[i]), id(loop[(i + 1) % n])))
                    counts[key] = counts.get(key, 0) + 1
        # Edges interior to the union appear in both faces; the outline is the
        # edges that appear exactly once.
        shared = [k for k, c in counts.items() if c >= 2]
        if not shared:
            return None  # not adjacent
        by_id = {id(v): v for loop in (a_loops + b_loops) for v in loop}
        outline: list[tuple[Vertex, Vertex]] = []
        for loops in (a_loops, b_loops):
            for loop in loops:
                n = len(loop)
                for i in range(n):
                    u, w = loop[i], loop[(i + 1) % n]
                    if counts[frozenset((id(u), id(w)))] == 1:
                        outline.append((u, w))
        loops_out = _trace_loops(outline)
        if loops_out is None:
            return None  # pinched / non-simple union → leave it
        # Largest-area loop is the outer boundary; the rest are holes.
        loops_out.sort(key=lambda lp: _loop_area(lp), reverse=True)
        outer = loops_out[0]
        holes = loops_out[1:]
        shared_edges = [self.find_edge(by_id[next(iter(k))],
                                       by_id[list(k)[-1]]) for k in shared]
        self.remove_face(face_a)
        self.remove_face(face_b)
        for e in shared_edges:
            if e is not None:
                self.remove_edge(e)
        merged = self.add_face([v.position for v in outer],
                               [[v.position for v in h] for h in holes] or None)
        merged.attrs = dict(_dominant_attrs((face_a, face_b)))
        return {"kind": "merge_pair", "face_a": face_a, "face_b": face_b,
                "edges": [e for e in shared_edges if e is not None], "merged": merged}

    def dissolve_coplanar_region(self, faces: Iterable["Face"]) -> Optional["Face"]:
        """Merge a set of mutually-coplanar, edge-connected faces into one face
        spanning the boundary of their union, removing every interior edge.

        Pairwise merging is order-dependent — fusing two of three faces can leave
        a valence-3 junction the third can't bridge — so the stitch merges the
        whole coplanar component at once, which is deterministic. ``None`` if the
        faces aren't all coplanar, aren't connected, or the union isn't simple."""
        faces = list(faces)
        if len(faces) < 2:
            return None
        n0 = faces[0].normal()
        # Coplanar regardless of winding sign (a push/pull can flip a fragment's
        # winding): the union outline is traced from undirected edges, so an
        # opposite-wound coplanar face merges in cleanly.
        if any(abs(QVector3D.dotProduct(n0, f.normal())) < 0.999 for f in faces[1:]):
            return None
        counts: dict = {}
        for f in faces:
            for loop in (f.loop, *f.hole_loops):
                n = len(loop)
                for i in range(n):
                    key = frozenset((id(loop[i]), id(loop[(i + 1) % n])))
                    counts[key] = counts.get(key, 0) + 1
        if not any(c >= 2 for c in counts.values()):
            return None  # not edge-connected
        by_id = {id(v): v for f in faces
                 for loop in (f.loop, *f.hole_loops) for v in loop}
        outline = [
            (u, w)
            for f in faces
            for loop in (f.loop, *f.hole_loops)
            for u, w in zip(loop, loop[1:] + loop[:1])
            if counts[frozenset((id(u), id(w)))] == 1
        ]
        loops_out = _trace_loops(outline)
        if loops_out is None:
            return None
        if not loops_out:
            # The union has no boundary of its own: identical cycles stacked on
            # one another (raising a room whose shared wall the neighbour's push
            # already built). Keep one face, drop the duplicates.
            if not faces[0].attrs:
                faces[0].attrs = dict(_dominant_attrs(faces[1:]))
            for f in faces[1:]:
                self.remove_face(f)
            return faces[0]
        loops_out.sort(key=_loop_area, reverse=True)
        outer, holes = loops_out[0], loops_out[1:]
        interior = [self.find_edge(by_id[tuple(k)[0]], by_id[tuple(k)[-1]])
                    for k, c in counts.items() if c >= 2]
        for f in faces:
            self.remove_face(f)
        for e in interior:
            if e is not None and not e.faces:
                self.remove_edge(e)
        merged = self.add_face([v.position for v in outer],
                               [[v.position for v in h] for h in holes] or None)
        merged.attrs = dict(_dominant_attrs(faces))
        return merged

    def revert_stitch(self, rec: dict) -> None:
        """Undo a single stitch primitive recorded by the methods above."""
        kind = rec["kind"]
        if kind == "split":
            for face in rec["faces"]:
                for loop in (face.loop, *face.hole_loops):
                    if rec["mid"] in loop:
                        loop.remove(rec["mid"])
            # New sub-edges are deleted; reused ones (a neighbour's edge) only
            # lose the faces this split added to them.
            for ekey, enew in (("e0", "e0_new"), ("e1", "e1_new")):
                sub = rec[ekey]
                if rec[enew]:
                    self.remove_edge(sub)
                else:
                    for face in rec["faces"]:
                        if face in sub.faces:
                            sub.faces.remove(face)
            self.relink_edge(rec["edge"])
            for face in rec["faces"]:
                if face not in rec["edge"].faces:
                    rec["edge"].faces.append(face)
        elif kind == "collapse":
            v = rec["v"]
            if v not in self.vertices:
                self.vertices.append(v)
            self._registry.setdefault(_key(v.position), v)
            if rec["new_edge_new"]:
                self.remove_edge(rec["new_edge"])
            else:
                for face in rec["faces"]:
                    if face in rec["new_edge"].faces:
                        rec["new_edge"].faces.remove(face)
            self.relink_edge(rec["e1"])
            self.relink_edge(rec["e2"])
            for face in rec["faces"]:
                self._insert_into_face_loops(face, rec["n1"], rec["n2"], v)
                if face not in rec["e1"].faces:
                    rec["e1"].faces.append(face)
                if face not in rec["e2"].faces:
                    rec["e2"].faces.append(face)
        elif kind == "merge":
            self.remove_face(rec["merged"])
            self.relink_edge(rec["edge"])
            self.relink_face(rec["face_a"])
            self.relink_face(rec["face_b"])
        elif kind == "merge_pair":
            self.remove_face(rec["merged"])
            for e in rec["edges"]:
                self.relink_edge(e)
            self.relink_face(rec["face_a"])
            self.relink_face(rec["face_b"])

    def dissolve_edge(self, edge: Edge) -> Optional[Face]:
        """Dissolve a *redundant* coplanar edge, merging its two faces into one.

        SketchUp's coplanar-merge: an edge bordering exactly two faces that lie
        in the same plane (same outward normal) carries no silhouette — it is the
        seam left when a pushed wall ends up flush with an adjacent one. Splicing
        the two face loops along the shared edge and dropping the edge yields a
        single face (the "L"), no phantom line.

        Returns the new merged face, or ``None`` if the edge is not mergeable
        (not exactly two faces, not coplanar, shared on a hole loop, or the
        splice would not be a simple loop). The radial model makes this cheap:
        the two incident faces are right there on ``edge.faces``; nothing is
        rediscovered by position.
        """
        if len(edge.faces) != 2:
            return None
        face_a, face_b = edge.faces[0], edge.faces[1]
        if face_a is face_b:
            return None  # both sides are the same face (a slit) — leave it
        dot = QVector3D.dotProduct(face_a.normal(), face_b.normal())
        if abs(dot) < 0.999:
            return None  # not coplanar
        # Opposite-wound coplanar fragments (a push/pull artefact) are still one
        # face — flip B so the splice closes instead of leaving a phantom seam.
        loop_b = face_b.loop if dot > 0 else list(reversed(face_b.loop))
        merged = _splice_loops(face_a.loop, loop_b, edge.v0, edge.v1)
        if merged is None:
            return None
        loop_positions = [v.position for v in merged]
        hole_positions = [
            [v.position for v in h]
            for h in (*face_a.hole_loops, *face_b.hole_loops)
        ]
        self.remove_face(face_a)
        self.remove_face(face_b)
        self.remove_edge(edge)
        merged_face = self.add_face(loop_positions, hole_positions or None)
        merged_face.attrs = dict(_dominant_attrs((face_a, face_b)))
        return merged_face


def _dominant_attrs(faces) -> dict:
    """The attrs of the largest-area face that carries any — the dominant
    contributor when a merge collapses several faces into one survivor."""
    best: dict = {}
    best_area = -1.0
    for f in faces:
        if f.attrs and f.area() > best_area:
            best = f.attrs
            best_area = f.area()
    return best


def _shared_edge_index(loop: list[Vertex], v0: Vertex, v1: Vertex) -> Optional[int]:
    """Index ``i`` where ``loop[i]``–``loop[i+1]`` is the edge ``{v0, v1}``."""
    n = len(loop)
    for i in range(n):
        a, b = loop[i], loop[(i + 1) % n]
        if (a is v0 and b is v1) or (a is v1 and b is v0):
            return i
    return None


def _splice_loops(
    loop_a: list[Vertex], loop_b: list[Vertex], v0: Vertex, v1: Vertex
) -> Optional[list[Vertex]]:
    """Merge two vertex loops sharing the edge ``{v0, v1}`` into one, dropping the
    shared edge. Both loops must carry the edge on their *outer* boundary with
    consistent winding (the edge runs ``u→w`` in one and ``w→u`` in the other);
    returns ``None`` otherwise, or if the result would repeat a vertex."""
    ia = _shared_edge_index(loop_a, v0, v1)
    ib = _shared_edge_index(loop_b, v0, v1)
    if ia is None or ib is None:
        return None
    na, nb = len(loop_a), len(loop_b)
    u, w = loop_a[ia], loop_a[(ia + 1) % na]  # A traverses the edge u → w
    # Consistent winding requires B to traverse it the other way, w → u.
    if not (loop_b[ib] is w and loop_b[(ib + 1) % nb] is u):
        return None
    sa = (ia + 1) % na                          # rotate A to start at w → [w … u]
    a_path = loop_a[sa:] + loop_a[:sa]
    sb = (ib + 1) % nb                          # rotate B to start at u → [u … w]
    b_path = loop_b[sb:] + loop_b[:sb]
    merged = a_path[:-1] + b_path[:-1]          # drop the duplicated u and w
    if len({id(v) for v in merged}) != len(merged):
        return None                             # a vertex repeats → not simple
    return merged


def _trace_loops(outline: list[tuple[Vertex, Vertex]]) -> Optional[list[list[Vertex]]]:
    """Trace a set of undirected boundary edges into closed vertex loops. Returns
    ``None`` if any vertex has valence ≠ 2 (a pinched / non-simple outline)."""
    adj: dict[Vertex, list[Vertex]] = {}
    edges: set = set()
    for u, w in outline:
        adj.setdefault(u, []).append(w)
        adj.setdefault(w, []).append(u)
        edges.add(frozenset((u, w)))
    for nbrs in adj.values():
        if len(nbrs) != 2:
            return None
    loops: list[list[Vertex]] = []
    while edges:
        a, b = tuple(next(iter(edges)))
        edges.discard(frozenset((a, b)))
        loop = [a, b]
        prev, cur = a, b
        while True:
            nbrs = adj[cur]
            nxt = nbrs[0] if nbrs[0] is not prev else nbrs[1]
            edges.discard(frozenset((cur, nxt)))
            if nxt is loop[0]:
                break
            loop.append(nxt)
            prev, cur = cur, nxt
        loops.append(loop)
    return loops


def _loop_area(loop: list[Vertex]) -> float:
    """Newell-magnitude area of a vertex loop (orientation-independent)."""
    n = QVector3D(0.0, 0.0, 0.0)
    count = len(loop)
    for i in range(count):
        cur = loop[i].position
        nxt = loop[(i + 1) % count].position
        n = n + QVector3D(
            (cur.y() - nxt.y()) * (cur.z() + nxt.z()),
            (cur.z() - nxt.z()) * (cur.x() + nxt.x()),
            (cur.x() - nxt.x()) * (cur.y() + nxt.y()),
        )
    return 0.5 * n.length()
