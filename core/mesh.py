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

    __slots__ = ("v0", "v1", "faces")

    def __init__(self, v0: Vertex, v1: Vertex) -> None:
        self.v0 = v0
        self.v1 = v1
        self.faces: list[Face] = []

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

    __slots__ = ("loop", "hole_loops")

    def __init__(
        self, loop: list[Vertex], hole_loops: Optional[list[list[Vertex]]] = None
    ) -> None:
        self.loop = list(loop)
        self.hole_loops = [list(h) for h in hole_loops] if hole_loops else []

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
        self.vertices: list[Vertex] = []
        self.edges: list[Edge] = []
        self.faces: list[Face] = []

    # ---- Vertices -----------------------------------------------------------
    def vertex(self, position: QVector3D) -> Vertex:
        """Get-or-create the shared vertex at ``position`` (welds coincident
        points to one object)."""
        k = _key(position)
        v = self._registry.get(k)
        if v is None:
            v = Vertex(position)
            self._registry[k] = v
            self.vertices.append(v)
        return v

    def vertex_at(self, position: QVector3D) -> Optional[Vertex]:
        """The existing vertex at ``position``, or ``None``."""
        return self._registry.get(_key(position))

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

    def _link_edge(self, v0: Vertex, v1: Vertex) -> Edge:
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
        face — radial, so an edge can carry several)."""
        loop = [self.vertex(p) for p in loop_positions]
        holes = [[self.vertex(p) for p in h] for h in (hole_loops or [])]
        face = Face(loop, holes)  # Face stores them as hole_loops (vertices)
        for lp in (loop, *holes):
            n = len(lp)
            for i in range(n):
                edge = self._link_edge(lp[i], lp[(i + 1) % n])
                edge.faces.append(face)
        self.faces.append(face)
        return face

    def remove_face(self, face: Face) -> None:
        """Drop a face and detach it from its boundary edges' radial lists. The
        edges themselves stay (they may border other faces or stand alone)."""
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

    # ---- Reset --------------------------------------------------------------
    def clear(self) -> None:
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
