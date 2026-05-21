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
    QVector3D,
    QVector4D,
)
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from core.camera import OrbitCamera
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
    because Wasia doesn't (yet) cull back faces."""
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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
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

        self.snap_threshold_px = 12.0
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
        self._faces_vao = None
        self._faces_vbo = None
        self._faces_count = 0
        self._edges_version = -1

        self._rubber_vao = None
        self._rubber_vbo = None

        # Camera navigation state (middle button)
        self._last_pos = None
        self._pan_mode = False

        # Numeric value buffer (VCB-style typed length).
        self._value_buffer = ""

    # ---- GL lifecycle -------------------------------------------------------
    def initializeGL(self) -> None:
        self._gl = QOpenGLFunctions(self.context())
        self._gl.initializeOpenGLFunctions()
        self._gl.glClearColor(0.93, 0.94, 0.96, 1.0)
        self._gl.glEnable(GL_DEPTH_TEST)
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
        self._faces_vao, self._faces_vbo = self._create_dynamic()
        self._rubber_vao, self._rubber_vbo = self._create_dynamic()

    def resizeGL(self, w: int, h: int) -> None:
        if self._gl is None:
            return
        self._gl.glViewport(0, 0, w, h)
        self.camera.set_aspect(w, h)

    def paintGL(self) -> None:
        if self._gl is None or self._program is None:
            return
        self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        mvp = self.camera.projection_matrix() * self.camera.view_matrix()
        self._program.bind()
        self._program.setUniformValue(self._loc_mvp, mvp)

        # Grid
        self._set_color(0.78, 0.80, 0.84, 1.0)
        self._grid_vao.bind()
        self._gl.glDrawArrays(GL_LINES, 0, self._grid_count)
        self._grid_vao.release()

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

        # Axes — drawn before the rubber band so coincident rubber-band lines
        # (e.g. while axis-locked) still overlay them.
        self._axes_vao.bind()
        self._set_color(0.86, 0.22, 0.27, 1.0)  # X red
        self._gl.glDrawArrays(GL_LINES, 0, 2)
        self._set_color(0.16, 0.62, 0.36, 1.0)  # Y green
        self._gl.glDrawArrays(GL_LINES, 2, 2)
        self._set_color(0.20, 0.40, 0.78, 1.0)  # Z blue
        self._gl.glDrawArrays(GL_LINES, 4, 2)
        self._axes_vao.release()

        # Rubber band preview — always on top. Depth test off so it doesn't
        # z-fight with coincident axes.
        self._gl.glDisable(GL_DEPTH_TEST)
        self._draw_rubber_band()
        self._gl.glEnable(GL_DEPTH_TEST)

        self._program.release()

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

        sel_data = array("f")
        for e in self.scene.selection:
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

        # Faces: fan-triangulate each face and concatenate into a single VBO.
        face_data = array("f")
        for face in self.scene.faces:
            v = face.vertices
            if len(v) < 3:
                continue
            for i in range(1, len(v) - 1):
                face_data.extend([
                    v[0].x(), v[0].y(), v[0].z(),
                    v[i].x(), v[i].y(), v[i].z(),
                    v[i + 1].x(), v[i + 1].y(), v[i + 1].z(),
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

    def _draw_rubber_band(self) -> None:
        tool = self.active_tool
        if tool is None:
            return
        segments = tool.rubber_band_lines()
        if not segments:
            return

        snap = self.last_snap
        if snap is not None and snap.kind == "axis":
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

        painter.end()

    def _draw_snap_indicator(self, painter: QPainter, snap: SnapResult) -> None:
        # Axis inference is conveyed by the rubber-band color alone; no badge.
        if snap.kind == "axis_inference":
            return
        pixel = self._world_to_pixel(snap.point)
        if pixel is None:
            return
        r, g, b = snap.color
        color = QColor.fromRgbF(r, g, b, 1.0)
        painter.setPen(QPen(color, 2.0))
        painter.setBrush(QColor.fromRgbF(r, g, b, 0.25))
        px, py = pixel
        if snap.kind == "endpoint" or snap.kind == "origin":
            painter.drawRect(QRectF(px - 5, py - 5, 10, 10))
        elif snap.kind == "close":
            painter.drawEllipse(QPointF(px, py), 7.0, 7.0)
        elif snap.kind == "axis":
            painter.drawEllipse(QPointF(px, py), 4.0, 4.0)
        elif snap.kind == "reference":
            # Diamond marker for reference lock.
            painter.drawEllipse(QPointF(px, py), 5.0, 5.0)

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
        """Pixel → world hit on the Z=0 plane, or None."""
        origin, direction = self._pixel_to_ray(x, y)
        if origin is None or direction is None:
            return None
        if abs(direction.z()) < 1e-6:
            return None
        t = -origin.z() / direction.z()
        if t < 0:
            return None
        return QVector3D(
            origin.x() + t * direction.x(),
            origin.y() + t * direction.y(),
            0.0,
        )

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

    def pick_face(self, screen_x: float, screen_y: float):
        """Return the front-most face the cursor ray hits, or ``None``."""
        origin, direction = self._pixel_to_ray(screen_x, screen_y)
        if origin is None or direction is None:
            return None
        best_t = float("inf")
        best = None
        for face in self.scene.faces:
            v = face.vertices
            if len(v) < 3:
                continue
            for i in range(1, len(v) - 1):
                t = _ray_triangle(origin, direction, v[0], v[i], v[i + 1])
                if t is not None and t < best_t:
                    best_t = t
                    best = face
                    break  # triangles of one face are coplanar
        return best

    # ---- Tool management ----------------------------------------------------
    def set_active_tool(self, tool: Optional[Tool]) -> None:
        if self.active_tool is tool:
            return
        if self.active_tool is not None:
            self.active_tool.on_deactivate(self)
        self.active_tool = tool
        if tool is not None:
            tool.on_activate(self)
        self.update()

    # ---- Input --------------------------------------------------------------
    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MiddleButton:
            self._last_pos = ev.position().toPoint()
            self._pan_mode = bool(ev.modifiers() & Qt.ShiftModifier)
            return
        if ev.button() == Qt.LeftButton and self.active_tool is not None:
            ctx = self._build_ctx(ev)
            if ctx is not None:
                self.active_tool.on_click(ctx)
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

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MiddleButton:
            self._last_pos = None
            self._pan_mode = False

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
        """Buffer digit / dot / comma / backspace. Return True if consumed.

        Enter applies the buffer via ``active_tool.on_value(...)``.
        """
        if self.active_tool is None:
            return False

        text = ev.text()
        key = ev.key()

        if key in (Qt.Key_Return, Qt.Key_Enter):
            if not self._value_buffer:
                return False
            try:
                value = float(self._value_buffer.replace(",", "."))
            except ValueError:
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

        if text and (text.isdigit() or text in (".", ",")):
            # Forbid two separators in the buffer.
            if text in (".", ",") and ("." in self._value_buffer or "," in self._value_buffer):
                return True  # swallow but don't append
            self._set_value_buffer(self._value_buffer + text)
            return True

        return False

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
        )
        return ToolContext(
            viewport=self,
            world=snap.point,
            screen=ev.position(),
            modifiers=ev.modifiers(),
            snap=snap,
        )
