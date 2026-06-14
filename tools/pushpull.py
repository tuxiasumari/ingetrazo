"""Push/Pull tool: extrude a face along its normal.

UX (SketchUp-like):
- Hover a face; the cursor picks the front-most face under it.
- First click: lock onto that face and start a drag along its normal.
- Subsequent mouse motion slides the extrusion preview (wireframe of
  the future box) along the normal axis. Length is shown near the
  midpoint, same overlay as the line tool.
- Second click commits at the current distance.
- Typing a number + Enter (VCB) commits at exactly that distance,
  preserving the current direction's sign; a negative value ("-2")
  reverses it, and unit suffixes ("30cm", "1500mm") are understood.
- **Ctrl** = push/pull a copy: the start face stays in place (a slab
  division) and the extrusion stacks as a new segment — how floors are
  stacked.
- **Double-click** repeats the last committed distance on the face under
  the cursor (with Ctrl, as a copy).
- Esc cancels without committing.

Commit pipeline (the deterministic root-fix, no case tree):
1. Orient the mesh outward (entry invariant; no-op on open sheets).
2. Build the *naive* extrusion: consumed/kept base, moved cap (with holes),
   one wall quad per boundary/hole edge — even where a quad lands on an
   existing face's plane.
3. Stitch connectivity (T-junctions, collinear vertices).
4. On a solid: deterministically rebuild every touched plane (seams, overlaps,
   cracks) from its edges via ``core.cap_rebuild``; on a flat sheet, fall back
   to the seeded coplanar merge (no outward side exists there).
5. Orient outward again — every committed solid upholds the invariant.

A bordered (embedded) base face is consumed either direction — pushing in
carves a recess / step / through-hole (the far face is punched when the push
reaches it); pushing out grows the solid. A free-standing face keeps its base
as the cap of the new box.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
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
from core.arrangement import _interior_point, _point_in_polygon, plane_basis
from core.orient import _ray_triangle, orient_outward
from core.cap_rebuild import (
    apply_rebuild,
    crack_planes,
    plane_key,
    prune_plane_debris,
    seam_planes,
)
from core.topology import (
    _key,
    _mesh_is_flat,
    classify_push_edge,
    heal_overlapping_faces,
    loop_inside_face,
    refine_loop_with_points,
)
from tools.base import Tool, ToolContext


def _project_loop_2d(loop, normal):
    u, v = plane_basis(normal)
    return [(QVector3D.dotProduct(p, u), QVector3D.dotProduct(p, v)) for p in loop]


def _loops_overlap_2d(a_xy, b_xy) -> bool:
    """Whether two loops projected to the same plane overlap laterally: a
    representative interior point of one inside the other, or any vertex
    strictly inside. Good for the push-clamp candidates (a far cap under the
    whole loop, a floor under a corner notch); exotic offset-cross overlaps
    with no contained vertex are not the clamp's concern. Holes are ignored."""
    if _point_in_polygon(_interior_point(a_xy), b_xy):
        return True
    if _point_in_polygon(_interior_point(b_xy), a_xy):
        return True
    return any(_point_in_polygon(p, b_xy) for p in a_xy) or \
        any(_point_in_polygon(p, a_xy) for p in b_xy)


class _GroupScene:
    """Scene facade aiming the push/pull machinery at a Group's isolated mesh.

    Mirrors the slice of :class:`core.scene.Scene` the commands, the stitch and
    the per-plane rebuild touch — ``mesh`` / ``faces`` / ``edges`` /
    ``selection`` / ``version`` — so the whole pipeline runs unchanged inside
    the group (its geometry is already in world coordinates). The version
    bump lands on the real scene so the viewport re-renders."""

    def __init__(self, scene, group) -> None:
        self._scene = scene
        self.mesh = group.mesh
        self.selection = scene.selection

    @property
    def faces(self):
        return self.mesh.faces

    @property
    def edges(self):
        return self.mesh.edges

    @property
    def version(self):
        return self._scene.version

    @version.setter
    def version(self, value):
        self._scene.version = value


def _swept_by_push(neighbour: Face, push_normal: QVector3D) -> bool:
    """Whether ``neighbour`` is *swept* by a push along ``push_normal`` — it has
    an edge parallel to the push, so translating its shared edge slides the wall
    along its own length (a prism wall getting taller). A cap perpendicular to
    the prism axis (a triangular floor/top) has no such edge, so pushing one of
    *its* edges out would shear it instead — that case must add a coplanar strip
    (a box bump), not translate/extend."""
    n = push_normal.normalized()
    verts = neighbour.vertices
    m = len(verts)
    for i in range(m):
        e = verts[(i + 1) % m] - verts[i]
        if e.length() > 1e-9 and abs(QVector3D.dotProduct(e.normalized(), n)) > 0.999:
            return True
    return False


# The smallest extrusion that still moves geometry: anything below the mesh's
# weld resolution (1e-4, ~0.1 mm) would weld the top loop onto the base loop
# vertex-by-vertex and degenerate the wall quads. Treated as a no-op push.
_MIN_EXTRUDE = 2e-4


class PushPullTool(Tool):
    name = "Push / Pull"
    shortcut = "U"
    uses_snap = False  # picks a face to extrude; no snap markers
    vcb_label = "Distance"
    # Preview lines in the normal edge colour, not the loose orange rubber band,
    # and depth-tested so the forming box hides its own back edges.
    wireframe_color = (0.13, 0.17, 0.23, 1.0)
    wireframe_depth_tested = True

    # Last committed distance (signed along the base's outward normal), shared
    # across activations: double-click repeats it on another face, SketchUp-style.
    last_distance: float | None = None

    def __init__(self) -> None:
        self.hovered_face: Face | None = None
        self.base_face: Face | None = None
        self.extrusion: float = 0.0  # signed distance along normal
        self.dragging: bool = False
        # Ctrl held = "push/pull a copy": the base face stays in place (a slab
        # division), the extrusion stacks as a new segment instead of growing
        # the neighbours — how floors are stacked in SketchUp.
        self._keep_base: bool = False
        # Deepest allowed inward push (positive, along −normal), computed at
        # drag start; None = unbounded. SketchUp's "Offset limited to" clamp.
        self._limit_in: float | None = None
        # The Group whose face is being pushed (None = the loose mesh). The
        # whole pipeline then runs on that group's isolated mesh directly — no
        # "enter the group" step needed, unlike SketchUp.
        self._group = None
        self._hover_group = None
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
        # The model point the distance inference is currently locked onto (a
        # corner or a face hit), drawn as a green marker by the viewport overlay.
        self._inference_point: QVector3D | None = None
        self._inference_kind: str | None = None

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
            self._inference_point = None
            self._inference_kind = None
            self.hovered_face, self._hover_group = viewport.pick_face_any(
                ctx.screen.x(), ctx.screen.y())
            # Shade the face that would be pushed, SketchUp-style, so the target
            # is unmistakable before clicking.
            viewport.set_hover(self.hovered_face)
            return

        if self.base_face is None or self._anchor is None:
            return
        # Ctrl can be pressed/released mid-drag; the live preview follows.
        self._keep_base = bool(ctx.modifiers & Qt.ControlModifier)
        # Work on the clean mesh: revert the preview before reading geometry,
        # so reference inference never sees the forming solid's moving points.
        self._revert_preview(viewport)
        inferred = self._infer_reference_distance(ctx)
        if inferred is not None:
            self.extrusion = inferred
        else:
            projected = viewport._project_to_lock_line(
                self._anchor, self._normal, ctx.screen.x(), ctx.screen.y()
            )
            self.extrusion = QVector3D.dotProduct(
                projected - self._anchor, self._normal)
        self._clamp_extrusion(viewport)
        # Apply the real commit to the mesh as a live preview (reverting last
        # frame's first), so the forming solid renders exactly as it will commit.
        self._apply_preview(viewport)

    def on_click(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        self._keep_base = bool(ctx.modifiers & Qt.ControlModifier)
        if not self.dragging:
            face = self.hovered_face
            if face is None:
                return
            self.base_face = face
            self.extrusion = 0.0
            self.dragging = True
            self._group = self._hover_group
            target = self._target_scene(viewport.scene)
            self._anchor = face.centroid()
            self._normal = face.normal()
            self._attached, self._prism_cap = self._classify_base(target)
            self._cap_positions = self._cap_loop_positions(face)
            self._compute_inward_limit(target)
            self._preview_snapshot = None
            # The live preview takes over from the hover shade now.
            viewport.set_hover(None)
            viewport.update()
            return

        # Already dragging — second click commits.
        if abs(self.extrusion) < _MIN_EXTRUDE:
            # No-op extrusion; just stay in drag mode so the user can keep going.
            return
        self._commit(viewport)

    def on_double_click(self, ctx: ToolContext) -> None:
        """Repeat the last committed distance on the face under the cursor,
        SketchUp-style. Works both as a fresh double-click (the first press
        already locked the face) and right after a commit (the next quick
        click arrives as a double-click)."""
        viewport = ctx.viewport
        last = PushPullTool.last_distance
        if last is None:
            self.on_click(ctx)
            return
        self._keep_base = bool(ctx.modifiers & Qt.ControlModifier)
        if not self.dragging:
            face, grp = viewport.pick_face_any(ctx.screen.x(), ctx.screen.y())
            if face is None:
                return
            self.base_face = face
            self.dragging = True
            self._group = grp
            target = self._target_scene(viewport.scene)
            self._anchor = face.centroid()
            self._normal = face.normal()
            self._attached, self._prism_cap = self._classify_base(target)
            self._cap_positions = self._cap_loop_positions(face)
            self._compute_inward_limit(target)
            self._preview_snapshot = None
        elif abs(self.extrusion) > _MIN_EXTRUDE:
            # Mid-drag with a real distance: treat as the commit click.
            self._commit(viewport)
            return
        self.extrusion = last
        self._clamp_extrusion(viewport)
        self._commit(viewport)

    def on_value(self, viewport, value) -> bool:
        # Push/Pull only takes a single extrusion length; 3D deltas don't apply.
        if isinstance(value, tuple):
            return False
        if not self.dragging or self.base_face is None or value == 0.0:
            return False
        # A positive value goes the way the user is dragging (default +normal);
        # a negative one reverses it, SketchUp-style.
        sign = -1.0 if self.extrusion < 0.0 else 1.0
        self.extrusion = sign * value
        self._clamp_extrusion(viewport)
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

    def _target_scene(self, scene):
        """The scene the machinery edits: the real one, or a facade over the
        target group's isolated mesh."""
        return scene if self._group is None else _GroupScene(scene, self._group)

    def _target_mesh(self, scene):
        return self._group.mesh if self._group is not None else scene.mesh

    def _apply_preview(self, viewport) -> None:
        """Show the forming solid by applying the real commit to the mesh, after
        reverting the previous frame's preview."""
        self._revert_preview(viewport)
        if self.base_face is None or abs(self.extrusion) < _MIN_EXTRUDE:
            viewport.update()
            return
        self._preview_snapshot = self._target_mesh(viewport.scene).capture_state()
        self._mutate(viewport.scene)
        viewport.scene.version += 1
        viewport.update()

    def _revert_preview(self, viewport) -> None:
        if self._preview_snapshot is not None:
            self._target_mesh(viewport.scene).restore_state(self._preview_snapshot)
            self._preview_snapshot = None
            viewport.scene.version += 1

    def inference_marker(self):
        """Return ``(world_point, kind)`` for the green marker the viewport draws
        when the distance inference is locked onto a model corner/face, or
        ``None``. ``kind`` is ``"vertex"`` or ``"face"``."""
        if not self.dragging or self._inference_point is None:
            return None
        return self._inference_point, self._inference_kind

    def value_label(self):
        """Return ``(text, midpoint_world)`` for the floating distance label.
        Uses the anchor captured at drag start, which stays fixed even while a
        prism cap deforms the real geometry live."""
        if not self.dragging or self._anchor is None:
            return None
        midpoint = self._anchor + self._normal * (self.extrusion * 0.5)
        return (f"{abs(self.extrusion):.2f} m", midpoint)

    # ---- Internals ----------------------------------------------------------
    def _compute_inward_limit(self, scene) -> None:
        """How far the face can be pushed *into* the solid before the sweep
        would shoot past blocking geometry — SketchUp's "Offset limited to" rule.

        A blocker is a parallel face on the material side (−normal: solids are
        committed outward) that overlaps the base loop laterally but can't be
        punched (the loop is not strictly inside it — those are through-hole
        targets and stay unbounded; the punch stops at them on its own).
        Pushing exactly *to* the limit is allowed: landing flush dissolves or
        opens the geometry (collapse a box flat, cut a notch through a floor).

        A second, *lateral* bound: the depth at which the swept loop would
        first leave the solid through a **non-parallel** boundary face (push a
        triangular prism's long wall inward and the sweep exits through a
        slanted wall long before any parallel blocker). Beyond that depth the
        result isn't representable without boolean subtraction, so the push
        clamps there — the solid stays a solid.

        Stored in ``self._limit_in``; ``None`` = unbounded."""
        self._limit_in = None
        if not self._attached or self.base_face is None:
            return
        n = (self._normal if self._normal is not None
             else self.base_face.normal()).normalized()
        anchor = (self._anchor if self._anchor is not None
                  else self.base_face.centroid())
        base_xy = _project_loop_2d(self.base_face.vertices, n)
        for g in scene.faces:
            if g is self.base_face:
                continue
            gn = g.normal().normalized()
            if abs(QVector3D.dotProduct(gn, n)) < 0.999:
                continue  # not parallel
            dist = QVector3D.dotProduct(anchor - g.centroid(), n)
            if dist <= 1e-6:
                continue  # coplanar or on the outward side
            back_loop = [v - n * dist for v in self.base_face.vertices]
            if loop_inside_face(g, back_loop):
                continue  # a through-hole target, not a blocker
            if not _loops_overlap_2d(base_xy, _project_loop_2d(g.vertices, n)):
                continue  # laterally elsewhere (another building's wall)
            if self._limit_in is None or dist < self._limit_in:
                self._limit_in = dist

        # Immediate lateral exit: a backing wall that leans *away* from the
        # push (−n has a component out of it) overflows at any depth — the
        # long wall of a triangular prism can't move inward at all without
        # boolean subtraction. Limit 0 (the push becomes a no-op).
        direction = -n
        mesh = scene.mesh
        for loop in [self.base_face.loop] + self.base_face.hole_loops:
            cnt = len(loop)
            for i in range(cnt):
                ed = mesh.find_edge(loop[i], loop[(i + 1) % cnt])
                if ed is None:
                    continue
                for g in ed.faces:
                    if g is self.base_face or g.interior:
                        continue
                    gn = g.normal().normalized()
                    if abs(QVector3D.dotProduct(gn, n)) > 0.999:
                        continue  # parallel/coplanar: not a lateral wall
                    if QVector3D.dotProduct(direction, gn) > 1e-6:
                        self._limit_in = 0.0
                        return

        # Lateral exit bound: march inward (−n) from points just inside the
        # base loop and take the first crossing of any non-parallel boundary
        # face. Parallel faces are the blocker/through logic above; interior
        # partitions are not boundary and don't end the solid.
        loop3 = self.base_face.vertices
        u, w = plane_basis(n)
        origin3 = loop3[0]
        loop_xy = [(QVector3D.dotProduct(p - origin3, u),
                    QVector3D.dotProduct(p - origin3, w)) for p in loop3]
        ip_xy = _interior_point(loop_xy)
        ip3 = origin3 + u * ip_xy[0] + w * ip_xy[1]
        samples = [ip3]
        for i, p in enumerate(loop3):
            samples.append(p + (ip3 - p) * 1e-3)
            mid = (p + loop3[(i + 1) % len(loop3)]) * 0.5
            samples.append(mid + (ip3 - mid) * 1e-3)
        lateral = None
        for g in scene.faces:
            if g is self.base_face or g.interior:
                continue
            if abs(QVector3D.dotProduct(g.normal().normalized(), n)) > 0.999:
                continue  # parallel: blocker/through logic owns it
            for tri in g.triangulate():
                for q in samples:
                    t = _ray_triangle(q, direction, tri)
                    if t is not None and t > 1e-4 and (
                            lateral is None or t < lateral):
                        lateral = t
        if lateral is not None:
            # Unlike a parallel blocker (face lands flush on face and
            # dissolves), the lateral contact is an edge-on graze — landing
            # exactly there degenerates the swept walls. Stop a hair short.
            lateral = max(lateral - 1e-3, 0.0)
            if self._limit_in is None or lateral < self._limit_in:
                self._limit_in = lateral

    def _clamp_extrusion(self, viewport=None) -> None:
        if self._limit_in is not None and self.extrusion < -self._limit_in:
            self.extrusion = -self._limit_in
            if viewport is not None:
                viewport.flash_status(
                    f"Offset limited to {self._limit_in:.2f} m")

    def _infer_reference_distance(self, ctx: ToolContext):
        """Distance making the moved face level with the model geometry under the
        cursor — SketchUp's mid-push inference ("push until even with that
        corner / that face"). Scans the *clean* mesh (the caller reverts the live
        preview first, so the forming solid's own moving vertices never feed
        back). A model **vertex** within the snap threshold wins first (a precise
        corner); otherwise the **face** under the cursor is used, projecting the
        ray∩plane hit onto the push axis (level with where you point on it).
        Records the engaged point in ``self._inference_point`` for the overlay
        marker. Returns ``None`` when nothing engages."""
        vp = ctx.viewport
        sx, sy = ctx.screen.x(), ctx.screen.y()
        thr = getattr(vp, "snap_threshold_px", 9.0)
        exclude = {_key(p) for p in self._cap_positions}
        best = None
        meshes = [vp.scene.mesh] + [g.mesh for g in getattr(vp.scene, "groups", [])]
        for mesh in meshes:
            for vtx in mesh.vertices:
                if _key(vtx.position) in exclude:
                    continue  # the base's own corners would pin the drag to 0
                pix = vp._world_to_pixel(vtx.position)
                if pix is None:
                    continue
                d2 = (pix[0] - sx) ** 2 + (pix[1] - sy) ** 2
                if d2 <= thr * thr and (best is None or d2 < best[0]):
                    best = (d2, QVector3D.dotProduct(
                        vtx.position - self._anchor, self._normal),
                        QVector3D(vtx.position), "vertex")
        if best is not None:
            self._inference_point = best[2]
            self._inference_kind = best[3]
            return best[1]

        # No corner nearby: align to the face under the cursor (its plane).
        # Project the ray∩plane hit onto the push axis. The base face (and
        # anything coplanar with it) reads ~0 distance — guarded out so the
        # push doesn't pin to its own plane.
        face, _grp = vp.pick_face_any(sx, sy)
        if face is not None and face is not self.base_face:
            origin, direction = vp._pixel_to_ray(sx, sy)
            if origin is not None and direction is not None:
                fn = face.normal().normalized()
                denom = QVector3D.dotProduct(fn, direction)
                if abs(denom) >= 1e-6:
                    t = QVector3D.dotProduct(
                        fn, face.centroid() - origin) / denom
                    if t > 0:
                        hit = origin + direction * t
                        dist = QVector3D.dotProduct(
                            hit - self._anchor, self._normal)
                        if abs(dist) >= _MIN_EXTRUDE:
                            self._inference_point = hit
                            self._inference_kind = "face"
                            return dist
        self._inference_point = None
        self._inference_kind = None
        return None

    @staticmethod
    def _cap_loop_positions(face) -> list[QVector3D]:
        """Every boundary position of ``face`` — outer loop *and* hole rims.
        The prism-translate path moves all of them (a hole rim left behind
        warps the cap into a non-planar holed face), and reference inference
        excludes all of them from snapping."""
        pts = [QVector3D(v) for v in face.vertices]
        for hole in face.holes:
            pts.extend(QVector3D(v) for v in hole)
        return pts

    def _classify_base(self, scene) -> tuple[bool, bool]:
        """Classify the base face for previewing.

        Returns ``(attached, prism_cap)``:
        - ``attached`` — every edge is shared (embedded in a surface/solid), so
          an inner face pushed in is a recess (hidden so the pocket shows).
        - ``prism_cap`` — every edge backs onto a *perpendicular* wall, so the
          push is a clean prism extend/shrink and can be previewed by live
          translation (walls deform with the cap, clean both ways).

        Hole rims count as edges of the cap too: a hole edge backing onto a
        coplanar inner panel (a rectangle drawn on a wall) breaks prism_cap —
        translating the cap would leave the panel and rim behind. A hole whose
        rim backs onto perpendicular swept walls (a window tube through the
        slab) keeps the clean translate.
        """
        faces = scene.faces
        kinds = []
        for loop in [self.base_face.vertices] + self.base_face.holes:
            n = len(loop)
            kinds.extend(
                classify_push_edge(self.base_face, loop[i], loop[(i + 1) % n],
                                   faces)
                for i in range(n)
            )
        normal = self.base_face.normal()
        attached = all(kind != "free" for kind, _ in kinds)
        # A clean prism-cap translate only applies when every backing wall is
        # *swept* by the push (has an edge along the normal). Otherwise the push
        # is into a cap that would shear — handle that via the extrude path so it
        # adds a box bump instead of dragging the whole cross-section.
        prism_cap = bool(kinds) and all(
            kind == "perp" and nb is not None and _swept_by_push(nb, normal)
            for kind, nb in kinds
        )
        return attached, prism_cap

    def _commit(self, viewport) -> None:
        viewport.set_hover(None)  # the hovered face is about to be replaced
        viewport.set_suppressed_faces(set())
        self._revert_preview(viewport)  # drop the live preview; redo it for real
        if self.base_face is None or abs(self.extrusion) < _MIN_EXTRUDE:
            self._reset()
            viewport.update()
            return
        # One snapshot wraps the edit *and* the watertight stitch: undo is exact,
        # and it is the identical mutation the live preview just showed. A push
        # aimed at a group snapshots that group's mesh instead.
        viewport.history.execute(SnapshotMutation(
            self._mutate,
            mesh=self._group.mesh if self._group is not None else None))
        PushPullTool.last_distance = self.extrusion  # double-click repeats this
        self._reset()
        viewport.update()

    def _mutate(self, scene) -> None:
        """Apply the push to the target mesh (the scene's, or the locked
        group's) and stitch it watertight. Shared by the committed edit
        (wrapped in :class:`SnapshotMutation`) and the live preview, so the
        drag renders exactly what will commit."""
        scene = self._target_scene(scene)
        face = self.base_face
        d = self.extrusion

        # A prism cap is exactly a translation of the cap along its normal; the
        # walls follow through shared vertices. Then repair connectivity
        # (welds, T-junctions, collinear) — no coplanar-merge, nothing new is
        # added. Ctrl ("push/pull a copy") must not translate: it stacks a new
        # segment, so it takes the extrude path below with the base kept.
        if self._prism_cap and not self._keep_base:
            normal = self._normal if self._normal is not None else face.normal()
            translate_points(scene, {_key(p) for p in self._cap_positions},
                             normal * d)
            seed = list(self._cap_positions) + [p + normal * d
                                                for p in self._cap_positions]
            seedkeys = {_key(p) for p in seed}
            run_stitch(scene.mesh, seedkeys, set(), coplanar_merge=False)
            # A shrink can land the cap flush on a coplanar neighbour — a bump
            # pushed back level with its host wall, or a side flank pushed
            # clear across the bump onto the opposite flank. The weld may then
            # consume the pushed face itself (the coincident pair must vanish,
            # not survive as a zero-thickness fin), so the cleanup can't hinge
            # on it: run the same fixpoint plane rebuild as the extrude path
            # over every face the translation touched.
            if not _mesh_is_flat(scene.mesh):
                fresh = {f for f in scene.mesh.faces
                         if any(_key(v) in seedkeys for v in f.vertices)}
                self._rebuild_planes_fixpoint(scene.mesh, fresh, seedkeys,
                                              removing=d < 0)
            else:
                # A full collapse flattened the solid. The cap may have landed
                # on a *subdivided* base (hole-bearing cycles never dedupe as
                # identical): it is now a redundant mother over the
                # subdivision — exactly what the flat-drawing heal removes.
                heal_overlapping_faces(scene.mesh)
            orient_outward(scene.mesh)
            return

        # Establish the outward invariant *up front* instead of trusting history:
        # hand-built or loaded meshes can arrive with mixed winding, and every
        # downstream decision (the wall quads' deterministic winding, the
        # per-plane rebuild's material-side classification) reads face normals.
        # If the base face flips, the drag distance flips with it so the push
        # still goes where the user dragged. No-op on open meshes.
        pre_normal = face.normal()
        orient_outward(scene.mesh)
        normal = face.normal()
        if QVector3D.dotProduct(normal, pre_normal) < 0:
            d = -d
        # A push on a *pre-existing solid* must stay watertight; one on a flat
        # sheet (a recess carved into a surface) legitimately leaves open edges.
        was_solid = not _mesh_is_flat(scene.mesh)
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
        # Ctrl ("push/pull a copy") forces the free-extrusion semantics: the
        # base face stays in place as a slab division and no through-hole is
        # punched — the stacked segment and its belt of edges are the point,
        # exactly like SketchUp. The solid rebuild still runs: the kept base /
        # inward cap are interior partitions the rebuild preserves, the belt
        # survives as crease boundaries (``keep_keys``), and an inward stack's
        # tube quads dissolve into the boundary faces they overlap instead of
        # festering as opposite-winding coincident pairs that corrupt later
        # parity queries.
        attached_any = all(kind != "free" for kind, _ in kinds)
        attached = attached_any and not self._keep_base
        through = self._find_through_face(face, d, faces) if attached else None

        before = set(scene.mesh.faces)
        self._cap_cmd = None
        base_attrs = dict(face.attrs)
        if through is not None:
            commands = self._through_commands(face, base, through)
        else:
            commands = self._extrude_commands(
                face, base, top, base_holes, top_holes, count, attached
            )
        for cmd in commands:
            cmd.do(scene)
        if (base_attrs and self._cap_cmd is not None
                and self._cap_cmd.face in scene.mesh.faces):
            # The moved cap continues the (consumed) base: same attrs.
            self._cap_cmd.face.attrs = dict(base_attrs)
        new_faces = set(scene.mesh.faces) - before
        if self._keep_base and attached_any and d < 0:
            # The Ctrl-stack grows *into* the solid: its cap is a deliberate
            # interior division (the inward counterpart of the kept base), not
            # a boundary declaration — mark it so the rebuild preserves it.
            for f in new_faces:
                fn = f.normal().normalized()
                if (abs(QVector3D.dotProduct(fn, normal)) > 0.999
                        and abs(QVector3D.dotProduct(
                            f.centroid() - top[0], normal)) < 1e-4):
                    f.interior = True
        seed = list(base) + list(top)
        for hb, ht in zip(base_holes, top_holes):
            seed += list(hb) + list(ht)
        seedkeys = {_key(p) for p in seed}
        # On a solid, the naive extrude's seams and overlaps are dissolved by the
        # deterministic per-plane rebuild below, so the winding-tolerant coplanar
        # merge (phase 3) stays off. On raw/open geometry (a flat sheet) there is
        # no "outside" to classify against, so the merge still applies there.
        solid = attached_any and was_solid
        # On solids the identical-cycle dedupe waits for the rebuild: a flush
        # collapse's sweep quad lands identical to the face it annihilates
        # with, and only the volumetric classification can tell "keep one"
        # (shared wall) from "drop both" (emptied region).
        run_stitch(scene.mesh, seedkeys, new_faces,
                   coplanar_merge=not solid and not self._keep_base,
                   dedupe=not solid)
        if solid:
            self._rebuild_planes_fixpoint(scene.mesh, set(new_faces), seedkeys,
                                          keep_mode=self._keep_base,
                                          removing=d < 0)
        # Give any closed solid a consistent outward orientation — every face's
        # normal pointing out. The extrude can otherwise commit a closed solid
        # wound inconsistently (a flipped cap, or the base of a first flat→solid
        # extrude), invisible until you push that face and it extrudes *inward*.
        # No-op when the result is legitimately open (a recess in a flat sheet).
        orient_outward(scene.mesh)

    @staticmethod
    def _rebuild_planes_fixpoint(mesh, fresh: set, seedkeys: set,
                                 keep_mode: bool = False,
                                 removing: bool = True) -> None:
        """Deterministic root-fix (path C): recompute each touched plane's
        faces from its edges — the planar arrangement finds every region,
        winding-classification keeps the ones inside the solid and drops
        phantoms outside, and the union dissolves coplanar seams. Seam planes
        (a fresh face coplanar-adjacent to another) cover the strip stacked on
        a wall and the quad overlapping a wall to be notched; crack planes
        cover anything a nested push left unfaced. Shared by the extrude path
        and the prism-translate path (whose flush landings need the same
        volumetric resolution).

        Rebuilt to a **fixpoint**: a plane's classification reads the current
        mesh, so a plane rebuilt while a neighbour still carries its dirty
        phantom can come out wrong — which made the result depend on iteration
        order (set order = the Python hash seed). Treating the faces a rebuild
        adds as fresh re-flags the affected planes next round, and
        ``apply_rebuild`` returning False on a stable plane makes the loop
        terminate. Plane keys are sorted, so the whole pass is
        order-deterministic. No case tree."""
        # The push's own rims, captured by *position* before any rebuild (the
        # objects die as rounds consolidate them): plane edges lying on none
        # of these are the user's hand-drawn subdivisions and survive the
        # union (A.4); edges on them are the op's seams and may dissolve.
        op_rims: list = []
        for f in fresh:
            for lp in (f.loop, *f.hole_loops):
                cnt = len(lp)
                for i in range(cnt):
                    op_rims.append((QVector3D(lp[i].position),
                                    QVector3D(lp[(i + 1) % cnt].position)))
        for _ in range(4):  # converges in 1-2 rounds; hard cap for safety
            planes: dict = {}
            for origin, plane_n in seam_planes(mesh, fresh):
                planes.setdefault(plane_key(origin, plane_n)[0],
                                  (origin, plane_n))
            for origin, plane_n in crack_planes(mesh):
                planes.setdefault(plane_key(origin, plane_n)[0],
                                  (origin, plane_n))
            changed = False
            for key in sorted(planes):
                origin, plane_n = planes[key]
                before_faces = set(mesh.faces)
                if apply_rebuild(mesh, origin, plane_n, fresh, keep_mode,
                                 removing, op=op_rims):
                    changed = True
                    fresh |= set(mesh.faces) - before_faces
            if not changed:
                break
            fresh = {f for f in fresh if f in mesh.faces}
        # The dissolved seams can leave redundant collinear vertices on the
        # rebuilt faces' borders (the old cap ring's corners); one more
        # connectivity pass collapses them. No merge — rebuild already did.
        run_stitch(mesh, seedkeys, None, coplanar_merge=False)

    def _extrude_commands(self, face, base, top, base_holes, top_holes,
                          count, attached) -> list:
        """Build the naive extrusion of ``face`` to ``top``: a consumed/kept
        base, the moved cap (carrying any holes), one wall quad per boundary
        edge, and an inner wall per hole edge — no special cases. Where a quad
        lands on an existing face's plane (a strip on a wall, a phantom over a
        region being carved away) the per-plane rebuild dissolves it.

        Winding is deterministic: with the base wound outward (the commit
        invariant on solids), ``[a, b, b2, a2]`` is outward for *both* push
        directions — flipping the push flips the quad's geometric normal and
        which side holds material together — and the cap keeps the base's
        winding (+n faces out of an added box and out of a carved pocket
        alike). That is what lets the rebuild trust fresh faces' normals."""
        commands: list = []
        if attached:
            commands.append(DeleteFaceCommand(face))  # base consumed into the solid
        for i in range(count):
            commands.append(AddEdgeCommand(top[i], top[(i + 1) % count]))
        for i in range(count):
            commands.append(AddEdgeCommand(base[i], top[i]))
        cap_cmd = AddFaceCommand(
            list(top), auto=not attached,
            holes=[list(th) for th in top_holes] or None)
        commands.append(cap_cmd)
        # The moved cap is the base's continuation: it inherits its attrs
        # (material, future BIM tag). Remembered so _mutate can stamp the face
        # the command creates when it runs.
        self._cap_cmd = cap_cmd

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
            commands.append(AddFaceCommand(
                [base[i], base[j], top[j], top[i]], auto=False))

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
        self._keep_base = False
        self._limit_in = None
        self._group = None
        self._hover_group = None
        self._anchor = None
        self._normal = None
        self._cap_positions = []
        self._preview_snapshot = None
        self._inference_point = None
        self._inference_kind = None
