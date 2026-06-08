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
    CompoundCommand,
    DeleteFaceCommand,
    MoveVerticesCommand,
    PruneOrphanEdgesCommand,
    translate_points,
)
from core.topology import (
    _key,
    classify_push_edge,
    extend_wall_edge,
    loop_inside_face,
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
        # A prism cap (every edge backs onto a perpendicular wall) previews by
        # live-deforming the real geometry — translating the cap along its
        # normal, walls following — which is clean in *both* directions. Other
        # pushes (free face, recess) keep the shaded overlay preview.
        self._prism_cap: bool = False
        self._anchor: QVector3D | None = None  # fixed centroid for measuring extrusion
        self._normal: QVector3D | None = None
        self._cap_positions: list[QVector3D] = []  # original cap vertices
        self._preview_delta = QVector3D(0.0, 0.0, 0.0)  # live translation applied
        # When a recess push reaches a parallel far face, this holds
        # ``(far_face, back_loop)`` so the preview shows a clean through-hole.
        self._through = None

    # ---- Lifecycle ----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        self._reset()

    def on_deactivate(self, viewport) -> None:
        self._revert_translate(viewport)
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
        if self._prism_cap:
            # Deform the real solid live: the cap slides along its normal and
            # the walls follow, so shrinking or growing the box stays clean in
            # both directions (no overlay, no leftover edges).
            self._apply_translate(viewport, self._normal * self.extrusion)
        elif self._attached and abs(self.extrusion) > 1e-6:
            # Recess (window/door): hide the flat inner face once it starts
            # moving, so the pocket shows. If the push has reached a parallel far
            # face, it's a through-hole — hide that face too so you see through.
            self._through = self._find_through_face(
                self.base_face, self.extrusion, viewport.scene.faces
            )
            if self._through is not None:
                viewport.set_suppressed_faces({self.base_face, self._through[0]})
            else:
                viewport.set_suppressed_faces({self.base_face})
        else:
            self._through = None
            viewport.set_suppressed_faces(set())
        viewport.update()

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
            self._preview_delta = QVector3D(0.0, 0.0, 0.0)
            # The shaded solid preview takes over from the hover shade now.
            # (A recess hides its flat inner face later, once it starts moving —
            # see on_hover — so it doesn't flash transparent before any drag.)
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
        self._revert_translate(viewport)
        viewport.set_hover(None)
        viewport.set_suppressed_faces(set())
        self._reset()
        viewport.update()

    # ---- Visual preview -----------------------------------------------------
    def rubber_band_lines(self):
        # Prism caps deform the real geometry live — no overlay wireframe.
        if not self.dragging or self.base_face is None or self._prism_cap:
            return []
        n = self.base_face.normal()
        d = self.extrusion
        base = self.base_face.vertices
        top = [v + n * d for v in base]
        segments = []
        # Top boundary
        count = len(top)
        for i in range(count):
            segments.append((top[i], top[(i + 1) % count]))
        # Vertical edges
        for v_base, v_top in zip(base, top):
            segments.append((v_base, v_top))
        return segments

    def preview_faces(self):
        """Shaded solid preview: the moved cap plus a side wall per base edge,
        so the box reads as a forming solid while dragging (SketchUp-style),
        not just a wireframe. Prism caps deform the real solid instead, so they
        return nothing here."""
        if (
            not self.dragging
            or self.base_face is None
            or self._prism_cap
            or abs(self.extrusion) < 1e-6
        ):
            return []
        base = self.base_face.vertices
        count = len(base)
        if self._through is not None:
            # Through-hole: only the tunnel walls, clamped to the far face, and
            # no cap — so the opening reads as see-through.
            _, back_loop = self._through
            return [
                Face([base[i], base[j], back_loop[j], back_loop[i]])
                for i in range(count)
                for j in ((i + 1) % count,)
            ]
        n = self.base_face.normal()
        d = self.extrusion
        top = [v + n * d for v in base]
        faces = [Face(list(top))]
        for i in range(count):
            j = (i + 1) % count
            faces.append(Face([base[i], base[j], top[j], top[i]]))
        return faces

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

    def _apply_translate(self, viewport, target_delta: QVector3D) -> None:
        """Live-deform the solid so the cap sits at ``target_delta`` from its
        start, by translating the incremental step (same mechanic as Move)."""
        step = target_delta - self._preview_delta
        if step.length() < 1e-12:
            return
        keys = {_key(p + self._preview_delta) for p in self._cap_positions}
        translate_points(viewport.scene, keys, step)
        self._preview_delta = target_delta

    def _revert_translate(self, viewport) -> None:
        """Undo the live deformation, returning the cap to its start position."""
        if self._preview_delta.length() < 1e-12:
            return
        keys = {_key(p + self._preview_delta) for p in self._cap_positions}
        translate_points(viewport.scene, keys, -self._preview_delta)
        self._preview_delta = QVector3D(0.0, 0.0, 0.0)

    def _commit(self, viewport) -> None:
        viewport.set_hover(None)  # the hovered face is about to be replaced
        viewport.set_suppressed_faces(set())
        # Undo any live prism-cap deformation so the real edit rebuilds the
        # topology cleanly from the original positions (same final geometry,
        # but on the undo stack as one atomic command).
        self._revert_translate(viewport)
        face = self.base_face
        if face is None or abs(self.extrusion) < 1e-6:
            self._reset()
            viewport.update()
            return

        # A prism cap push is exactly a translation of the cap along its normal:
        # the walls (and any host hole the cap sits in — a stacked block on a
        # cube) deform through their shared vertices. Commit it as that, the same
        # mechanic as the live preview. The extrude/extend path below would
        # instead patch in coplanar strips and leave the host hole un-extended,
        # which broke the next push on a neighbouring wall.
        if self._prism_cap:
            viewport.history.execute(
                MoveVerticesCommand(self._cap_positions, self._normal * self.extrusion)
            )
            self._reset()
            viewport.update()
            return

        normal = face.normal()
        d = self.extrusion
        base = face.vertices
        top = [v + normal * d for v in base]
        count = len(base)
        faces = viewport.scene.faces

        # Classify every side edge: coplanar neighbour (inner wall), a
        # perpendicular face it sits on (the solid's side wall — notch it), or
        # free (open extrusion). A face whose edges are all attached is part of
        # a surface/solid, so the push *moves* it (base consumed); a fully
        # free-standing face is extruded keeping its base as a cap.
        kinds = [
            classify_push_edge(face, base[i], base[(i + 1) % count], faces)
            for i in range(count)
        ]
        attached = all(kind != "free" for kind, _ in kinds)

        # Through-hole: pushing an embedded face (a window/door in a wall) far
        # enough to reach a parallel face on the far side of the solid punches
        # clean through — the opening appears on both faces, joined by tunnel
        # walls, instead of a blind recess.
        through = self._find_through_face(face, d, faces) if attached else None
        if through is not None:
            self._commit_through_hole(viewport, face, base, through)
            self._reset()
            viewport.update()
            return

        commands: list = []
        if attached:
            commands.append(DeleteFaceCommand(face))

        # Moved boundary + vertical edges, and the moved face (floor / top).
        for i in range(count):
            commands.append(AddEdgeCommand(top[i], top[(i + 1) % count]))
        for i in range(count):
            commands.append(AddEdgeCommand(base[i], top[i]))
        commands.append(AddFaceCommand(list(top), auto=not attached))

        # Sides: a perpendicular edge notches its wall when the strip falls
        # inside it (pushing in → a step / recess opening); otherwise a wall
        # quad is raised (inner wall, free extrusion, or pushing out).
        for i in range(count):
            j = (i + 1) % count
            a, b, b2, a2 = base[i], base[j], top[j], top[i]
            kind, neighbour = kinds[i]
            if attached and kind == "perp":
                # Extending a prism: the wall this edge sits on grows in its own
                # plane. Move the wall's shared edge up rather than stacking a
                # coplanar strip, so no seam is left at the old cap level.
                extended = extend_wall_edge(neighbour, a, b, a2, b2)
                if extended is not None:
                    commands.append(DeleteFaceCommand(neighbour))
                    # Carry over any opening (window / door hole) the wall had.
                    commands.append(AddFaceCommand(
                        extended, auto=False, holes=neighbour.holes or None
                    ))
                    en = len(extended)
                    for k in range(en):
                        commands.append(
                            AddEdgeCommand(extended[k], extended[(k + 1) % en])
                        )
                    continue  # wall extended — no separate strip, seam pruned
                remainder = subtract_loop_from_face(neighbour, [a, b, b2, a2])
                if remainder is not None:
                    commands.append(DeleteFaceCommand(neighbour))
                    commands.append(AddFaceCommand(remainder, auto=False))
                    # The notch puts a new vertex on the wall's side edge,
                    # splitting it; add the remainder's boundary edges so the
                    # surviving lower segment (e.g. the corner vertical up to
                    # the step) exists rather than being pruned with the rest.
                    rn = len(remainder)
                    for k in range(rn):
                        commands.append(
                            AddEdgeCommand(remainder[k], remainder[(k + 1) % rn])
                        )
                    continue  # notched open — no wall here
            commands.append(AddFaceCommand([a, b, b2, a2], auto=False))

        # Sweep up edges left dangling where the base used to be (e.g. a step's
        # old top edges at the corner that no longer border any face).
        if attached:
            commands.append(PruneOrphanEdgesCommand(list(base)))

        viewport.history.execute(CompoundCommand(commands))
        self._reset()
        viewport.update()

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

    def _commit_through_hole(self, viewport, face, base, through) -> None:
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
        # The front opening's edges now border the front hole + the tunnel; sweep
        # only anything genuinely left dangling.
        commands.append(PruneOrphanEdgesCommand(list(base)))
        viewport.history.execute(CompoundCommand(commands))

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
        self._preview_delta = QVector3D(0.0, 0.0, 0.0)
        self._through = None
