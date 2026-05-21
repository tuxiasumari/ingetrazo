"""Wasia main window: toolbar, viewport, side panels, status bar."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMainWindow, QStatusBar, QToolBar

from views.viewport import Viewport


class MainWindow(QMainWindow):
    """Top-level Wasia window.

    Hosts the 3D viewport in the center, the menu bar and a status bar that
    reminds the user of navigation shortcuts.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Wasia")
        self.resize(1280, 800)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.viewport = Viewport(self)
        self.setCentralWidget(self.viewport)

        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        self._build_menubar()

        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage(
            "Middle-drag: orbit  ·  Shift + Middle-drag: pan  ·  "
            "Wheel: zoom  ·  P: perspective / parallel"
        )

    def _build_menubar(self) -> None:
        view_menu = self.menuBar().addMenu("View")

        action_proj = QAction("Toggle Perspective / Parallel", self)
        action_proj.setShortcut(QKeySequence("P"))
        action_proj.triggered.connect(self.viewport.toggle_projection)
        view_menu.addAction(action_proj)
