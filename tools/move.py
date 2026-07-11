# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Move tool: drag geometry (and everything connected to it) to a new spot.

UX (SketchUp-like):
- If there's a selection, Move acts on it; otherwise the first click grabs the
  edge / face under the cursor.
- First click sets the grab point (a snapped handle). Move the mouse — snap,
  axis lock (arrow keys) and the VCB all work, same as drawing — and the real
  geometry deforms live as you drag (faces tilt, walls stretch).
- Second click drops at the current offset. Typing a length + Enter moves
  exactly that far along the current direction; ``X;Y;Z`` + Enter is a 3D delta.
- Esc (or switching tools) cancels and snaps the geometry back.

Move shifts *positions*: every point coincident with a grabbed vertex moves with
it, so connected faces deform instead of tearing — that's what lets you raise a
ridge edge into a gable roof.

The live preview mutates the scene directly (no history entry); the move only
lands on the undo stack on commit, and is reverted on cancel — so the undo
history stays clean while you still see the deformation as it happens.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.group import Group
from core.mesh import Edge, Face
from core.history import MoveGroupCommand, MoveVerticesCommand
from core.topology import _key
from tools.base import Tool, ToolContext



def _dedup(positions: list[QVector3D]) -> list[QVector3D]:
    seen = set()
    out = []
    for p in positions:
        k = _key(p)
        if k not in seen:
            seen.add(k)
            out.append(QVector3D(p))
    return out


def gather_targets(ctx: ToolContext):
    """What a transform tool acts on: ``(group, positions)``. A selected or
    hovered group transforms as a unit; otherwise unique positions from the
    selection or the entity under the cursor. Shared by Move and Rotate."""
    viewport = ctx.viewport
    entities = list(viewport.scene.selection)
    if not entities:
        group = viewport.pick_group(ctx.screen.x(), ctx.screen.y())
        if group is not None:
            return group, []
        edge = viewport.pick_edge(ctx.screen.x(), ctx.screen.y())
        if edge is not None:
            entities = [edge]
        else:
            face = viewport.pick_face(ctx.screen.x(), ctx.screen.y())
            if face is not None:
                entities = [face]
    for ent in entities:
        if isinstance(ent, Group):
            return ent, []
    positions: list[QVector3D] = []
    for ent in entities:
        if isinstance(ent, Edge):
            positions.extend([ent.a, ent.b])
        elif isinstance(ent, Face):
            positions.extend(ent.vertices)
    return None, _dedup(positions)


class MoveTool(Tool):
    name = "Move"
    shortcut = "M"
    vcb_label = "Distance"

    # Drag on a camera-facing vertical plane (not the ground) so pulling the
    # mouse up raises geometry — what lets you lift a roof ridge straight up.
    prefers_vertical_drag = True
    # Magnetically lock to the nearest world axis within this angle, projecting
    # onto the axis line. Keeps a move rigid and axis-aligned (a 3 m ridge stays
    # 3 m and level) without holding Shift; arrow-key locks still override it.
    magnetic_axis_deg = 15.0

    def __init__(self) -> None:
        # ``start_point`` drives the viewport's snap / axis-lock machinery, same
        # as the drawing tools. ``grab`` is the handle the delta is measured
        # from (identical to start_point, kept separate for clarity).
        self.start_point: QVector3D | None = None
        self.grab: QVector3D | None = None
        self.hover_point: QVector3D | None = None
        self.chain_first_point: QVector3D | None = None  # silence close-snap
        self._positions: list[QVector3D] = []  # unique positions to translate
        self._verts: list = []                 # resolved vertex objects (identity)
        self._group: Group | None = None       # set when moving a whole group
        self._preview_delta = QVector3D(0.0, 0.0, 0.0)  # currently applied live

    # ---- Lifecycle ----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        self._reset()

    def on_deactivate(self, viewport) -> None:
        self._revert_preview(viewport)
        self._reset()

    # ---- Spatial input ------------------------------------------------------
    def on_click(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        if self.start_point is None:
            group, positions = self._gather(ctx)
            if group is None and not positions:
                return  # nothing under the cursor / selected to move
            self.start_point = ctx.world
            self.grab = ctx.world
            self._group = group
            self._positions = positions
            # Resolve the grabbed positions to vertex OBJECTS once: the live
            # preview then moves these identities directly, so dragging through
            # (or onto) a coincident vertex never drags the innocent one along.
            mesh = viewport.scene.mesh
            self._verts = [v for v in (mesh.vertex_at(p) for p in positions)
                           if v is not None]
            return
        self._commit(viewport, ctx.world - self.grab)

    def on_hover(self, ctx: ToolContext) -> None:
        self.hover_point = ctx.world
        if self.grab is not None:
            self._apply_preview(ctx.viewport, ctx.world - self.grab)
        ctx.viewport.update()

    def on_value(self, viewport, value) -> bool:
        if self.start_point is None or self.grab is None:
            return False
        if isinstance(value, tuple):
            if len(value) != 3:
                return False  # 2-tuple is a rectangle's W×H, not a move delta
            delta = QVector3D(value[0], value[1], value[2])
        else:
            if self.hover_point is None:
                return False
            direction = self.hover_point - self.grab
            if direction.length() < 1e-9:
                return False
            delta = direction.normalized() * value
        self._commit(viewport, delta)
        return True

    def on_cancel(self, viewport) -> None:
        self._revert_preview(viewport)
        self._reset()
        viewport.update()

    # ---- Visual preview -----------------------------------------------------
    def rubber_band_lines(self):
        # The geometry deforms live, so the only extra cue is the move vector
        # from the grab point to the cursor (also carries the axis-lock colour).
        if self.grab is None or self.hover_point is None:
            return []
        return [(self.grab, self.hover_point)]

    def value_label(self):
        if self.grab is None or self.hover_point is None:
            return None
        delta = self.hover_point - self.grab
        mid = (self.grab + self.hover_point) * 0.5
        return (f"{delta.length():.2f} m", mid)

    # ---- Internals ----------------------------------------------------------
    def _gather(self, ctx: ToolContext):
        return gather_targets(ctx)

    def _shift(self, viewport, step: QVector3D) -> None:
        """Translate the live geometry by ``step`` — a whole group's mesh, or the
        grabbed vertex objects (identity-exact: apply and revert stay symmetric
        even when the drag crosses another vertex's position)."""
        if self._group is not None:
            for v in list(self._group.mesh.vertices):
                self._group.mesh.move_vertex(v, step)
        else:
            for v in self._verts:
                viewport.scene.mesh.move_vertex(v, step)
        viewport.scene.version += 1

    def _apply_preview(self, viewport, target_delta: QVector3D) -> None:
        """Live-deform the scene so the current offset is ``target_delta`` from
        the grab point, by translating the incremental step."""
        step = target_delta - self._preview_delta
        if step.length() < 1e-12:
            return
        self._shift(viewport, step)
        self._preview_delta = target_delta

    def _revert_preview(self, viewport) -> None:
        """Undo the live deformation, returning geometry to its grab-time spot."""
        if self._preview_delta.length() < 1e-12:
            return
        self._shift(viewport, -self._preview_delta)
        self._preview_delta = QVector3D(0.0, 0.0, 0.0)

    def _commit(self, viewport, delta: QVector3D) -> None:
        # Revert the live preview, then apply the move as one undoable command so
        # the history holds a single clean entry (and the geometry doesn't shift
        # by double the delta).
        self._revert_preview(viewport)
        if delta.length() > 1e-9:
            if self._group is not None:
                viewport.history.execute(MoveGroupCommand(self._group, delta))
            elif self._positions:
                viewport.history.execute(MoveVerticesCommand(self._positions, delta))
        self._reset()
        viewport.update()

    def _reset(self) -> None:
        self.start_point = None
        self.grab = None
        self.hover_point = None
        self._positions = []
        self._verts = []
        self._group = None
        self._preview_delta = QVector3D(0.0, 0.0, 0.0)
