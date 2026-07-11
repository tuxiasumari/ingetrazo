# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Edit operations that turn drawn segments into history commands.

This is the bridge between the pure geometry planner in :mod:`core.topology`
and the undo/redo commands in :mod:`core.history`. Tools hand it the raw
segments the user drew; it returns a single reversible command that:

- splits existing edges the new segments cross (SketchUp-style auto-split),
- breaks each new segment at those crossings,
- welds coincident edges (via ``AddEdgeCommand``'s merge), and
- optionally auto-faces any planar cycle the new sub-edges close.

Segments are processed in order against a running simulation of the scene's
edge list, so a batch (e.g. a rectangle's four edges) splits correctly even
when later segments cross edges created by earlier ones.

Kept in its own module to avoid an import cycle: ``history`` imports
``topology``, so the command-building glue can't live in either of them.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from PySide6.QtGui import QVector3D

from core.geometry import Edge, Face
from core.history import (
    AddEdgeCommand,
    AddFaceCommand,
    Command,
    CompoundCommand,
    DeleteEdgesCommand,
    DeleteFaceCommand,
    SnapshotCompound,
)
from core.topology import (
    face_exists,
    find_chord_split,
    find_cycles_through,
    find_duplicate_edge,
    is_planar,
    plan_edge_split,
    split_edge_in_faces,
)

Segment = tuple[QVector3D, QVector3D]


def plan_edge_commands(
    scene,
    segments: Sequence[Segment],
    detect_faces: bool = True,
) -> list[Command]:
    """Build the ordered command list to add ``segments`` to ``scene``.

    Mirrors what each emitted command will do to the scene in a local
    ``simulated`` edge list, so planning and execution stay in lock-step.
    Returns a flat list (callers wrap it, possibly alongside their own
    commands such as a tool-managed face, in a single ``CompoundCommand``).
    """
    commands: list[Command] = []
    simulated: list[Edge] = list(scene.edges)
    faces_snapshot: list[Face] = list(scene.faces)

    for a, b in segments:
        new_segments, edge_cuts = plan_edge_split(simulated, a, b)

        # Replace each crossed existing edge with its two sub-edges.
        for edge, point in edge_cuts.items():
            # Splitting an edge, not erasing it — the sub-edges keep any face's
            # boundary intact, so don't cascade-delete faces here.
            commands.append(DeleteEdgesCommand([edge], cascade_faces=False))
            if edge in simulated:
                simulated.remove(edge)
            # Sub-edges inherit the split edge's curve/soft flags, so cutting a
            # circle leaves its pieces selectable as the same curve entity.
            e_soft = getattr(edge, "soft", False) or None
            e_curve = getattr(edge, "curve", None)
            for sa, sb in ((edge.a, point), (point, edge.b)):
                if find_duplicate_edge(simulated, sa, sb) is None:
                    commands.append(
                        AddEdgeCommand(sa, sb, soft=e_soft, curve=e_curve))
                    simulated.append(Edge(sa, sb))

            # Carry the split into faces sharing this edge — but not the face
            # the drawn segment chord-splits, which inserts the point itself.
            # This is what makes a gable wall gain the ridge apex (and fill its
            # triangular gap) when the ridge is later moved up. Gated on
            # ``detect_faces``: tools that manage their own faces (Rectangle, and
            # push/pull's prep edges) opt out of auto-topology, so they keep
            # their simple rectangular walls for the push machinery.
            if detect_faces:
                for f, new_verts in split_edge_in_faces(
                    faces_snapshot, edge.a, edge.b, point, skip_endpoints=(a, b)
                ):
                    commands.append(DeleteFaceCommand(f))
                    commands.append(
                        AddFaceCommand(new_verts, auto=False, holes=f.holes or None)
                    )
                    faces_snapshot[faces_snapshot.index(f)] = Face(
                        list(new_verts), [list(h) for h in f.holes]
                    )

        # Add the new segment's pieces (welding duplicates), auto-facing.
        for sa, sb in new_segments:
            already = find_duplicate_edge(simulated, sa, sb)
            # Always emit the add: AddEdgeCommand welds, so a duplicate is a
            # no-op, but face detection below must still run (drawing over an
            # existing edge can still close a new face).
            commands.append(AddEdgeCommand(sa, sb))
            if already is None:
                simulated.append(Edge(sa, sb))
            if detect_faces:
                _plan_faces(commands, faces_snapshot, simulated, sa, sb)

    return commands


def _plan_faces(commands, faces_snapshot, simulated, sa, sb) -> None:
    """Emit the face commands for a freshly added edge ``sa``–``sb``.

    Two paths, mutually exclusive:

    - *Chord split* — the edge divides an existing face: replace that mother
      face with its two halves (handles "draw a diagonal across a square → two
      triangles, each pushable on its own").
    - *Auto-face* — otherwise close any new minimal cycles the edge forms.
      ``find_cycles_through`` returns both sides, so a diagonal across a
      face-less square still faces both triangles.
    """
    chord = find_chord_split(faces_snapshot, sa, sb)
    if chord is not None:
        mother, loop_a, loop_b = chord
        commands.append(DeleteFaceCommand(mother))
        faces_snapshot.remove(mother)
        for loop in (loop_a, loop_b):
            if is_planar(loop) and not face_exists(faces_snapshot, loop):
                commands.append(AddFaceCommand(loop))
                faces_snapshot.append(Face(list(loop)))
        return

    for cycle in find_cycles_through(simulated, sa, sb):
        if is_planar(cycle) and not face_exists(faces_snapshot, cycle):
            commands.append(AddFaceCommand(cycle))
            faces_snapshot.append(Face(list(cycle)))


def _scene_has_curves(scene) -> bool:
    return any(getattr(e, "curve", None) is not None for e in scene.edges)


def _append_flat_curve_rebuild(scene, commands, points) -> None:
    """When straight edges land on a drawing plane that contains curves,
    append the planar-arrangement rebuild (the same deterministic pass the
    circle/arc tools run). The cycle-detection planner reasons in straight
    segments and cannot form the regions a curve crossing creates — a square
    drawn over a circle must split into three areas (SketchUp), not stack a
    duplicate face over the lens. Whole-flat drawings get the full rebuild;
    3D scenes get the SCOPED per-plane one (only when that plane carries curve
    edges, so straight-only 3D drawing keeps its proven path). Dangling edges
    survive the rebuild (spur pruning only affects face tracing), so
    half-drawn chains are safe."""
    from core.history import RebuildPlanarFacesCommand, RebuildPlaneFacesCommand

    if not _scene_has_curves(scene):
        return
    if any(isinstance(c, (RebuildPlanarFacesCommand, RebuildPlaneFacesCommand))
           for c in commands):
        return
    from core.arrangement import coplanar_plane

    verts = [v.position for v in scene.mesh.vertices] + list(points)
    if coplanar_plane(verts) is not None:
        commands.append(RebuildPlanarFacesCommand())
        return
    plane = coplanar_plane(list(points))
    if plane is None:
        return
    origin, normal = plane
    tol = 1e-4

    def on_plane(p):
        return abs(QVector3D.dotProduct(p - origin, normal)) < tol

    if any(e.curve is not None and on_plane(e.a) and on_plane(e.b)
           for e in scene.mesh.edges):
        commands.append(RebuildPlaneFacesCommand(origin, normal))


def build_add_edge(scene, a: QVector3D, b: QVector3D, detect_faces: bool = True) -> Command:
    """Single-segment convenience: one drawn edge → one atomic command."""
    commands = plan_edge_commands(scene, [(a, b)], detect_faces=detect_faces)
    _append_flat_curve_rebuild(scene, commands, [a, b])
    # When curves exist, even a single added edge can break a circle into
    # contours (a tangent line landing on a curve vertex splits it in SketchUp),
    # so it must go through SnapshotCompound, which runs the contour re-split —
    # and whose undo restores the reunited curve.
    if len(commands) == 1 and not _scene_has_curves(scene):
        return commands[0]
    # Splits/welds/hole-punches don't compose into a clean per-op inverse — undo
    # via one snapshot so it reverses exactly (no orphan edges/vertices left).
    return SnapshotCompound(commands)


def build_add_edges(
    scene,
    segments: Sequence[Segment],
    detect_faces: bool = True,
    extra: Iterable[Command] = (),
) -> Command:
    """Batch convenience: many drawn edges (+ optional ``extra`` commands such
    as a tool-managed face) → one atomic command."""
    commands = plan_edge_commands(scene, segments, detect_faces=detect_faces)
    commands.extend(extra)
    _append_flat_curve_rebuild(
        scene, commands, [p for seg in segments for p in seg])
    if len(commands) == 1 and not _scene_has_curves(scene):
        return commands[0]
    return SnapshotCompound(commands)
