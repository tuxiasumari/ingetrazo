"""Undo / redo history using the command pattern, over the shared-vertex mesh.

Every mutation that should be reversible goes through a :class:`Command`
subclass. The viewport owns a :class:`History` that maintains the undo and redo
stacks. Tools call ``viewport.history.execute(...)`` rather than mutating the
scene directly.

Commands mutate ``scene.mesh`` (welding, incidence) and resolve the geometry
they act on **by position at do-time**, so the position-based plans that
:mod:`core.edits` builds (against a throwaway simulation) execute correctly
onto the real mesh — and undo re-links the very objects that were removed,
preserving identity for any references other commands hold.

Why a command stack and not snapshots? Commands store only the delta, so memory
cost stays proportional to the action, not to the model size.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Optional

from PySide6.QtGui import QVector3D

from core.mesh import Edge, Face, Vertex
from core.topology import (
    _key,
    _loop_edges,
    find_containing_face,
    loop_inside_face,
    orphaned_edges_at,
    subtract_loop_from_face,
)


def _find_face_by_loop(mesh, loop_positions) -> Optional[Face]:
    """The mesh face whose outer loop matches ``loop_positions`` (by key), or
    ``None``. Used to resolve a command's target face against the live mesh."""
    target = frozenset(_key(p) for p in loop_positions)
    for f in mesh.faces:
        if frozenset(_key(p) for p in f.vertices) == target:
            return f
    return None


class Command(ABC):
    """Abstract reversible operation against a :class:`Scene`."""

    @abstractmethod
    def do(self, scene) -> None:
        """Apply the operation."""

    @abstractmethod
    def undo(self, scene) -> None:
        """Reverse the operation."""


class History:
    def __init__(self, scene) -> None:
        self.scene = scene
        self.undo_stack: list[Command] = []
        self.redo_stack: list[Command] = []

    def execute(self, cmd: Command) -> None:
        cmd.do(self.scene)
        self.undo_stack.append(cmd)
        self.redo_stack.clear()

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        cmd = self.undo_stack.pop()
        cmd.undo(self.scene)
        self.redo_stack.append(cmd)
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        cmd = self.redo_stack.pop()
        cmd.do(self.scene)
        self.undo_stack.append(cmd)
        return True

    def clear(self) -> None:
        self.undo_stack.clear()
        self.redo_stack.clear()


# ---- Concrete commands ------------------------------------------------------

class AddEdgeCommand(Command):
    """Add a single edge. The mesh always welds and dedups, so a coincident
    edge is reused rather than duplicated; ``_owned`` records whether this
    command actually created the edge, so undo only removes an edge it owns
    (never the pre-existing one it merged into)."""

    def __init__(self, a: QVector3D, b: QVector3D, merge: bool = True) -> None:
        self.a = QVector3D(a)
        self.b = QVector3D(b)
        self.merge = merge  # kept for API parity; the mesh never duplicates
        self.edge: Optional[Edge] = None
        self._owned = False

    def do(self, scene) -> None:
        m = scene.mesh
        if self._owned and self.edge is not None:
            m.relink_edge(self.edge)  # redo of an edge this command owns
            scene.version += 1
            return
        v0 = m.vertex_at(self.a)
        v1 = m.vertex_at(self.b)
        pre = m.find_edge(v0, v1) if (v0 is not None and v1 is not None) else None
        if pre is not None:
            # The mesh already has this edge — merged no-op. Own nothing, so
            # ``self.edge`` stays None and undo leaves the pre-existing edge.
            scene.version += 1
            return
        self.edge = m.add_edge(self.a, self.b)
        self._owned = True
        scene.version += 1

    def undo(self, scene) -> None:
        if not self._owned or self.edge is None:
            return
        scene.mesh.remove_edge(self.edge)
        scene.selection.discard(self.edge)
        scene.version += 1


class DeleteEdgesCommand(Command):
    """Erase edges (resolved by endpoint position). A face can't outlive a
    bounding edge — SketchUp erases an edge and its faces go with it — so every
    face that used a deleted edge on its *outer* boundary is removed too (its
    other edges stay, now free). Hole edges are left alone."""

    def __init__(self, edges: Iterable, cascade_faces: bool = True) -> None:
        self._endpoints = [(QVector3D(e.a), QVector3D(e.b)) for e in edges]
        self.cascade_faces = cascade_faces
        self.removed_edges: list[Edge] = []
        self.removed_faces: list[Face] = []

    def do(self, scene) -> None:
        m = scene.mesh
        self.removed_edges = []
        for a, b in self._endpoints:
            v0 = m.vertex_at(a)
            v1 = m.vertex_at(b)
            edge = m.find_edge(v0, v1) if (v0 is not None and v1 is not None) else None
            if edge is not None:
                self.removed_edges.append(edge)

        if self.cascade_faces:
            gone = {frozenset((_key(a), _key(b))) for a, b in self._endpoints}
            self.removed_faces = [
                f for f in m.faces if set(_loop_edges(f.vertices)) & gone
            ]
            for f in self.removed_faces:
                m.remove_face(f)
                scene.selection.discard(f)

        for edge in self.removed_edges:
            m.remove_edge(edge)
            scene.selection.discard(edge)
        scene.version += 1

    def undo(self, scene) -> None:
        m = scene.mesh
        for edge in self.removed_edges:
            m.relink_edge(edge)
        for face in self.removed_faces:
            m.relink_face(face)
        self.removed_faces = []
        scene.version += 1


class AddFaceCommand(Command):
    """Add a face, dividing any coplanar face it lands strictly inside.

    When the new loop falls wholly within an existing face, that mother face
    gains a hole so it no longer overlaps the new face. The hole is recorded so
    undo removes exactly the loop this command punched.
    """

    def __init__(
        self,
        vertices: Iterable[QVector3D],
        auto: bool = True,
        holes: Optional[Iterable[Iterable[QVector3D]]] = None,
    ) -> None:
        self.vertices = [QVector3D(v) for v in vertices]
        self.preset_holes = (
            [[QVector3D(v) for v in loop] for loop in holes] if holes else None
        )
        self.auto = auto
        self.face: Optional[Face] = None
        # (face_that_gained_a_hole, the vertex loop punched) for undo.
        self._punches: list[tuple[Face, list]] = []
        self._subdiv_mother: Optional[Face] = None
        self._subdiv_remainder: Optional[Face] = None

    def do(self, scene) -> None:
        m = scene.mesh
        if self.face is None:
            holes = (
                [list(loop) for loop in self.preset_holes]
                if self.preset_holes else None
            )
            self.face = m.add_face(self.vertices, holes)
        else:
            m.relink_face(self.face)  # redo

        if not self.auto:
            scene.version += 1
            return

        # Direction A: the new face falls inside an existing mother → the mother
        # gains the new loop as a hole.
        mother = find_containing_face(m.faces, self.face.vertices, exclude=self.face)
        if mother is not None:
            loop = m.add_hole(mother, self.face.vertices)
            self._punches.append((mother, loop))

        # Direction B: the new face encloses existing smaller faces → it gains
        # each of them as a hole.
        for other in list(m.faces):
            if other is self.face:
                continue
            if loop_inside_face(self.face, other.vertices):
                loop = m.add_hole(self.face, other.vertices)
                self._punches.append((self.face, loop))

        # Direction C: drawn against an existing face's boundary (corner / edge
        # rectangle) → carve a connected sub-region. Only when no hole applied.
        if not self._punches:
            for other in list(m.faces):
                if other is self.face:
                    continue
                remainder = subtract_loop_from_face(other, self.face.vertices)
                if remainder is None:
                    continue
                rem_holes: list[list[QVector3D]] = []
                straddle = False
                for hole in other.holes:
                    if loop_inside_face(Face([Vertex(v) for v in remainder]), hole):
                        rem_holes.append([QVector3D(v) for v in hole])
                    else:
                        straddle = True
                        break
                if straddle:
                    continue
                m.remove_face(other)
                rem_face = m.add_face(remainder, rem_holes)
                self._subdiv_mother = other
                self._subdiv_remainder = rem_face
                break

        scene.version += 1

    def undo(self, scene) -> None:
        m = scene.mesh
        if self._subdiv_mother is not None:
            if self._subdiv_remainder is not None:
                m.remove_face(self._subdiv_remainder)
            m.relink_face(self._subdiv_mother)
            self._subdiv_mother = None
            self._subdiv_remainder = None
        for face, loop in self._punches:
            m.remove_hole(face, loop)
        self._punches = []
        if self.face is not None:
            m.remove_face(self.face)
        scene.version += 1


class DeleteFaceCommand(Command):
    """Remove a face (resolved by its outer loop). Holes travel with it, so undo
    restores them via relink."""

    def __init__(self, face) -> None:
        self._loop = [QVector3D(v) for v in face.vertices]
        self.face: Optional[Face] = None

    def do(self, scene) -> None:
        self.face = _find_face_by_loop(scene.mesh, self._loop)
        if self.face is not None:
            scene.mesh.remove_face(self.face)
            scene.selection.discard(self.face)
        scene.version += 1

    def undo(self, scene) -> None:
        if self.face is not None:
            scene.mesh.relink_face(self.face)
        scene.version += 1


def translate_points(scene, keys: set, delta: QVector3D) -> None:
    """Move every shared vertex whose position key is in ``keys`` by ``delta``.

    Because vertices are shared, every edge and face referencing a moved vertex
    follows for free — the mechanic behind raising a ridge into a gable roof.
    Shared by :class:`MoveVerticesCommand` and the Push/Pull live preview.
    """
    moving = [v for v in scene.mesh.vertices if _key(v.position) in keys]
    for v in moving:
        scene.mesh.move_vertex(v, delta)
    scene.version += 1


class MoveVerticesCommand(Command):
    """Translate every shared vertex at a set of positions by ``delta``.

    A moved face may become non-planar, which the Newell normal and the
    triangulator already tolerate. Topology is not restructured (no merge when a
    vertex lands on another); that is a follow-up.
    """

    def __init__(self, positions: Iterable[QVector3D], delta: QVector3D) -> None:
        self.src = [QVector3D(p) for p in positions]
        self.delta = QVector3D(delta)

    def do(self, scene) -> None:
        translate_points(scene, {_key(p) for p in self.src}, self.delta)

    def undo(self, scene) -> None:
        translate_points(scene, {_key(p + self.delta) for p in self.src}, -self.delta)


class PruneOrphanEdgesCommand(Command):
    """Remove edges incident to ``vertices`` that, once the rest of a compound
    has run, border no face — the dangling lines left where push/pull carved
    geometry away. Computed at ``do`` time so it reflects the real post-carve
    scene; ``undo`` re-links the swept edges."""

    def __init__(self, vertices: Iterable[QVector3D]) -> None:
        self.vertices = [QVector3D(v) for v in vertices]
        self.removed: list[Edge] = []

    def do(self, scene) -> None:
        self.removed = orphaned_edges_at(scene.edges, scene.faces, self.vertices)
        for edge in self.removed:
            scene.mesh.remove_edge(edge)
            scene.selection.discard(edge)
        if self.removed:
            scene.version += 1

    def undo(self, scene) -> None:
        for edge in self.removed:
            scene.mesh.relink_edge(edge)
        if self.removed:
            scene.version += 1
        self.removed = []


class CompoundCommand(Command):
    """A list of commands executed and reverted as one atomic step."""

    def __init__(self, commands: Iterable[Command]) -> None:
        self.commands: list[Command] = list(commands)

    def do(self, scene) -> None:
        for cmd in self.commands:
            cmd.do(scene)

    def undo(self, scene) -> None:
        for cmd in reversed(self.commands):
            cmd.undo(scene)
