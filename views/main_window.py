"""Wasia main window: toolbar, viewport, side panels, status bar."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QStatusBar, QToolBar

from views.viewport import Viewport


class MainWindow(QMainWindow):
    """Top-level Wasia window.

    Hosts the 3D viewport in the center and tool / panel chrome around it.
    Tools, panels and shortcuts are registered by the rest of the app.
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

        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage("Ready")
