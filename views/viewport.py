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
from array import array
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
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
    QOpenGLVertexArrayObject,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from core.camera import OrbitCamera
from core.geometry import Edge, Face
from core.history import History
from core.scene import Scene
from core.snap import SnapResult, compute_snap
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

def _grid_vertices(half_size: int = 50, step: float = 1.0) -> array:
    coords = array("f")
    extent = half_size * step
    for i in range(-half_size, half_size + 1):
        c = i * step
        coords.extend([c, -extent, 0.0,  c, extent, 0.0])    # parallel to Y
        coords.extend([-extent, c, 0.0,  extent, c, 0.0])    # parallel to X
    return coords


def _axes_vertices(length: float = 10.0) -> array:
    return array("f", [
        0.0, 0.0, 0.0,  length, 0.0, 0.0,
        0.0, 0.0, 0.0,  0.0, length, 0.0,
        0.0, 0.0, 0.0,  0.0, 0.0, length,
    ])


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

    # Tooltip text shown next to the snap marker, SketchUp-style.
    _SNAP_LABELS = {
        "endpoint": "Endpoint",
        "midpoint": "Midpoint",
        "on_edge": "On Edge",
        "on_face": "On Face",
        "origin": "Origin",
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
        fmt.setSamples(4)
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

        # Reference-edge state (Down arrow → parallel / perpendicular).
        self.reference_edge = None
        self.reference_mode: Optional[str] = None  # None | "parallel" | "perpendicular"
        self._hover_edge = None  # last edge under cursor (candidate for capture)
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

        self._grid_vao = None
        self._grid_vbo = None
        self._grid_count = 0
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
        self._edges_version = -1

        # Hover highlight (Select tool). Not version-tracked — it changes with
        # the cursor, not with scene mutations — so it's uploaded per paint.
        self._hover_entity = None  # None | Edge | Face under the cursor
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

        # Rubber-band box selection (left-drag with a box_select tool).
        self._box_active = False
        self._box_start: Optional[QPointF] = None
        self._box_cur: Optional[QPointF] = None

        # Numeric value buffer (VCB-style typed length).
        self._value_buffer = ""

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
        self._loc_pos = self._program.attributeLocation("a_pos")

        self._grid_vao, self._grid_vbo, self._grid_count = self._upload_static(
            _grid_vertices()
        )
        self._axes_vao, self._axes_vbo, _ = self._upload_static(_axes_vertices())

        self._edges_vao, self._edges_vbo = self._create_dynamic()
        self._selected_vao, self._selected_vbo = self._create_dynamic()
        self._sel_faces_vao, self._sel_faces_vbo = self._create_dynamic()
        self._faces_vao, self._faces_vbo = self._create_dynamic()
        self._hover_faces_vao, self._hover_faces_vbo = self._create_dynamic()
        self._hover_edges_vao, self._hover_edges_vbo = self._create_dynamic()
        self._rubber_vao, self._rubber_vbo = self._create_dynamic()
        self._preview_faces_vao, self._preview_faces_vbo = self._create_dynamic()

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
        self._scene_fbo = QOpenGLFramebufferObject(size[0], size[1], fmt)
        self._fbo_size = size

    def paintGL(self) -> None:
        if self._gl is None or self._program is None:
            return

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
        self._gl.glClearColor(0.93, 0.94, 0.96, 1.0)
        self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        mvp = self.camera.projection_matrix() * self.camera.view_matrix()
        self._program.bind()
        self._program.setUniformValue(self._loc_mvp, mvp)

        # Grid — depth-tested (so geometry hides it) but depth-write OFF, so
        # grid lines don't pollute the depth buffer at z=0 and accidentally
        # cull the bottom face of a freshly extruded box where they overlap.
        self._gl.glDepthMask(GL_FALSE)
        self._set_color(0.78, 0.80, 0.84, 1.0)
        self._grid_vao.bind()
        self._gl.glDrawArrays(GL_LINES, 0, self._grid_count)
        self._grid_vao.release()
        self._gl.glDepthMask(GL_TRUE)

        # Persistent edges + faces
        self._sync_edges()

        # Faces — drawn before edges, with polygon offset so coincident
        # boundary edges sit cleanly on top instead of z-fighting.
        if self._faces_count > 0:
            self._gl.glEnable(GL_POLYGON_OFFSET_FILL)
            self._gl.glPolygonOffset(1.0, 1.0)
            self._set_color(0.92, 0.89, 0.81, 1.0)  # warm cream (SketchUp-ish)
            self._faces_vao.bind()
            self._gl.glDrawArrays(GL_TRIANGLES, 0, self._faces_count)
            self._faces_vao.release()
            self._gl.glDisable(GL_POLYGON_OFFSET_FILL)

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

        # Axes — drawn BEFORE user edges so any edge the user happens to draw
        # along an axis (or coincident with one) wins the GL_LEQUAL depth
        # test and shows on top of the axis colour. Rubber-band stays on top
        # of both because it's drawn last with depth test off.
        self._axes_vao.bind()
        self._set_color(0.86, 0.22, 0.27, 1.0)  # X red
        self._gl.glDrawArrays(GL_LINES, 0, 2)
        self._set_color(0.16, 0.62, 0.36, 1.0)  # Y green
        self._gl.glDrawArrays(GL_LINES, 2, 2)
        self._set_color(0.20, 0.40, 0.78, 1.0)  # Z blue
        self._gl.glDrawArrays(GL_LINES, 4, 2)
        self._axes_vao.release()

        if self._edges_count > 0:
            self._set_color(0.13, 0.17, 0.23, 1.0)
            self._edges_vao.bind()
            self._gl.glDrawArrays(GL_LINES, 0, self._edges_count)
            self._edges_vao.release()

        # Selected edges (drawn on top, highlighted)
        if self._selected_count > 0:
            self._set_color(0.95, 0.45, 0.16, 1.0)
            self._selected_vao.bind()
            self._gl.glDrawArrays(GL_LINES, 0, self._selected_count)
            self._selected_vao.release()

        # Hovered edge — light blue, on top of everything else so it reads as
        # the pick candidate even when it overlaps a selected (orange) edge.
        if isinstance(self._hover_entity, Edge):
            self._upload_hover_edge(self._hover_entity)
            self._set_color(0.30, 0.55, 0.95, 1.0)
            self._hover_edges_vao.bind()
            self._gl.glDrawArrays(GL_LINES, 0, 2)
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

    def _set_color(self, r: float, g: float, b: float, a: float) -> None:
        self._program.setUniformValue(self._loc_color, QVector4D(r, g, b, a))

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

        all_data = array("f")
        for e in self.scene.edges:
            all_data.extend([
                e.a.x(), e.a.y(), e.a.z(),
                e.b.x(), e.b.y(), e.b.z(),
            ])
        self._edges_vbo.bind()
        if all_data:
            raw = all_data.tobytes()
            self._edges_vbo.allocate(raw, len(raw))
        else:
            self._edges_vbo.allocate(24)
        self._edges_vbo.release()
        self._edges_count = len(all_data) // 3

        # The selection set is heterogeneous (edges and/or faces). Split it:
        # edges go to the highlighted-line VBO, faces to the overlay VBO.
        sel_data = array("f")
        for e in self.scene.selection:
            if not isinstance(e, Edge):
                continue
            sel_data.extend([
                e.a.x(), e.a.y(), e.a.z(),
                e.b.x(), e.b.y(), e.b.z(),
            ])
        self._selected_vbo.bind()
        if sel_data:
            sel_raw = sel_data.tobytes()
            self._selected_vbo.allocate(sel_raw, len(sel_raw))
        else:
            self._selected_vbo.allocate(24)
        self._selected_vbo.release()
        self._selected_count = len(sel_data) // 3

        sel_face_data = array("f")
        for face in self.scene.selection:
            if not isinstance(face, Face):
                continue
            for t0, t1, t2 in face.triangulate():
                sel_face_data.extend([
                    t0.x(), t0.y(), t0.z(),
                    t1.x(), t1.y(), t1.z(),
                    t2.x(), t2.y(), t2.z(),
                ])
        self._sel_faces_vbo.bind()
        if sel_face_data:
            sel_face_raw = sel_face_data.tobytes()
            self._sel_faces_vbo.allocate(sel_face_raw, len(sel_face_raw))
        else:
            self._sel_faces_vbo.allocate(24)
        self._sel_faces_vbo.release()
        self._sel_faces_count = len(sel_face_data) // 3

        # Faces: triangulate each face (fan when simple, hole-aware when the
        # face has been divided) and concatenate into a single VBO.
        face_data = array("f")
        suppressed_faces = self._suppressed_faces
        for face in self.scene.faces:
            if face in suppressed_faces:
                continue
            for t0, t1, t2 in face.triangulate():
                face_data.extend([
                    t0.x(), t0.y(), t0.z(),
                    t1.x(), t1.y(), t1.z(),
                    t2.x(), t2.y(), t2.z(),
                ])
        self._faces_vbo.bind()
        if face_data:
            face_raw = face_data.tobytes()
            self._faces_vbo.allocate(face_raw, len(face_raw))
        else:
            self._faces_vbo.allocate(24)
        self._faces_vbo.release()
        self._faces_count = len(face_data) // 3

        self._edges_version = self.scene.version
        self.sceneVersionChanged.emit(self._edges_version)

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

    def _upload_hover_edge(self, edge: Edge) -> None:
        """Upload the single hovered edge into the hover-edges VBO."""
        data = array("f", [
            edge.a.x(), edge.a.y(), edge.a.z(),
            edge.b.x(), edge.b.y(), edge.b.z(),
        ])
        self._hover_edges_vbo.bind()
        raw = data.tobytes()
        self._hover_edges_vbo.allocate(raw, len(raw))
        self._hover_edges_vbo.release()

    def set_hover(self, entity) -> None:
        """Set the entity (edge/face) highlighted under the cursor and repaint
        if it changed. ``None`` clears the highlight."""
        if entity is self._hover_entity:
            return
        self._hover_entity = entity
        self.update()

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
        for face in faces:
            for t0, t1, t2 in face.triangulate():
                data.extend([
                    t0.x(), t0.y(), t0.z(),
                    t1.x(), t1.y(), t1.z(),
                    t2.x(), t2.y(), t2.z(),
                ])
        if not data:
            return
        self._preview_faces_vbo.bind()
        raw = data.tobytes()
        self._preview_faces_vbo.allocate(raw, len(raw))
        self._preview_faces_vbo.release()

        self._gl.glEnable(GL_POLYGON_OFFSET_FILL)
        self._gl.glPolygonOffset(1.0, 1.0)
        self._set_color(0.92, 0.89, 0.81, 1.0)  # warm cream, same as real faces
        self._preview_faces_vao.bind()
        self._gl.glDrawArrays(GL_TRIANGLES, 0, len(data) // 3)
        self._preview_faces_vao.release()
        self._gl.glDisable(GL_POLYGON_OFFSET_FILL)

    def _draw_rubber_band(self) -> None:
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
        if forced is not None:
            color = forced
        elif snap is not None and snap.kind == "axis":
            r, g, b = snap.color
            color = (r, g, b, 1.0)
        elif snap is not None and snap.kind == "axis_inference":
            r, g, b = snap.color
            color = (r, g, b, 0.50)
        elif snap is not None and snap.kind == "reference":
            r, g, b = snap.color
            color = (r, g, b, 1.0)
        elif snap is not None and snap.kind == "close":
            color = (0.20, 0.40, 0.78, 0.95)
        else:
            color = (0.95, 0.45, 0.16, 0.85)

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

    # ---- 2D overlay (QPainter on top of OpenGL) -----------------------------
    def _draw_overlay(self) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        # Snap indicator
        if (
            self.active_tool is not None
            and self.last_snap is not None
            and self.last_snap.kind != "none"
        ):
            self._draw_snap_indicator(painter, self.last_snap)

        # Length measurement near rubber band
        self._draw_length_label(painter)

        # Labels in the top-left. Reference > explicit axis lock > soft inference.
        if self.reference_mode is not None:
            self._draw_reference_label(painter)
        elif self.axis_lock is not None:
            self._draw_axis_lock_label(painter)
        else:
            self._draw_inference_label(painter)

        # Rubber-band selection box.
        self._draw_selection_box(painter)

        painter.end()

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
        painter.setPen(QPen(color, 2.0))
        painter.setBrush(QColor.fromRgbF(r, g, b, 0.25))
        px, py = pixel
        if snap.kind in ("endpoint", "origin", "on_edge"):
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
        elif snap.kind == "reference":
            # Diamond marker for reference lock.
            painter.drawEllipse(QPointF(px, py), 5.0, 5.0)

        # Tooltip text next to the marker (SketchUp shows "On Edge", etc.).
        label = self._SNAP_LABELS.get(snap.kind)
        if label:
            font = QFont()
            font.setPointSize(9)
            painter.setFont(font)
            painter.setPen(QPen(QColor(255, 255, 255, 220)))
            painter.drawText(QPointF(px + 11, py + 17), label)
            painter.setPen(QPen(color))
            painter.drawText(QPointF(px + 10, py + 16), label)

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
        if abs(forward.z()) >= math.sin(math.radians(self.HORIZON_PITCH_THRESHOLD_DEG)):
            return start, QVector3D(0.0, 0.0, 1.0)
        # Near-horizon view — pick the vertical plane whose normal is more
        # end-on to the camera so cursor motion maps cleanly to it.
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

    def pick_edge(self, screen_x: float, screen_y: float):
        """Return the edge closest to ``(screen_x, screen_y)`` within threshold."""
        best = None
        best_d = self.pick_threshold_px
        for edge in self.scene.edges:
            pa = self._world_to_pixel(edge.a)
            pb = self._world_to_pixel(edge.b)
            if pa is None or pb is None:
                continue
            d = _point_to_segment_distance_2d((screen_x, screen_y), pa, pb)
            if d < best_d:
                best_d = d
                best = edge
        return best

    def _is_occluded(self, world: QVector3D) -> bool:
        """Whether a face sits between the camera and ``world`` — i.e. the
        point is hidden behind solid geometry from the current view. Used to
        keep snaps from firing on edges/vertices the user can't see.

        A small epsilon keeps a point that lies *on* a face (e.g. an edge on
        that face's boundary) from being reported as occluded by its own
        face."""
        origin = self.camera.eye()
        delta = world - origin
        dist = delta.length()
        if dist < 1e-9:
            return False
        direction = delta.normalized()
        eps = 1e-3
        for face in self.scene.faces:
            for t0, t1, t2 in face.triangulate():
                t = _ray_triangle(origin, direction, t0, t1, t2)
                if t is not None and t < dist - eps:
                    return True
        return False

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
        hits: list[tuple[float, object]] = []
        for face in self.scene.faces:
            face_t = None
            for t0, t1, t2 in face.triangulate():
                t = _ray_triangle(origin, direction, t0, t1, t2)
                if t is not None and (face_t is None or t < face_t):
                    face_t = t
            if face_t is not None:
                hits.append((face_t, face))
        if not hits:
            return None
        best_t = min(t for t, _ in hits)
        eps = max(1e-4, best_t * 1e-4)
        candidates = [f for t, f in hits if t <= best_t + eps]
        if len(candidates) == 1:
            return candidates[0]
        return min(candidates, key=lambda f: f.area())

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
        if tool is not None:
            tool.on_activate(self)
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
            # Box-select tools defer the decision to release: a tiny drag is a
            # click, a real drag is a rubber-band box.
            if self.active_tool.box_select:
                self._box_active = True
                self._box_start = ev.position()
                self._box_cur = ev.position()
                return
            had_start = getattr(self.active_tool, "start_point", None) is not None
            had_plane = getattr(self.active_tool, "work_plane", None) is not None
            face_at_click = None
            if not had_start and not had_plane:
                face_at_click = self.pick_face(ev.position().x(), ev.position().y())
            ctx = self._build_ctx(ev)
            if ctx is not None:
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
                self.update()

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
        self._hover_edge = self.pick_edge(ev.position().x(), ev.position().y())

        if self.active_tool is None:
            return
        ctx = self._build_ctx(ev)
        if ctx is None:
            return
        self.last_snap = ctx.snap
        self.active_tool.on_hover(ctx)
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

    def wheelEvent(self, ev) -> None:
        self.camera.zoom(ev.angleDelta().y() / 120.0)
        self.update()

    def keyPressEvent(self, ev) -> None:
        # 0. Shift state change → refresh snap immediately so the user sees
        #    the contextual lock take effect without moving the mouse.
        if ev.key() == Qt.Key_Shift and not ev.isAutoRepeat():
            self._refresh_snap()
            # Do not return — Shift is a modifier; let the rest fall through.

        # 1. Numeric value buffer (VCB-style length input).
        if self._handle_value_key(ev):
            return

        # 2. Active tool gets first shot at the key.
        if self.active_tool is not None:
            if self.active_tool.on_key(self, ev.key(), ev.modifiers()):
                return

        # 3. Esc cancels the in-progress tool action (or clears the buffer
        #    first if it has any content).
        if ev.key() == Qt.Key_Escape:
            if self._value_buffer:
                self._set_value_buffer("")
                return
            if self.active_tool is not None:
                self.active_tool.on_cancel(self)
                return

        # 3. Projection toggle.
        if ev.key() == Qt.Key_P:
            self.toggle_projection()
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
            scene=self.scene,
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

    def keyReleaseEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Shift and not ev.isAutoRepeat():
            self._refresh_snap()
        super().keyReleaseEvent(ev)

    # ---- Numeric value buffer (VCB-style) ----------------------------------
    def _handle_value_key(self, ev) -> bool:
        """Buffer digit / dot / comma / semicolon / space / backspace.

        Enter applies the buffer via ``active_tool.on_value(...)``.

        Input forms:
        - ``"5"`` or ``"5,3"`` or ``"5.3"`` → single length (float).
        - ``"3;4;5"`` or ``"3 4 5"``       → 3D delta from the start point
                                              (passed as a ``(dx, dy, dz)`` tuple).
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

        if text and (text.isdigit() or text in (".", ",", ";", " ")):
            # A field separator (space / ;) with an empty buffer isn't VCB
            # input — let it fall through so Space can act as the Select
            # shortcut (SketchUp-style). It only separates fields mid-number.
            if text in (";", " ") and not self._value_buffer:
                return False
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
        """Return a float, a ``(dx, dy, dz)`` tuple, or ``None`` on parse error."""
        normalized = buffer.replace(",", ".").replace(";", " ")
        parts = normalized.split()
        try:
            nums = [float(p) for p in parts if p]
        except ValueError:
            return None
        if len(nums) == 1:
            return nums[0]
        if len(nums) == 3:
            return (nums[0], nums[1], nums[2])
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
            scene=self.scene,
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
        )
        return ToolContext(
            viewport=self,
            world=snap.point,
            screen=ev.position(),
            modifiers=ev.modifiers(),
            snap=snap,
        )
