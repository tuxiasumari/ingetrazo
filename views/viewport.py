"""3D viewport: orbital camera, infinite-feel grid, XYZ axes.

Uses PySide6's bundled QOpenGL* helper classes (QOpenGLShaderProgram,
QOpenGLBuffer, QOpenGLVertexArrayObject) — no external GL bindings yet.
moderngl lands when we start dealing with real meshes.

Wayland requires every frame to be drawn explicitly: `paintGL` always
calls `glClear` first to avoid showing stale GPU memory.

Navigation (SketchUp-like):
- Middle-button drag: orbit
- Shift + Middle-button drag: pan
- Wheel: zoom
- P: toggle perspective / parallel projection
"""
from __future__ import annotations

from array import array
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QOpenGLFunctions, QVector4D
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from core.camera import OrbitCamera


# OpenGL constants — kept as literals so we don't need PyOpenGL just for them.
GL_FLOAT = 0x1406
GL_LINES = 0x0001
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_DEPTH_TEST = 0x0B71
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303


SHADER_DIR = Path(__file__).resolve().parents[1] / "resources" / "shaders"


def _grid_vertices(half_size: int = 50, step: float = 1.0) -> array:
    """Lines forming an N×N grid on the Z=0 plane, packed as raw floats."""
    coords = array("f")
    extent = half_size * step
    for i in range(-half_size, half_size + 1):
        c = i * step
        coords.extend([c, -extent, 0.0,  c, extent, 0.0])    # parallel to Y
        coords.extend([-extent, c, 0.0,  extent, c, 0.0])    # parallel to X
    return coords


def _axes_vertices(length: float = 10.0) -> array:
    """Three line segments from the origin along +X, +Y, +Z."""
    return array("f", [
        0.0, 0.0, 0.0,  length, 0.0, 0.0,   # X
        0.0, 0.0, 0.0,  0.0, length, 0.0,   # Y
        0.0, 0.0, 0.0,  0.0, 0.0, length,   # Z
    ])


class Viewport(QOpenGLWidget):
    """OpenGL viewport with orbital camera, grid and XYZ axes."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(640, 480)
        self.setFocusPolicy(Qt.StrongFocus)

        self.camera = OrbitCamera()

        self._gl: QOpenGLFunctions | None = None
        self._program: QOpenGLShaderProgram | None = None
        self._loc_mvp = -1
        self._loc_color = -1

        self._grid_vao: QOpenGLVertexArrayObject | None = None
        self._grid_vbo: QOpenGLBuffer | None = None
        self._grid_count = 0

        self._axes_vao: QOpenGLVertexArrayObject | None = None
        self._axes_vbo: QOpenGLBuffer | None = None

        self._last_pos = None
        self._pan_mode = False

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
        loc_pos = self._program.attributeLocation("a_pos")

        self._grid_vao, self._grid_vbo, self._grid_count = self._upload(
            _grid_vertices(), loc_pos
        )
        self._axes_vao, self._axes_vbo, _ = self._upload(
            _axes_vertices(), loc_pos
        )

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

        # Grid — soft slate
        self._set_color(0.78, 0.80, 0.84, 1.0)
        self._grid_vao.bind()
        self._gl.glDrawArrays(GL_LINES, 0, self._grid_count)
        self._grid_vao.release()

        # Axes — RGB
        self._axes_vao.bind()
        self._set_color(0.86, 0.22, 0.27, 1.0)  # X red
        self._gl.glDrawArrays(GL_LINES, 0, 2)
        self._set_color(0.16, 0.62, 0.36, 1.0)  # Y green
        self._gl.glDrawArrays(GL_LINES, 2, 2)
        self._set_color(0.20, 0.40, 0.78, 1.0)  # Z blue
        self._gl.glDrawArrays(GL_LINES, 4, 2)
        self._axes_vao.release()

        self._program.release()

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

    def _upload(self, data: array, loc_pos: int):
        vao = QOpenGLVertexArrayObject(self)
        vao.create()
        vao.bind()

        vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        vbo.create()
        vbo.bind()
        raw = data.tobytes()
        vbo.allocate(raw, len(raw))

        self._program.bind()
        self._program.enableAttributeArray(loc_pos)
        self._program.setAttributeBuffer(loc_pos, GL_FLOAT, 0, 3)
        self._program.release()

        vbo.release()
        vao.release()
        return vao, vbo, len(data) // 3

    def _set_color(self, r: float, g: float, b: float, a: float) -> None:
        self._program.setUniformValue(self._loc_color, QVector4D(r, g, b, a))

    # ---- Input --------------------------------------------------------------
    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MiddleButton:
            self._last_pos = ev.position().toPoint()
            self._pan_mode = bool(ev.modifiers() & Qt.ShiftModifier)

    def mouseMoveEvent(self, ev) -> None:
        if self._last_pos is None:
            return
        p = ev.position().toPoint()
        dx = p.x() - self._last_pos.x()
        dy = p.y() - self._last_pos.y()
        self._last_pos = p
        if self._pan_mode:
            self.camera.pan(dx, dy, self.height())
        else:
            self.camera.orbit(dx, dy, self.height())
        self.update()

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MiddleButton:
            self._last_pos = None
            self._pan_mode = False

    def wheelEvent(self, ev) -> None:
        self.camera.zoom(ev.angleDelta().y() / 120.0)
        self.update()

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_P:
            self.toggle_projection()
            return
        super().keyPressEvent(ev)

    def toggle_projection(self) -> None:
        """Public hook for menus / toolbars."""
        self.camera.toggle_projection()
        self.update()
