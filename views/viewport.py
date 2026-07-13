# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""3D viewport: orbital camera, grid, XYZ axes, tools, snapping and overlays.

Uses PySide6's bundled QOpenGL* helper classes (QOpenGLShaderProgram,
QOpenGLBuffer, QOpenGLVertexArrayObject) — no external GL bindings yet.
moderngl lands when we start dealing with real meshes.

Wayland requires every frame to be drawn explicitly: ``paintGL`` always
calls ``glClear`` first to avoid showing stale GPU memory.

Navigation (SketchUp-like):
- Middle-button drag: orbit
- Shift + Middle-button drag: pan
- Wheel: zoom
- P: toggle perspective / parallel projection

Axis lock (active while drawing) — SketchUp-style:
- Right arrow: toggle lock to X (red)
- Left arrow:  toggle lock to Y (green)
- Up arrow:    toggle lock to Z (blue)
- Down arrow:  toggle parallel / perpendicular lock to the edge under cursor
- Shift held:  contextual lock — locks whatever inference is active at the
               moment (auto-axis or reference). Hold to lock, release to free.

While drawing, the rubber band also auto-aligns to axes within ~3° (soft
inference, visual cue only). Press Shift while the rubber band turns an
axis colour to lock that direction.

Tool input (when a tool is active):
- Left click: ``tool.on_click(ToolContext)``
- Mouse move: ``tool.on_hover(ToolContext)``
- Esc:        ``tool.on_cancel(viewport)``
- Other keys: tool gets first shot via ``tool.on_key(...)``
"""
from __future__ import annotations

import math
import os
import re
import time as _time_mod
from array import array
from pathlib import Path
from typing import Optional

# Perf telemetry (INGETRAZO_PERF=1): every operation slower than 50 ms and a
# once-per-second frame summary land in ~/ingetrazo-perf.log — the tool for
# "it feels slow" reports from real sessions, where synthetic benchmarks lie.
_PERF = bool(os.environ.get("INGETRAZO_PERF"))
_perf_file = None


def _plog(tag: str, ms: float, extra: str = "", floor: float = 50.0) -> None:
    global _perf_file
    if not _PERF or ms < floor:
        return
    if _perf_file is None:
        _perf_file = open(Path.home() / "ingetrazo-perf.log", "a", buffering=1)
    _perf_file.write(f"{_time_mod.strftime('%H:%M:%S')} {tag} {ms:.0f}ms"
                     f"{' ' + extra if extra else ''}\n")

from PySide6.QtCore import QEvent, Qt, QPointF, QRectF, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QMatrix4x4,
    QOpenGLFunctions,
    QPainter,
    QPen,
    QPolygonF,
    QSurfaceFormat,
    QVector3D,
    QVector4D,
)
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLFramebufferObject,
    QOpenGLFramebufferObjectFormat,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLTexture,
    QOpenGLVertexArrayObject,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from core.camera import OrbitCamera
from core.i18n import tr
from core.group import Group
from core.mesh import Edge, Face
from core.history import EraseSelectionCommand, History
from core.scene import Scene
from core.snap import SnapResult, compute_snap
from core.triangulate import plane_axes
from tools.base import Tool, ToolContext


# OpenGL constants — kept as literals so we don't depend on PyOpenGL.
GL_FLOAT = 0x1406
GL_LINES = 0x0001
GL_TRIANGLES = 0x0004
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_DEPTH_TEST = 0x0B71
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_POLYGON_OFFSET_FILL = 0x8037
GL_LEQUAL = 0x0203
GL_FALSE = 0
GL_TRUE = 1
GL_FRAMEBUFFER = 0x8D40
GL_READ_FRAMEBUFFER = 0x8CA8
GL_DRAW_FRAMEBUFFER = 0x8CA9
GL_NEAREST = 0x2600


SHADER_DIR = Path(__file__).resolve().parents[1] / "resources" / "shaders"


# ---- Geometry helpers ------------------------------------------------------

_AXIS_DIRS = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def _axes_vertices(spacing: float, pos_len: float = 1.0e5):
    """SketchUp-style axes: a long solid line in the positive direction and an
    **evenly-spaced** dashed line in the negative (constant world ``spacing``, so
    the dashes converge toward the horizon by perspective — like SketchUp, not
    spreading apart). ``spacing`` scales with the camera distance so the on-screen
    density stays stable across zoom. Returns ``(coords, spans)`` where ``spans``
    maps ``'x'|'y'|'z'`` → ``(first_vertex, vertex_count)`` for a per-axis draw."""
    coords = array("f")
    spans: dict[str, tuple[int, int]] = {}
    spacing = max(spacing, 1e-4)
    dash = spacing * 0.5
    n = 140                          # dashes → reach = spacing*n past the model
    for name, (dx, dy, dz) in _AXIS_DIRS.items():
        start = len(coords) // 3
        coords.extend([0.0, 0.0, 0.0, dx * pos_len, dy * pos_len, dz * pos_len])
        for k in range(n):
            t0 = k * spacing
            t1 = t0 + dash
            coords.extend([-dx * t0, -dy * t0, -dz * t0,
                           -dx * t1, -dy * t1, -dz * t1])
        spans[name] = (start, len(coords) // 3 - start)
    return coords, spans


def _ray_triangle(
    origin: QVector3D,
    direction: QVector3D,
    v0: QVector3D,
    v1: QVector3D,
    v2: QVector3D,
) -> Optional[float]:
    """Möller–Trumbore ray / triangle intersection. Returns distance ``t``
    along the ray, or ``None`` for a miss / behind-camera hit. The triangle
    is intersected from both sides — front/back orientation does not matter
    because IngeTrazo doesn't (yet) cull back faces."""
    eps = 1e-6
    e1 = v1 - v0
    e2 = v2 - v0
    h = QVector3D.crossProduct(direction, e2)
    a = QVector3D.dotProduct(e1, h)
    if abs(a) < eps:
        return None
    f = 1.0 / a
    s = origin - v0
    u = f * QVector3D.dotProduct(s, h)
    if u < 0.0 or u > 1.0:
        return None
    q = QVector3D.crossProduct(s, e1)
    v = f * QVector3D.dotProduct(direction, q)
    if v < 0.0 or u + v > 1.0:
        return None
    t = f * QVector3D.dotProduct(e2, q)
    if t < eps:
        return None
    return t


def _point_to_segment_distance_2d(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx = bx - ax
    dy = by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    qx = ax + t * dx
    qy = ay + t * dy
    return math.hypot(px - qx, py - qy)


# ---- Viewport --------------------------------------------------------------

class Viewport(QOpenGLWidget):
    """OpenGL viewport with orbital camera, grid, XYZ axes, tools and snapping."""

    valueBufferChanged = Signal(str)
    sceneVersionChanged = Signal(int)
    # Live measurement for the VCB box (e.g. "5.00 m", "3.00 × 2.00 m").
    measurementChanged = Signal(str)
    # A base-map tile finished downloading (Track G) — lets the 3D terrain
    # rebuild its mosaic as imagery arrives.
    tilesChanged = Signal()

    # Soft warm white painted on faces with no material colour — like the matte
    # cardstock of an architecture model (SketchUp's near-white default).
    DEFAULT_FACE_COLOR = (0.96, 0.95, 0.925)
    # Fixed world light (from above, slightly front-right) for the subtle diffuse
    # face shading. World-fixed so shading is stable while orbiting, like SketchUp.
    _LIGHT = QVector3D(0.35, 0.25, 1.0).normalized()

    # Tooltip text shown next to the snap marker, SketchUp-style. English source
    # strings; translated at draw time via ``tr`` (see i18n/es.json).
    _SNAP_LABELS = {
        "endpoint": "Endpoint",
        "midpoint": "Midpoint",
        "on_edge": "On edge",
        "on_face": "On face",
        "origin": "Origin",
        "extension": "Extension",
        "intersection": "Intersection",
        "from_point": "From point",
        "through_point": "Through point",
        "perp_face": "Perpendicular to face",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Hidden-line removal needs a real depth buffer. setDefaultFormat() in
        # main.py is best-effort; many platforms ignore it for QOpenGLWidget
        # and hand us a 0-bit depth context. Forcing the format here is the
        # only reliable way.
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        fmt.setDepthBufferSize(24)
        fmt.setStencilBufferSize(8)
        # NO samples on the widget surface: the scene renders into our own
        # multisampled FBO (_ensure_scene_fbo) and the blit resolves it. A
        # multisampled widget FBO adds a second resolve that interleaves stale
        # frames on Wayland (ghost frames during fast zoom) and cannot smooth
        # the already-resolved pixels we blit into it.
        self.setFormat(fmt)
        self.setMinimumSize(640, 480)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        self.camera = OrbitCamera()
        self.scene = Scene()
        self.history = History(self.scene)
        self.active_tool: Optional[Tool] = None
        self.axis_lock: Optional[str] = None  # None | "x" | "y" | "z"
        self.last_snap: Optional[SnapResult] = None
        # Copy/paste clipboard: copied geometry (faces + edges) as positions,
        # plus a reference corner so Paste can place it under the cursor.
        self.clipboard: Optional[dict] = None

        # Reference-edge state (Down arrow → parallel / perpendicular).
        self.reference_edge = None
        self.reference_mode: Optional[str] = None  # None | "parallel" | "perpendicular"
        # Linear-inference toggle (SketchUp's Alt): "all" | "off" | "parallel_perp".
        self.linear_inference_mode = "all"
        # Sticky inference lock (Shift): (direction, color) captured from the
        # active inference, held until Shift is released.
        self._shift_lock: Optional[tuple] = None
        self._hover_edge = None  # last edge under cursor (candidate for capture)
        # Edge/corner/face hovered while drawing, held as soft references
        # (SketchUp "from point" / "through point" / "perpendicular to face"
        # acquisition). Cleared when no segment is in progress.
        self._acquired_edge = None
        self._acquired_point = None
        self._acquired_face_normal = None
        self._last_mouse_pos: Optional[QPointF] = None

        # Pixel radius for point snaps (endpoint, origin, close). 12 px felt
        # mushy when the cursor was running along an existing edge: as long
        # as the cursor was within 12 px of either end of a short edge,
        # endpoint snap kept firing. SketchUp is tighter — the green dot
        # only lights up right at the vertex.
        self.snap_threshold_px = 9.0
        # On-edge snap gets a bigger radius than point snaps: an edge is a large
        # linear target, so resting a corner on it (e.g. a door on the floor
        # line) should be forgiving and not slip just outside the face.
        self.edge_snap_threshold_px = 14.0
        self.pick_threshold_px = 8.0
        self.inference_angle_deg = 3.0

        self._gl: Optional[QOpenGLFunctions] = None
        self._program: Optional[QOpenGLShaderProgram] = None
        self._loc_mvp = -1
        self._loc_color = -1
        self._loc_pos = -1

        self._axes_vao = None
        self._axes_vbo = None

        self._edges_vao = None
        self._edges_vbo = None
        self._edges_count = 0
        self._selected_vao = None
        self._selected_vbo = None
        self._selected_count = 0
        self._sel_faces_vao = None
        self._sel_faces_vbo = None
        self._sel_faces_count = 0
        self._faces_vao = None
        self._faces_vbo = None
        self._faces_count = 0
        # Per-colour draw ranges into the face VBO: [((r,g,b), start, count)].
        # Faces share one VBO but are grouped by their attrs["color"] (default
        # cream), so each material is one glDrawArrays with its own uniform.
        self._face_runs: list = []
        # Textured faces (pos+uv VBO) grouped by image path: [(path, start, count)].
        self._tex_faces_count = 0
        self._tex_runs: list = []
        self._tex_cache: dict = {}
        self._edges_version = -1

        # Hover highlight (Select tool). Not version-tracked — it changes with
        # the cursor, not with scene mutations — so it's uploaded per paint.
        self._hover_entity = None  # None | Edge | Face under the cursor
        self._last_double = None   # (timestamp, pos) of the last double-click
        self._hover_faces_vao = None
        self._hover_faces_vbo = None
        self._hover_edges_vao = None
        self._hover_edges_vbo = None

        self._rubber_vao = None
        self._rubber_vbo = None

        # Shaded solid preview (Push/Pull): the forming box's faces, uploaded
        # per paint while the tool drags.
        self._preview_faces_vao = None
        self._preview_faces_vbo = None

        # Faces hidden from the normal pass while a tool previews — Push/Pull
        # hides the flat inner face it's pushing in (a window/door) so the recess
        # forming behind it is visible instead of covered. Keyed by identity.
        self._suppressed_faces: set = set()

        # Offscreen FBO with depth attachment. QOpenGLWidget's default target
        # on some Mesa/Wayland stacks has no depth buffer, which silently
        # breaks hidden-line removal. Rendering into our own FBO and blitting
        # color out guarantees a real depth buffer is present.
        self._scene_fbo: Optional[QOpenGLFramebufferObject] = None
        self._fbo_size = (0, 0)

        # Camera navigation state (middle button)
        self._last_pos = None
        self._pan_mode = False
        # SketchUp-style navigation mode for trackpad users with no middle
        # mouse button: when set ("orbit" / "pan"), a left-drag drives the
        # camera instead of the active tool. None means a drawing tool is in
        # charge of the left button.
        self.nav_mode: Optional[str] = None

        # Preview line for the "always on top" tools, stashed during GL render
        # and drawn in the QPainter overlay (thick, reliable pen).
        self._overlay_rubber: Optional[tuple] = None

        # Rubber-band box selection (left-drag with a box_select tool).
        self._box_active = False
        self._box_start: Optional[QPointF] = None
        self._box_cur: Optional[QPointF] = None

        # Numeric value buffer (VCB-style typed length).
        self._value_buffer = ""

        # Base-map tiles (Track G, G1). The fetcher is created lazily (needs a
        # running app); GL textures are cached per tile, keyed by (source, x,y,z).
        self._tile_fetcher = None
        self._tile_textures: dict = {}
        self._tile_quad_vao = None
        self._tile_quad_vbo = None
        # Cached base-map tile geometry (built once per capture, not per frame).
        self._tile_geom = None
        # Per-frame GL-texture-creation budget (spreads big captures over frames).
        self._tex_budget = 0
        self._tex_deferred = False

        # Georef path node being hovered ``(path, index)`` — for the drag handle
        # highlight (Track G, GeoPath node editing).
        self._hover_geo_node = None
        # World point on the profiled route to mark (profile→plan link), or None.
        self._route_marker = None

        # 3D draped terrain (Track G, G2 full).
        self._terrain_vao = None
        self._terrain_vbo = None
        self._terrain_count = 0
        self._terrain_texture = None

    # ---- GL lifecycle -------------------------------------------------------
    def initializeGL(self) -> None:
        self._gl = QOpenGLFunctions(self.context())
        self._gl.initializeOpenGLFunctions()
        self._gl.glClearColor(0.93, 0.94, 0.96, 1.0)
        self._gl.glClearDepthf(1.0)
        self._gl.glEnable(GL_DEPTH_TEST)
        # LEQUAL (instead of the default LESS) lets a fragment win when its
        # depth equals the existing one — important for edges drawn on top of
        # coincident faces, which can rasterize to bit-identical depths.
        self._gl.glDepthFunc(GL_LEQUAL)
        self._gl.glEnable(GL_BLEND)
        self._gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        self._program = self._compile_program()
        self._loc_mvp = self._program.uniformLocation("u_mvp")
        self._loc_color = self._program.uniformLocation("u_color")
        self._loc_back_color = self._program.uniformLocation("u_back_color")
        self._loc_pos = self._program.attributeLocation("a_pos")
        self._loc_uv = self._program.attributeLocation("a_uv")
        self._loc_use_tex = self._program.uniformLocation("u_use_texture")
        self._loc_tex = self._program.uniformLocation("u_tex")

        # Axes rebuilt per frame (dash spacing scales with zoom), so dynamic.
        self._axes_vao, self._axes_vbo = self._create_dynamic()
        self._axes_spans: dict = {}

        self._sky_vao, self._sky_vbo = self._create_dynamic()
        self._edges_vao, self._edges_vbo = self._create_dynamic()
        self._selected_vao, self._selected_vbo = self._create_dynamic()
        self._sel_faces_vao, self._sel_faces_vbo = self._create_dynamic()
        self._faces_vao, self._faces_vbo = self._create_dynamic()
        self._tex_faces_vao, self._tex_faces_vbo = self._create_dynamic_uv()
        self._billboard_vao, self._billboard_vbo = self._create_dynamic_uv()
        self._hover_faces_vao, self._hover_faces_vbo = self._create_dynamic()
        self._hover_edges_vao, self._hover_edges_vbo = self._create_dynamic()
        self._silhouette_vao, self._silhouette_vbo = self._create_dynamic()
        self._rubber_vao, self._rubber_vbo = self._create_dynamic()
        self._preview_faces_vao, self._preview_faces_vbo = self._create_dynamic()
        self._tile_quad_vao, self._tile_quad_vbo = self._create_dynamic_uv()
        self._terrain_vao, self._terrain_vbo = self._create_dynamic_uv()

    def resizeGL(self, w: int, h: int) -> None:
        # Qt passes framebuffer-pixel sizes here (already scaled by DPR), so
        # this is the authoritative source for FBO and viewport dimensions.
        if self._gl is None:
            return
        self._gl.glViewport(0, 0, w, h)
        self.camera.set_aspect(w, h)
        self._ensure_scene_fbo(w, h)

    def _fb_size(self) -> tuple[int, int]:
        """Framebuffer pixel size (logical size × device pixel ratio)."""
        dpr = self.devicePixelRatioF()
        return max(int(round(self.width() * dpr)), 1), max(int(round(self.height() * dpr)), 1)

    def _ensure_scene_fbo(self, w: int, h: int) -> None:
        """Create or resize the offscreen FBO used for depth-tested rendering."""
        size = (max(w, 1), max(h, 1))
        if self._scene_fbo is not None and self._fbo_size == size:
            return
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.CombinedDepthStencil)
        # Real MSAA happens HERE, where the scene actually renders; the blit
        # to the widget's single-sample FBO is the resolve (MSAA read → plain
        # draw is the legal direction). The widget surface itself stays
        # single-sample — see __init__.
        fmt.setSamples(4)
        self._scene_fbo = QOpenGLFramebufferObject(size[0], size[1], fmt)
        if self._scene_fbo.format().samples() == 0:
            # Driver refused multisampling — retry single-sample but KEEP the
            # depth/stencil attachment (the Wayland depth workaround).
            fmt.setSamples(0)
            self._scene_fbo = QOpenGLFramebufferObject(size[0], size[1], fmt)
        self._fbo_size = size

    def paintGL(self) -> None:
        if self._gl is None or self._program is None:
            return
        _pt0 = _time_mod.perf_counter() if _PERF else 0.0

        # Render the 3D scene into our own FBO (which has a real depth buffer)
        # then blit the colour to the widget's default framebuffer. Sizes are
        # in framebuffer pixels — using logical (self.width/height) here would
        # blit into a fraction of the widget on HiDPI displays and shift the
        # rendered scene away from the mouse cursor.
        w, h = self._fb_size()
        self._ensure_scene_fbo(w, h)
        default_fbo = self.defaultFramebufferObject()
        self._scene_fbo.bind()
        self._gl.glViewport(0, 0, w, h)

        # Re-establish GL state every frame. QPainter (used for the 2D overlay)
        # leaves GL state in an undefined shape — in particular it tends to
        # disable depth test — so we can't trust state to persist across
        # paintGL calls.
        self._gl.glEnable(GL_DEPTH_TEST)
        self._gl.glDepthFunc(GL_LEQUAL)
        self._gl.glDepthMask(GL_TRUE)
        self._gl.glEnable(GL_BLEND)
        self._gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        self._gl.glClearDepthf(1.0)
        self._gl.glClearColor(0.90, 0.91, 0.92, 1.0)
        self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        mvp = self.camera.projection_matrix() * self.camera.view_matrix()
        self._program.bind()
        self._program.setUniformValue(self._loc_mvp, mvp)
        # Solid-colour by default; the textured-face pass flips this on.
        self._program.setUniformValue(self._loc_use_tex, 0)
        self._program.setUniformValue(self._loc_tex, 0)  # sampler → unit 0

        # Sky / ground backdrop with a horizon anchored to the camera pitch —
        # premium SketchUp feel. Fixed on zoom (it's the point at infinity),
        # moves only on orbit. Skipped over the base map / terrain (which supply
        # their own ground).
        if not self._base_map_showing() and not self._terrain_showing():
            self._draw_sky(mvp)
            self._program.setUniformValue(self._loc_mvp, mvp)

        # Base-map tiles (Track G) — the ground image, drawn before the grid so
        # the grid lines read on top of the imagery. Depth-write OFF: it's a
        # backdrop, geometry always draws over it.
        self._render_tiles()

        # 3D draped terrain (Track G, G2 full) — real depth-tested relief that
        # replaces the flat map when enabled.
        self._render_terrain()

        # No grid — the infinite axes are the spatial reference (SketchUp).

        # Persistent edges + faces
        self._sync_edges()

        # Faces — drawn before edges, with polygon offset so coincident
        # boundary edges sit cleanly on top instead of z-fighting.
        if self._faces_count > 0:
            self._gl.glEnable(GL_POLYGON_OFFSET_FILL)
            self._gl.glPolygonOffset(1.0, 1.0)
            self._faces_vao.bind()
            # One draw per material colour (default cream for unpainted faces).
            for (r, g, b), start, count in self._face_runs:
                self._set_color(r, g, b, 1.0)
                self._set_back_face_color()
                self._gl.glDrawArrays(GL_TRIANGLES, start, count)
            self._faces_vao.release()
            self._gl.glDisable(GL_POLYGON_OFFSET_FILL)

        # Textured faces — same depth/offset treatment, sampling each face's
        # image. One draw per texture (its GL texture bound to unit 0).
        if self._tex_faces_count > 0:
            self._gl.glEnable(GL_POLYGON_OFFSET_FILL)
            self._gl.glPolygonOffset(1.0, 1.0)
            self._program.setUniformValue(self._loc_use_tex, 1)
            self._tex_faces_vao.bind()
            for path, start, count in self._tex_runs:
                tex = self._get_texture(path)
                if tex is None:
                    continue
                tex.bind(0)
                self._gl.glDrawArrays(GL_TRIANGLES, start, count)
                tex.release(0)
            self._tex_faces_vao.release()
            self._program.setUniformValue(self._loc_use_tex, 0)
            self._gl.glDisable(GL_POLYGON_OFFSET_FILL)

        # Face-me billboards (SketchUp 2D people): per-frame textured cutout
        # quads turned toward the camera.
        self._draw_billboards()

        # Face highlights (selection + hover) — translucent overlays drawn on
        # top of the cream faces. Same polygon offset as the faces so they sit
        # at matching depth (LEQUAL lets this later draw win); depth-write OFF
        # so the overlay tints without blocking the edges drawn afterwards.
        if self._sel_faces_count > 0 or self._hover_entity is not None:
            self._gl.glEnable(GL_POLYGON_OFFSET_FILL)
            self._gl.glPolygonOffset(1.0, 1.0)
            self._gl.glDepthMask(GL_FALSE)
            if self._sel_faces_count > 0:
                self._set_color(0.95, 0.45, 0.16, 0.35)  # selection orange tint
                self._sel_faces_vao.bind()
                self._gl.glDrawArrays(GL_TRIANGLES, 0, self._sel_faces_count)
                self._sel_faces_vao.release()
            if isinstance(self._hover_entity, Face):
                hover_count = self._upload_hover_face(self._hover_entity)
                if hover_count > 0:
                    self._set_color(0.30, 0.55, 0.95, 0.28)  # hover blue tint
                    self._hover_faces_vao.bind()
                    self._gl.glDrawArrays(GL_TRIANGLES, 0, hover_count)
                    self._hover_faces_vao.release()
            self._gl.glDepthMask(GL_TRUE)
            self._gl.glDisable(GL_POLYGON_OFFSET_FILL)

        # Shaded solid preview (Push/Pull box forming as you drag). Drawn after
        # the persistent faces, depth-tested so it occludes geometry behind it
        # and reads as a real solid; its wireframe goes on top via the rubber
        # band below.
        self._draw_preview_faces()

        # Axes — long solid positive + evenly-dashed negative per axis (SketchUp).
        # Dash spacing scales with the camera distance so the on-screen density
        # stays stable across zoom. Depth-write OFF so the ground axes don't cull
        # geometry sitting on z=0; drawn BEFORE user edges so an edge along an
        # axis wins the LEQUAL depth test. Rubber-band stays on top (drawn last).
        spacing = max(self.camera.distance * 0.03, 1e-4)
        axes_coords, self._axes_spans = _axes_vertices(spacing)
        data = axes_coords.tobytes()
        self._axes_vbo.bind()
        self._axes_vbo.allocate(data, len(data))
        self._axes_vbo.release()
        self._axes_vao.bind()
        self._gl.glDepthMask(GL_FALSE)
        for name, rgb in (("x", (0.86, 0.22, 0.27)),   # red
                          ("y", (0.16, 0.62, 0.36)),   # green
                          ("z", (0.20, 0.40, 0.78))):  # blue
            start, count = self._axes_spans[name]
            self._set_color(*rgb, 1.0)
            self._gl.glDrawArrays(GL_LINES, start, count)
        self._gl.glDepthMask(GL_TRUE)
        self._axes_vao.release()

        if self._edges_count > 0:
            self._set_color(0.13, 0.17, 0.23, 1.0)
            self._edges_vao.bind()
            self._gl.glDrawArrays(GL_LINES, 0, self._edges_count)
            self._edges_vao.release()

        # Profile (silhouette) edges: soft seams of a curved surface are hidden,
        # except where the surface turns away from the viewer — the cylinder's
        # outline. View-dependent, so rebuilt every frame, SketchUp-style.
        sil_count = self._upload_silhouette_edges()
        if sil_count > 0:
            self._set_color(0.13, 0.17, 0.23, 1.0)
            self._silhouette_vao.bind()
            self._gl.glDrawArrays(GL_LINES, 0, sil_count)
            self._silhouette_vao.release()

        # Selected edges (drawn on top, highlighted)
        if self._selected_count > 0:
            self._set_color(0.95, 0.45, 0.16, 1.0)
            self._selected_vao.bind()
            self._gl.glDrawArrays(GL_LINES, 0, self._selected_count)
            self._selected_vao.release()

        # Hovered edge — light blue, on top of everything else so it reads as
        # the pick candidate even when it overlaps a selected (orange) edge.
        # A curve segment highlights its whole contour (what a click selects).
        if isinstance(self._hover_entity, Edge):
            hover_count = self._upload_hover_edge(self._hover_entity)
            self._set_color(0.30, 0.55, 0.95, 1.0)
            self._hover_edges_vao.bind()
            self._gl.glDrawArrays(GL_LINES, 0, hover_count)
            self._hover_edges_vao.release()

        # Rubber band preview. Loose drawing tools float it on top (depth test
        # off, so it never z-fights with coincident axes). Push/Pull's solid
        # preview keeps depth testing on, so the forming box's back edges are
        # hidden behind its faces — SketchUp-style hidden-line removal.
        depth_wire = (
            getattr(self.active_tool, "wireframe_depth_tested", False)
            if self.active_tool is not None
            else False
        )
        if not depth_wire:
            self._gl.glDisable(GL_DEPTH_TEST)
        self._draw_rubber_band()
        if not depth_wire:
            self._gl.glEnable(GL_DEPTH_TEST)

        self._program.release()

        # Blit colour from our scene FBO to the widget's default framebuffer.
        # We can't use QOpenGLFramebufferObject.blitFramebuffer(None, src) here
        # because in QOpenGLWidget the "default" framebuffer the widget shows
        # is its own internal FBO (returned by defaultFramebufferObject()),
        # NOT the system framebuffer 0. So we bind the read/draw targets by id
        # and call glBlitFramebuffer directly via the GL3+ extra functions.
        extra = self.context().extraFunctions()
        self._gl.glBindFramebuffer(GL_READ_FRAMEBUFFER, self._scene_fbo.handle())
        self._gl.glBindFramebuffer(GL_DRAW_FRAMEBUFFER, default_fbo)
        extra.glBlitFramebuffer(
            0, 0, w, h, 0, 0, w, h, GL_COLOR_BUFFER_BIT, GL_NEAREST
        )
        self._gl.glBindFramebuffer(GL_FRAMEBUFFER, default_fbo)

        # 2D overlays on top of the OpenGL framebuffer.
        self._draw_overlay()

        if _PERF:
            _dt = (_time_mod.perf_counter() - _pt0) * 1000.0
            _plog("paintGL", _dt)
            st = getattr(self, "_perf_stat", None) or \
                [_time_mod.perf_counter(), 0, 0.0]
            st[1] += 1
            st[2] += _dt
            now = _time_mod.perf_counter()
            if now - st[0] >= 1.0:
                _plog("frames/s", st[2] / max(st[1], 1),
                      extra=f"{st[1]} paints en {now-st[0]:.1f}s (avg ms)",
                      floor=0.0)
                st = [now, 0, 0.0]
            self._perf_stat = st

    # ---- Setup helpers ------------------------------------------------------
    def _compile_program(self) -> QOpenGLShaderProgram:
        prog = QOpenGLShaderProgram(self)
        ok_v = prog.addShaderFromSourceFile(
            QOpenGLShader.Vertex, str(SHADER_DIR / "basic.vert")
        )
        ok_f = prog.addShaderFromSourceFile(
            QOpenGLShader.Fragment, str(SHADER_DIR / "basic.frag")
        )
        if not (ok_v and ok_f and prog.link()):
            raise RuntimeError("shader compile/link failed:\n" + prog.log())
        return prog

    def _upload_static(self, data: array):
        vao = QOpenGLVertexArrayObject(self)
        vao.create()
        vao.bind()
        vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        vbo.create()
        vbo.bind()
        raw = data.tobytes()
        vbo.allocate(raw, len(raw))
        self._program.bind()
        self._program.enableAttributeArray(self._loc_pos)
        self._program.setAttributeBuffer(self._loc_pos, GL_FLOAT, 0, 3)
        self._program.release()
        vbo.release()
        vao.release()
        return vao, vbo, len(data) // 3

    def _create_dynamic(self):
        vao = QOpenGLVertexArrayObject(self)
        vao.create()
        vao.bind()
        vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        vbo.setUsagePattern(QOpenGLBuffer.DynamicDraw)
        vbo.create()
        vbo.bind()
        vbo.allocate(24)  # 2 vertices × 3 floats × 4 bytes
        self._program.bind()
        self._program.enableAttributeArray(self._loc_pos)
        self._program.setAttributeBuffer(self._loc_pos, GL_FLOAT, 0, 3)
        self._program.release()
        vbo.release()
        vao.release()
        return vao, vbo

    def _create_dynamic_uv(self):
        """A dynamic VAO/VBO interleaving position (3f) + UV (2f) per vertex —
        for textured faces."""
        vao = QOpenGLVertexArrayObject(self)
        vao.create()
        vao.bind()
        vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        vbo.setUsagePattern(QOpenGLBuffer.DynamicDraw)
        vbo.create()
        vbo.bind()
        vbo.allocate(40)  # 2 vertices × 5 floats × 4 bytes
        stride = 5 * 4
        self._program.bind()
        self._program.enableAttributeArray(self._loc_pos)
        self._program.setAttributeBuffer(self._loc_pos, GL_FLOAT, 0, 3, stride)
        self._program.enableAttributeArray(self._loc_uv)
        self._program.setAttributeBuffer(self._loc_uv, GL_FLOAT, 3 * 4, 2, stride)
        self._program.release()
        vbo.release()
        vao.release()
        return vao, vbo

    def _get_texture(self, path: str):
        """GL texture for an image ``path``, cached on the viewport. Returns the
        :class:`QOpenGLTexture` (Repeat wrap, linear+mipmap) or ``None`` if the
        image can't be loaded."""
        cache = getattr(self, "_tex_cache", None)
        if cache is None:
            cache = self._tex_cache = {}
        if path in cache:
            return cache[path]
        img = QImage(path)
        tex = None
        if not img.isNull():
            tex = QOpenGLTexture(img.mirrored())  # OBJ/SketchUp V is bottom-up
            tex.setWrapMode(QOpenGLTexture.Repeat)
            tex.setMinificationFilter(QOpenGLTexture.LinearMipMapLinear)
            tex.setMagnificationFilter(QOpenGLTexture.Linear)
        cache[path] = tex
        return tex

    #: Back-face colour, SketchUp's blue-grey: a visible back face means
    #: "you are looking at the inside" (or at a genuinely inverted face) —
    #: honest feedback the winding-proof shading used to hide.
    BACK_FACE_COLOR = (0.62, 0.70, 0.78)

    def _set_color(self, r: float, g: float, b: float, a: float) -> None:
        self._program.setUniformValue(self._loc_color, QVector4D(r, g, b, a))
        # Keep the back colour in sync by default so lines and highlights
        # (where gl_FrontFacing is meaningless) render one colour; the face
        # passes override it just before their draws.
        self._program.setUniformValue(self._loc_back_color,
                                      QVector4D(r, g, b, a))

    def _set_back_face_color(self) -> None:
        r, g, b = self.BACK_FACE_COLOR
        self._program.setUniformValue(self._loc_back_color,
                                      QVector4D(r, g, b, 1.0))

    def _shaded_color(self, base, normal):
        """Multiply ``base`` RGB by a subtle diffuse term from the face normal vs
        the fixed world light — the matte-model shading. Returns a clamped RGB
        tuple used as the render key (identical normals/colours group together)."""
        if normal.length() < 1e-9:
            shade = 0.90
        else:
            # abs(): shading depends on the face's plane, not its winding — a
            # flat plan whose faces happen to wind downward still reads bright,
            # while a solid keeps its top-bright / sides-toned maquette look.
            d = abs(QVector3D.dotProduct(normal.normalized(), self._LIGHT))
            shade = 0.80 + 0.20 * d                                     # 0.80..1.0
        # Quantise to 1/64 steps: the tuple is the DRAW-RUN key, and a model
        # with thousands of distinct normals (imported trees, curved detail)
        # otherwise explodes into one draw call per unique shade — 63k draw
        # calls per frame on a real 100k-face project. 1/64 banding is
        # invisible; the run count collapses to a few hundred.
        return (round(min(1.0, base[0] * shade) * 64.0) / 64.0,
                round(min(1.0, base[1] * shade) * 64.0) / 64.0,
                round(min(1.0, base[2] * shade) * 64.0) / 64.0)

    # ---- Base-map tiles (Track G) -------------------------------------------
    def _base_map_showing(self) -> bool:
        """True when a georeferenced base map is currently visible."""
        layer = getattr(self.scene, "tile_layer", None)
        return (layer is not None and getattr(layer, "visible", False)
                and getattr(self.scene, "georef", None) is not None)

    def _ensure_tile_fetcher(self):
        """Create the tile fetcher on first use (needs a running app)."""
        if self._tile_fetcher is None:
            from georef.tile_fetcher import TileFetcher
            self._tile_fetcher = TileFetcher(parent=self)
            self._tile_fetcher.tileReady.connect(self._on_tile_ready)
        return self._tile_fetcher

    def _on_tile_ready(self, source_id, x, y, z, image) -> None:
        """A downloaded tile arrived: stash its image and schedule a repaint."""
        layer = getattr(self.scene, "tile_layer", None)
        if (layer is not None and layer.source.id == source_id
                and z == layer.zoom):
            layer.images[(x, y, z)] = image
            self.tilesChanged.emit()
            self.update()

    def reset_tiles(self) -> None:
        """Drop cached GL textures + pending images (source/datum changed)."""
        if self._tile_textures:
            # Destroying GL textures needs the context current — this runs from
            # the Tray, not paintGL.
            self.makeCurrent()
            try:
                for tex in self._tile_textures.values():
                    if tex is not None:
                        tex.destroy()
            finally:
                self.doneCurrent()
        self._tile_textures.clear()
        self._tile_geom = None       # capture patches / datum may have changed
        if self._tile_fetcher is not None:
            self._tile_fetcher.cancel_all()
        layer = getattr(self.scene, "tile_layer", None)
        if layer is not None:
            layer.images.clear()
        self.update()

    # Max NEW GL textures created per frame. Uploading hundreds at once (a big
    # capture) overwhelms Mesa and reads back as garbage (black/green tears at
    # the far edge); creating a few per frame spreads it — the map fills in over
    # a second and repaints itself until done.
    _TEX_PER_FRAME = 6

    def _tile_texture(self, layer, x, y):
        """GL texture for tile ``(x, y)`` of ``layer``, or ``None`` if not yet
        available (a download is kicked off and the frame repaints on arrival)."""
        z = layer.zoom
        key = (layer.source.id, x, y, z)
        if key in self._tile_textures:
            return self._tile_textures[key]
        img = layer.images.get((x, y, z))
        if img is None:
            # Cache hit returns the image synchronously; a miss returns None and
            # starts an async download (see _on_tile_ready).
            img = self._ensure_tile_fetcher().request(layer.source, x, y, z)
            if img is None:
                return None
            layer.images[(x, y, z)] = img
        if self._tex_budget <= 0:
            self._tex_deferred = True     # too many this frame — next frame
            return None
        self._tex_budget -= 1
        tex = QOpenGLTexture(img)  # QImage is top-down; our UVs map north→v=0
        tex.setWrapMode(QOpenGLTexture.ClampToEdge)
        tex.setMinificationFilter(QOpenGLTexture.LinearMipMapLinear)
        tex.setMagnificationFilter(QOpenGLTexture.Linear)
        self._tile_textures[key] = tex
        return tex

    def _terrain_showing(self) -> bool:
        t = getattr(self.scene, "terrain", None)
        return t is not None and getattr(t, "visible", False)

    def prefetch_tiles(self, source, tile_list, zoom) -> None:
        """Request the given tiles so their images populate ``tile_layer.images``
        (used to build the 3D terrain mosaic even when the flat map is hidden)."""
        layer = getattr(self.scene, "tile_layer", None)
        if layer is None:
            return
        fetcher = self._ensure_tile_fetcher()
        for (x, y) in tile_list:
            img = fetcher.request(source, x, y, zoom)
            if img is not None:
                layer.images[(x, y, zoom)] = img

    def upload_terrain(self, terrain) -> None:
        """Build the terrain VBO (pos+uv) and its mosaic texture from a
        :class:`~georef.terrain.TerrainObject`."""
        if terrain is None or not terrain.vertices or not terrain.triangles:
            self._terrain_count = 0
            return
        self.makeCurrent()
        try:
            raw = array("f")
            verts, uvs = terrain.vertices, terrain.uvs
            for (i, j, k) in terrain.triangles:
                for idx in (i, j, k):
                    v = verts[idx]
                    u, w = uvs[idx]
                    raw.extend([v.x(), v.y(), v.z(), u, w])
            data = raw.tobytes()
            self._terrain_vbo.bind()
            self._terrain_vbo.allocate(data, len(data))
            self._terrain_vbo.release()
            self._terrain_count = len(raw) // 5
            if self._terrain_texture is not None:
                self._terrain_texture.destroy()
                self._terrain_texture = None
            img = terrain.texture_image
            if img is not None and not img.isNull():
                self._terrain_texture = QOpenGLTexture(img)
                self._terrain_texture.setWrapMode(QOpenGLTexture.ClampToEdge)
                self._terrain_texture.setMinificationFilter(
                    QOpenGLTexture.LinearMipMapLinear)
                self._terrain_texture.setMagnificationFilter(QOpenGLTexture.Linear)
        finally:
            self.doneCurrent()
        self.update()

    def _render_terrain(self) -> None:
        if not self._terrain_showing() or self._terrain_count == 0:
            return
        self._gl.glEnable(GL_POLYGON_OFFSET_FILL)
        self._gl.glPolygonOffset(1.0, 1.0)
        self._terrain_vao.bind()
        if self._terrain_texture is not None:
            self._program.setUniformValue(self._loc_use_tex, 1)
            self._terrain_texture.bind(0)
            self._gl.glDrawArrays(GL_TRIANGLES, 0, self._terrain_count)
            self._terrain_texture.release(0)
            self._program.setUniformValue(self._loc_use_tex, 0)
        else:
            self._set_color(0.55, 0.60, 0.52, 1.0)
            self._gl.glDrawArrays(GL_TRIANGLES, 0, self._terrain_count)
        self._terrain_vao.release()
        self._gl.glDisable(GL_POLYGON_OFFSET_FILL)

    def _ensure_tile_geometry(self, layer, datum):
        """Build the base-map tile quad VBO **once** for the current capture
        patches (not per frame), returning ``[(x, y, vert_start), ...]``. The
        capture is static, so a strip of many tiles still draws fast — each
        frame just binds textures and draws slices; no per-tile re-allocation."""
        key = (id(datum), tuple(layer.patches), layer.zoom, layer.source.id)
        cache = getattr(self, "_tile_geom", None)
        if cache is not None and cache[0] == key:
            return cache[1]
        raw = array("f")
        runs = []
        for (x, y) in layer.flat_tiles(datum):
            start = len(raw) // 5
            for pos, (u, v) in layer.quad_local(datum, x, y):
                raw.extend([pos.x(), pos.y(), pos.z(), u, v])
            runs.append((x, y, start))
        self._tile_quad_vbo.bind()
        self._tile_quad_vbo.allocate(raw.tobytes(), len(raw) * 4 or 4)
        self._tile_quad_vbo.release()
        self._tile_geom = (key, runs)
        return runs

    # Sky (top) and ground (bottom) backdrop colours — subtle two-tone, SketchUp.
    _SKY_RGB = (0.925, 0.935, 0.945)
    _GROUND_RGB = (0.815, 0.820, 0.815)

    def _horizon_ndc_y(self, mvp) -> float:
        """Screen-space NDC y of the horizon (the ground plane at infinity),
        from the camera orientation. Returns a value that may exceed ±1 when the
        horizon is off-screen (looking straight down = all ground)."""
        eye = self.camera.eye()
        fwd = self.camera.target - eye
        # Horizontal component of the view direction → its vanishing point.
        dh = QVector3D(fwd.x(), fwd.y(), 0.0)
        if dh.length() < 1e-5:
            # Looking straight down/up: no horizon on screen.
            return 2.0 if fwd.z() < 0 else -2.0
        dh = dh.normalized()
        # A point very far along the horizontal heading, at eye height: as the
        # distance → ∞ it converges to the horizon, so it stays put on zoom.
        far = eye + dh * 1.0e6
        clip = mvp.map(QVector4D(far.x(), far.y(), far.z(), 1.0))
        if abs(clip.w()) < 1e-9:
            return 2.0 if fwd.z() < 0 else -2.0
        return clip.y() / clip.w()

    def _draw_sky(self, mvp) -> None:
        """Fill sky above the horizon and ground below with two flat tones."""
        hy = max(-1.0, min(1.0, self._horizon_ndc_y(mvp)))
        self._program.setUniformValue(self._loc_mvp, QMatrix4x4())  # identity/NDC
        self._gl.glDisable(GL_DEPTH_TEST)
        self._gl.glDepthMask(GL_FALSE)
        self._sky_vao.bind()

        def quad(y0, y1, rgb):
            data = array("f", [-1, y0, 0, 1, y0, 0, 1, y1, 0,
                               -1, y0, 0, 1, y1, 0, -1, y1, 0])
            self._sky_vbo.bind()
            self._sky_vbo.allocate(data.tobytes(), len(data) * 4)
            self._sky_vbo.release()
            self._set_color(*rgb, 1.0)
            self._gl.glDrawArrays(GL_TRIANGLES, 0, 6)

        if hy < 1.0:
            quad(hy, 1.0, self._SKY_RGB)
        if hy > -1.0:
            quad(-1.0, hy, self._GROUND_RGB)
        # A subtle horizon line where sky meets ground (SketchUp).
        if -1.0 < hy < 1.0:
            line = array("f", [-1.0, hy, 0.0, 1.0, hy, 0.0])
            self._sky_vbo.bind()
            self._sky_vbo.allocate(line.tobytes(), len(line) * 4)
            self._sky_vbo.release()
            self._set_color(0.62, 0.64, 0.66, 1.0)
            self._gl.glDrawArrays(GL_LINES, 0, 2)
        self._sky_vao.release()
        self._gl.glEnable(GL_DEPTH_TEST)
        self._gl.glDepthMask(GL_TRUE)

    def _render_tiles(self) -> None:
        if self._terrain_showing():
            return  # the 3D terrain replaces the flat map
        layer = getattr(self.scene, "tile_layer", None)
        datum = getattr(self.scene, "georef", None)
        if layer is None or datum is None or not getattr(layer, "visible", False):
            return
        try:
            runs = self._ensure_tile_geometry(layer, datum)
        except Exception:
            return
        if not runs:
            return
        # Budget new texture uploads this frame (see _tile_texture).
        self._tex_budget = self._TEX_PER_FRAME
        self._tex_deferred = False
        self._program.setUniformValue(self._loc_use_tex, 1)
        self._gl.glDepthMask(GL_FALSE)
        self._tile_quad_vao.bind()
        for (x, y, start) in runs:
            tex = self._tile_texture(layer, x, y)
            if tex is None:
                continue
            tex.bind(0)
            self._gl.glDrawArrays(GL_TRIANGLES, start, 6)
            tex.release(0)
        self._tile_quad_vao.release()
        self._gl.glDepthMask(GL_TRUE)
        self._program.setUniformValue(self._loc_use_tex, 0)
        if self._tex_deferred:            # more tiles to upload — schedule a frame
            self.update()

    # ---- Dynamic uploads ----------------------------------------------------
    def notify_scene_changed(self) -> None:
        """Force a redraw and emit the version-changed signal.

        Use this when an outside system (load, undo, redo) has mutated the
        scene and wants subscribers (title-bar dirty flag, etc.) to react
        without waiting for the next paint.
        """
        self.sceneVersionChanged.emit(self.scene.version)
        self.update()

    def _sync_edges(self) -> None:
        if self.scene.version == self._edges_version:
            return
        _st0 = _time_mod.perf_counter() if _PERF else 0.0

        # The scene changed: purge hover/selection references to entities that
        # no longer exist, or deleted geometry keeps ghost-rendering (blue
        # hover / orange selection) until the mouse moves or a click replaces
        # the selection. Renderer-level guarantee — holds no matter which
        # command forgot to discard. Membership is checked per candidate
        # (identity scans in C) instead of materialising 300k-entity sets;
        # a huge candidate list falls back to the set walk.
        hover = self._hover_entity
        cands = [hover] if isinstance(hover, (Edge, Face)) else []
        cands += [s for s in self.scene.selection
                  if isinstance(s, (Edge, Face))]
        if cands:
            if len(cands) > 64:
                alive_e: set = set(self.scene.render_edges())
                alive_f: set = set(self.scene.render_faces())

                def alive(ent):
                    return ent in (alive_e if isinstance(ent, Edge)
                                   else alive_f)
            else:
                gmeshes = [g.mesh for g in self.scene.groups
                           if self.scene.entity_visible(g)
                           and not getattr(g, "billboard", False)]

                def alive(ent):
                    if isinstance(ent, Edge):
                        if ent in self.scene.loose_mesh.edges:
                            return self.scene.entity_visible(ent)
                        return any(ent in gm.edges for gm in gmeshes)
                    if ent in self.scene.loose_mesh.faces:
                        return self.scene.entity_visible(ent)
                    return any(ent in gm.faces for gm in gmeshes)

            if isinstance(hover, (Edge, Face)) and not alive(hover):
                self._hover_entity = None
            for s in [s for s in self.scene.selection
                      if isinstance(s, (Edge, Face)) and not alive(s)]:
                self.scene.selection.discard(s)

        # Hard edges: loose ones rebuilt fresh, group ones from cached chunks
        # (composition mirrors scene.render_edges()).
        all_loose = array("f")
        for e in self.scene.loose_mesh.edges:
            if not self.scene.entity_visible(e) or getattr(e, "soft", False):
                continue  # hidden layer / curve segment (hidden, reads smooth)
            all_loose.extend([
                e.a.x(), e.a.y(), e.a.z(),
                e.b.x(), e.b.y(), e.b.z(),
            ])
        edge_parts = [all_loose.tobytes()]
        for g in self.scene.groups:
            if (self.scene.entity_visible(g)
                    and not getattr(g, "billboard", False)):
                edge_parts.append(self._group_chunk(g)["edges"])
        edges_raw = b"".join(edge_parts)
        self._edges_vbo.bind()
        if edges_raw:
            self._edges_vbo.allocate(edges_raw, len(edges_raw))
        else:
            self._edges_vbo.allocate(24)
        self._edges_vbo.release()
        self._edges_count = len(edges_raw) // 12

        # The selection set is heterogeneous (edges, faces and/or whole
        # groups). A selected GROUP highlights via its cached chunk — walking
        # + re-triangulating a 100k-face imported group froze the app the
        # moment the user clicked it.
        sel_loose = array("f")
        sel_edge_parts = []
        sel_face_parts = []
        for ent in self.scene.selection:
            if isinstance(ent, Edge):
                sel_loose.extend([ent.a.x(), ent.a.y(), ent.a.z(),
                                  ent.b.x(), ent.b.y(), ent.b.z()])
            elif isinstance(ent, Group):
                # Chunk edges already exclude soft seams (a grouped smooth
                # cylinder must not flash its segment seams in orange).
                chunk = self._group_chunk(ent)
                sel_edge_parts.append(chunk["edges"])
                sel_face_parts.append(self._chunk_tri_pos(chunk))
        sel_raw = sel_loose.tobytes() + b"".join(sel_edge_parts)
        self._selected_vbo.bind()
        if sel_raw:
            self._selected_vbo.allocate(sel_raw, len(sel_raw))
        else:
            self._selected_vbo.allocate(24)
        self._selected_vbo.release()
        self._selected_count = len(sel_raw) // 12

        sel_face_loose = array("f")
        for ent in self.scene.selection:
            if isinstance(ent, Face):
                for t0, t1, t2 in ent.triangulate():
                    sel_face_loose.extend([
                        t0.x(), t0.y(), t0.z(),
                        t1.x(), t1.y(), t1.z(),
                        t2.x(), t2.y(), t2.z(),
                    ])
        sel_face_raw = sel_face_loose.tobytes() + b"".join(sel_face_parts)
        self._sel_faces_vbo.bind()
        if sel_face_raw:
            self._sel_faces_vbo.allocate(sel_face_raw, len(sel_face_raw))
        else:
            self._sel_faces_vbo.allocate(24)
        self._sel_faces_vbo.release()
        self._sel_faces_count = len(sel_face_raw) // 12

        # Faces: triangulate each face (fan when simple, hole-aware when the
        # face has been divided) into one VBO, but grouped by material colour
        # (attrs["color"], default cream) so each colour is a single draw call
        # with its own uniform. Loose faces rebuild fresh; untouched groups
        # contribute their cached chunk buffers (a 17k-face reference model
        # made every stroke pay a ~1 s re-triangulation otherwise).
        suppressed_faces = self._suppressed_faces
        by_color: dict = {}          # loose faces (array("f") buffers)
        by_texture: dict = {}        # image path -> interleaved pos+uv array
        group_color: dict = {}       # chunk byte-parts per colour key
        group_texture: dict = {}     # chunk byte-parts per image path

        def bucket_face(face):
            if face in suppressed_faces:
                return
            tex = face.attrs.get("texture")
            if tex is not None and tex.get("path"):
                self._append_textured_face(by_texture, face, tex)
                return
            col = face.attrs.get("color")
            base = tuple(col) if col is not None else self.DEFAULT_FACE_COLOR
            # Bake a subtle diffuse shade from the face normal against a fixed
            # world light — the matte-model look of SketchUp. World-fixed, so
            # it doesn't change as you orbit; faces sharing a normal+colour
            # share the shaded key, keeping the draw-call count low.
            key = self._shaded_color(base, face.normal())
            buf = by_color.get(key)
            if buf is None:
                buf = by_color[key] = array("f")
            for t0, t1, t2 in face.triangulate():
                buf.extend([
                    t0.x(), t0.y(), t0.z(),
                    t1.x(), t1.y(), t1.z(),
                    t2.x(), t2.y(), t2.z(),
                ])

        for face in self.scene.loose_mesh.faces:
            if self.scene.entity_visible(face):
                bucket_face(face)
        for g in self.scene.groups:
            if (not self.scene.entity_visible(g)
                    or getattr(g, "billboard", False)):
                continue
            if suppressed_faces and any(f in suppressed_faces
                                        for f in g.mesh.faces):
                for face in g.mesh.faces:
                    bucket_face(face)   # push/pull preview suppresses faces
                continue
            chunk = self._group_chunk(g)
            for key, raw in chunk["by_color"].items():
                group_color.setdefault(key, []).append(raw)
            for path, raw in chunk["by_texture"].items():
                group_texture.setdefault(path, []).append(raw)

        face_parts = []
        self._face_runs = []
        start = 0
        for key in dict.fromkeys(list(by_color) + list(group_color)):
            parts = ([by_color[key].tobytes()] if key in by_color else [])
            parts += group_color.get(key, [])
            raw = b"".join(parts)
            face_parts.append(raw)
            count = len(raw) // 12
            self._face_runs.append((key, start, count))
            start += count
        face_raw = b"".join(face_parts)
        self._faces_vbo.bind()
        if face_raw:
            self._faces_vbo.allocate(face_raw, len(face_raw))
        else:
            self._faces_vbo.allocate(24)
        self._faces_vbo.release()
        self._faces_count = len(face_raw) // 12

        # Textured faces: one interleaved (pos+uv) VBO, a run per image path.
        tex_parts = []
        self._tex_runs = []
        start = 0
        for path in dict.fromkeys(list(by_texture) + list(group_texture)):
            parts = ([by_texture[path].tobytes()] if path in by_texture else [])
            parts += group_texture.get(path, [])
            raw = b"".join(parts)
            tex_parts.append(raw)
            count = len(raw) // 20
            self._tex_runs.append((path, start, count))
            start += count
        tex_raw = b"".join(tex_parts)
        self._tex_faces_vbo.bind()
        if tex_raw:
            self._tex_faces_vbo.allocate(tex_raw, len(tex_raw))
        else:
            self._tex_faces_vbo.allocate(40)
        self._tex_faces_vbo.release()
        self._tex_faces_count = len(tex_raw) // 20

        if _PERF:
            _plog("sync_edges", (_time_mod.perf_counter() - _st0) * 1000.0)
        self._edges_version = self.scene.version
        self.sceneVersionChanged.emit(self._edges_version)

    def _append_textured_face(self, by_texture: dict, face, tex: dict) -> None:
        """Triangulate ``face`` into ``by_texture[path]`` as interleaved
        ``pos(3) + uv(2)`` floats, the UVs planar-projected SketchUp-style from
        each triangle vertex's world position (so coplanar faces tile
        seamlessly)."""
        path = tex["path"]
        buf = by_texture.get(path)
        if buf is None:
            buf = by_texture[path] = array("f")
        n = face.normal().normalized()
        u_axis, v_axis = plane_axes(n)
        rot = float(tex.get("rot", 0.0))
        if rot:
            a = math.radians(rot)
            cos_a, sin_a = math.cos(a), math.sin(a)
            u_axis, v_axis = (u_axis * cos_a + v_axis * sin_a,
                              v_axis * cos_a - u_axis * sin_a)
        sw = tex.get("sw", 1.0) or 1.0
        sh = tex.get("sh", 1.0) or 1.0
        for tri in face.triangulate():
            for p in tri:
                buf.extend([
                    p.x(), p.y(), p.z(),
                    QVector3D.dotProduct(p, u_axis) / sw,
                    QVector3D.dotProduct(p, v_axis) / sh,
                ])

    def _billboard_quad(self, group):
        """The face-me quad of a billboard group, rotated around its vertical
        anchor axis to face the camera NOW. Returns (corners[4], tex_path) or
        ``None``. Shared by the render pass and picking so what you see is
        what you click."""
        verts = group.mesh.vertices
        if not verts:
            return None
        xs = [v.position.x() for v in verts]
        ys = [v.position.y() for v in verts]
        zs = [v.position.z() for v in verts]
        anchor = QVector3D((min(xs) + max(xs)) / 2,
                           (min(ys) + max(ys)) / 2, min(zs))
        w = max(max(xs) - min(xs), max(ys) - min(ys))
        h = max(zs) - min(zs)
        tex = None
        for f in group.mesh.faces:
            t = f.attrs.get("texture")
            if t and t.get("path"):
                tex = t["path"]
                break
        if tex is None or w < 1e-9 or h < 1e-9:
            return None
        d = self.camera.eye() - anchor
        d.setZ(0.0)
        if d.length() < 1e-6:
            d = QVector3D(1.0, 0.0, 0.0)
        d = d.normalized()
        r = QVector3D(-d.y(), d.x(), 0.0)
        up = QVector3D(0.0, 0.0, 1.0)
        c0 = anchor - r * (w / 2)
        c1 = anchor + r * (w / 2)
        return ([c0, c1, c1 + up * h, c0 + up * h], tex)

    def _draw_billboards(self) -> None:
        """Per-frame pass: each face-me billboard is a textured cutout quad
        turned toward the camera (SketchUp's 2D people). Depth-tested, so it
        hides behind walls correctly; the shader discards transparent texels."""
        groups = [g for g in self.scene.groups
                  if getattr(g, "billboard", False)
                  and self.scene.entity_visible(g)]
        if not groups:
            return
        self._program.setUniformValue(self._loc_use_tex, 1)
        self._billboard_vao.bind()
        for g in groups:
            quad = self._billboard_quad(g)
            if quad is None:
                continue
            corners, path = quad
            tex = self._get_texture(path)
            if tex is None:
                continue
            data = array("f")
            uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
            for idx in (0, 1, 2, 0, 2, 3):
                c = corners[idx]
                u, v = uvs[idx]
                data.extend([c.x(), c.y(), c.z(), u, v])
            raw = data.tobytes()
            self._billboard_vbo.bind()
            self._billboard_vbo.allocate(raw, len(raw))
            self._billboard_vbo.release()
            tex.bind(0)
            self._gl.glDrawArrays(GL_TRIANGLES, 0, 6)
            if g in self.scene.selection:
                # Selection cue: repaint with the highlight colour untextured.
                pass
        self._billboard_vao.release()
        self._program.setUniformValue(self._loc_use_tex, 0)

    def _upload_hover_face(self, face: Face) -> int:
        """Triangulate ``face`` into the hover-faces VBO. Returns vertex count."""
        data = array("f")
        for t0, t1, t2 in face.triangulate():
            data.extend([
                t0.x(), t0.y(), t0.z(),
                t1.x(), t1.y(), t1.z(),
                t2.x(), t2.y(), t2.z(),
            ])
        self._hover_faces_vbo.bind()
        if data:
            raw = data.tobytes()
            self._hover_faces_vbo.allocate(raw, len(raw))
        else:
            self._hover_faces_vbo.allocate(24)
        self._hover_faces_vbo.release()
        return len(data) // 3

    def _upload_silhouette_edges(self) -> int:
        """Upload the *profile* edges into the silhouette VBO and return the
        vertex count. A soft (hidden) edge is drawn when it lies on the
        silhouette — its two faces straddle the view, one turned toward the
        camera and one away — so a curved surface shows its outline. A soft
        edge with a single face is always a profile (a boundary). View-dependent,
        called each frame."""
        # Throttle: the silhouette is view-dependent but re-deriving it at
        # most ~12×/s is visually indistinguishable, and at 100k soft edges
        # the NumPy pass still costs ~4 ms a frame during orbits.
        import time as _time
        now = _time.monotonic()
        key = (self.scene.version, id(self.scene.mesh))
        last = getattr(self, "_sil_last", None)
        if last is not None and last[0] == key and now - last[1] < 0.08:
            return last[2]        # VBO still holds the last upload

        # Loose soft edges (few — the user's own curves) walk in Python;
        # group soft edges (an imported project can carry ~100k) run the view
        # test vectorised over the group chunk's cached arrays.
        cached = getattr(self, "_soft_edges_cache", None)
        if cached is None or cached[0] != key:
            cached = (key, [e for e in self.scene.loose_mesh.edges
                            if getattr(e, "soft", False)
                            and self.scene.entity_visible(e)])
            self._soft_edges_cache = cached
        eye = self.camera.eye()
        data = array("f")
        for e in cached[1]:
            faces = e.faces
            # A 1-face soft edge is an open-surface boundary (a real profile); a
            # 0-face one is a dangling line (not drawn here).
            silhouette = len(faces) == 1
            if len(faces) == 2:
                s0 = QVector3D.dotProduct(faces[0].normal(),
                                          faces[0].centroid() - eye)
                s1 = QVector3D.dotProduct(faces[1].normal(),
                                          faces[1].centroid() - eye)
                silhouette = (s0 < 0) != (s1 < 0)
            if silhouette:
                data.extend([e.a.x(), e.a.y(), e.a.z(),
                             e.b.x(), e.b.y(), e.b.z()])
        chunks: list = [data.tobytes()]
        groups = [g for g in self.scene.groups
                  if self.scene.entity_visible(g)
                  and not getattr(g, "billboard", False)]
        if groups:
            import numpy as np
            e_np = np.array([eye.x(), eye.y(), eye.z()])
            for g in groups:
                ch = self._group_chunk(g)
                if ch["soft_pts"] is None:
                    continue
                s0 = np.einsum("ij,ij->i", ch["soft_n0"],
                               ch["soft_c0"] - e_np)
                s1 = np.einsum("ij,ij->i", ch["soft_n1"],
                               ch["soft_c1"] - e_np)
                mask = ch["soft_single"] | ((s0 < 0) != (s1 < 0))
                if mask.any():
                    chunks.append(ch["soft_pts"][mask].tobytes())
        raw = b"".join(chunks)
        self._silhouette_vbo.bind()
        if raw:
            self._silhouette_vbo.allocate(raw, len(raw))
        else:
            self._silhouette_vbo.allocate(24)
        self._silhouette_vbo.release()
        count = len(raw) // 12
        self._sil_last = (key, now, count)
        return count

    def _upload_hover_edge(self, edge: Edge) -> int:
        """Upload the hovered edge — or, for a curve segment, its whole contour
        (what a click would select) — into the hover-edges VBO. Returns the
        vertex count to draw."""
        edges = (self.scene.mesh.curve_edges(edge)
                 if getattr(edge, "curve", None) is not None else [edge])
        if edge not in edges:      # e.g. a group's edge — not in the main mesh
            edges = [edge]
        data = array("f")
        for e in edges:
            data.extend([e.a.x(), e.a.y(), e.a.z(),
                         e.b.x(), e.b.y(), e.b.z()])
        self._hover_edges_vbo.bind()
        raw = data.tobytes()
        self._hover_edges_vbo.allocate(raw, len(raw))
        self._hover_edges_vbo.release()
        return len(data) // 3

    def set_hover(self, entity) -> None:
        """Set the entity (edge/face) highlighted under the cursor and repaint
        if it changed. ``None`` clears the highlight."""
        if entity is self._hover_entity:
            return
        self._hover_entity = entity
        self.update()

    def flash_status(self, text: str, msec: int = 2500) -> None:
        """Briefly show ``text`` in the main window's status bar (e.g. Push/Pull's
        "Offset limited to X m"). No-op if there is no status bar yet."""
        window = self.window()
        bar = window.statusBar() if window is not None else None
        if bar is not None:
            bar.showMessage(text, msec)

    def set_suppressed_faces(self, faces) -> None:
        """Hide a set of scene faces from the normal pass (e.g. the flat inner
        face a Push/Pull is recessing). Identity-keyed; empty set restores.
        No-op when unchanged so the drag doesn't rebuild every frame."""
        faces = set(faces)
        if faces == self._suppressed_faces:
            return
        self._suppressed_faces = faces
        self._edges_version = -1  # the faces VBO is rebuilt by _sync_edges
        self.update()

    def _draw_preview_faces(self) -> None:
        """Triangulate and draw the active tool's solid preview faces (if any)
        in the same warm cream as real faces, so an extrusion looks solid as it
        forms. Depth-tested with a polygon offset so the wireframe sits cleanly
        on top."""
        tool = self.active_tool
        provider = getattr(tool, "preview_faces", None) if tool is not None else None
        if not callable(provider):
            return
        faces = provider()
        if not faces:
            return
        data = array("f")
        runs = []                        # (shaded_rgb, start_vertex, count)
        for face in faces:
            start = len(data) // 3
            for t0, t1, t2 in face.triangulate():
                data.extend([
                    t0.x(), t0.y(), t0.z(),
                    t1.x(), t1.y(), t1.z(),
                    t2.x(), t2.y(), t2.z(),
                ])
            runs.append((self._shaded_color(self.DEFAULT_FACE_COLOR, face.normal()),
                         start, len(data) // 3 - start))
        if not data:
            return
        self._preview_faces_vbo.bind()
        raw = data.tobytes()
        self._preview_faces_vbo.allocate(raw, len(raw))
        self._preview_faces_vbo.release()

        self._gl.glEnable(GL_POLYGON_OFFSET_FILL)
        self._gl.glPolygonOffset(1.0, 1.0)
        self._preview_faces_vao.bind()
        for (r, g, b), start, count in runs:
            self._set_color(r, g, b, 1.0)
            self._set_back_face_color()
            self._gl.glDrawArrays(GL_TRIANGLES, start, count)
        self._preview_faces_vao.release()
        self._gl.glDisable(GL_POLYGON_OFFSET_FILL)

    def _draw_rubber_band(self) -> None:
        self._overlay_rubber = None
        tool = self.active_tool
        if tool is None:
            return
        segments = tool.rubber_band_lines()
        if not segments:
            return

        # A tool can force its preview-line colour (Push/Pull uses the normal
        # edge colour so its forming box reads like real geometry, not a loose
        # orange rubber band).
        forced = getattr(tool, "wireframe_color", None)
        snap = self.last_snap
        # Axis / reference / extension cues read as "projection lines": draw them
        # a touch thicker so the alignment is easy to spot while drawing.
        inference = (
            forced is None
            and snap is not None
            and snap.kind in ("axis", "axis_inference", "reference", "extension",
                              "through_point", "perp_face")
        )
        if forced is not None:
            color = forced
        elif snap is not None and snap.kind == "axis":
            r, g, b = snap.color
            color = (r, g, b, 1.0)
        elif snap is not None and snap.kind == "axis_inference":
            r, g, b = snap.color
            color = (r, g, b, 0.50)
        elif snap is not None and snap.kind in ("reference", "through_point", "perp_face"):
            r, g, b = snap.color
            color = (r, g, b, 1.0)
        elif snap is not None and snap.kind == "close":
            color = (0.20, 0.40, 0.78, 0.95)
        else:
            color = (0.95, 0.45, 0.16, 0.85)

        # Depth-tested previews (Push/Pull, Offset, Paste) read like real
        # geometry and need GL hidden-line removal — draw them via GL. The
        # "always on top" previews (Line/Rectangle/Move) are stashed for the
        # QPainter overlay instead, where a thick pen is reliable (Core-profile
        # glLineWidth often clamps to 1px on Mesa, so GL can't thicken them).
        if getattr(tool, "wireframe_depth_tested", False):
            data = array("f")
            for a, b in segments:
                data.extend([a.x(), a.y(), a.z(), b.x(), b.y(), b.z()])
            raw = data.tobytes()
            self._rubber_vbo.bind()
            self._rubber_vbo.allocate(raw, len(raw))
            self._rubber_vbo.release()

            self._set_color(*color)
            self._rubber_vao.bind()
            self._gl.glDrawArrays(GL_LINES, 0, len(data) // 3)
            self._rubber_vao.release()
        else:
            self._overlay_rubber = (segments, color, 2.5 if inference else 2.0)

    def _draw_rubber_band_overlay(self, painter: QPainter) -> None:
        if self._overlay_rubber is None:
            return
        segments, color, width = self._overlay_rubber
        r, g, b = color[0], color[1], color[2]
        a = color[3] if len(color) > 3 else 1.0
        pen = QPen(QColor.fromRgbF(r, g, b, a), width)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        for p0, p1 in segments:
            q0 = self._world_to_pixel(p0)
            q1 = self._world_to_pixel(p1)
            if q0 is not None and q1 is not None:
                painter.drawLine(QPointF(*q0), QPointF(*q1))

    # ---- 2D overlay (QPainter on top of OpenGL) -----------------------------
    def _draw_overlay(self) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        # Rubber band for the "always on top" tools (Line/Rectangle/Move),
        # drawn here with a thick, reliable pen.
        self._draw_rubber_band_overlay(painter)

        # Snap indicator
        if (
            self.active_tool is not None
            and self.last_snap is not None
            and self.last_snap.kind != "none"
        ):
            self._draw_snap_indicator(painter, self.last_snap)

        # Push/Pull distance-inference marker (a green square on the corner/face
        # the extrusion is snapping level with).
        self._draw_inference_marker(painter)

        # Construction guides (Tape Measure) — fine dashed scaffolding lines.
        self._draw_guides(painter)
        self._draw_edit_group_box(painter)

        # Terrain-surface fills (draped / flat) under the georef paths — Track G.
        self._draw_geo_surfaces(painter)

        # Traced georef paths (roads / boundaries) — Track G.
        self._draw_geo_paths(painter)

        # Profile→plan marker: the route point at the station hovered in the
        # profile panel (Track G).
        if self._route_marker is not None:
            q = self._world_to_pixel(self.drape(self._route_marker))
            if q is not None:
                painter.setBrush(QColor(243, 115, 41))
                painter.setPen(QPen(QColor(255, 255, 255), 1.5))
                painter.drawEllipse(QPointF(*q), 6, 6)
                painter.setBrush(Qt.NoBrush)

        # Persistent dimension annotations
        self._draw_dimensions(painter)

        # Length measurement near rubber band
        self._draw_length_label(painter)

        # Labels in the top-left. Reference > explicit axis lock > soft inference.
        if self.reference_mode is not None:
            self._draw_reference_label(painter)
        elif self.axis_lock is not None:
            self._draw_axis_lock_label(painter)
        else:
            self._draw_inference_label(painter)

        # Linear-inference toggle state (Alt), shown while not on the default.
        if self.linear_inference_mode != "all":
            self._draw_linear_mode_label(painter)

        # Rubber-band selection box.
        self._draw_selection_box(painter)

        painter.end()

    def _draw_linear_mode_label(self, painter: QPainter) -> None:
        text = {
            "off": "Inferencias lineales: OFF (Alt)",
            "parallel_perp": "Inferencias: solo paralela / perpendicular (Alt)",
        }.get(self.linear_inference_mode)
        if not text:
            return
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        painter.setPen(QPen(QColor(210, 150, 40)))
        painter.drawText(QPointF(14, self.height() - 16), text)

    def _draw_selection_box(self, painter: QPainter) -> None:
        if not self._box_active or self._box_start is None or self._box_cur is None:
            return
        s, c = self._box_start, self._box_cur
        if math.hypot(c.x() - s.x(), c.y() - s.y()) < self.BOX_DRAG_THRESHOLD_PX:
            return
        rect = QRectF(
            min(s.x(), c.x()), min(s.y(), c.y()),
            abs(c.x() - s.x()), abs(c.y() - s.y()),
        )
        crossing = (c.x() - s.x()) < 0
        if crossing:
            # Crossing: dashed green, selects anything it touches.
            pen = QPen(QColor(40, 158, 92), 1.5, Qt.DashLine)
            fill = QColor(40, 158, 92, 28)
        else:
            # Window: solid blue, selects only fully enclosed.
            pen = QPen(QColor(51, 102, 199), 1.5, Qt.SolidLine)
            fill = QColor(51, 102, 199, 28)
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawRect(rect)

    def _draw_snap_indicator(self, painter: QPainter, snap: SnapResult) -> None:
        # Axis-lock and inference state is conveyed by the coloured rubber
        # band; no badge follows the cursor along the lock line. Only the
        # discrete point snaps get a marker.
        if snap.kind in ("axis_inference", "axis"):
            return
        pixel = self._world_to_pixel(snap.point)
        if pixel is None:
            return
        r, g, b = snap.color
        color = QColor.fromRgbF(r, g, b, 1.0)
        # Dashed guide line (the extension inference shows the edge's dashed
        # continuation to the cursor).
        if snap.guide is not None:
            gp0 = self._world_to_pixel(snap.guide[0])
            gp1 = self._world_to_pixel(snap.guide[1])
            if gp0 is not None and gp1 is not None:
                # A snap can colour its guide differently from the marker (the
                # 'from point' guide is axis-coloured while the point is green).
                gc = snap.guide_color if snap.guide_color is not None else (r, g, b)
                dash = QPen(QColor.fromRgbF(gc[0], gc[1], gc[2], 0.9), 2.0, Qt.DashLine)
                painter.setPen(dash)
                painter.drawLine(QPointF(*gp0), QPointF(*gp1))
        painter.setPen(QPen(color, 2.0))
        painter.setBrush(QColor.fromRgbF(r, g, b, 0.25))
        px, py = pixel
        if snap.kind == "intersection":
            # X marker at the crossing (drawn line × projected guide),
            # SketchUp-style — reads as a distinct point on the junction.
            painter.setPen(QPen(color, 2.0))
            painter.drawLine(QPointF(px - 6, py - 6), QPointF(px + 6, py + 6))
            painter.drawLine(QPointF(px - 6, py + 6), QPointF(px + 6, py - 6))
        elif snap.kind in ("endpoint", "origin", "on_edge", "extension", "from_point"):
            painter.drawRect(QRectF(px - 5, py - 5, 10, 10))
        elif snap.kind == "midpoint":
            # Cyan diamond, SketchUp-style.
            diamond = QPolygonF([
                QPointF(px, py - 6),
                QPointF(px + 6, py),
                QPointF(px, py + 6),
                QPointF(px - 6, py),
            ])
            painter.drawPolygon(diamond)
        elif snap.kind == "on_face":
            # Small dot — the cursor is over a face, ready to draw on it.
            painter.drawEllipse(QPointF(px, py), 4.0, 4.0)
        elif snap.kind == "close":
            painter.drawEllipse(QPointF(px, py), 7.0, 7.0)
        elif snap.kind in ("reference", "through_point", "perp_face"):
            # Small circle marker for directional locks (parallel/perpendicular,
            # through point, perpendicular to face).
            painter.drawEllipse(QPointF(px, py), 5.0, 5.0)

        # Tooltip text next to the marker (SketchUp shows "On Edge", etc.).
        label = self._SNAP_LABELS.get(snap.kind)
        if label:
            label = tr(label)
            font = QFont()
            font.setPointSize(9)
            painter.setFont(font)
            painter.setPen(QPen(QColor(255, 255, 255, 220)))
            painter.drawText(QPointF(px + 11, py + 17), label)
            painter.setPen(QPen(color))
            painter.drawText(QPointF(px + 10, py + 16), label)

    def _draw_edit_group_box(self, painter: QPainter) -> None:
        """Dashed bounding box around the group being edited — the visual cue
        that you are INSIDE it (SketchUp draws the same box)."""
        group = self.scene.edit_group
        if group is None or not group.mesh.vertices:
            return
        xs = [v.position.x() for v in group.mesh.vertices]
        ys = [v.position.y() for v in group.mesh.vertices]
        zs = [v.position.z() for v in group.mesh.vertices]
        lo = (min(xs), min(ys), min(zs))
        hi = (max(xs), max(ys), max(zs))
        corners = [QVector3D(x, y, z)
                   for x in (lo[0], hi[0])
                   for y in (lo[1], hi[1])
                   for z in (lo[2], hi[2])]
        pix = [self._world_to_pixel(c) for c in corners]
        if any(p is None for p in pix):
            return
        pen = QPen(QColor(90, 110, 140), 1, Qt.DashLine)
        painter.setPen(pen)
        # Box edges: corner indices differing in exactly one axis bit.
        for i in range(8):
            for bit in (1, 2, 4):
                j = i | bit
                if j != i:
                    painter.drawLine(int(pix[i][0]), int(pix[i][1]),
                                     int(pix[j][0]), int(pix[j][1]))

    def _draw_guides(self, painter: QPainter) -> None:
        """Draw construction guides: fine dashed lines (and small crosses for
        guide points), SketchUp-style scaffolding."""
        guides = getattr(self.scene, "guides", None)
        if not guides:
            return
        pen = QPen(QColor(70, 90, 120), 1, Qt.DashLine)
        painter.setPen(pen)
        for g in guides:
            if g.is_line:
                a, b = g.segment()
                pa = self._world_to_pixel(a)
                pb = self._world_to_pixel(b)
                if pa is not None and pb is not None:
                    painter.drawLine(QPointF(*pa), QPointF(*pb))
            else:
                q = self._world_to_pixel(g.point)
                if q is not None:
                    painter.drawLine(QPointF(q[0] - 5, q[1]), QPointF(q[0] + 5, q[1]))
                    painter.drawLine(QPointF(q[0], q[1] - 5), QPointF(q[0], q[1] + 5))

    def _draw_geo_surfaces(self, painter: QPainter) -> None:
        """Draw terrain-surface fills as shaded, back-to-front triangles so the
        relief reads in 3D (Track G). Semi-transparent, so the base map shows."""
        paths = getattr(self.scene, "geo_paths", None)
        if not paths:
            return
        eye = self.camera.eye()
        light = QVector3D(0.3, 0.4, 0.85)
        light = light.normalized()
        for path in paths:
            tris = getattr(path, "_surface_tris", None)
            if not tris:
                continue
            flat = getattr(path, "surface", None) == "flat"
            # Painter's algorithm: far triangles first.
            ordered = sorted(
                tris, key=lambda t: -((t[0] + t[1] + t[2]) - eye * 3.0).lengthSquared())
            painter.setPen(Qt.NoPen)
            for v0, v1, v2 in ordered:
                p0 = self._world_to_pixel(v0)
                p1 = self._world_to_pixel(v1)
                p2 = self._world_to_pixel(v2)
                if p0 is None or p1 is None or p2 is None:
                    continue
                # Flat shading from the face normal (relief legibility).
                n = QVector3D.crossProduct(v1 - v0, v2 - v0)
                if n.length() > 1e-9:
                    n = n.normalized()
                shade = 0.55 + 0.45 * max(0.0, abs(QVector3D.dotProduct(n, light)))
                if flat:
                    col = QColor(int(90 * shade), int(120 * shade), int(200 * shade), 150)
                else:
                    col = QColor(int(120 * shade), int(160 * shade), int(90 * shade), 150)
                painter.setBrush(col)
                painter.drawPolygon(QPolygonF([QPointF(*p0), QPointF(*p1), QPointF(*p2)]))
            painter.setBrush(Qt.NoBrush)

    def drape(self, v: QVector3D) -> QVector3D:
        """Lift a Z=0 georef point onto the 3D terrain (its relief height) when
        the terrain is showing, so routes/markers sit on the ground instead of
        floating at the Z=0 reference plane. A no-op otherwise."""
        t = getattr(self.scene, "terrain", None)
        if t is not None and getattr(t, "visible", False):
            z = t.height_at(v.x(), v.y())
            if z is not None:
                return QVector3D(v.x(), v.y(), z)
        return v

    def _draw_geo_paths(self, painter: QPainter) -> None:
        """Draw committed georef paths (roads / boundaries): a coloured polyline
        with node handles. Selected paths and the hovered node highlight."""
        paths = getattr(self.scene, "geo_paths", None)
        if not paths:
            return
        base_ink = QColor(0, 190, 210)          # cyan — reads over terrain
        sel_ink = QColor(243, 115, 41)          # selection orange
        selection = self.scene.selection
        hover_node = getattr(self, "_hover_geo_node", None)
        for path in paths:
            ink = sel_ink if path in selection else base_ink
            pix = [self._world_to_pixel(self.drape(p)) for p in path.points]
            painter.setPen(QPen(ink, 2.5))
            for a, b in zip(pix, pix[1:]):
                if a is not None and b is not None:
                    painter.drawLine(QPointF(*a), QPointF(*b))
            if path.closed and len(pix) > 2 and pix[0] and pix[-1]:
                painter.drawLine(QPointF(*pix[-1]), QPointF(*pix[0]))
            # Node handles.
            painter.setBrush(ink)
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            for i, q in enumerate(pix):
                if q is None:
                    continue
                r = 5.0 if (hover_node == (path, i)) else 3.5
                painter.drawEllipse(QPointF(*q), r, r)
            painter.setBrush(Qt.NoBrush)
            # Area + perimeter label at the centroid of a selected polygon.
            if path in selection and path.closed and len(path.points) >= 3:
                self._draw_geo_path_label(painter, path)

    def _draw_geo_path_label(self, painter: QPainter, path) -> None:
        n = len(path.points)
        cx = sum(p.x() for p in path.points) / n
        cy = sum(p.y() for p in path.points) / n
        cz = sum(p.z() for p in path.points) / n
        q = self._world_to_pixel(self.drape(QVector3D(cx, cy, cz)))
        if q is None:
            return
        area = path.area()
        text = f"{tr('Area')}: {area:.1f} m²  ({area / 10000:.3f} ha)\n" \
               f"{tr('Perimeter')}: {path.perimeter():.1f} m"
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        lines = text.split("\n")
        tw = max(fm.horizontalAdvance(ln) for ln in lines)
        th = fm.height() * len(lines)
        x, y = q[0] - tw / 2, q[1] - th / 2
        painter.fillRect(int(x) - 5, int(y) - 3, tw + 10, th + 6,
                         QColor(20, 24, 30, 190))
        painter.setPen(QColor(255, 255, 255))
        for i, ln in enumerate(lines):
            painter.drawText(QPointF(x, y + fm.ascent() + i * fm.height()), ln)

    def _draw_dimensions(self, painter: QPainter) -> None:
        """Draw every committed static dimension: extension lines from the
        measured endpoints out to the dimension line, the dimension line with
        end ticks, and the value label at its midpoint."""
        dims = getattr(self.scene, "dimensions", None)
        if not dims:
            return
        style = getattr(self.scene, "dimension_style", {})
        col = style.get("color", [45, 55, 75])
        default_ink = QColor(col[0], col[1], col[2])
        sel_ink = QColor(243, 115, 41)  # selection orange
        selection = self.scene.selection
        font = QFont()
        font.setPointSize(int(style.get("font_size", 9)))
        font.setBold(True)
        for dim in dims:
            ink = (sel_ink if (dim in selection or dim is self._hover_entity)
                   else default_ink)
            ap, bp = dim.line_points()
            pap = self._world_to_pixel(ap)
            pbp = self._world_to_pixel(bp)
            if pap is None or pbp is None:
                continue
            # Lines are hidden where solid geometry sits in front of them, so a
            # dimension reads as part of the model instead of floating over it.
            # Each 3D segment is sampled and only its visible runs are drawn.
            painter.setPen(QPen(ink, 1.0))
            self._draw_occluded_segment(painter, dim.a, ap)      # extension
            self._draw_occluded_segment(painter, dim.b, bp)
            painter.setPen(QPen(ink, 1.5))
            self._draw_occluded_segment(painter, ap, bp)         # dimension line
            # End ticks: short screen-space perpendiculars at each end (drawn
            # only when the end point itself is visible).
            dx, dy = pbp[0] - pap[0], pbp[1] - pap[1]
            ln = math.hypot(dx, dy)
            if ln > 1e-6:
                ox, oy = -dy / ln * 4.0, dx / ln * 4.0
                for (cx, cy), w in ((pap, ap), (pbp, bp)):
                    if not self._is_occluded(w):
                        painter.drawLine(QPointF(cx - ox, cy - oy),
                                         QPointF(cx + ox, cy + oy))
            # Value label at the dimension line's midpoint — hidden if that
            # point is behind the solid.
            mid_world = dim.midpoint()
            mid = self._world_to_pixel(mid_world)
            if mid is not None and not self._is_occluded(mid_world):
                text = self._format_dim_value(dim.value(), style)
                painter.setFont(font)
                painter.setPen(QPen(QColor(255, 255, 255, 230)))
                painter.drawText(QPointF(mid[0] + 5, mid[1] - 4), text)
                painter.setPen(QPen(ink))
                painter.drawText(QPointF(mid[0] + 4, mid[1] - 5), text)

    @staticmethod
    def _format_dim_value(metres: float, style: dict) -> str:
        """Format a length (metres) per the dimension style: unit + precision."""
        units = style.get("units", "m")
        decimals = int(style.get("decimals", 2))
        factor = {"m": 1.0, "cm": 100.0, "mm": 1000.0}.get(units, 1.0)
        return f"{metres * factor:.{decimals}f} {units}"

    def _draw_occluded_segment(self, painter: QPainter, p3a: QVector3D,
                               p3b: QVector3D, samples: int = 16) -> None:
        """Draw the 3D segment ``p3a``–``p3b`` in screen space, skipping the
        parts hidden behind solid geometry (CPU occlusion sample). A sub-segment
        is drawn only when both its sampled ends are visible, so the line never
        bleeds over the solid; the silhouette gap is sub-pixel at this density."""
        prev_px = None
        prev_vis = False
        for i in range(samples + 1):
            t = i / samples
            w = p3a + (p3b - p3a) * t
            px = self._world_to_pixel(w)
            vis = px is not None and not self._is_occluded(w)
            if prev_px is not None and px is not None and prev_vis and vis:
                painter.drawLine(QPointF(*prev_px), QPointF(*px))
            prev_px, prev_vis = px, vis

    def _draw_inference_marker(self, painter: QPainter) -> None:
        """Green endpoint-style square where the active tool's distance
        inference is engaged (Push/Pull snapping level with a corner or face)."""
        tool = self.active_tool
        provider = getattr(tool, "inference_marker", None) if tool is not None else None
        if not callable(provider):
            return
        result = provider()
        if result is None:
            return
        world, _kind = result
        pixel = self._world_to_pixel(world)
        if pixel is None:
            return
        px, py = pixel
        color = QColor.fromRgbF(0.16, 0.62, 0.36, 1.0)  # SketchUp endpoint green
        painter.setPen(QPen(color, 2.0))
        painter.setBrush(QColor.fromRgbF(0.16, 0.62, 0.36, 0.25))
        painter.drawRect(QRectF(px - 5, py - 5, 10, 10))

    def _draw_length_label(self, painter: QPainter) -> None:
        tool = self.active_tool
        if tool is None:
            return

        # Tool-provided label takes priority (e.g. PushPullTool's signed
        # extrusion distance). Otherwise fall back to the single-segment
        # length used by LineTool.
        label_provider = getattr(tool, "value_label", None)
        if callable(label_provider):
            result = label_provider()
            if result is None:
                return
            text, mid_world = result
        else:
            segments = tool.rubber_band_lines()
            if len(segments) != 1:
                return
            start, hover = segments[0]
            text = f"{(hover - start).length():.2f} m"
            mid_world = QVector3D(
                (start.x() + hover.x()) * 0.5,
                (start.y() + hover.y()) * 0.5,
                (start.z() + hover.z()) * 0.5,
            )
        pixel = self._world_to_pixel(mid_world)
        if pixel is None:
            return
        if self._value_buffer:
            text = f"{self._value_buffer} m"
            fg = QColor("#0F141B")
            shadow = QColor(255, 220, 130, 235)  # warm tint while typing
        else:
            fg = QColor("#0F141B")
            shadow = QColor(255, 255, 255, 220)
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(shadow))
        painter.drawText(QPointF(pixel[0] + 12, pixel[1] - 7), text)
        painter.setPen(QPen(fg))
        painter.drawText(QPointF(pixel[0] + 11, pixel[1] - 8), text)

    def _measurement_text(self) -> str:
        """Live measurement string for the VCB box: the active tool's
        ``value_label`` text (rectangle dimensions, push distance, …) or the
        single-segment length a line is drawing. Empty when nothing applies."""
        tool = self.active_tool
        if tool is None:
            return ""
        provider = getattr(tool, "value_label", None)
        if callable(provider):
            result = provider()
            if result is not None:
                return result[0]
        segments = tool.rubber_band_lines()
        if len(segments) == 1:
            start, hover = segments[0]
            return f"{(hover - start).length():.2f} m"
        return ""

    def _draw_axis_lock_label(self, painter: QPainter) -> None:
        label = {
            "x": ("X", QColor(220, 56, 69)),
            "y": ("Y", QColor(40, 158, 92)),
            "z": ("Z", QColor(51, 102, 199)),
        }[self.axis_lock]
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(label[1]))
        painter.drawText(QPointF(14, 24), f"{label[0]} axis locked")

    def _draw_inference_label(self, painter: QPainter) -> None:
        """Show 'On Red Axis' style label when soft inference is active."""
        snap = self.last_snap
        if snap is None or snap.kind != "axis_inference":
            return
        names = {"x": "Red", "y": "Green", "z": "Blue"}
        name = names.get(snap.axis or "", "?")
        r, g, b = snap.color
        font = QFont()
        font.setPointSize(10)
        font.setItalic(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor.fromRgbF(r, g, b, 0.95)))
        painter.drawText(QPointF(14, 44), f"On {name} Axis (hold Shift to lock)")

    def _draw_reference_label(self, painter: QPainter) -> None:
        if self.reference_mode is None or self.reference_edge is None:
            return
        r, g, b = (0.85, 0.30, 0.80)
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor.fromRgbF(r, g, b, 1.0)))
        word = "Parallel" if self.reference_mode == "parallel" else "Perpendicular"
        painter.drawText(QPointF(14, 24), f"{word} to reference edge")

    # ---- Pixel ↔ world ------------------------------------------------------
    def _pixel_to_ray(
        self, x: float, y: float
    ) -> tuple[Optional[QVector3D], Optional[QVector3D]]:
        """Camera ray (origin, unit direction) through the given pixel."""
        w = max(self.width(), 1)
        h = max(self.height(), 1)
        ndc_x = 2.0 * x / w - 1.0
        ndc_y = 1.0 - 2.0 * y / h
        mvp = self.camera.projection_matrix() * self.camera.view_matrix()
        inv, ok = mvp.inverted()
        if not ok:
            return None, None
        p_near = inv.map(QVector3D(ndc_x, ndc_y, -1.0))
        p_far = inv.map(QVector3D(ndc_x, ndc_y, 1.0))
        direction = p_far - p_near
        if direction.length() < 1e-9:
            return None, None
        return p_near, direction.normalized()

    def _world_from_pixel(self, x: int, y: int) -> Optional[QVector3D]:
        """Pixel → world hit on the *current* work plane.

        Plane choice priority:
        1. ``tool.work_plane``, captured at first click when the user clicked
           on a face — keeps the rest of the chain coplanar with that face.
        2. If a tool has a ``start_point``, the plane goes through it; its
           orientation is horizontal for most camera tilts and vertical only
           near the horizon (so dragging up/down can move in Z).
        3. If no ``start_point`` yet and the cursor is over an existing
           face, that face's plane (this is what lets the user draw a new
           polygon *inside* an existing one — e.g. on top of a box).
        4. Ground (Z=0).
        """
        origin, direction = self._pixel_to_ray(x, y)
        if origin is None or direction is None:
            return None
        plane_point, plane_normal = self._current_work_plane(cursor=(x, y))
        denom = QVector3D.dotProduct(plane_normal, direction)
        if abs(denom) < 1e-6:
            return None
        t = QVector3D.dotProduct(plane_normal, plane_point - origin) / denom
        if t < 0:
            return None
        return origin + direction * t

    # When the camera is at least this tilted off the horizon, the work plane
    # stays horizontal (XY) — that covers top, iso, and most architectural
    # angles. Only at near-horizon views does it switch to a vertical plane,
    # which is the only orientation where cursor-to-ground is ambiguous.
    HORIZON_PITCH_THRESHOLD_DEG = 15.0

    def _current_work_plane(
        self, cursor: Optional[tuple[float, float]] = None
    ) -> tuple[QVector3D, QVector3D]:
        """Return ``(point, normal)`` of the current drawing plane.

        Priority: tool-captured plane > camera-aware plane through the active
        ``start_point`` > face under cursor (face-plane inference) > ground.
        """
        tool = self.active_tool
        captured = getattr(tool, "work_plane", None) if tool is not None else None
        if captured is not None:
            # SketchUp's escape hatch: orbiting down to the horizon means "I
            # want to draw UPWARD now". A first click on a horizontal face
            # (the ground, a slab) captures its plane and would pin the whole
            # chain flat forever; at near-horizon views, where the horizontal
            # plane is unreadable anyway, a HORIZONTAL captured plane yields
            # to the vertical plane through the start point so the line can
            # rise in Z. Vertical captured planes (walls) already allow it.
            start = (getattr(tool, "start_point", None)
                     if tool is not None else None)
            if start is not None and abs(
                    captured[1].normalized().z()) > 0.94:
                vertical = self._near_horizon_vertical(start)
                if vertical is not None:
                    return vertical
            return captured

        start = getattr(tool, "start_point", None) if tool is not None else None
        if start is None:
            # First-click hover: if the cursor is over an existing face, use
            # that face's plane so a new polygon drawn "inside" it lands on
            # the face instead of falling to the ground.
            if cursor is not None and tool is not None:
                face = self.pick_face(cursor[0], cursor[1])
                if face is not None:
                    return face.centroid(), face.normal()
            return QVector3D(0.0, 0.0, 0.0), QVector3D(0.0, 0.0, 1.0)

        forward = (self.camera.target - self.camera.eye())
        if forward.length() < 1e-9:
            return start, QVector3D(0.0, 0.0, 1.0)
        forward = forward.normalized()
        # Tools that drag geometry up and down (Move) use a camera-facing
        # vertical plane through the grab point, so pulling the mouse up raises
        # the geometry rigidly instead of sliding it across the ground (which
        # shears connected faces and looks disordered). The plane contains the
        # world Z axis; its normal is the camera's horizontal heading. Only when
        # looking nearly straight down — where height is unreadable anyway — does
        # it fall back to the horizontal plane.
        if getattr(tool, "prefers_vertical_drag", False):
            horiz = QVector3D(forward.x(), forward.y(), 0.0)
            if horiz.length() >= math.sin(math.radians(self.HORIZON_PITCH_THRESHOLD_DEG)):
                return start, horiz.normalized()
            return start, QVector3D(0.0, 0.0, 1.0)
        # |forward.z| ≈ sin(pitch). Anything tilted more than the threshold
        # keeps the horizontal plane.
        vertical = self._near_horizon_vertical(start)
        if vertical is not None:
            return vertical
        return start, QVector3D(0.0, 0.0, 1.0)

    def _near_horizon_vertical(
        self, start: QVector3D
    ) -> Optional[tuple[QVector3D, QVector3D]]:
        """The vertical work plane through ``start`` when the camera sits near
        the horizon — the only orientation where cursor→ground is ambiguous
        and dragging up must move in Z. ``None`` at steeper tilts."""
        forward = (self.camera.target - self.camera.eye())
        if forward.length() < 1e-9:
            return None
        forward = forward.normalized()
        if abs(forward.z()) >= math.sin(
                math.radians(self.HORIZON_PITCH_THRESHOLD_DEG)):
            return None
        # Pick the vertical plane whose normal is more end-on to the camera
        # so cursor motion maps cleanly to it.
        if abs(forward.x()) >= abs(forward.y()):
            return start, QVector3D(1.0, 0.0, 0.0)
        return start, QVector3D(0.0, 1.0, 0.0)

    def _project_to_lock_line(
        self,
        start: QVector3D,
        lock_dir: QVector3D,
        pixel_x: float,
        pixel_y: float,
    ) -> QVector3D:
        """Closest point on the lock line (``start``, ``lock_dir``) to the
        camera ray that passes through ``(pixel_x, pixel_y)``.

        This is what makes Z-axis locks actually let you draw vertical
        lines — moving the mouse up/down on screen slides the projected
        point along the Z line.
        """
        ray_origin, ray_dir = self._pixel_to_ray(pixel_x, pixel_y)
        if ray_origin is None or ray_dir is None:
            return start
        d1 = lock_dir.normalized()
        d2 = ray_dir
        r = start - ray_origin
        b = QVector3D.dotProduct(d1, d2)
        d = QVector3D.dotProduct(d1, r)
        e = QVector3D.dotProduct(d2, r)
        denom = 1.0 - b * b
        if abs(denom) < 1e-6:
            # Lock line is parallel to the camera ray — project the ray
            # origin onto the lock line as a stable fallback.
            t = -d
        else:
            t = (b * e - d) / denom
        return start + d1 * t

    def _world_to_pixel(self, world: QVector3D) -> Optional[tuple[float, float]]:
        """World point → screen pixel (or None if behind the camera)."""
        mvp = self.camera.projection_matrix() * self.camera.view_matrix()
        clip = mvp.map(QVector4D(world.x(), world.y(), world.z(), 1.0))
        if clip.w() <= 0:
            return None
        ndc_x = clip.x() / clip.w()
        ndc_y = clip.y() / clip.w()
        px = (ndc_x * 0.5 + 0.5) * self.width()
        py = (1.0 - (ndc_y * 0.5 + 0.5)) * self.height()
        return (px, py)

    @staticmethod
    def _mesh_fingerprint(mesh):
        """Cheap content fingerprint of a group's mesh — counts, a coordinate
        checksum, render-relevant attrs and soft flags. Self-healing cache key
        (no dirty-flag invariant to maintain): any geometry/paint change on
        the group produces a new value; ~20 ms on a 17k-face mesh versus the
        ~1.5 s rebuild it lets untouched groups skip."""
        s = 0.0
        for v in mesh.vertices:
            p = v.position
            s += p.x() + p.y() * 1.000003 + p.z() * 1.000007
        a = 0
        for i, f in enumerate(mesh.faces):
            if f.attrs:
                c = f.attrs.get("color")
                t = f.attrs.get("texture")
                a ^= hash((i,
                           None if c is None else tuple(c),
                           None if not t else (t.get("path"), t.get("sw"),
                                               t.get("sh"), t.get("rot", 0)),
                           f.attrs.get("layer")))
        soft = sum(1 for e in mesh.edges if getattr(e, "soft", False))
        return (len(mesh.vertices), len(mesh.edges), len(mesh.faces),
                round(s, 4), a, soft)

    def _group_fp(self, group):
        """Fingerprint of a group's mesh, memoised per scene version — the
        chunk is consulted several times per frame/stroke (faces, edges,
        silhouette, pick index) and the fingerprint walk over a 130k-vertex
        reference model costs ~200 ms."""
        key = (id(group), self.scene.version, id(self.scene.mesh))
        memo = getattr(self, "_fp_memo", None)
        if memo is None:
            memo = self._fp_memo = {}
        fp = memo.get(key)
        if fp is None:
            if len(memo) > 64:
                memo.clear()
            _f0 = _time_mod.perf_counter() if _PERF else 0.0
            fp = memo[key] = self._mesh_fingerprint(group.mesh)
            if _PERF:
                _plog("fingerprint", (_time_mod.perf_counter() - _f0) * 1000.0,
                      extra=f"nv={fp[0]}")
        return fp

    @staticmethod
    def _translation_probe(entry, mesh):
        """When the group's mesh is the chunk, purely TRANSLATED, return the
        delta; else ``None``. Counts must match and every sampled vertex must
        have moved by the same vector. This is what keeps dragging a 100k-face
        reference group interactive: Move live-deforms the mesh per frame, and
        a full chunk rebuild at that scale takes ~15 s."""
        verts = mesh.vertices
        if (len(verts) != entry["nv"] or len(mesh.edges) != entry["ne"]
                or len(mesh.faces) != entry["nf"] or not entry["samples"]):
            return None
        i0, p0 = entry["samples"][0]
        d = verts[i0].position - QVector3D(*p0)
        if d.length() < 1e-9:
            return None                   # unchanged, or a non-geometric edit
        for i, p in entry["samples"][1:]:
            # float32 storage rounds each translated vertex differently (up
            # to ~1e-5 at building-scale coordinates); a real rotation moves
            # samples apart by millimetres — no ambiguity at this tolerance.
            if ((verts[i].position - QVector3D(*p)) - d).length() > 2e-4:
                return None
        return d

    @staticmethod
    def _samples_match(entry, mesh) -> bool:
        verts = mesh.vertices
        if len(verts) != entry["nv"]:
            return False
        for i, p in entry["samples"]:
            if (verts[i].position - QVector3D(*p)).length() > 1e-6:
                return False
        return True

    def _shift_chunk(self, entry, d, mesh) -> None:
        """Translate every cached array of ``entry`` by ``d`` in place —
        NumPy adds instead of a rebuild — and refresh samples + fingerprint
        analytically."""
        import numpy as np
        dx = np.array([d.x(), d.y(), d.z()])

        def flat3(b):
            a = np.frombuffer(b, dtype=np.float32).reshape(-1, 3).copy()
            a += dx
            return a.astype(np.float32).tobytes()

        entry["edges"] = flat3(entry["edges"])
        entry["by_color"] = {k: flat3(v) for k, v in entry["by_color"].items()}
        tex = {}
        for k, v in entry["by_texture"].items():
            a = np.frombuffer(v, dtype=np.float32).reshape(-1, 5).copy()
            a[:, :3] += dx
            tex[k] = a.astype(np.float32).tobytes()
        entry["by_texture"] = tex
        if entry["v0"] is not None:
            entry["v0"] = entry["v0"] + dx
        for kk in ("soft_c0", "soft_c1"):
            if len(entry[kk]):
                entry[kk] = entry[kk] + dx
        if entry["soft_pts"] is not None:
            sp = entry["soft_pts"].reshape(-1, 3) + dx
            entry["soft_pts"] = sp.astype(np.float32).reshape(-1, 6)
        entry["tri_pos"] = None            # lazy; rebuilt from v0 on demand
        entry["samples"] = [(i, (mesh.vertices[i].position.x(),
                                 mesh.vertices[i].position.y(),
                                 mesh.vertices[i].position.z()))
                            for i, _p in entry["samples"]]
        entry["coordsum"] += entry["nv"] * (
            d.x() + d.y() * 1.000003 + d.z() * 1.000007)
        fp = entry["fp"]
        entry["fp"] = (fp[0], fp[1], fp[2], round(entry["coordsum"], 4),
                       fp[4], fp[5])
        # float32 vertex storage makes the analytic checksum drift from a
        # fresh walk — mark it approximate so the next full comparison
        # verifies by samples instead of rebuilding 100k faces for nothing.
        entry["fp_approx"] = True

    @staticmethod
    def _chunk_tri_pos(entry):
        """Flat float32 triangle positions of a chunk (for the selection
        tint) — derived lazily from the pick arrays, never by re-running
        earcut over the group."""
        tp = entry.get("tri_pos")
        if tp is None:
            if entry["v0"] is None:
                tp = b""
            else:
                import numpy as np
                v0, e1, e2 = entry["v0"], entry["e1"], entry["e2"]
                arr = np.empty((len(v0), 9), dtype=np.float32)
                arr[:, 0:3] = v0
                arr[:, 3:6] = v0 + e1
                arr[:, 6:9] = v0 + e2
                tp = arr.tobytes()
            entry["tri_pos"] = tp
        return tp

    def _group_chunk(self, group):
        """Cached render + pick payload of one group, keyed by its content
        fingerprint. A big imported reference model (17k faces) made EVERY
        stroke beside it pay a full VBO + pick-index rebuild (~1.5 s); with
        the chunk, untouched groups just re-concatenate, and a pure
        translation (Move drag) shifts the arrays instead of rebuilding."""
        cache = getattr(self, "_group_chunks", None)
        if cache is None:
            cache = self._group_chunks = {}
        entry = cache.get(id(group))
        vkey = (self.scene.version, id(self.scene.mesh))
        if entry is not None:
            if entry.get("vkey") == vkey:
                return entry
            d = self._translation_probe(entry, group.mesh)
            if d is not None:
                self._shift_chunk(entry, d, group.mesh)
                entry["vkey"] = vkey
                return entry
        fp = self._group_fp(group)
        if entry is not None:
            same = entry["fp"] == fp
            if not same and entry.get("fp_approx"):
                # Post-shift: the checksum is approximate (float32 drift).
                # Counts/attrs/soft equal + every sampled vertex in place is
                # the real test; heal the stored fingerprint on acceptance.
                same = (entry["fp"][:3] == fp[:3]
                        and entry["fp"][4:] == fp[4:]
                        and self._samples_match(entry, group.mesh))
            if same:
                entry["fp"] = fp
                entry["fp_approx"] = False
                entry["vkey"] = vkey
                return entry
        _c0 = _time_mod.perf_counter() if _PERF else 0.0
        import numpy as np
        mesh = group.mesh
        edges_data = array("f")
        # Soft-edge silhouette source data: per-frame the view test runs
        # vectorised over these (99k soft edges walked in Python per frame
        # made orbiting a full imported project a 4-second slide show).
        soft_pts: list = []
        soft_n0: list = []
        soft_c0: list = []
        soft_n1: list = []
        soft_c1: list = []
        soft_single: list = []
        fprops: dict = {}

        def props(f):
            r = fprops.get(id(f))
            if r is None:
                n, c = f.normal(), f.centroid()
                r = fprops[id(f)] = ((n.x(), n.y(), n.z()),
                                     (c.x(), c.y(), c.z()))
            return r

        for e in mesh.edges:
            if not getattr(e, "soft", False):
                edges_data.extend([e.a.x(), e.a.y(), e.a.z(),
                                   e.b.x(), e.b.y(), e.b.z()])
                continue
            fs = e.faces
            if len(fs) not in (1, 2):
                continue                  # dangling / non-manifold: not drawn
            soft_pts.append([e.a.x(), e.a.y(), e.a.z(),
                             e.b.x(), e.b.y(), e.b.z()])
            n0, c0 = props(fs[0])
            soft_n0.append(n0)
            soft_c0.append(c0)
            if len(fs) == 2:
                n1, c1 = props(fs[1])
                soft_single.append(False)
            else:
                n1, c1 = n0, c0
                soft_single.append(True)
            soft_n1.append(n1)
            soft_c1.append(c1)
        by_color: dict = {}
        by_texture: dict = {}
        faces: list = []
        areas: list = []
        tris: list = []
        tri_ent: list = []
        for f in mesh.faces:
            i = len(faces)
            faces.append(f)
            areas.append(f.area())
            tri_list = f.triangulate()
            tex = f.attrs.get("texture")
            if tex is not None and tex.get("path"):
                self._append_textured_face(by_texture, f, tex)
            else:
                col = f.attrs.get("color")
                base = tuple(col) if col is not None else self.DEFAULT_FACE_COLOR
                buf = by_color.setdefault(self._shaded_color(base, f.normal()),
                                          array("f"))
                for t0, t1, t2 in tri_list:
                    buf.extend([t0.x(), t0.y(), t0.z(),
                                t1.x(), t1.y(), t1.z(),
                                t2.x(), t2.y(), t2.z()])
            for t0, t1, t2 in tri_list:
                tris.append([[t0.x(), t0.y(), t0.z()],
                             [t1.x(), t1.y(), t1.z()],
                             [t2.x(), t2.y(), t2.z()]])
                tri_ent.append(i)
        if tris:
            t = np.asarray(tris, dtype=np.float64)
            v0, e1, e2 = t[:, 0], t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
            tri_ent_a = np.asarray(tri_ent, dtype=np.int64)
        else:
            v0 = e1 = e2 = tri_ent_a = None
        verts = mesh.vertices
        nv = len(verts)
        coordsum = 0.0
        for v in verts:
            p = v.position
            coordsum += p.x() + p.y() * 1.000003 + p.z() * 1.000007
        idxs = sorted({k * max(nv - 1, 0) // 31 for k in range(32)}) if nv else []
        samples = [(i, (verts[i].position.x(), verts[i].position.y(),
                        verts[i].position.z())) for i in idxs]
        entry = {"fp": fp, "vkey": vkey,
                 "nv": nv, "ne": len(mesh.edges), "nf": len(mesh.faces),
                 "samples": samples, "coordsum": coordsum,
                 "edges": edges_data.tobytes(),
                 "by_color": {k: v.tobytes() for k, v in by_color.items()},
                 "by_texture": {k: v.tobytes() for k, v in by_texture.items()},
                 "faces": faces,
                 "areas": np.asarray(areas, dtype=np.float64),
                 "v0": v0, "e1": e1, "e2": e2, "tri_ent": tri_ent_a,
                 "tri_pos": None,
                 "soft_pts": (np.asarray(soft_pts, dtype=np.float32)
                              if soft_pts else None),
                 "soft_n0": np.asarray(soft_n0, dtype=np.float64),
                 "soft_c0": np.asarray(soft_c0, dtype=np.float64),
                 "soft_n1": np.asarray(soft_n1, dtype=np.float64),
                 "soft_c1": np.asarray(soft_c1, dtype=np.float64),
                 "soft_single": np.asarray(soft_single, dtype=bool)}
        if _PERF:
            _plog("chunk_rebuild", (_time_mod.perf_counter() - _c0) * 1000.0,
                  extra=f"faces={len(faces)}")
        cache[id(group)] = entry
        return entry

    def _np_mvp(self):
        """Current MVP as a (4, 4) float64 NumPy matrix (row-major indexing).
        ``QMatrix4x4.data()`` is column-major, hence the Fortran reshape."""
        import numpy as np
        m = self.camera.projection_matrix() * self.camera.view_matrix()
        return np.array(m.data(), dtype=np.float64).reshape(4, 4, order="F")

    def _project_px(self, pts):
        """Batch world points (N, 3) → ``(px, py, in_front)`` arrays — the
        exact math of :meth:`_world_to_pixel`, vectorised."""
        import numpy as np
        M = self._np_mvp()
        clip = pts @ M[:, :3].T + M[:, 3]
        w = clip[:, 3]
        ok = w > 0
        safe = np.where(ok, w, 1.0)
        px = (clip[:, 0] / safe * 0.5 + 0.5) * self.width()
        py = (1.0 - (clip[:, 1] / safe * 0.5 + 0.5)) * self.height()
        return px, py, ok

    def _pick_index(self):
        """Flat NumPy pick index of the scene — triangles of every loose and
        group face (with visibility/selectability masks and areas) plus the
        loose edges — rebuilt when the scene changes.

        Every mouse-move pick used to walk the mesh in Python re-running
        earcut per face (~1–2 s per move against an imported 17k-triangle
        building — the app read as frozen); batched over this index a pick
        is a couple of milliseconds."""
        key = (self.scene.version, id(self.scene.mesh))
        cached = getattr(self, "_pick_index_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        _p0 = _time_mod.perf_counter() if _PERF else 0.0
        import numpy as np
        from types import SimpleNamespace
        scene = self.scene
        entities: list = []           # (face, group_or_None)
        ent_area: list = []
        ent_sel: list = []
        ent_vis: list = []
        ent_loose: list = []
        tris: list = []
        tri_ent: list = []

        def add_face(f, grp, vis, sel):
            i = len(entities)
            entities.append((f, grp))
            ent_area.append(f.area())
            ent_vis.append(vis)
            ent_sel.append(sel)
            ent_loose.append(grp is None)
            for t0, t1, t2 in f.triangulate():
                tris.append([[t0.x(), t0.y(), t0.z()],
                             [t1.x(), t1.y(), t1.z()],
                             [t2.x(), t2.y(), t2.z()]])
                tri_ent.append(i)

        for f in scene.faces:
            add_face(f, None, scene.entity_visible(f),
                     scene.entity_selectable(f))

        # Loose part → arrays; groups append their cached chunk arrays.
        if tris:
            t = np.asarray(tris, dtype=np.float64)
            v0s = [t[:, 0]]
            e1s = [t[:, 1] - t[:, 0]]
            e2s = [t[:, 2] - t[:, 0]]
            tents = [np.asarray(tri_ent, dtype=np.int64)]
        else:
            v0s, e1s, e2s, tents = [], [], [], []
        areas = [np.asarray(ent_area, dtype=np.float64)]
        vis_parts = [np.asarray(ent_vis, dtype=bool)]
        sel_parts = [np.asarray(ent_sel, dtype=bool)]
        loose_parts = [np.asarray(ent_loose, dtype=bool)]

        if scene.edit_group is None:
            for g in scene.groups:
                if getattr(g, "billboard", False):
                    continue          # per-frame quad; picked separately
                gvis = scene.entity_visible(g)
                gsel = scene.entity_selectable(g)
                if not (gvis or gsel):
                    continue
                chunk = self._group_chunk(g)
                n = len(chunk["faces"])
                if not n:
                    continue
                offset = len(entities)
                entities.extend((f, g) for f in chunk["faces"])
                areas.append(chunk["areas"])
                vis_parts.append(np.full(n, gvis, dtype=bool))
                sel_parts.append(np.full(n, gsel, dtype=bool))
                loose_parts.append(np.zeros(n, dtype=bool))
                if chunk["v0"] is not None:
                    v0s.append(chunk["v0"])
                    e1s.append(chunk["e1"])
                    e2s.append(chunk["e2"])
                    tents.append(chunk["tri_ent"] + offset)

        edges: list = []
        ea: list = []
        eb: list = []
        esel: list = []
        for e in scene.edges:
            edges.append(e)
            ea.append([e.a.x(), e.a.y(), e.a.z()])
            eb.append([e.b.x(), e.b.y(), e.b.z()])
            esel.append(scene.entity_selectable(e))

        idx = SimpleNamespace(
            entities=entities,
            ent_area=np.concatenate(areas) if entities else np.empty(0),
            ent_sel=np.concatenate(sel_parts) if entities else np.empty(0, bool),
            ent_vis=np.concatenate(vis_parts) if entities else np.empty(0, bool),
            ent_loose=(np.concatenate(loose_parts) if entities
                       else np.empty(0, bool)),
            tri_v0=np.concatenate(v0s) if v0s else None,
            tri_e1=np.concatenate(e1s) if v0s else None,
            tri_e2=np.concatenate(e2s) if v0s else None,
            tri_ent=np.concatenate(tents) if v0s else None,
            edges=edges,
            edge_a=np.asarray(ea, dtype=np.float64) if edges else None,
            edge_b=np.asarray(eb, dtype=np.float64) if edges else None,
            edge_sel=np.asarray(esel, dtype=bool) if edges else None,
        )
        if _PERF:
            _plog("pick_index", (_time_mod.perf_counter() - _p0) * 1000.0)
        self._pick_index_cache = (key, idx)
        return idx

    def _ray_hits(self, idx, origin, direction, ent_mask):
        """Per-entity nearest ray parameter over the index triangles whose
        entity passes ``ent_mask``. Returns an (E,) array of t (``inf`` = no
        hit) or ``None`` when the index has no triangles. Same acceptance
        thresholds as :func:`_ray_triangle`."""
        import numpy as np
        if idx.tri_v0 is None:
            return None
        o = np.array([origin.x(), origin.y(), origin.z()])
        d = np.array([direction.x(), direction.y(), direction.z()])
        v0, e1, e2 = idx.tri_v0, idx.tri_e1, idx.tri_e2
        p = np.cross(d, e2)
        det = np.einsum("ij,ij->i", e1, p)
        ok = np.abs(det) > 1e-6
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = o - v0
        u = np.einsum("ij,ij->i", s, p) * inv
        q = np.cross(s, e1)
        v = (q @ d) * inv
        t = np.einsum("ij,ij->i", e2, q) * inv
        hit = (ok & (u >= 0.0) & (u <= 1.0) & (v >= 0.0) & (u + v <= 1.0)
               & (t > 1e-6) & ent_mask[idx.tri_ent])
        tvals = np.where(hit, t, np.inf)
        face_t = np.full(len(idx.entities), np.inf)
        np.minimum.at(face_t, idx.tri_ent, tvals)
        return face_t

    def pick_edge(self, screen_x: float, screen_y: float):
        """Return the edge closest to ``(screen_x, screen_y)`` within threshold."""
        import numpy as np
        idx = self._pick_index()
        if idx.edge_a is None:
            return None
        ax, ay, oka = self._project_px(idx.edge_a)
        bx, by, okb = self._project_px(idx.edge_b)
        ok = oka & okb & idx.edge_sel
        if not ok.any():
            return None
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        safe = np.where(l2 > 1e-12, l2, 1.0)
        t = np.clip(((screen_x - ax) * dx + (screen_y - ay) * dy) / safe,
                    0.0, 1.0)
        d = np.hypot(ax + t * dx - screen_x, ay + t * dy - screen_y)
        d = np.where(ok, d, np.inf)
        i = int(np.argmin(d))
        return idx.edges[i] if d[i] < self.pick_threshold_px else None

    def pick_dimension(self, screen_x: float, screen_y: float):
        """Return the dimension whose lines (extension + dimension line) are
        closest to the cursor within the pick threshold, or ``None``."""
        dims = getattr(self.scene, "dimensions", None)
        if not dims:
            return None
        best = None
        best_d = self.pick_threshold_px
        for dim in dims:
            ap, bp = dim.line_points()
            for s, e in ((dim.a, ap), (dim.b, bp), (ap, bp)):
                ps = self._world_to_pixel(s)
                pe = self._world_to_pixel(e)
                if ps is None or pe is None:
                    continue
                d = _point_to_segment_distance_2d((screen_x, screen_y), ps, pe)
                if d < best_d:
                    best_d = d
                    best = dim
        return best

    def pick_geopath(self, screen_x: float, screen_y: float):
        """Return the georef path whose polyline is closest to the cursor within
        the pick threshold, or ``None`` (Track G)."""
        paths = getattr(self.scene, "geo_paths", None)
        if not paths:
            return None
        best, best_d = None, self.pick_threshold_px
        for path in paths:
            for a, b in path.segments():
                pa = self._world_to_pixel(self.drape(a))
                pb = self._world_to_pixel(self.drape(b))
                if pa is None or pb is None:
                    continue
                d = _point_to_segment_distance_2d((screen_x, screen_y), pa, pb)
                if d < best_d:
                    best_d = d
                    best = path
        return best

    @staticmethod
    def _tool_busy(tool) -> bool:
        """Whether the active tool has an operation in progress that Esc should
        cancel before falling through to clearing the selection: an unfinished
        chain (start_point / nodes), a drag (dragging / grab / node edit), or
        an eraser stroke."""
        for attr in ("start_point", "dragging", "grab", "_drag"):
            if getattr(tool, attr, None):
                return True
        if getattr(tool, "nodes", None):
            return True
        if getattr(tool, "_stroke", False):
            return True
        return False

    def _snap_scene(self):
        """The scene the snap engine sees: real edges plus construction guides
        as pseudo-edges (``Guide.a/.b`` span the long segment), so drawing tools
        lock onto guides — a guide's whole purpose."""
        guides = getattr(self.scene, "guides", None)
        if not guides:
            return self.scene
        from types import SimpleNamespace
        lines = [g for g in guides if g.is_line]
        if not lines:
            return self.scene
        return SimpleNamespace(edges=list(self.scene.edges) + lines)

    def pick_guide(self, screen_x: float, screen_y: float):
        """Return the construction guide nearest the cursor within the pick
        threshold, or ``None`` (for the Eraser)."""
        guides = getattr(self.scene, "guides", None)
        if not guides:
            return None
        best, best_d = None, self.pick_threshold_px
        for g in guides:
            a, b = g.segment()
            pa = self._world_to_pixel(a)
            pb = self._world_to_pixel(b)
            if pa is None or pb is None:
                continue
            d = _point_to_segment_distance_2d((screen_x, screen_y), pa, pb)
            if d < best_d:
                best_d = d
                best = g
        return best

    def pick_vertex(self, screen_x: float, screen_y: float):
        """Return the scene vertex (corner) closest to the cursor within the
        pick threshold, or ``None``. Used to acquire a corner as a 'from point'
        reference while drawing. Occluded vertices are ignored (tested in
        ascending screen distance, so the nearest visible corner wins — the
        same answer the old per-edge scan produced)."""
        import numpy as np
        idx = self._pick_index()
        if idx.edge_a is None:
            return None
        pts = np.concatenate([idx.edge_a, idx.edge_b])
        px, py, ok = self._project_px(pts)
        d = np.where(ok, np.hypot(px - screen_x, py - screen_y), np.inf)
        n = len(idx.edges)
        cand = np.where(d < self.pick_threshold_px)[0]
        for i in cand[np.argsort(d[cand])]:
            e = idx.edges[int(i) % n]
            vertex = e.a if i < n else e.b
            if not self._is_occluded(vertex):
                return vertex
        return None

    def _occlusion_triangles(self):
        """Cached NumPy arrays ``(v0, e1, e2)`` of every loose-mesh triangle,
        rebuilt when the scene changes. ``_is_occluded`` fires dozens of times
        per frame while dimensions are on screen (each cota samples its three
        lines against the model); re-running earcut per query made orbiting a
        dimensioned plaza crawl (~280 ms/frame on the plaza.igz report —
        batched here it's ~5 ms)."""
        key = (self.scene.version, id(self.scene.mesh))
        cached = getattr(self, "_occl_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        import numpy as np
        tris = []
        for face in self.scene.faces:
            for t0, t1, t2 in face.triangulate():
                tris.append([[t0.x(), t0.y(), t0.z()],
                             [t1.x(), t1.y(), t1.z()],
                             [t2.x(), t2.y(), t2.z()]])
        if tris:
            t = np.asarray(tris, dtype=np.float64)
            arrays = (t[:, 0], t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        else:
            arrays = None
        self._occl_cache = (key, arrays)
        return arrays

    def _is_occluded(self, world: QVector3D) -> bool:
        """Whether a face sits between the camera and ``world`` — i.e. the
        point is hidden behind solid geometry from the current view. Used to
        keep snaps from firing on edges/vertices the user can't see.

        A small epsilon keeps a point that lies *on* a face (e.g. an edge on
        that face's boundary) from being reported as occluded by its own
        face. Vectorised Möller–Trumbore over the cached triangle arrays."""
        arrays = self._occlusion_triangles()
        if arrays is None:
            return False
        import numpy as np
        origin = self.camera.eye()
        eye = np.array([origin.x(), origin.y(), origin.z()])
        w = np.array([world.x(), world.y(), world.z()])
        delta = w - eye
        dist = float(np.linalg.norm(delta))
        if dist < 1e-9:
            return False
        d = delta / dist
        v0, e1, e2 = arrays
        p = np.cross(d, e2)
        det = np.einsum("ij,ij->i", e1, p)
        ok = np.abs(det) > 1e-9
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = eye - v0
        u = np.einsum("ij,ij->i", s, p) * inv
        q = np.cross(s, e1)
        v = (q @ d) * inv
        t = np.einsum("ij,ij->i", e2, q) * inv
        hit = (ok & (u >= 0.0) & (u <= 1.0) & (v >= 0.0) & (u + v <= 1.0)
               & (t > 1e-9) & (t < dist - 1e-3))
        return bool(hit.any())

    def pick_face(self, screen_x: float, screen_y: float):
        """Return the face the cursor ray hits, or ``None``.

        Normally that's the front-most face. But when several *coplanar* faces
        overlap at the cursor — e.g. a small rectangle drawn on a larger face
        that didn't subdivide it — the ray hits them at the same depth. In that
        case prefer the smallest one, so push/pull and select grab the inner
        face the user is pointing at instead of the big face behind it (the
        old behaviour silently pushed the whole face)."""
        origin, direction = self._pixel_to_ray(screen_x, screen_y)
        if origin is None or direction is None:
            return None
        import numpy as np
        idx = self._pick_index()
        if not idx.entities:
            return None
        face_t = self._ray_hits(idx, origin, direction,
                                idx.ent_loose & idx.ent_sel)
        if face_t is None:
            return None
        best_t = face_t.min()
        if not np.isfinite(best_t):
            return None
        eps = max(1e-4, best_t * 1e-4)
        cand = np.where(face_t <= best_t + eps)[0]
        if len(cand) == 1:
            return idx.entities[int(cand[0])][0]
        return idx.entities[int(cand[np.argmin(idx.ent_area[cand])])][0]

    def pick_face_any(self, screen_x: float, screen_y: float):
        """Front-most face under the cursor across the loose mesh **and** every
        group: returns ``(face, group_or_None)``. Same coplanar tiebreak as
        :meth:`pick_face` (the smallest of the overlapping faces wins). Lets
        Push/Pull act on a group's face directly — no "enter the group" step."""
        origin, direction = self._pixel_to_ray(screen_x, screen_y)
        if origin is None or direction is None:
            return None, None
        import numpy as np
        idx = self._pick_index()
        if not idx.entities:
            return None, None
        face_t = self._ray_hits(idx, origin, direction, idx.ent_sel)
        if face_t is None:
            return None, None
        best_t = face_t.min()
        if not np.isfinite(best_t):
            return None, None
        eps = max(1e-4, best_t * 1e-4)
        cand = np.where(face_t <= best_t + eps)[0]
        if len(cand) == 1:
            return idx.entities[int(cand[0])]
        return idx.entities[int(cand[np.argmin(idx.ent_area[cand])])]

    def pick_group(self, screen_x: float, screen_y: float):
        """The group whose geometry the cursor hits (front-most face, or nearest
        edge for a group that's only lines), or ``None``."""
        if self.scene.edit_group is not None:
            return None                     # inside a group: pick content
        origin, direction = self._pixel_to_ray(screen_x, screen_y)
        if origin is not None and direction is not None:
            import numpy as np
            best = None  # (t, group)
            idx = self._pick_index()
            if idx.entities:
                face_t = self._ray_hits(idx, origin, direction,
                                        (~idx.ent_loose) & idx.ent_sel)
                if face_t is not None:
                    i = int(np.argmin(face_t))
                    if np.isfinite(face_t[i]):
                        best = (float(face_t[i]), idx.entities[i][1])
            for g in self.scene.groups:
                if not self.scene.entity_selectable(g):
                    continue                    # hidden or locked layer
                if getattr(g, "billboard", False):
                    quad = self._billboard_quad(g)
                    if quad is not None:
                        c = quad[0]
                        for tri in ((c[0], c[1], c[2]), (c[0], c[2], c[3])):
                            t = _ray_triangle(origin, direction, *tri)
                            if t is not None and (best is None or t < best[0]):
                                best = (t, g)
            if best is not None:
                return best[1]
        best_d = self.pick_threshold_px
        best_g = None
        for g in self.scene.groups:
            if not self.scene.entity_selectable(g):
                continue                        # hidden or locked layer
            for e in g.mesh.edges:
                pa = self._world_to_pixel(e.a)
                pb = self._world_to_pixel(e.b)
                if pa is None or pb is None:
                    continue
                d = _point_to_segment_distance_2d((screen_x, screen_y), pa, pb)
                if d < best_d:
                    best_d = d
                    best_g = g
        return best_g

    # ---- Tool management ----------------------------------------------------
    def set_active_tool(self, tool: Optional[Tool]) -> None:
        if self.active_tool is tool and self.nav_mode is None:
            return
        # Picking a drawing tool always leaves camera-navigation mode.
        self.nav_mode = None
        self.unsetCursor()
        if self.active_tool is not None:
            self.active_tool.on_deactivate(self)
        self.active_tool = tool
        self._hover_entity = None  # stale highlight from the previous tool
        self.last_snap = None      # stale snap marker from the previous tool
        self._acquired_edge = None  # drop any held parallel reference
        self._acquired_point = None
        self._acquired_face_normal = None
        if tool is not None:
            tool.on_activate(self)
        self.measurementChanged.emit(self._measurement_text())
        self.update()

    # ---- Copy / paste -------------------------------------------------------
    def copy_selection(self) -> bool:
        """Copy the selected faces and edges into the clipboard (as positions,
        with a reference corner). Returns False if nothing is selected."""
        faces = [f for f in self.scene.selection if isinstance(f, Face)]
        edges = [e for e in self.scene.selection if isinstance(e, Edge)]
        if not faces and not edges:
            return False
        face_data = [
            ([QVector3D(v) for v in f.vertices],
             [[QVector3D(v) for v in h] for h in f.holes])
            for f in faces
        ]
        # Keep soft/curve flags so a pasted circle stays ONE selectable curve
        # (ids are remapped to fresh ones at paste time).
        edge_data = [(QVector3D(e.a), QVector3D(e.b), e.soft, e.curve)
                     for e in edges]
        pts = [p for loop, holes in face_data for p in loop]
        pts += [p for _, holes in face_data for h in holes for p in h]
        pts += [p for a, b, _, _ in edge_data for p in (a, b)]
        ref = QVector3D(min(p.x() for p in pts),
                        min(p.y() for p in pts),
                        min(p.z() for p in pts))
        self.clipboard = {"faces": face_data, "edges": edge_data, "ref": ref}
        return True

    def cut_selection(self) -> bool:
        """Copy the selection, then erase it (one undoable step)."""
        if not self.copy_selection():
            return False
        faces = [f for f in self.scene.selection if isinstance(f, Face)]
        edges = [e for e in self.scene.selection if isinstance(e, Edge)]
        self.history.execute(EraseSelectionCommand(edges, faces))
        self.update()
        return True

    # ---- Group-edit context (Groups v2) --------------------------------------
    def begin_group_edit(self, group) -> None:
        """Enter a group for editing (SketchUp double-click-into-group)."""
        self.scene.begin_group_edit(group)
        self._hover_entity = None
        self.flash_status(tr(
            "Editing group '{name}' — Esc or click outside to leave",
            name=group.name), 4000)
        self.update()

    def end_group_edit(self) -> None:
        if self.scene.edit_group is None:
            return
        self.scene.end_group_edit()
        self._hover_entity = None
        self.flash_status(tr("Left the group"), 2000)
        self.update()

    def set_nav_mode(self, mode: Optional[str]) -> None:
        """Enter a SketchUp-style camera navigation mode ("orbit" / "pan").

        For trackpad users with no middle mouse button: while a nav mode is
        active the left-drag drives the camera (orbit or pan). The active
        drawing tool is suspended; return to drawing by picking any tool or
        pressing Space (Select). ``None`` clears the nav mode.
        """
        if self.active_tool is not None:
            self.active_tool.on_deactivate(self)
            self.active_tool = None
        self._hover_entity = None
        self.last_snap = None
        self.nav_mode = mode
        if mode is not None:
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.unsetCursor()
        self.update()

    def leaveEvent(self, ev) -> None:
        if self._hover_entity is not None:
            self._hover_entity = None
            self.update()
        super().leaveEvent(ev)

    # ---- Input --------------------------------------------------------------
    def contextMenuEvent(self, ev) -> None:
        """Right-click: select what's under the cursor (SketchUp-style) and open
        a context menu of actions relevant to the current selection."""
        win = self.window()
        if not hasattr(win, "show_viewport_context_menu"):
            return
        x, y = ev.pos().x(), ev.pos().y()
        picked = (self.pick_group(x, y) or self.pick_edge(x, y)
                  or self.pick_geopath(x, y) or self.pick_dimension(x, y)
                  or self.pick_face(x, y))
        if picked is not None and picked not in self.scene.selection:
            self.scene.select([picked])
            self.update()
        win.show_viewport_context_menu(ev.globalPos())

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MiddleButton:
            self._last_pos = ev.position().toPoint()
            self._pan_mode = bool(ev.modifiers() & Qt.ShiftModifier)
            return
        # SketchUp-style nav buttons: left-drag orbits/pans the camera.
        # Hold Shift while orbiting to pan temporarily (matches MMB+Shift).
        if ev.button() == Qt.LeftButton and self.nav_mode is not None:
            self._last_pos = ev.position().toPoint()
            self._pan_mode = (
                self.nav_mode == "pan"
                or bool(ev.modifiers() & Qt.ShiftModifier)
            )
            self.setCursor(Qt.ClosedHandCursor)
            return
        if ev.button() == Qt.LeftButton and self.active_tool is not None:
            # Triple click: a press landing right after a double-click at the
            # same spot (Qt has no native triple event). SketchUp: select all
            # connected.
            if self._is_triple_click(ev):
                self._last_double = None
                ctx = self._build_ctx(ev)
                if ctx is not None:
                    self.active_tool.on_triple_click(ctx)
                    self.update()
                return
            # Box-select tools defer the decision to release: a tiny drag is a
            # click, a real drag is a rubber-band box.
            if self.active_tool.box_select:
                self._box_active = True
                self._box_start = ev.position()
                self._box_cur = ev.position()
                return
            self._dispatch_tool_click(ev)

    def _is_triple_click(self, ev) -> bool:
        from PySide6.QtWidgets import QApplication
        last = self._last_double
        if last is None:
            return False
        t, pos = last
        return (ev.timestamp() - t
                <= QApplication.doubleClickInterval()
                and (ev.position() - pos).manhattanLength() < 8)

    def _dispatch_tool_click(self, ev, double: bool = False) -> None:
        """Forward a (double-)click to the active tool, then run the shared
        follow-ups: lock the chain to a clicked face's plane, clear the VCB."""
        had_start = getattr(self.active_tool, "start_point", None) is not None
        had_plane = getattr(self.active_tool, "work_plane", None) is not None
        face_at_click = None
        if not had_start and not had_plane:
            face_at_click = self.pick_face(ev.position().x(), ev.position().y())
        ctx = self._build_ctx(ev)
        if ctx is not None:
            if double:
                self.active_tool.on_double_click(ctx)
            else:
                self.active_tool.on_click(ctx)
            # If the click established a new start point on top of an
            # existing face, lock the rest of the chain to that face's
            # plane so subsequent clicks stay coplanar.
            now_start = getattr(self.active_tool, "start_point", None)
            if (
                not had_start
                and now_start is not None
                and face_at_click is not None
                and hasattr(self.active_tool, "work_plane")
            ):
                self.active_tool.work_plane = (
                    face_at_click.centroid(),
                    face_at_click.normal(),
                )
            # Any pending typed value is invalidated once the user
            # commits a point with the mouse.
            self._set_value_buffer("")
            # A command that failed was rolled back (History is
            # transactional) — surface it instead of failing silently.
            if self.history.last_error:
                self.flash_status(
                    tr("Operation failed and was undone: {err}",
                       err=self.history.last_error), 8000)
            self.update()

    def mouseDoubleClickEvent(self, ev) -> None:
        """Qt replaces the second press of a double-click with this event, so
        route it to ``tool.on_double_click`` — whose default re-runs
        ``on_click``, keeping fast click-click rhythms working for drawing
        tools while Push/Pull overrides it to repeat its last distance.

        Box-select tools (Select) get the double-click DIRECTLY (no drag box
        starts on a double), and the event is remembered so the next press in
        place counts as a triple click."""
        if ev.button() != Qt.LeftButton or self.active_tool is None \
                or self.nav_mode is not None:
            self.mousePressEvent(ev)
            return
        self._last_double = (ev.timestamp(), ev.position())
        if self.active_tool.box_select:
            ctx = self._build_ctx(ev)
            if ctx is not None:
                self.active_tool.on_double_click(ctx)
                self.update()
            return
        self._dispatch_tool_click(ev, double=True)

    def mouseMoveEvent(self, ev) -> None:
        if self._last_pos is not None:
            p = ev.position().toPoint()
            dx = p.x() - self._last_pos.x()
            dy = p.y() - self._last_pos.y()
            self._last_pos = p
            if self._pan_mode:
                self.camera.pan(dx, dy, self.height())
            else:
                self.camera.orbit(dx, dy, self.height())
            self.update()
            return

        if self._box_active:
            self._box_cur = ev.position()
            self.update()
            return

        # Track cursor + hover edge so Down can capture a reference edge.
        self._last_mouse_pos = ev.position()
        # Plan↔profile link (Track G): let an open profile mark the station of
        # the route point under the cursor.
        win = self.window()
        if hasattr(win, "on_viewport_hover"):
            win.on_viewport_hover(ev.position().x(), ev.position().y())
        self._hover_edge = self.pick_edge(ev.position().x(), ev.position().y())

        # While a segment is being drawn, hovering an edge acquires it as a soft
        # parallel reference; the acquisition is dropped once nothing is in
        # progress, so it never goes stale across separate draws.
        drawing = (
            self.active_tool is not None
            and getattr(self.active_tool, "start_point", None) is not None
        )
        if not drawing:
            self._acquired_edge = None
            self._acquired_point = None
            self._acquired_face_normal = None
        else:
            if self._hover_edge is not None:
                self._acquired_edge = self._hover_edge
            corner = self.pick_vertex(ev.position().x(), ev.position().y())
            if corner is not None:
                self._acquired_point = corner
            face = self.pick_face(ev.position().x(), ev.position().y())
            if face is not None:
                self._acquired_face_normal = face.normal()

        if self.active_tool is None:
            return
        ctx = self._build_ctx(ev)
        if ctx is None:
            return
        self.last_snap = ctx.snap
        self.active_tool.on_hover(ctx)
        self.measurementChanged.emit(self._measurement_text())
        self.update()

    # Below this many pixels of drag, a left press/release is a click, not a box.
    BOX_DRAG_THRESHOLD_PX = 4.0

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MiddleButton:
            self._last_pos = None
            self._pan_mode = False
            return

        if ev.button() == Qt.LeftButton and self.nav_mode is not None:
            self._last_pos = None
            self._pan_mode = False
            self.setCursor(Qt.OpenHandCursor)
            return

        # Stroke tools (the Eraser): notify the release so a press-drag-release
        # stroke can commit as one step. No-op default on other tools.
        if (ev.button() == Qt.LeftButton and self.active_tool is not None
                and not self._box_active and self.nav_mode is None):
            self.active_tool.on_release(self)

        if ev.button() == Qt.LeftButton and self._box_active:
            self._box_active = False
            start = self._box_start
            end = ev.position()
            self._box_start = None
            self._box_cur = None
            tool = self.active_tool
            if tool is None or start is None:
                self.update()
                return
            dx = end.x() - start.x()
            dy = end.y() - start.y()
            additive = bool(ev.modifiers() & Qt.ShiftModifier)
            if math.hypot(dx, dy) < self.BOX_DRAG_THRESHOLD_PX:
                # A click: pick the single entity under the cursor.
                ctx = self._build_ctx(ev)
                if ctx is not None:
                    tool.on_click(ctx)
            else:
                rect = (
                    min(start.x(), end.x()), min(start.y(), end.y()),
                    max(start.x(), end.x()), max(start.y(), end.y()),
                )
                crossing = dx < 0  # right-to-left drag = crossing selection
                tool.on_box_select(self, rect, crossing, additive)
            self.update()

    def _world_under_cursor(self, x: float, y: float) -> Optional[QVector3D]:
        """The world point the cursor points at: nearest geometry hit, else the
        ground plane (Z=0), else the focal plane through the target."""
        origin, direction = self._pixel_to_ray(x, y)
        if origin is None or direction is None:
            return None
        best_t = None
        idx = self._pick_index()
        if idx.entities:
            import numpy as np
            face_t = self._ray_hits(idx, origin, direction, idx.ent_vis)
            if face_t is not None:
                t = face_t.min()
                if np.isfinite(t):
                    best_t = float(t)
        if best_t is not None:
            return origin + direction * best_t
        if abs(direction.z()) > 1e-6:
            t = -origin.z() / direction.z()
            if t > 0:
                return origin + direction * t
        view = (self.camera.target - self.camera.eye())
        if view.length() > 1e-9:
            view = view.normalized()
            denom = QVector3D.dotProduct(direction, view)
            if abs(denom) > 1e-6:
                t = QVector3D.dotProduct(self.camera.target - origin, view) / denom
                if t > 0:
                    return origin + direction * t
        return None

    def wheelEvent(self, ev) -> None:
        # Zoom toward the cursor (SketchUp-style): keep the point under the
        # pointer fixed on screen, not the origin. During a wheel burst the
        # focus is CACHED: zoom_to pins that world point, so re-picking every
        # tick both wasted ~25 ms/tick against a big model (the zoom felt
        # heavy) and let float error drift the pinned point.
        import time as _time
        steps = ev.angleDelta().y() / 120.0
        pos = ev.position()
        now = _time.monotonic()
        cached = getattr(self, "_zoom_focus", None)
        if (cached is not None and now - cached[0] < 0.4
                and abs(pos.x() - cached[1]) < 3.0
                and abs(pos.y() - cached[2]) < 3.0
                and self.scene.version == cached[3]):
            focus = cached[4]
        else:
            focus = self._world_under_cursor(pos.x(), pos.y())
        self._zoom_focus = (now, pos.x(), pos.y(), self.scene.version, focus)
        if focus is not None:
            self.camera.zoom_to(steps, focus)
        else:
            self.camera.zoom(steps)
        if _PERF:
            _plog("wheel", (_time_mod.monotonic() - now) * 1000.0)
        self.update()

    def event(self, ev) -> bool:
        # With a VCB buffer in progress, claim keys that continue it (unit
        # suffixes m/cm/mm, separators, sign) before the window's QAction
        # shortcuts swallow them — otherwise typing "2m" would fire the Move
        # tool instead of finishing the length. Bare letters with no buffer
        # still reach the shortcuts.
        if ev.type() == QEvent.ShortcutOverride and self._value_buffer:
            t = ev.text().lower()
            if t and (t.isdigit() or t in (".", ",", ";", " ", "-", "m", "c")):
                ev.accept()
                return True
        return super().event(ev)

    def keyPressEvent(self, ev) -> None:
        # 0. Shift state change → refresh snap immediately so the user sees
        #    the contextual lock take effect without moving the mouse.
        if ev.key() == Qt.Key_Shift and not ev.isAutoRepeat():
            self._capture_shift_lock()
            self._refresh_snap()
            # Do not return — Shift is a modifier; let the rest fall through.

        # 1. Numeric value buffer (VCB-style length input).
        if self._handle_value_key(ev):
            return

        # 2. Active tool gets first shot at the key.
        if self.active_tool is not None:
            if self.active_tool.on_key(self, ev.key(), ev.modifiers()):
                return

        # 3. Esc, escalating (standard CAD): first clear the typed value buffer,
        #    then cancel the tool's in-progress action (an unfinished chain, a
        #    drag), and finally — nothing in progress — clear the selection.
        if ev.key() == Qt.Key_Escape:
            if self._value_buffer:
                self._set_value_buffer("")
                return
            if self.active_tool is not None and self._tool_busy(self.active_tool):
                self.active_tool.on_cancel(self)
                return
            if self.scene.selection:
                self.scene.clear_selection()
                self.update()
                return
            if self.scene.edit_group is not None:
                self.end_group_edit()           # step out of the group
                return
            if self.active_tool is not None:
                self.active_tool.on_cancel(self)
                return

        # 3. Projection toggle.
        if ev.key() == Qt.Key_P:
            self.toggle_projection()
            return

        # 3b. Alt: cycle linear inferences (SketchUp) — all → off → parallel/perp.
        if ev.key() == Qt.Key_Alt and not ev.isAutoRepeat():
            self._cycle_linear_inference_mode()
            return

        # 4. Axis lock (arrow keys). Pressing the same arrow toggles it off.
        if ev.key() == Qt.Key_Right:
            self.axis_lock = None if self.axis_lock == "x" else "x"
            self._refresh_snap()
            return
        if ev.key() == Qt.Key_Left:
            self.axis_lock = None if self.axis_lock == "y" else "y"
            self._refresh_snap()
            return
        if ev.key() == Qt.Key_Up:
            self.axis_lock = None if self.axis_lock == "z" else "z"
            self._refresh_snap()
            return

        # 5. Reference edge — Down cycles None → parallel → perpendicular → None.
        if ev.key() == Qt.Key_Down:
            self._cycle_reference_mode()
            self._refresh_snap()
            return

        super().keyPressEvent(ev)

    def _cycle_linear_inference_mode(self) -> None:
        """Alt: cycle linear inferences all → off → parallel/perp → all
        (SketchUp's Alt toggle). Point snaps (endpoint, midpoint, …) stay on;
        explicit locks (arrow keys, Down reference) keep working in every mode."""
        order = {"all": "off", "off": "parallel_perp", "parallel_perp": "all"}
        self.linear_inference_mode = order[self.linear_inference_mode]
        label = {
            "all": "Linear inferences: all on",
            "off": "Linear inferences: off",
            "parallel_perp": "Linear inferences: parallel / perpendicular only",
        }[self.linear_inference_mode]
        self.measurementChanged.emit(label)
        self._refresh_snap()

    def _cycle_reference_mode(self) -> None:
        """Down arrow: cycle None → parallel → perpendicular → None.

        Captures whichever edge is currently under the cursor on entry to
        parallel mode. If no edge is under the cursor when starting, do
        nothing — there is nothing to be parallel/perpendicular to.
        """
        if self.reference_mode is None:
            if self._hover_edge is None:
                return  # nothing to capture
            self.reference_edge = self._hover_edge
            self.reference_mode = "parallel"
        elif self.reference_mode == "parallel":
            self.reference_mode = "perpendicular"
        else:
            self.reference_edge = None
            self.reference_mode = None

    def _refresh_snap(self) -> None:
        """Re-run snap with the last known cursor position. Used when modifier
        state changes (axis lock, reference mode, Shift) without mouse motion."""
        self.update()
        if (
            self._last_mouse_pos is None
            or self.active_tool is None
            or not self.active_tool.uses_snap
        ):
            return
        from PySide6.QtGui import QGuiApplication

        p = self._last_mouse_pos.toPoint()
        px_x, px_y = p.x(), p.y()
        world_raw = self._world_from_pixel(px_x, px_y)
        if world_raw is None:
            return
        modifiers = QGuiApplication.keyboardModifiers()
        chain_first = getattr(self.active_tool, "chain_first_point", None)
        start_pt = getattr(self.active_tool, "start_point", None)
        snap = compute_snap(
            candidate_world=world_raw,
            candidate_pixel=(px_x, px_y),
            scene=self._snap_scene(),
            world_to_pixel=self._world_to_pixel,
            threshold_px=self.snap_threshold_px,
            project_onto_line=lambda s, d: self._project_to_lock_line(s, d, px_x, px_y),
            chain_first_point=chain_first,
            start_point=start_pt,
            axis_lock=self.axis_lock,
            shift_held=bool(modifiers & Qt.ShiftModifier),
            reference_edge=self.reference_edge,
            reference_mode=self.reference_mode,
            inference_angle_deg=self.inference_angle_deg,
            is_occluded=self._is_occluded,
            face_under_cursor=self.pick_face(px_x, px_y) is not None,
            edge_threshold_px=self.edge_snap_threshold_px,
            magnetic_axis_deg=getattr(self.active_tool, "magnetic_axis_deg", None),
            acquired_edge=self._acquired_edge,
            acquired_point=self._acquired_point,
            acquired_face_normal=self._acquired_face_normal,
            shift_lock_dir=self._shift_lock[0] if self._shift_lock else None,
            shift_lock_color=self._shift_lock[1] if self._shift_lock else None,
            linear_mode=self.linear_inference_mode,
        )
        self.last_snap = snap
        ctx = ToolContext(
            viewport=self,
            world=snap.point,
            screen=self._last_mouse_pos,
            modifiers=modifiers,
            snap=snap,
        )
        self.active_tool.on_hover(ctx)
        self.measurementChanged.emit(self._measurement_text())

    def keyReleaseEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Shift and not ev.isAutoRepeat():
            self._shift_lock = None
            self._refresh_snap()
        super().keyReleaseEvent(ev)

    # Inferences whose direction can be captured by a Shift lock.
    _SHIFT_LOCKABLE = frozenset({
        "axis", "axis_inference", "reference", "through_point", "perp_face",
        "extension",
    })

    def _capture_shift_lock(self) -> None:
        """On Shift press, freeze the active inference's direction so it holds
        even as the cursor wanders off it (SketchUp's inference lock)."""
        self._shift_lock = None
        snap = self.last_snap
        tool = self.active_tool
        start = getattr(tool, "start_point", None) if tool is not None else None
        if snap is None or start is None or snap.kind not in self._SHIFT_LOCKABLE:
            return
        d = snap.point - start
        if d.length() > 1e-6:
            self._shift_lock = (d.normalized(), snap.color)

    # ---- Numeric value buffer (VCB-style) ----------------------------------
    def _handle_value_key(self, ev) -> bool:
        """Buffer digit / dot / comma / semicolon / space / backspace.

        Enter applies the buffer via ``active_tool.on_value(...)``.

        Input forms:
        - ``"5"`` or ``"5,3"`` or ``"5.3"`` → single length (float).
        - ``"3;4;5"`` or ``"3 4 5"``       → 3D delta from the start point
                                              (passed as a ``(dx, dy, dz)`` tuple).
        - ``"-2"``                          → negative value (tools that take a
                                              direction flip it, SketchUp-style).
        - ``"30cm"`` / ``"1500mm"`` / ``"2m"`` → unit suffix per field; bare
                                              numbers are metres (project unit).
        Comma is always the decimal separator; ``;`` and space are field
        separators (SketchUp convention adapted to our locale).
        """
        if self.active_tool is None:
            return False

        text = ev.text()
        key = ev.key()

        if key in (Qt.Key_Return, Qt.Key_Enter):
            if not self._value_buffer:
                return False
            value = self._parse_value_buffer(self._value_buffer)
            if value is None:
                self._set_value_buffer("")
                return True
            self.active_tool.on_value(self, value)
            self._set_value_buffer("")
            return True

        if key == Qt.Key_Backspace:
            if not self._value_buffer:
                return False
            self._set_value_buffer(self._value_buffer[:-1])
            return True

        if text and (text.isdigit() or text in (".", ",", ";", " ", "-")
                     or text.lower() in ("m", "c")):
            # A field separator (space / ;) with an empty buffer isn't VCB
            # input — let it fall through so Space can act as the Select
            # shortcut (SketchUp-style). It only separates fields mid-number.
            if text in (";", " ") and not self._value_buffer:
                return False
            # A unit letter with an empty buffer is a tool shortcut (M = Move,
            # C = Circle), not VCB input — only buffer it after a digit.
            if text.lower() in ("m", "c") and not self._current_token_tail():
                return False
            # Minus only opens a token (a sign, not an operator).
            if text == "-" and self._current_token_tail():
                return True
            # Forbid two decimal separators in the current numeric token.
            if text in (".", ","):
                tail = self._current_token_tail()
                if "." in tail or "," in tail:
                    return True
            self._set_value_buffer(self._value_buffer + text)
            return True

        return False

    @staticmethod
    def _parse_value_buffer(buffer: str):
        """Return a float, a 2-tuple ``(w, h)`` (rectangle dimensions), a
        3-tuple ``(dx, dy, dz)`` (delta), or ``None`` on parse error. Each tool's
        ``on_value`` accepts the arity it understands and ignores the rest.
        Fields may carry a unit suffix (``m``/``cm``/``mm``); bare numbers are
        metres, and a leading minus is kept (direction tools flip on it)."""
        normalized = buffer.replace(",", ".").replace(";", " ")
        scale = {"": 1.0, "m": 1.0, "cm": 0.01, "mm": 0.001}
        nums = []
        for p in normalized.split():
            m = re.fullmatch(r"(-?(?:\d+\.?\d*|\.\d+))(mm|cm|m)?", p.lower())
            if m is None:
                return None
            nums.append(float(m.group(1)) * scale[m.group(2) or ""])
        if len(nums) == 1:
            return nums[0]
        if len(nums) in (2, 3):
            return tuple(nums)
        return None

    def _current_token_tail(self) -> str:
        """The portion of the buffer after the last ``;`` or space."""
        normalized = self._value_buffer.replace(";", " ")
        idx = normalized.rfind(" ")
        if idx < 0:
            return self._value_buffer
        return self._value_buffer[idx + 1 :]

    def _set_value_buffer(self, text: str) -> None:
        if text == self._value_buffer:
            return
        self._value_buffer = text
        self.valueBufferChanged.emit(text)
        self.update()

    def toggle_projection(self) -> None:
        self.camera.toggle_projection()
        self.update()

    # ---- Helpers ------------------------------------------------------------
    def _build_ctx(self, ev) -> Optional[ToolContext]:
        p = ev.position().toPoint()
        px_x, px_y = p.x(), p.y()
        world_raw = self._world_from_pixel(px_x, px_y)
        if world_raw is None:
            return None
        # Tools that don't snap (Select, Push/Pull) skip the snap engine and its
        # occlusion raycasts entirely, and show no snap marker.
        if self.active_tool is not None and not self.active_tool.uses_snap:
            snap = SnapResult(world_raw, "none")
            return ToolContext(
                viewport=self,
                world=world_raw,
                screen=ev.position(),
                modifiers=ev.modifiers(),
                snap=snap,
            )
        chain_first = None
        start_pt = None
        if self.active_tool is not None:
            chain_first = getattr(self.active_tool, "chain_first_point", None)
            start_pt = getattr(self.active_tool, "start_point", None)
        shift_held = bool(ev.modifiers() & Qt.ShiftModifier)
        snap = compute_snap(
            candidate_world=world_raw,
            candidate_pixel=(px_x, px_y),
            scene=self._snap_scene(),
            world_to_pixel=self._world_to_pixel,
            threshold_px=self.snap_threshold_px,
            project_onto_line=lambda s, d: self._project_to_lock_line(s, d, px_x, px_y),
            chain_first_point=chain_first,
            start_point=start_pt,
            axis_lock=self.axis_lock,
            shift_held=shift_held,
            reference_edge=self.reference_edge,
            reference_mode=self.reference_mode,
            inference_angle_deg=self.inference_angle_deg,
            is_occluded=self._is_occluded,
            face_under_cursor=self.pick_face(px_x, px_y) is not None,
            edge_threshold_px=self.edge_snap_threshold_px,
            magnetic_axis_deg=getattr(self.active_tool, "magnetic_axis_deg", None),
            acquired_edge=self._acquired_edge,
            acquired_point=self._acquired_point,
            acquired_face_normal=self._acquired_face_normal,
            shift_lock_dir=self._shift_lock[0] if self._shift_lock else None,
            shift_lock_color=self._shift_lock[1] if self._shift_lock else None,
            linear_mode=self.linear_inference_mode,
        )
        return ToolContext(
            viewport=self,
            world=snap.point,
            screen=ev.position(),
            modifiers=ev.modifiers(),
            snap=snap,
        )
