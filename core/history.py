# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
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

from core.group import Group
from core.mesh import Edge, Face, Mesh, Vertex
from core.topology import (
    _key,
    _loop_edges,
    find_containing_face,
    fold_nonplanar_faces,
    heal_overlapping_faces,
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
    """Undo/redo stacks. ``execute`` is TRANSACTIONAL: if a command throws
    mid-mutation, the mesh is restored to its pre-command state, the failure
    is logged (stderr + ``error_log`` file), and nothing lands on the undo
    stack — a failed operation must be a no-op, never a half-committed mess
    (an aas.igz-style aborted draw left a quarter circle, an unsplit face and
    a duplicated edge behind, with the traceback swallowed by the Qt event
    loop). Same fail-safe doctrine as the BIM push guard, one level up."""

    #: Where failed-command tracebacks are appended (project-local, so the
    #: user can just send the file when reporting a bug).
    error_log = "ingetrazo-errors.log"

    def __init__(self, scene) -> None:
        self.scene = scene
        self.undo_stack: list[Command] = []
        self.redo_stack: list[Command] = []
        #: Message describing the last rolled-back failure (UI may flash it).
        self.last_error: Optional[str] = None

    def execute(self, cmd: Command) -> None:
        snapshot = self.scene.mesh.capture_state()
        try:
            cmd.do(self.scene)
        except Exception as exc:
            self.scene.mesh.restore_state(snapshot)
            self.scene.selection.clear()
            self.scene.version += 1
            self.last_error = f"{type(cmd).__name__}: {exc}"
            self._log_failure(cmd, exc)
            return
        self.last_error = None
        self.undo_stack.append(cmd)
        self.redo_stack.clear()

    def _log_failure(self, cmd: Command, exc: Exception) -> None:
        import datetime
        import sys
        import traceback

        text = (f"[{datetime.datetime.now().isoformat(timespec='seconds')}] "
                f"command {type(cmd).__name__} failed and was rolled back:\n"
                f"{''.join(traceback.format_exception(exc))}\n")
        print(text, file=sys.stderr)
        try:
            with open(self.error_log, "a", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass

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

    def __init__(self, a: QVector3D, b: QVector3D, merge: bool = True,
                 soft: bool | None = None, curve: int | None = None) -> None:
        self.a = QVector3D(a)
        self.b = QVector3D(b)
        self.merge = merge  # kept for API parity; the mesh never duplicates
        # Optional flags stamped on the resulting edge — used when a split
        # replaces a curve/soft edge with sub-edges, so the pieces inherit.
        self.soft = soft
        self.curve = curve
        self.edge: Optional[Edge] = None
        self._owned = False

    def _stamp(self, edge) -> None:
        if self.soft is not None:
            edge.soft = self.soft
        if self.curve is not None:
            edge.curve = self.curve

    def do(self, scene) -> None:
        m = scene.mesh
        if self._owned and self.edge is not None:
            m.relink_edge(self.edge)  # redo of an edge this command owns
            self._stamp(self.edge)
            scene.version += 1
            return
        v0 = m.vertex_at(self.a)
        v1 = m.vertex_at(self.b)
        pre = m.find_edge(v0, v1) if (v0 is not None and v1 is not None) else None
        if pre is not None:
            # The mesh already has this edge — merged no-op. Own nothing, so
            # ``self.edge`` stays None and undo leaves the pre-existing edge.
            self._stamp(pre)
            scene.version += 1
            return
        self.edge = m.add_edge(self.a, self.b)
        self._owned = True
        self._stamp(self.edge)
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


class EraseSelectionCommand(Command):
    """Erase selected edges and faces, SketchUp-style.

    An edge that divides two *coplanar* faces is dissolved and the faces merge
    back into one (rubbing out a face's split line reunites it). Any other erased
    edge takes the faces it bounds with it (a non-planar pair can't become one
    face). Undo restores an identity-preserving snapshot — the merge restructures
    connectivity too much for a clean per-edge inverse."""

    def __init__(self, edges: Iterable, faces: Iterable = ()) -> None:
        self._edge_endpoints = [(QVector3D(e.a), QVector3D(e.b)) for e in edges]
        self._face_loops = [[QVector3D(v) for v in f.vertices] for f in faces]
        self.snapshot: Optional[dict] = None

    def do(self, scene) -> None:
        m = scene.mesh
        self.snapshot = m.capture_state()
        for loop in self._face_loops:
            f = _find_face_by_loop(m, loop)
            if f is not None:
                m.remove_face(f)
                scene.selection.discard(f)
        for a, b in self._edge_endpoints:
            v0 = m.vertex_at(a)
            v1 = m.vertex_at(b)
            edge = m.find_edge(v0, v1) if (v0 is not None and v1 is not None) else None
            if edge is None:
                continue
            faces = list(edge.faces)
            merged = None
            if len(faces) == 2 and QVector3D.dotProduct(
                faces[0].normal(), faces[1].normal()
            ) > 0.999:
                merged = m.dissolve_coplanar_region(faces)  # reunite the split
            if merged is None:
                # Cascade: the edge and every face bounding it (a face can't
                # outlive a bounding edge) go away.
                ekey = frozenset((_key(edge.v0.position), _key(edge.v1.position)))
                for f in [g for g in list(m.faces)
                          if ekey in set(_loop_edges(g.vertices))]:
                    m.remove_face(f)
                    scene.selection.discard(f)
                m.remove_edge(edge)
            scene.selection.discard(edge)
        # Deleting a curved surface takes its hidden seams with it: a soft edge
        # left bordering no face is a dangling curve segment (a cylinder side's
        # vertical seam) — prune it so no stray vertical lines remain. Hard
        # orphan edges stay (an erased flat face keeps its outline, SketchUp).
        for e in list(m.edges):
            if e.soft and not e.faces:
                m.remove_edge(e)
        # A merge can leave the big enclosing face overlapping its subdivisions;
        # drop any such redundant mother (covered by the snapshot undo above).
        for f in heal_overlapping_faces(m):
            scene.selection.discard(f)
        scene.version += 1

    def undo(self, scene) -> None:
        if self.snapshot is not None:
            scene.mesh.restore_state(self.snapshot)
            scene.version += 1


class TagCurveCommand(Command):
    """Mark the edges of a drawn circle/arc loop as one curve (shared id), so
    selecting a segment selects the whole curve. Runs as the last step of the
    draw so the id is captured in the enclosing snapshot (redo keeps it); undo is
    a no-op because that snapshot restores the pre-draw state."""

    def __init__(self, loop_points, closed: bool = True) -> None:
        self.pts = [QVector3D(p) for p in loop_points]
        self.closed = closed

    def do(self, scene) -> None:
        scene.mesh.tag_curve(self.pts, self.closed)

    def undo(self, scene) -> None:
        pass


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
                rem_face.attrs = dict(other.attrs)  # carved mother continues
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


class SetFaceColorCommand(Command):
    """Paint a set of faces with an RGB colour (or clear it with ``None``),
    stored in each face's ``attrs["color"]`` — the first user-facing use of the
    generic per-region attrs (A.3), so the colour rides through push/pull and
    the plane rebuild. Painting changes no topology, so a direct attrs swap
    inverts it exactly — no snapshot needed. Faces are held by reference (the
    paint click resolves them live); undo restores each face's prior colour."""

    def __init__(self, faces, color) -> None:
        self._faces = list(faces)
        self._color = list(color) if color is not None else None
        self._old: Optional[list] = None  # captured on first do

    def do(self, scene) -> None:
        if self._old is None:
            self._old = [f.attrs.get("color") for f in self._faces]
        for f in self._faces:
            if self._color is None:
                f.attrs.pop("color", None)
            else:
                f.attrs["color"] = list(self._color)
        scene.version += 1

    def undo(self, scene) -> None:
        for f, old in zip(self._faces, self._old or []):
            if old is None:
                f.attrs.pop("color", None)
            else:
                f.attrs["color"] = list(old)
        scene.version += 1


class AddDimensionCommand(Command):
    """Add a static dimension annotation to ``scene.dimensions``."""

    def __init__(self, dimension) -> None:
        self.dimension = dimension

    def do(self, scene) -> None:
        scene.dimensions.append(self.dimension)
        scene.version += 1

    def undo(self, scene) -> None:
        if self.dimension in scene.dimensions:
            scene.dimensions.remove(self.dimension)
        scene.version += 1


class DeleteDimensionsCommand(Command):
    """Remove a set of dimensions from ``scene.dimensions``."""

    def __init__(self, dimensions) -> None:
        self._dims = list(dimensions)
        self._restore: list[tuple[int, object]] = []

    def do(self, scene) -> None:
        self._restore = [(scene.dimensions.index(d), d)
                         for d in self._dims if d in scene.dimensions]
        for d in self._dims:
            if d in scene.dimensions:
                scene.dimensions.remove(d)
            scene.selection.discard(d)
        scene.version += 1

    def undo(self, scene) -> None:
        for i, d in sorted(self._restore):
            scene.dimensions.insert(i, d)
        scene.version += 1


class AddGuideCommand(Command):
    """Add a construction guide (Tape Measure) to ``scene.guides``."""

    def __init__(self, guide) -> None:
        self.guide = guide

    def do(self, scene) -> None:
        scene.guides.append(self.guide)
        scene.version += 1

    def undo(self, scene) -> None:
        if self.guide in scene.guides:
            scene.guides.remove(self.guide)
        scene.version += 1


class DeleteGuidesCommand(Command):
    """Remove construction guides (the Eraser, or Edit ▸ Delete Guides)."""

    def __init__(self, guides) -> None:
        self._guides = list(guides)
        self._restore: list[tuple[int, object]] = []

    def do(self, scene) -> None:
        self._restore = [(scene.guides.index(g), g)
                         for g in self._guides if g in scene.guides]
        for g in self._guides:
            if g in scene.guides:
                scene.guides.remove(g)
        scene.version += 1

    def undo(self, scene) -> None:
        for i, g in sorted(self._restore):
            scene.guides.insert(i, g)
        scene.version += 1


class AddGeoPathCommand(Command):
    """Add a traced georef path to ``scene.geo_paths`` (Track G)."""

    def __init__(self, path) -> None:
        self.path = path

    def do(self, scene) -> None:
        scene.geo_paths.append(self.path)
        scene.version += 1

    def undo(self, scene) -> None:
        if self.path in scene.geo_paths:
            scene.geo_paths.remove(self.path)
        scene.selection.discard(self.path)
        scene.version += 1


class DeleteGeoPathsCommand(Command):
    """Remove georef paths from ``scene.geo_paths``."""

    def __init__(self, paths) -> None:
        self._paths = list(paths)
        self._restore: list[tuple[int, object]] = []

    def do(self, scene) -> None:
        self._restore = [(scene.geo_paths.index(p), p)
                         for p in self._paths if p in scene.geo_paths]
        for p in self._paths:
            if p in scene.geo_paths:
                scene.geo_paths.remove(p)
            scene.selection.discard(p)
        scene.version += 1

    def undo(self, scene) -> None:
        for i, p in sorted(self._restore):
            scene.geo_paths.insert(i, p)
        scene.version += 1


class ToggleGeoPathClosedCommand(Command):
    """Flip open ↔ closed (loop) on a set of georef paths."""

    def __init__(self, paths) -> None:
        self._paths = list(paths)

    def _flip(self, scene) -> None:
        for p in self._paths:
            p.closed = not p.closed
        scene.version += 1

    do = _flip
    undo = _flip


class SetGeoPathSurfaceCommand(Command):
    """Set the terrain-surface mode (None/"flat"/"draped") on georef paths.

    A surface implies a closed polygon, so this also closes the path; undo
    restores the prior mode and closed flag. Built triangles are recomputed
    outside (they depend on the DEM), so they're just cleared here.
    """

    def __init__(self, paths, mode) -> None:
        self.mode = mode
        self._paths = list(paths)
        self._prev: list = []

    def do(self, scene) -> None:
        self._prev = [(p, p.surface, p.closed) for p in self._paths]
        for p in self._paths:
            p.surface = self.mode
            if self.mode:
                p.closed = True
            p._surface_tris = None
        scene.version += 1

    def undo(self, scene) -> None:
        for p, surface, closed in self._prev:
            p.surface = surface
            p.closed = closed
            p._surface_tris = None
        scene.version += 1


class MoveGeoPathNodeCommand(Command):
    """Move one node of a georef path to a new position (undoable)."""

    def __init__(self, path, index, new_point) -> None:
        from PySide6.QtGui import QVector3D
        self.path = path
        self.index = index
        self._new = QVector3D(new_point)
        self._old = None

    def do(self, scene) -> None:
        from PySide6.QtGui import QVector3D
        self._old = QVector3D(self.path.points[self.index])
        self.path.points[self.index] = QVector3D(self._new)
        scene.version += 1

    def undo(self, scene) -> None:
        from PySide6.QtGui import QVector3D
        self.path.points[self.index] = QVector3D(self._old)
        scene.version += 1


class SetFaceTextureCommand(Command):
    """Apply an image texture (``{"path","sw","sh"}``) to a set of faces, or
    clear it with ``None`` — stored in each face's ``attrs["texture"]`` (rides
    the rebuild like the colour). Topology-free, so the attrs swap inverts it."""

    def __init__(self, faces, texture) -> None:
        self._faces = list(faces)
        self._tex = dict(texture) if texture is not None else None
        self._old: Optional[list] = None

    def do(self, scene) -> None:
        if self._old is None:
            self._old = [f.attrs.get("texture") for f in self._faces]
        for f in self._faces:
            if self._tex is None:
                f.attrs.pop("texture", None)
            else:
                f.attrs["texture"] = dict(self._tex)
        scene.version += 1

    def undo(self, scene) -> None:
        for f, old in zip(self._faces, self._old or []):
            if old is None:
                f.attrs.pop("texture", None)
            else:
                f.attrs["texture"] = dict(old)
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
    """Translate every shared vertex at a set of positions by ``delta``, then
    **autofold**: any face the move warped out of its plane is split into
    planar pieces along fold edges (SketchUp behaviour — a quad with a lifted
    corner becomes two triangles, not a fake bent "face").

    Undo/redo restore identity-preserving snapshots. The old "cheap" inverse
    translation resolved vertices *by position*, so when a moved corner landed
    exactly on another vertex (an endpoint snap does this constantly) the undo
    dragged the innocent coincident vertex along too, warping the drawing. The
    before-snapshot was already being captured every time — restoring it is the
    exact inverse for every case (plain move, fold, landed-on-vertex)."""

    def __init__(self, positions: Iterable[QVector3D], delta: QVector3D) -> None:
        self.src = [QVector3D(p) for p in positions]
        self.delta = QVector3D(delta)
        self._before: Optional[dict] = None
        self._after: Optional[dict] = None

    def do(self, scene) -> None:
        if self._after is not None:  # redo
            scene.mesh.restore_state(self._after)
            scene.version += 1
            return
        self._before = scene.mesh.capture_state()
        translate_points(scene, {_key(p) for p in self.src}, self.delta)
        fold_nonplanar_faces(scene.mesh)
        self._after = scene.mesh.capture_state()

    def undo(self, scene) -> None:
        if self._before is not None:
            scene.mesh.restore_state(self._before)
            scene.version += 1


def rotation_matrix(center: QVector3D, axis: QVector3D, degrees: float):
    """Rigid rotation of ``degrees`` around ``axis`` through ``center``."""
    from PySide6.QtGui import QMatrix4x4
    m = QMatrix4x4()
    m.translate(center)
    m.rotate(degrees, axis.normalized())
    m.translate(-center)
    return m


def rotate_points(scene, keys: set, matrix) -> None:
    """Rotate every shared vertex whose position key is in ``keys`` by the
    rigid ``matrix`` (the rotation twin of :func:`translate_points`). Shared
    by :class:`RotateVerticesCommand` and the Rotate tool's live preview."""
    moving = [v for v in scene.mesh.vertices if _key(v.position) in keys]
    for v in moving:
        scene.mesh.move_vertex(v, matrix.map(v.position) - v.position)
    scene.version += 1


class RotateVerticesCommand(Command):
    """Rotate every shared vertex at a set of positions around ``axis``
    through ``center`` by ``degrees``, then **autofold** (a partial rotation
    can warp attached faces out of plane, same as Move). Undo/redo restore
    identity-preserving snapshots — the exact mirror of
    :class:`MoveVerticesCommand`."""

    def __init__(self, positions: Iterable[QVector3D], center: QVector3D,
                 axis: QVector3D, degrees: float) -> None:
        self.src = [QVector3D(p) for p in positions]
        self.center = QVector3D(center)
        self.axis = QVector3D(axis)
        self.degrees = degrees
        self._before: Optional[dict] = None
        self._after: Optional[dict] = None

    def do(self, scene) -> None:
        if self._after is not None:  # redo
            scene.mesh.restore_state(self._after)
            scene.version += 1
            return
        self._before = scene.mesh.capture_state()
        m = rotation_matrix(self.center, self.axis, self.degrees)
        rotate_points(scene, {_key(p) for p in self.src}, m)
        fold_nonplanar_faces(scene.mesh)
        self._after = scene.mesh.capture_state()

    def undo(self, scene) -> None:
        if self._before is not None:
            scene.mesh.restore_state(self._before)
            scene.version += 1


class RotateGroupCommand(Command):
    """Rotate a whole group's isolated mesh (rigid — nothing folds). Snapshot
    undo/redo on the group's own mesh."""

    def __init__(self, group, center: QVector3D, axis: QVector3D,
                 degrees: float) -> None:
        self.group = group
        self.center = QVector3D(center)
        self.axis = QVector3D(axis)
        self.degrees = degrees
        self._before: Optional[dict] = None
        self._after: Optional[dict] = None

    def do(self, scene) -> None:
        gmesh = self.group.mesh
        if self._after is not None:  # redo
            gmesh.restore_state(self._after)
            scene.version += 1
            return
        self._before = gmesh.capture_state()
        m = rotation_matrix(self.center, self.axis, self.degrees)
        for v in list(gmesh.vertices):
            gmesh.move_vertex(v, m.map(v.position) - v.position)
        self._after = gmesh.capture_state()
        scene.version += 1

    def undo(self, scene) -> None:
        if self._before is not None:
            self.group.mesh.restore_state(self._before)
            scene.version += 1


def scale_matrix(center: QVector3D, factor: float):
    """Uniform scale by ``factor`` about ``center``. A negative factor mirrors
    through the centre (SketchUp allows it)."""
    from PySide6.QtGui import QMatrix4x4
    m = QMatrix4x4()
    m.translate(center)
    m.scale(factor)
    m.translate(-center)
    return m


class ScaleVerticesCommand(Command):
    """Uniformly scale every shared vertex at a set of positions about
    ``center`` by ``factor``, then autofold (scaling a subset of connected
    geometry can warp attached faces). Snapshot undo/redo — the mirror of
    Move/RotateVerticesCommand."""

    def __init__(self, positions: Iterable[QVector3D], center: QVector3D,
                 factor: float) -> None:
        self.src = [QVector3D(p) for p in positions]
        self.center = QVector3D(center)
        self.factor = factor
        self._before: Optional[dict] = None
        self._after: Optional[dict] = None

    def do(self, scene) -> None:
        if self._after is not None:  # redo
            scene.mesh.restore_state(self._after)
            scene.version += 1
            return
        self._before = scene.mesh.capture_state()
        m = scale_matrix(self.center, self.factor)
        rotate_points(scene, {_key(p) for p in self.src}, m)  # generic mapper
        fold_nonplanar_faces(scene.mesh)
        self._after = scene.mesh.capture_state()

    def undo(self, scene) -> None:
        if self._before is not None:
            scene.mesh.restore_state(self._before)
            scene.version += 1


class ScaleGroupCommand(Command):
    """Uniformly scale a whole group's isolated mesh about ``center``.
    Snapshot undo/redo on the group's own mesh."""

    def __init__(self, group, center: QVector3D, factor: float) -> None:
        self.group = group
        self.center = QVector3D(center)
        self.factor = factor
        self._before: Optional[dict] = None
        self._after: Optional[dict] = None

    def do(self, scene) -> None:
        gmesh = self.group.mesh
        if self._after is not None:  # redo
            gmesh.restore_state(self._after)
            scene.version += 1
            return
        self._before = gmesh.capture_state()
        m = scale_matrix(self.center, self.factor)
        for v in list(gmesh.vertices):
            gmesh.move_vertex(v, m.map(v.position) - v.position)
        self._after = gmesh.capture_state()
        scene.version += 1

    def undo(self, scene) -> None:
        if self._before is not None:
            self.group.mesh.restore_state(self._before)
            scene.version += 1


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


class CoplanarMergeCommand(Command):
    """Dissolve coplanar seams left by push/pull, SketchUp-style.

    After a wall is pushed flush against an adjacent one, the shared edge borders
    two faces in the same plane and carries no silhouette — a phantom line. This
    command sweeps the edges incident to the operation's vertices and merges any
    such redundant pair into one face (the "L"), so the result reads as a clean
    solid. Seeded with the operation's vertices (not the whole model) so a
    *deliberately* drawn coplanar edge elsewhere is left alone.

    Each merge is recorded as ``(face_a, face_b, edge, merged_face)`` so undo
    restores the exact objects other commands may reference.
    """

    def __init__(self, seed_positions: Iterable[QVector3D]) -> None:
        self.seed = [QVector3D(p) for p in seed_positions]
        self.merges: list[tuple[Face, Face, Edge, Face]] = []

    def do(self, scene) -> None:
        mesh = scene.mesh
        seedkeys = {_key(p) for p in self.seed}
        progress = True
        while progress:
            progress = False
            for edge in list(mesh.edges):
                if len(edge.faces) != 2:
                    continue
                if (_key(edge.v0.position) not in seedkeys
                        and _key(edge.v1.position) not in seedkeys):
                    continue
                face_a, face_b = edge.faces[0], edge.faces[1]
                merged = mesh.dissolve_edge(edge)
                if merged is None:
                    continue
                self.merges.append((face_a, face_b, edge, merged))
                progress = True
                break  # mesh mutated — restart the scan
        if self.merges:
            scene.version += 1

    def undo(self, scene) -> None:
        mesh = scene.mesh
        for face_a, face_b, edge, merged in reversed(self.merges):
            mesh.remove_face(merged)
            mesh.relink_edge(edge)
            mesh.relink_face(face_a)
            mesh.relink_face(face_b)
        if self.merges:
            scene.version += 1
        self.merges = []


class StitchSolidCommand(Command):
    """Make a solid watertight again after push/pull, SketchUp-style.

    Repeated pushes leave three kinds of connectivity debris: edges that run past
    a vertex belonging to a neighbour (a *T-junction* — the two sides share a
    line but no edge, so the seam reads as a naked crack), redundant valence-2
    collinear vertices left by mismatched subdivision, and coplanar faces that
    should be one. This runs in three phases:

    1. **Resolve T-junctions** (global): split every edge at any vertex on its
       interior, so mismatched subdivisions share edges → no naked cracks.
    2. **Collapse collinear vertices** (global): drop spurious valence-2 points.
    3. **Coplanar-merge** (seeded): fuse coplanar faces around the operation
       into one — seeded so a deliberately drawn coplanar line elsewhere stays.

    Phases 1–2 only repair connectivity (no shape change), so they are safe to
    run model-wide. The splits, collapses and merges interact too tightly for a
    clean per-op inverse, so undo restores an identity-preserving snapshot taken
    before the pass — robust, and it keeps the surrounding delta commands' object
    references valid (this command runs last in the push/pull compound).
    """

    def __init__(self, seed_positions: Iterable[QVector3D]) -> None:
        self.seed = [QVector3D(p) for p in seed_positions]
        self.snapshot: Optional[dict] = None

    def do(self, scene) -> None:
        self.snapshot = scene.mesh.capture_state()
        run_stitch(scene.mesh, {_key(p) for p in self.seed})
        scene.version += 1

    def undo(self, scene) -> None:
        if self.snapshot is not None:
            scene.mesh.restore_state(self.snapshot)
            scene.version += 1


def run_stitch(mesh, seedkeys: set, new_faces: Optional[set] = None,
               coplanar_merge: bool = True, dedupe: bool = True) -> None:
    """Three-phase watertight cleanup (no undo bookkeeping — the caller snapshots).
    See :class:`StitchSolidCommand` for the rationale of each phase.

    ``new_faces`` (when given) are the faces this operation created; phase 3 only
    fuses a coplanar component that contains one of them, so a seam a push just
    made is merged while a pre-existing coplanar split (a user's diagonal) is
    left intact. ``None`` means merge any seeded component (manual stitch).

    ``coplanar_merge=False`` runs only phases 0–2 (connectivity repair). Solid
    push/pull uses this: its seams are dissolved by the deterministic per-plane
    rebuild (:mod:`core.cap_rebuild`) instead of the winding-tolerant merge,
    which only remains for raw/open geometry where outwardness is undefined.

    ``dedupe=False`` skips the identical-cycle face dedupe of phase 0. The
    solid path's *first* stitch needs that: a flush-collapse sweep quad lands
    identical to the face it must annihilate *with*, and the per-plane rebuild
    is what decides whether the pair means "keep one" (a shared wall built
    twice — material on both sides) or "drop both" (an emptied region) — by
    parity, where the pair's two crossings cancel. Deduping it early restores
    the material reading and the collapse never classifies."""
    # Phase 0 — weld coincident vertices (a translated cap landing flush on the
    # ring it came from); merges duplicate edges, drops degenerated faces. Then
    # drop faces stacked on an identical cycle (a shared wall built twice).
    mesh.weld_coincident()
    if dedupe:
        mesh.dedupe_faces()
    # Phase 1 — resolve T-junctions (global).
    while True:
        split = False
        for e in list(mesh.edges):
            mid = mesh.interior_vertex_on(e)
            if mid is not None:
                mesh.split_edge_at(e, mid)
                split = True
                break
        if not split:
            break
    # Phase 2 — collapse redundant valence-2 collinear vertices (global).
    while True:
        collapsed = False
        for v in list(mesh.vertices):
            if mesh.collapsible_vertex(v):
                mesh.collapse_vertex(v)
                collapsed = True
                break
        if not collapsed:
            break
    if not coplanar_merge:
        return
    # Phase 3 — coplanar-merge, seeded to the operation. Fuses the whole coplanar
    # component (every shared edge, any number of faces) at once, but only when it
    # includes a face this operation created (so user diagonals survive).
    while True:
        merged = False
        ncache: dict = {}
        scache: dict = {}
        for f0 in list(mesh.faces):
            comp = _coplanar_component(mesh, f0, seedkeys, ncache, scache)
            if len(comp) < 2:
                continue
            if new_faces is not None and not (comp & new_faces):
                continue
            region = mesh.dissolve_coplanar_region(comp)
            if region is not None:
                if new_faces is not None:
                    new_faces -= comp
                    new_faces.add(region)
                merged = True
                break
        if not merged:
            break


def _coplanar_component(mesh, f0, seedkeys: set,
                        ncache: Optional[dict] = None,
                        scache: Optional[dict] = None) -> set:
    """Maximal set of coplanar, edge-connected faces that touch the operation's
    seed. Whether the component is actually merged is gated separately on it
    containing a face the operation created (see ``run_stitch``).

    An edge that also carries a *non-coplanar* face is a **crease** — a wall
    standing under the seam — and the component never crosses it: two roof
    slabs over a dividing wall stay two faces with a visible ridge,
    SketchUp-style, instead of fusing into one slab floating over the wall.

    ``ncache``/``scache`` memoise per-face normalized normals and seed tests
    within one (mutation-free) scan — Newell normals recomputed per comparison
    dominated the push drag preview."""
    if ncache is None:
        ncache = {}
    if scache is None:
        scache = {}

    def nrm(f):
        v = ncache.get(f)
        if v is None:
            v = f.normal().normalized()
            ncache[f] = v
        return v

    def seeded(f):
        v = scache.get(f)
        if v is None:
            v = any(_key(p) in seedkeys for p in f.vertices)
            scache[f] = v
        return v

    if not seeded(f0):
        return set()
    n0 = nrm(f0)
    comp = {f0}
    stack = [f0]
    while stack:
        f = stack.pop()
        for loop in (f.loop, *f.hole_loops):
            for a, b in zip(loop, loop[1:] + loop[:1]):
                e = mesh.find_edge(a, b)
                if e is None:
                    continue
                if any(abs(QVector3D.dotProduct(n0, nrm(h))) < 0.999
                       for h in e.faces):
                    continue  # crease: a perpendicular face holds this edge
                for g in e.faces:
                    if g in comp or not seeded(g):
                        continue
                    # Coplanar regardless of winding sign — a push/pull can leave
                    # a fragment wound the opposite way (a prism floor cap +Z vs a
                    # bump strip -Z); same surface, so it belongs to the region.
                    if abs(QVector3D.dotProduct(n0, g.normal())) > 0.999:
                        comp.add(g)
                        stack.append(g)
    return comp


class SnapshotMutation(Command):
    """Wrap an arbitrary mesh mutation with snapshot undo. The push/pull live
    preview applies the *same* mutation each drag frame (then reverts via its own
    snapshot), so the forming solid renders exactly as it will commit — clean,
    already stitched — instead of flashing the pre-stitch seams.

    Redo restores the captured *result* rather than re-running the mutation: the
    closure usually closes over tool state (``base_face`` etc.) that is reset
    right after commit, so re-running it on redo would crash — and re-running the
    deterministic plane rebuild on stale state would be wasteful besides.

    ``mesh`` (when given) is the mesh to snapshot instead of the scene's loose
    one — a push/pull aimed at a Group edits that group's isolated mesh."""

    def __init__(self, mutate, mesh: Optional[Mesh] = None) -> None:
        self.mutate = mutate
        self._mesh = mesh
        self.before: Optional[dict] = None
        self.after: Optional[dict] = None

    def _target(self, scene) -> Mesh:
        return self._mesh if self._mesh is not None else scene.mesh

    def do(self, scene) -> None:
        mesh = self._target(scene)
        if self.after is None:
            self.before = mesh.capture_state()
            self.mutate(scene)
            self.after = mesh.capture_state()
        else:
            mesh.restore_state(self.after)
        scene.version += 1

    def undo(self, scene) -> None:
        if self.before is not None:
            self._target(scene).restore_state(self.before)
            scene.version += 1


class SnapshotCompound(Command):
    """Run a list of sub-commands under one identity-preserving snapshot, undone
    by restoring it.

    The line-draw plan splits and welds edges (and punches holes in coplanar
    faces); the per-command inverses don't compose into a clean whole — undoing
    them piecemeal leaves orphan split edges and stray vertices behind. One
    snapshot of the entire edit reverses it exactly, and because identity is
    preserved, earlier history entries keep working across this undo."""

    def __init__(self, inner: Iterable[Command]) -> None:
        self.inner = list(inner)
        self.before: Optional[dict] = None
        self.after: Optional[dict] = None

    def do(self, scene) -> None:
        if self.after is None:
            # First run: snapshot before, apply the plan, clean up any coplanar
            # overlap it created (redundant nested holes / spurious mother), then
            # snapshot the result so undo/redo restore exactly.
            self.before = scene.mesh.capture_state()
            for cmd in self.inner:
                cmd.do(scene)
            for f in heal_overlapping_faces(scene.mesh):
                scene.selection.discard(f)
            # A draw that split a curve leaves it in separate contours — break
            # the curve ids there (SketchUp), before the snapshot so redo keeps it.
            scene.mesh.resplit_curves()
            self.after = scene.mesh.capture_state()
        else:
            # Redo: re-running the delta plan wouldn't reproduce the splits, so
            # restore the captured result directly.
            scene.mesh.restore_state(self.after)
        scene.version += 1

    def undo(self, scene) -> None:
        if self.before is not None:
            scene.mesh.restore_state(self.before)
            scene.version += 1


class MeshSnapshotCommand(Command):
    """Run a list of sub-commands plus the stitch pass under a single
    identity-preserving snapshot, undone by restoring that snapshot.

    Push/pull builds its edit as delta commands and then stitches the result
    watertight; the stitch's splits/merges restructure the very edges those
    commands own, so composing their individual undos leaves orphan edges. One
    snapshot of the whole push is exact and robust — and because it preserves
    object identity, *other* history entries (a drawn line, an earlier push) keep
    working across this undo."""

    def __init__(self, inner: Iterable[Command], stitch_seed: Iterable[QVector3D]) -> None:
        self.inner = list(inner)
        self.seed = [QVector3D(p) for p in stitch_seed]
        self.snapshot: Optional[dict] = None

    def do(self, scene) -> None:
        self.snapshot = scene.mesh.capture_state()
        before = set(self.snapshot["faces"])
        for cmd in self.inner:
            cmd.do(scene)
        new_faces = set(scene.mesh.faces) - before
        run_stitch(scene.mesh, {_key(p) for p in self.seed}, new_faces)
        scene.version += 1

    def undo(self, scene) -> None:
        if self.snapshot is not None:
            scene.mesh.restore_state(self.snapshot)
            scene.version += 1


class MakeGroupCommand(Command):
    """Encapsulate the selected faces and edges into a new Group with its own
    mesh, removing them from the loose mesh so they no longer weld to the rest.
    Snapshot undo (geometry crosses meshes — too tangled for a per-op inverse)."""

    def __init__(self, faces: Iterable[Face], edges: Iterable[Edge]) -> None:
        faces = list(faces)
        self._face_loops = [
            ([QVector3D(v) for v in f.vertices],
             [[QVector3D(v) for v in h] for h in f.holes])
            for f in faces
        ]
        self._edge_ends = [(QVector3D(e.a), QVector3D(e.b)) for e in edges]
        # Soft/curve flags must travel into the group's fresh mesh: without
        # them, grouping a smooth cylinder suddenly shows every facet seam
        # (soft edges hidden in the loose mesh, visible in the group) and its
        # rims stop selecting as whole curves.
        self._flagged: list = []

        def note(e):
            if e is not None and (getattr(e, "soft", False)
                                  or getattr(e, "curve", None) is not None):
                self._flagged.append(
                    (QVector3D(e.a), QVector3D(e.b), e.soft, e.curve))

        for f in faces:
            for lp in (f.loop, *f.hole_loops):
                n = len(lp)
                for i in range(n):
                    va, vb = lp[i], lp[(i + 1) % n]
                    note(next((k for k in va.edges if k.other(va) is vb),
                              None))
        for e in edges:
            note(e)
        self.snapshot: Optional[dict] = None
        self.group: Optional[Group] = None

    def do(self, scene) -> None:
        m = scene.mesh
        self.snapshot = m.capture_state()
        # The group is a fresh copy built from the captured positions.
        gmesh = Mesh()
        for loop, holes in self._face_loops:
            gmesh.add_face([QVector3D(p) for p in loop],
                           [[QVector3D(p) for p in h] for h in holes] or None)
        for a, b in self._edge_ends:
            gmesh.add_edge(QVector3D(a), QVector3D(b))
        for a, b, soft, curve in self._flagged:
            va, vb = gmesh.vertex_at(a), gmesh.vertex_at(b)
            e = (gmesh.find_edge(va, vb)
                 if va is not None and vb is not None else None)
            if e is not None:
                e.soft, e.curve = soft, curve
        self.group = Group(gmesh)
        # Remove the grouped geometry from the loose mesh.
        face_keysets = [frozenset(_key(p) for p in loop)
                        for loop, _ in self._face_loops]
        for f in list(m.faces):
            if frozenset(_key(p) for p in f.vertices) in face_keysets:
                m.remove_face(f)
                scene.selection.discard(f)
        grouped = {_key(p) for loop, holes in self._face_loops
                   for lst in (loop, *holes) for p in lst}
        sel_edges = {frozenset((_key(a), _key(b))) for a, b in self._edge_ends}
        for e in list(m.edges):
            ek = frozenset((_key(e.a), _key(e.b)))
            if not e.faces and (ek in sel_edges or
                                (_key(e.a) in grouped and _key(e.b) in grouped)):
                m.remove_edge(e)
                scene.selection.discard(e)
        scene.groups.append(self.group)
        scene.selection.clear()
        scene.selection.add(self.group)
        scene.version += 1

    def undo(self, scene) -> None:
        if self.group in scene.groups:
            scene.groups.remove(self.group)
        scene.selection.discard(self.group)
        scene.mesh.restore_state(self.snapshot)
        scene.version += 1


class ExplodeGroupCommand(Command):
    """Dissolve a group: merge its geometry back into the loose mesh (welding to
    whatever it touches). Snapshot undo restores the loose mesh and the group."""

    def __init__(self, group: Group) -> None:
        self.group = group
        self.snapshot: Optional[dict] = None
        self.index: Optional[int] = None

    def do(self, scene) -> None:
        m = scene.mesh
        self.snapshot = m.capture_state()
        self.index = scene.groups.index(self.group)
        for f in self.group.mesh.faces:
            m.add_face([QVector3D(v) for v in f.vertices],
                       [[QVector3D(v) for v in h] for h in f.holes] or None)
        for e in self.group.mesh.edges:
            v0, v1 = m.vertex_at(e.a), m.vertex_at(e.b)
            if v0 is None or v1 is None or m.find_edge(v0, v1) is None:
                m.add_edge(QVector3D(e.a), QVector3D(e.b))
        # Soft/curve flags travel back out of the group (the mirror of
        # MakeGroupCommand): an exploded cylinder must stay smooth and its
        # rims keep selecting as whole curves.
        for e in self.group.mesh.edges:
            if getattr(e, "soft", False) or getattr(e, "curve", None) is not None:
                v0, v1 = m.vertex_at(e.a), m.vertex_at(e.b)
                k = (m.find_edge(v0, v1)
                     if v0 is not None and v1 is not None else None)
                if k is not None:
                    k.soft, k.curve = e.soft, e.curve
        m.resplit_curves()
        scene.groups.remove(self.group)
        scene.selection.discard(self.group)
        scene.version += 1

    def undo(self, scene) -> None:
        scene.mesh.restore_state(self.snapshot)
        scene.groups.insert(self.index, self.group)
        scene.version += 1


class MoveGroupCommand(Command):
    """Translate a whole group by ``delta`` (every vertex of its mesh). Because
    the group is isolated, this never drags the rest of the model."""

    def __init__(self, group: Group, delta: QVector3D) -> None:
        self.group = group
        self.delta = QVector3D(delta)

    def _shift(self, scene, delta) -> None:
        for v in list(self.group.mesh.vertices):
            self.group.mesh.move_vertex(v, delta)
        scene.version += 1

    def do(self, scene) -> None:
        self._shift(scene, self.delta)

    def undo(self, scene) -> None:
        self._shift(scene, -self.delta)


class DeleteGroupCommand(Command):
    """Remove a whole group (and its geometry) from the scene; undo restores it
    at its original position in the list."""

    def __init__(self, group: Group) -> None:
        self.group = group
        self.index: Optional[int] = None

    def do(self, scene) -> None:
        self.index = scene.groups.index(self.group)
        scene.groups.remove(self.group)
        scene.selection.discard(self.group)
        scene.version += 1

    def undo(self, scene) -> None:
        scene.groups.insert(self.index, self.group)
        scene.version += 1


class HealOverlapsCommand(Command):
    """Remove redundant 'mother' faces left overlapping their own subdivisions
    (a draw/delete can leave the big enclosing face on top). Snapshot undo."""

    def __init__(self) -> None:
        self.snapshot: Optional[dict] = None
        self.healed = 0

    def do(self, scene) -> None:
        self.snapshot = scene.mesh.capture_state()
        # partial defaults to auto: the aggressive pass runs only on a flat plan.
        removed = heal_overlapping_faces(scene.mesh)
        self.healed = len(removed)
        for f in removed:
            scene.selection.discard(f)
        scene.version += 1

    def undo(self, scene) -> None:
        if self.snapshot is not None:
            scene.mesh.restore_state(self.snapshot)
            scene.version += 1


def _point_in_tri(p: QVector3D, t0: QVector3D, t1: QVector3D,
                  t2: QVector3D) -> bool:
    """Coplanar point-in-triangle via consistent cross-product orientation."""
    n = QVector3D.crossProduct(t1 - t0, t2 - t0)
    if n.length() < 1e-12:
        return False
    for a, b in ((t0, t1), (t1, t2), (t2, t0)):
        c = QVector3D.crossProduct(b - a, p - a)
        if QVector3D.dotProduct(c, n) < -1e-9:
            return False
    return True


class RebuildPlanarFacesCommand(Command):
    """Rebuild the minimal faces of a flat drawing from its edge graph (a planar
    arrangement) — the deterministic, from-scratch replacement for the heuristic
    heal. Splits every crossing/overlap, drops dangling spurs, and recomputes the
    rooms and their holes exactly. Only runs on a single-plane mesh; 3D is left
    untouched (coplanar nesting there is legitimate). Snapshot undo."""

    def __init__(self) -> None:
        self.snapshot: Optional[dict] = None
        self.rebuilt = 0
        self.flat = True

    def do(self, scene) -> None:
        from core.arrangement import coplanar_plane, planar_rebuild
        from core.topology import _point_on_seg_incl

        self.snapshot = scene.mesh.capture_state()
        mesh = scene.mesh
        if not mesh.edges:
            self.flat = False
            return
        # Every edge endpoint must share one plane; 3D models are left alone.
        verts = [v.position for v in mesh.vertices]
        plane = coplanar_plane(verts)
        if plane is None:
            self.flat = False
            return
        origin, normal = plane
        if mesh.faces:  # prefer a real face's normal for a stable orientation
            normal = mesh.faces[0].normal()
            origin = mesh.faces[0].vertices[0]
        segments = [(e.v0.position, e.v1.position) for e in mesh.edges]
        # Remember flagged edges and face attrs so the rebuild preserves them:
        # output edges are sub-segments of input ones (re-stamp by lie-on), and
        # output faces inherit attrs from the old face containing their interior.
        flagged = [(QVector3D(e.a), QVector3D(e.b), e.soft, e.curve)
                   for e in mesh.edges if e.soft or e.curve is not None]
        old_attrs = [([tuple(t) for t in f.triangulate()], dict(f.attrs))
                     for f in mesh.faces if f.attrs]
        edges, faces = planar_rebuild(segments, origin, normal)

        scene.selection.clear()
        mesh.clear()
        for a, b in edges:
            e = mesh.add_edge(a, b)
            for (fa, fb, soft, curve) in flagged:
                if _point_on_seg_incl(e.a, fa, fb) and _point_on_seg_incl(e.b, fa, fb):
                    e.soft, e.curve = soft, curve
                    break
        for outer, holes in faces:
            f = mesh.add_face(outer, holes or None)
            if f is None or not old_attrs:
                continue
            tris = f.triangulate()
            if not tris:
                continue
            probe = (tris[0][0] + tris[0][1] + tris[0][2]) / 3.0
            for old_tris, attrs in old_attrs:
                if any(_point_in_tri(probe, t0, t1, t2)
                       for t0, t1, t2 in old_tris):
                    f.attrs.update(attrs)
                    break
        mesh.resplit_curves()
        self.rebuilt = len(faces)
        scene.version += 1

    def undo(self, scene) -> None:
        if self.snapshot is not None:
            scene.mesh.restore_state(self.snapshot)
            scene.version += 1


class RebuildPlaneFacesCommand(Command):
    """Rebuild the faces of ONE plane of a (possibly 3D) mesh from that plane's
    edge subgraph — the per-plane cousin of :class:`RebuildPlanarFacesCommand`.

    Needed because the whole-mesh flat gate goes dark as soon as ANY 3D
    geometry exists in the scene: two circles drawn on the ground next to a
    solid stacked as full overlapping discs instead of splitting into three
    areas. This command recomputes the arrangement of just the drawing plane
    and leaves the rest of the model untouched.

    Semantics: a minimal region keeps a face only if its interior was covered
    by an existing on-plane face (the freshly drawn loop's face, added by the
    tool before this command, provides coverage for the new regions). Uncovered
    regions stay empty — no resurrecting faces the user deleted. Winding and
    attrs inherit from the covering face; edges are reused (the planner already
    split every crossing), so soft/curve flags survive. Snapshot undo."""

    _TOL = 1e-4

    def __init__(self, origin: QVector3D, normal: QVector3D) -> None:
        self.origin = QVector3D(origin)
        self.normal = QVector3D(normal).normalized()
        self.snapshot: Optional[dict] = None
        self.rebuilt = 0

    def _on_plane(self, p: QVector3D) -> bool:
        return abs(QVector3D.dotProduct(p - self.origin, self.normal)) < self._TOL

    def do(self, scene) -> None:
        from core.arrangement import planar_rebuild
        from core.triangulate import triangulate

        self.snapshot = scene.mesh.capture_state()
        mesh = scene.mesh
        plane_edges = [e for e in mesh.edges
                       if self._on_plane(e.a) and self._on_plane(e.b)]
        if not plane_edges:
            return
        plane_faces = [
            f for f in mesh.faces
            if all(self._on_plane(v.position) for v in f.loop)
            and all(self._on_plane(v.position) for h in f.hole_loops for v in h)
        ]
        old = [([tuple(t) for t in f.triangulate()], f.normal(), dict(f.attrs))
               for f in plane_faces]
        segments = [(QVector3D(e.a), QVector3D(e.b)) for e in plane_edges]
        _, regions = planar_rebuild(segments, self.origin, self.normal)

        def covering(outer, holes):
            tris = triangulate(outer, holes, self.normal)
            if not tris:
                return None
            probe = (tris[0][0] + tris[0][1] + tris[0][2]) / 3.0
            for old_tris, nrm, attrs in old:
                if any(_point_in_tri(probe, t0, t1, t2)
                       for t0, t1, t2 in old_tris):
                    return nrm, attrs
            return None

        keep = []
        for outer, holes in regions:
            cov = covering(outer, holes or [])
            if cov is None:
                continue                      # uncovered region: stays empty
            nrm, attrs = cov
            # Arrangement emits CCW around self.normal; match the old face.
            from core.triangulate import _newell
            if QVector3D.dotProduct(_newell(outer), nrm) < 0:
                outer = list(reversed(outer))
                holes = [list(reversed(h)) for h in (holes or [])]
            keep.append((outer, holes or None, attrs))
        for f in plane_faces:
            mesh.remove_face(f)
        for outer, holes, attrs in keep:
            f = mesh.add_face(outer, holes)
            if attrs:
                f.attrs.update(attrs)
        # AddFaceCommand re-creates a polygon's full-length side even when the
        # planner already split it at a crossing — a border-0 collinear
        # duplicate that fragments the curve contours at resplit (degree-3
        # vertices). The regions above were re-added from the fully noded
        # arrangement, so the duplicates now bound nothing: prune them.
        from core.topology import prune_collinear_orphan_edges

        prune_collinear_orphan_edges(mesh)
        mesh.resplit_curves()
        self.rebuilt = len(keep)
        scene.selection.clear()
        scene.version += 1

    def undo(self, scene) -> None:
        if self.snapshot is not None:
            scene.mesh.restore_state(self.snapshot)
            scene.version += 1


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
