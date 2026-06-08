"""Push/Pull tool: extrude a face along its normal.

UX (SketchUp-like):
- Hover a face; the cursor picks the front-most face under it.
- First click: lock onto that face and start a drag along its normal.
- Subsequent mouse motion slides the extrusion preview (wireframe of
  the future box) along the normal axis. Length is shown near the
  midpoint, same overlay as the line tool.
- Second click commits at the current distance.
- Typing a number + Enter (VCB) commits at exactly that distance,
  preserving the current direction's sign.
- Esc cancels without committing.

Commit creates:
- N new edges connecting each base vertex to the matching moved vertex.
- N new edges around the moved face boundary.
- 1 new moved face (the box top, or the floor of a recess).
- N new side faces (quads, one per base edge).

Additive vs subtractive:
- Pushing *out* (extrusion along the normal) leaves the base face in place —
  it becomes the box's "bottom".
- Pushing *in* (extrusion against the normal) is subtractive: the base face
  is removed. For a face drawn inside another (a coplanar surrounding face
  that gained a hole when the inner face was created), removing the base
  leaves that hole open as the mouth of a recess / pocket. For a standalone
  face it simply shortens the solid. Through-holes (pushing clear out the
  far side) are not detected yet — that's a follow-up.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.geometry import Face
from core.history import (
    AddEdgeCommand,
    AddFaceCommand,
    DeleteFaceCommand,
    PruneOrphanEdgesCommand,
    SnapshotMutation,
    run_stitch,
    translate_points,
)
from core.topology import (
    _key,
    classify_push_edge,
    extend_wall_edge,
    loop_inside_face,
    refine_loop_with_points,
    subtract_loop_from_face,
)
from tools.base import Tool, ToolContext


class PushPullTool(Tool):
    name = "Push / Pull"
    shortcut = "U"
    uses_snap = False  # picks a face to extrude; no snap markers
    vcb_label = "Distance"
    # Preview lines in the normal edge colour, not the loose orange rubber band,
    # and depth-tested so the forming box hides its own back edges.
    wireframe_color = (0.13, 0.17, 0.23, 1.0)
    wireframe_depth_tested = True

    def __init__(self) -> None:
        self.hovered_face: Face | None = None
        self.base_face: Face | None = None
        self.extrusion: float = 0.0  # signed distance along normal
        self.dragging: bool = False
        # Whether the base face is embedded in a solid (its boundary edges are
        # shared), so an inner face pushed in is a recess (window/door pocket).
        self._attached: bool = False
        # A prism cap is every edge backing onto a perpendicular wall — the push
        # is a clean translation of the cap (walls following through shared
        # vertices) rather than a strip-by-strip extrude.
        self._prism_cap: bool = False
        self._anchor: QVector3D | None = None  # fixed centroid for measuring extrusion
        self._normal: QVector3D | None = None
        self._cap_positions: list[QVector3D] = []  # original cap vertices
        # The live preview applies the real commit each frame and reverts it from
        # this snapshot before the next, so the drag shows the stitched result.
        self._preview_snapshot: dict | None = None

    # ---- Lifecycle ----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        self._reset()

    def on_deactivate(self, viewport) -> None:
        self._revert_preview(viewport)
        viewport.set_hover(None)
        viewport.set_suppressed_faces(set())
        self._reset()

    # ---- Spatial input ------------------------------------------------------
    def on_hover(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        if not self.dragging:
            self.hovered_face = viewport.pick_face(ctx.screen.x(), ctx.screen.y())
            # Shade the face that would be pushed, SketchUp-style, so the target
            # is unmistakable before clicking.
            viewport.set_hover(self.hovered_face)
            return

        if self.base_face is None or self._anchor is None:
            return
        projected = viewport._project_to_lock_line(
            self._anchor, self._normal, ctx.screen.x(), ctx.screen.y()
        )
        self.extrusion = QVector3D.dotProduct(projected - self._anchor, self._normal)
        # Apply the real commit to the mesh as a live preview (reverting last
        # frame's first), so the forming solid renders exactly as it will commit.
        self._apply_preview(viewport)

    def on_click(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        if not self.dragging:
            face = self.hovered_face
            if face is None:
                return
            self.base_face = face
            self.extrusion = 0.0
            self.dragging = True
            self._anchor = face.centroid()
            self._normal = face.normal()
            self._attached, self._prism_cap = self._classify_base(viewport.scene)
            self._cap_positions = [QVector3D(v) for v in face.vertices]
            self._preview_snapshot = None
            # The live preview takes over from the hover shade now.
            viewport.set_hover(None)
            viewport.update()
            return

        # Already dragging — second click commits.
        if abs(self.extrusion) < 1e-6:
            # No-op extrusion; just stay in drag mode so the user can keep going.
            return
        self._commit(viewport)

    def on_value(self, viewport, value) -> bool:
        # Push/Pull only takes a single extrusion length; 3D deltas don't apply.
        if isinstance(value, tuple):
            return False
        if not self.dragging or self.base_face is None or value <= 0.0:
            return False
        # Keep the sign the user has been dragging toward; default to +normal.
        sign = -1.0 if self.extrusion < 0.0 else 1.0
        self.extrusion = sign * value
        self._commit(viewport)
        return True

    def on_cancel(self, viewport) -> None:
        self._revert_preview(viewport)
        viewport.set_hover(None)
        viewport.set_suppressed_faces(set())
        self._reset()
        viewport.update()

    # ---- Visual preview -----------------------------------------------------
    # The drag preview is the real (stitched) geometry applied each frame, so no
    # overlay wireframe or shaded faces are needed.
    def rubber_band_lines(self):
        return []

    def preview_faces(self):
        return []

    def _apply_preview(self, viewport) -> None:
        """Show the forming solid by applying the real commit to the mesh, after
        reverting the previous frame's preview."""
        self._revert_preview(viewport)
        if self.base_face is None or abs(self.extrusion) < 1e-6:
            viewport.update()
            return
        self._preview_snapshot = viewport.scene.mesh.capture_state()
        self._mutate(viewport.scene)
        viewport.scene.version += 1
        viewport.update()

    def _revert_preview(self, viewport) -> None:
        if self._preview_snapshot is not None:
            viewport.scene.mesh.restore_state(self._preview_snapshot)
            self._preview_snapshot = None
            viewport.scene.version += 1

    def value_label(self):
        """Return ``(text, midpoint_world)`` for the floating distance label.
        Uses the anchor captured at drag start, which stays fixed even while a
        prism cap deforms the real geometry live."""
        if not self.dragging or self._anchor is None:
            return None
        midpoint = self._anchor + self._normal * (self.extrusion * 0.5)
        return (f"{abs(self.extrusion):.2f} m", midpoint)

    # ---- Internals ----------------------------------------------------------
    def _classify_base(self, scene) -> tuple[bool, bool]:
        """Classify the base face for previewing.

        Returns ``(attached, prism_cap)``:
        - ``attached`` — every edge is shared (embedded in a surface/solid), so
          an inner face pushed in is a recess (hidden so the pocket shows).
        - ``prism_cap`` — every edge backs onto a *perpendicular* wall, so the
          push is a clean prism extend/shrink and can be previewed by live
          translation (walls deform with the cap, clean both ways).
        """
        base = self.base_face.vertices
        n = len(base)
        faces = scene.faces
        kinds = [
            classify_push_edge(self.base_face, base[i], base[(i + 1) % n], faces)
            for i in range(n)
        ]
        attached = all(kind != "free" for kind, _ in kinds)
        prism_cap = bool(kinds) and all(kind == "perp" for kind, _ in kinds)
        return attached, prism_cap

    def _commit(self, viewport) -> None:
        viewport.set_hover(None)  # the hovered face is about to be replaced
        viewport.set_suppressed_faces(set())
        self._revert_preview(viewport)  # drop the live preview; redo it for real
        if self.base_face is None or abs(self.extrusion) < 1e-6:
            self._reset()
            viewport.update()
            return
        # One snapshot wraps the edit *and* the watertight stitch: undo is exact,
        # and it is the identical mutation the live preview just showed.
        viewport.history.execute(SnapshotMutation(self._mutate))
        self._reset()
        viewport.update()

    def _mutate(self, scene) -> None:
        """Apply the push to ``scene``'s mesh and stitch it watertight. Shared by
        the committed edit (wrapped in :class:`SnapshotMutation`) and the live
        preview, so the drag renders exactly what will commit."""
        face = self.base_face
        d = self.extrusion

        # A prism cap is exactly a translation of the cap along its normal; the
        # walls follow through shared vertices. Then repair connectivity
        # (T-junctions, collinear) — no coplanar-merge, as nothing new is added.
        if self._prism_cap:
            normal = self._normal if self._normal is not None else face.normal()
            translate_points(scene, {_key(p) for p in self._cap_positions},
                             normal * d)
            seed = list(self._cap_positions) + [p + normal * d
                                                for p in self._cap_positions]
            run_stitch(scene.mesh, {_key(p) for p in seed}, set())
            return

        normal = face.normal()
        allverts = [v.position for v in scene.mesh.vertices]
        # Split the base loop at any existing vertex sitting on one of its edges
        # (a T-junction where the host wall ends and an earlier overhang's floor
        # begins) so each sub-edge is carried by one face and classifies right.
        base = refine_loop_with_points(face.vertices, allverts)
        top = [v + normal * d for v in base]
        # A face with holes (an offset ring — the wall footprint) extrudes its
        # holes too: the inner walls rise and the cap keeps the opening, so a
        # ring lifts as walls-with-thickness, not the whole footprint.
        base_holes = [refine_loop_with_points(h, allverts) for h in face.holes]
        top_holes = [[v + normal * d for v in h] for h in base_holes]
        count = len(base)
        faces = scene.faces
        kinds = [
            classify_push_edge(face, base[i], base[(i + 1) % count], faces)
            for i in range(count)
        ]
        attached = all(kind != "free" for kind, _ in kinds)
        through = self._find_through_face(face, d, faces) if attached else None

        before = set(scene.mesh.faces)
        if through is not None:
            commands = self._through_commands(face, base, through)
        else:
            commands = self._extrude_commands(
                face, base, top, base_holes, top_holes, count, kinds, attached
            )
        for cmd in commands:
            cmd.do(scene)
        new_faces = set(scene.mesh.faces) - before
        seed = list(base) + list(top)
        for hb, ht in zip(base_holes, top_holes):
            seed += list(hb) + list(ht)
        run_stitch(scene.mesh, {_key(p) for p in seed}, new_faces)

    def _extrude_commands(self, face, base, top, base_holes, top_holes,
                          count, kinds, attached) -> list:
        """Build the commands that extrude ``face`` to ``top``: a consumed/kept
        base, the moved cap (carrying any holes), a wall per side, and an inner
        wall per hole edge."""
        commands: list = []
        if attached:
            commands.append(DeleteFaceCommand(face))  # base consumed into the solid
        for i in range(count):
            commands.append(AddEdgeCommand(top[i], top[(i + 1) % count]))
        for i in range(count):
            commands.append(AddEdgeCommand(base[i], top[i]))
        commands.append(AddFaceCommand(
            list(top), auto=not attached,
            holes=[list(th) for th in top_holes] or None))

        # Inner walls: each hole edge raises a wall to the moved cap's opening.
        for hb, ht in zip(base_holes, top_holes):
            hn = len(hb)
            for i in range(hn):
                commands.append(AddEdgeCommand(ht[i], ht[(i + 1) % hn]))
            for i in range(hn):
                commands.append(AddEdgeCommand(hb[i], ht[i]))
            for i in range(hn):
                j = (i + 1) % hn
                commands.append(AddFaceCommand([hb[i], hb[j], ht[j], ht[i]], auto=False))

        for i in range(count):
            j = (i + 1) % count
            a, b, b2, a2 = base[i], base[j], top[j], top[i]
            kind, neighbour = kinds[i]
            if attached and kind == "perp":
                # The wall this edge sits on grows in its own plane: move its
                # shared edge rather than stack a coplanar strip (no seam left).
                extended = extend_wall_edge(neighbour, a, b, a2, b2)
                if extended is not None:
                    commands.append(DeleteFaceCommand(neighbour))
                    commands.append(AddFaceCommand(
                        extended, auto=False, holes=neighbour.holes or None))
                    en = len(extended)
                    for k in range(en):
                        commands.append(
                            AddEdgeCommand(extended[k], extended[(k + 1) % en]))
                    continue
                remainder = subtract_loop_from_face(neighbour, [a, b, b2, a2])
                if remainder is not None:
                    commands.append(DeleteFaceCommand(neighbour))
                    commands.append(AddFaceCommand(remainder, auto=False))
                    rn = len(remainder)
                    for k in range(rn):
                        commands.append(
                            AddEdgeCommand(remainder[k], remainder[(k + 1) % rn]))
                    continue
            commands.append(AddFaceCommand([a, b, b2, a2], auto=False))

        if attached:
            commands.append(PruneOrphanEdgesCommand(list(base)))
        return commands

    # ---- Through-hole -------------------------------------------------------
    def _find_through_face(self, face, d: float, faces):
        """If pushing ``face`` by ``d`` along its normal reaches a face parallel
        to it on the far side of the solid (the opposite wall), return
        ``(far_face, back_loop)`` — the face to punch and the opening projected
        onto it. Otherwise ``None`` (a blind recess)."""
        fn = face.normal().normalized()
        push = fn if d > 0 else -fn
        origin = face.centroid()
        best = None
        for g in faces:
            if g is face:
                continue
            if abs(QVector3D.dotProduct(fn, g.normal().normalized())) < 0.999:
                continue  # not parallel
            dist = QVector3D.dotProduct(g.centroid() - origin, push)
            if dist <= 1e-4 or dist > abs(d) + 1e-4:
                continue  # coplanar / behind / farther than the push reaches
            back_loop = [v + push * dist for v in face.vertices]
            if loop_inside_face(g, back_loop):
                if best is None or dist < best[0]:
                    best = (dist, g, back_loop)
        if best is None:
            return None
        return best[1], best[2]

    def _through_commands(self, face, base, through) -> list:
        """Build the commands for a through-hole: punch the far face, join it to
        the front opening with a tunnel, and sweep the dangling base edges."""
        far_face, back_loop = through
        count = len(base)
        commands: list = [
            DeleteFaceCommand(face),       # remove the pushed cap (window pane)
            DeleteFaceCommand(far_face),   # re-add the far face with a new hole
            AddFaceCommand(
                list(far_face.vertices), auto=False,
                holes=[list(h) for h in far_face.holes] + [list(back_loop)],
            ),
        ]
        for i in range(count):             # back opening boundary
            commands.append(AddEdgeCommand(back_loop[i], back_loop[(i + 1) % count]))
        for i in range(count):             # tunnel verticals
            commands.append(AddEdgeCommand(base[i], back_loop[i]))
        for i in range(count):             # tunnel walls
            j = (i + 1) % count
            commands.append(AddFaceCommand(
                [base[i], base[j], back_loop[j], back_loop[i]], auto=False))
        commands.append(PruneOrphanEdgesCommand(list(base)))
        return commands

    def _reset(self) -> None:
        self.hovered_face = None
        self.base_face = None
        self.extrusion = 0.0
        self.dragging = False
        self._attached = False
        self._prism_cap = False
        self._anchor = None
        self._normal = None
        self._cap_positions = []
        self._preview_snapshot = None
