"""Undo / redo history using the command pattern.

Every mutation that should be reversible goes through a :class:`Command`
subclass. The viewport owns a :class:`History` that maintains the undo and
redo stacks. Tools call ``viewport.history.execute(...)`` rather than
mutating the scene directly.

Why a command stack and not snapshots? Snapshots scale poorly with large
scenes (every push/pull would copy the whole model). Commands store only
the delta, so memory cost stays proportional to the action.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Optional

from PySide6.QtGui import QVector3D

from core.geometry import Edge, Face
from core.topology import (
    _key,
    _loop_edges,
    find_containing_face,
    find_duplicate_edge,
    loop_inside_face,
    orphaned_edges_at,
    subtract_loop_from_face,
)


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
    """Add a single edge, welding to coincident geometry (SketchUp-style).

    With ``merge=True`` (the default), if an edge with the same endpoints
    already exists the command becomes a no-op instead of stacking a
    duplicate — so two rectangles sharing a border keep a single shared
    edge. ``do`` records in ``_added`` whether it actually appended, so
    ``undo`` only removes an edge *this* command created and never deletes
    the pre-existing one it merged into. ``self.edge`` only ever holds the
    edge this command owns (``None`` after a merged no-op).
    """

    def __init__(self, a: QVector3D, b: QVector3D, merge: bool = True) -> None:
        self.a = a
        self.b = b
        self.merge = merge
        self.edge: Optional[Edge] = None
        self._added = False

    def do(self, scene) -> None:
        if self.merge and find_duplicate_edge(scene.edges, self.a, self.b) is not None:
            self._added = False
            return
        if self.edge is None:
            self.edge = Edge(self.a, self.b)
        scene.edges.append(self.edge)
        self._added = True
        scene.version += 1

    def undo(self, scene) -> None:
        if not self._added or self.edge is None:
            return
        try:
            scene.edges.remove(self.edge)
        except ValueError:
            pass
        scene.selection.discard(self.edge)
        self._added = False
        scene.version += 1


class DeleteEdgesCommand(Command):
    """Erase edges. A face can't outlive a bounding edge — SketchUp erases an
    edge and its faces go with it — so every face that used a deleted edge on
    its outer boundary is removed too (its other edges stay, now free). Hole
    edges are left alone (deleting a window edge shouldn't drop the whole wall);
    that merge-back case is handled separately later."""

    def __init__(self, edges: Iterable[Edge], cascade_faces: bool = True) -> None:
        self.edges: list[Edge] = list(edges)
        # True (user erase): take down faces bounded by a deleted edge.
        # False (internal edge split): the edge is replaced by collinear
        # sub-edges, so its faces keep their boundary and must survive.
        self.cascade_faces = cascade_faces
        self.removed_faces: list[Face] = []

    def do(self, scene) -> None:
        edges_set = set(self.edges)
        scene.edges[:] = [e for e in scene.edges if e not in edges_set]
        for e in self.edges:
            scene.selection.discard(e)

        if not self.cascade_faces:
            scene.version += 1
            return

        gone = {frozenset((_key(e.a), _key(e.b))) for e in self.edges}
        self.removed_faces = [
            f for f in scene.faces if set(_loop_edges(f.vertices)) & gone
        ]
        if self.removed_faces:
            dead = set(self.removed_faces)
            scene.faces[:] = [f for f in scene.faces if f not in dead]
            for f in self.removed_faces:
                scene.selection.discard(f)
        scene.version += 1

    def undo(self, scene) -> None:
        for edge in self.edges:
            if edge not in scene.edges:
                scene.edges.append(edge)
        for face in self.removed_faces:
            if face not in scene.faces:
                scene.faces.append(face)
        self.removed_faces = []
        # The original selection is not restored — SketchUp behaves the same.
        scene.version += 1


class AddFaceCommand(Command):
    """Add a face, dividing any coplanar face it lands strictly inside.

    When the new loop falls wholly within an existing face (e.g. a small
    rectangle drawn inside a larger one), that mother face gains a hole so it
    no longer overlaps the new face — SketchUp-style "draw inside a face and
    it splits". The hole is recorded so ``undo`` can remove exactly the loop
    this command punched, leaving the mother untouched.
    """

    def __init__(
        self,
        vertices: Iterable[QVector3D],
        auto: bool = True,
        holes: Optional[Iterable[Iterable[QVector3D]]] = None,
    ) -> None:
        self.vertices = [QVector3D(v) for v in vertices]
        # Pre-set holes (e.g. extending a holed wall via push/pull). Copied so
        # the new face owns its loops. Only meaningful with auto=False, where
        # the face's relationships are managed by the caller.
        self.preset_holes = (
            [[QVector3D(v) for v in loop] for loop in holes] if holes else None
        )
        # When False, skip the hole/subdivision auto-logic — used by tools
        # (push/pull) that build faces whose relationships they manage
        # explicitly and don't want re-interpreted.
        self.auto = auto
        self.face: Optional[Face] = None
        # Each punch is (face_that_gained_a_hole, the_exact_hole_loop_object),
        # kept for identity-based removal on undo. Covers both directions:
        # the new face landing inside an existing mother, *and* the new face
        # enclosing existing smaller faces.
        self._punches: list[tuple[Face, list]] = []
        # A face this command subdivided (drawn against its boundary) and the
        # remainder face that replaced it, for undo.
        self._subdiv_mother: Optional[Face] = None
        self._subdiv_remainder: Optional[Face] = None

    def do(self, scene) -> None:
        if self.face is None:
            holes = (
                [list(loop) for loop in self.preset_holes]
                if self.preset_holes else []
            )
            self.face = Face(list(self.vertices), holes)
        scene.faces.append(self.face)

        if not self.auto:
            scene.version += 1
            return

        # Direction A: the new face falls inside an existing mother → the
        # mother gains the new loop as a hole.
        mother = find_containing_face(scene.faces, self.face.vertices, exclude=self.face)
        if mother is not None:
            loop = list(self.face.vertices)
            mother.holes.append(loop)
            self._punches.append((mother, loop))

        # Direction B: the new face encloses existing smaller faces → the new
        # face gains each of them as a hole (order independence).
        for other in scene.faces:
            if other is self.face:
                continue
            if loop_inside_face(self.face, other.vertices):
                loop = list(other.vertices)
                self.face.holes.append(loop)
                self._punches.append((self.face, loop))

        # Direction C: the new face was drawn against an existing face's
        # boundary (a corner / edge rectangle), carving a connected sub-region
        # rather than a hole. Replace that mother with its remainder; the new
        # face stands on its own. Only when no hole relationship applied.
        if not self._punches:
            for other in list(scene.faces):
                if other is self.face:
                    continue
                remainder = subtract_loop_from_face(other, self.face.vertices)
                if remainder is None:
                    continue
                # Carry the mother's holes (e.g. a window already on the wall)
                # into the remainder. If a hole isn't wholly inside it — it would
                # straddle the cut or fall in the new sub-region — don't
                # subdivide this face; that's safer than a broken split.
                rem_holes: list[list[QVector3D]] = []
                straddle = False
                for hole in other.holes:
                    if loop_inside_face(Face(list(remainder)), hole):
                        rem_holes.append([QVector3D(v) for v in hole])
                    else:
                        straddle = True
                        break
                if straddle:
                    continue
                rem_face = Face(list(remainder), rem_holes)
                scene.faces.remove(other)
                scene.faces.append(rem_face)
                self._subdiv_mother = other
                self._subdiv_remainder = rem_face
                break

        scene.version += 1

    def undo(self, scene) -> None:
        if self._subdiv_mother is not None:
            if self._subdiv_remainder in scene.faces:
                scene.faces.remove(self._subdiv_remainder)
            if self._subdiv_mother not in scene.faces:
                scene.faces.append(self._subdiv_mother)
            self._subdiv_mother = None
            self._subdiv_remainder = None
        for face, loop in self._punches:
            try:
                face.holes.remove(loop)
            except ValueError:
                pass
        self._punches = []
        if self.face is not None:
            try:
                scene.faces.remove(self.face)
            except ValueError:
                pass
        scene.version += 1


class DeleteFaceCommand(Command):
    """Remove a face (e.g. a mother face being replaced by its chord-split
    halves). Holes the face carried travel with it, so undo restores them."""

    def __init__(self, face: Face) -> None:
        self.face = face

    def do(self, scene) -> None:
        try:
            scene.faces.remove(self.face)
        except ValueError:
            pass
        scene.selection.discard(self.face)
        scene.version += 1

    def undo(self, scene) -> None:
        if self.face not in scene.faces:
            scene.faces.append(self.face)
        scene.version += 1


def translate_points(scene, keys: set, delta: QVector3D) -> None:
    """Shift every edge endpoint / face vertex whose position key is in ``keys``
    by ``delta`` and bump the scene version.

    Shared by :class:`MoveVerticesCommand` and the Move tool's live preview, so
    the on-the-fly drag and the committed command move geometry identically.
    """
    for e in scene.edges:
        if _key(e.a) in keys:
            e.a = e.a + delta
        if _key(e.b) in keys:
            e.b = e.b + delta
    for f in scene.faces:
        f.vertices = [v + delta if _key(v) in keys else v for v in f.vertices]
        if f.holes:
            f.holes = [
                [v + delta if _key(v) in keys else v for v in loop]
                for loop in f.holes
            ]
    scene.version += 1


class MoveVerticesCommand(Command):
    """Translate every point at a set of positions by ``delta``.

    Move works on *positions*, not entities: every edge endpoint and face
    vertex (boundary or hole) coincident with one of ``positions`` shifts by
    the same delta. Because identity-equal entities store separate copies of a
    shared corner, moving them together is what keeps the mesh connected — drag
    a ridge edge up and both roof slopes and the gable ends deform with it.

    Topology is not restructured (no merge when a vertex lands on another); that
    is a follow-up. A moved face may become non-planar, which the Newell normal
    and the triangulator already tolerate.
    """

    def __init__(self, positions: Iterable[QVector3D], delta: QVector3D) -> None:
        self.src = [QVector3D(p) for p in positions]
        self.delta = QVector3D(delta)

    def do(self, scene) -> None:
        translate_points(scene, {_key(p) for p in self.src}, self.delta)

    def undo(self, scene) -> None:
        # After do(), the moved points sit at src + delta; match those to undo.
        translate_points(scene, {_key(p + self.delta) for p in self.src}, -self.delta)


class PruneOrphanEdgesCommand(Command):
    """Remove edges incident to ``vertices`` that, once the rest of a compound
    has run, border no face — the dangling lines left where push/pull carved
    geometry away. The set is computed at ``do`` time so it reflects the real
    post-carve scene; ``undo`` puts the swept edges back."""

    def __init__(self, vertices: Iterable[QVector3D]) -> None:
        self.vertices = [QVector3D(v) for v in vertices]
        self.removed: list[Edge] = []

    def do(self, scene) -> None:
        self.removed = orphaned_edges_at(scene.edges, scene.faces, self.vertices)
        if not self.removed:
            return
        gone = set(self.removed)
        scene.edges[:] = [e for e in scene.edges if e not in gone]
        for e in self.removed:
            scene.selection.discard(e)
        scene.version += 1

    def undo(self, scene) -> None:
        for e in self.removed:
            if e not in scene.edges:
                scene.edges.append(e)
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
