"""3D viewport: QOpenGLWidget host for the ModernGL renderer.

Placeholder for now. Real rendering, orbit camera and tool dispatch
land in upcoming work.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtOpenGLWidgets import QOpenGLWidget


class Viewport(QOpenGLWidget):
    """OpenGL surface where the 3D scene is rendered.

    Will own a ModernGL context, the camera, and the grid. Tools receive
    mouse / keyboard events relayed from here.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(640, 480)
        self.setFocusPolicy(Qt.StrongFocus)

    def initializeGL(self) -> None:
        # ModernGL context creation will live here.
        pass

    def resizeGL(self, w: int, h: int) -> None:
        pass

    def paintGL(self) -> None:
        # Real render pipeline will live here.
        pass
