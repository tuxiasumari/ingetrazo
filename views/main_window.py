"""Wasia main window: toolbar, viewport, side panels, status bar."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QLabel, QMainWindow, QStatusBar, QToolBar

from tools.line import LineTool
from tools.select import SelectTool
from views.viewport import Viewport


class MainWindow(QMainWindow):
    """Top-level Wasia window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Wasia")
        self.resize(1280, 800)

        self._tools = {
            "select": SelectTool(),
            "line": LineTool(),
        }
        self._tool_actions: dict[str, QAction] = {}

        self._setup_ui()
        # Start with the Select tool so the user has a sensible default.
        self._activate_tool("select")

    # ---- Layout -------------------------------------------------------------
    def _setup_ui(self) -> None:
        self.viewport = Viewport(self)
        self.setCentralWidget(self.viewport)

        self._build_toolbar()
        self._build_menubar()
        self._build_statusbar()

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        self._tool_group = QActionGroup(self)
        self._tool_group.setExclusive(True)

        for key, tool in self._tools.items():
            label = f"{tool.name} ({tool.shortcut})" if tool.shortcut else tool.name
            action = QAction(label, self)
            action.setCheckable(True)
            if tool.shortcut:
                action.setShortcut(QKeySequence(tool.shortcut))
            action.triggered.connect(lambda _checked, k=key: self._activate_tool(k))
            self._tool_group.addAction(action)
            toolbar.addAction(action)
            self._tool_actions[key] = action

    def _build_menubar(self) -> None:
        menubar = self.menuBar()

        view_menu = menubar.addMenu("View")
        action_proj = QAction("Toggle Perspective / Parallel", self)
        action_proj.setShortcut(QKeySequence("P"))
        action_proj.triggered.connect(self.viewport.toggle_projection)
        view_menu.addAction(action_proj)

        tools_menu = menubar.addMenu("Tools")
        for action in self._tool_actions.values():
            tools_menu.addAction(action)
        tools_menu.addSeparator()
        action_cancel = QAction("Cancel current tool", self)
        action_cancel.setShortcut(QKeySequence("Esc"))
        action_cancel.triggered.connect(self._cancel_tool)
        tools_menu.addAction(action_cancel)

    def _build_statusbar(self) -> None:
        bar = QStatusBar(self)
        self.setStatusBar(bar)
        bar.showMessage(
            "MMB-drag: orbit  ·  Shift+MMB-drag: pan  ·  Wheel: zoom  ·  "
            "P: persp/parallel  ·  →←↑: lock X/Y/Z  ·  ↓: par/perp to ref  ·  "
            "Shift: lock inference  ·  Type number + Enter: exact length"
        )
        self._value_label = QLabel("")
        self._value_label.setStyleSheet(
            "color: #0F141B; background: #FFE082; padding: 2px 8px; border-radius: 3px;"
        )
        self._value_label.setVisible(False)
        bar.addPermanentWidget(self._value_label)

        self.viewport.valueBufferChanged.connect(self._on_value_buffer)

        self._tool_label = QLabel("Tool: none")
        bar.addPermanentWidget(self._tool_label)

    def _on_value_buffer(self, text: str) -> None:
        if text:
            self._value_label.setText(f"Length: {text} m")
            self._value_label.setVisible(True)
        else:
            self._value_label.setVisible(False)
            self._value_label.setText("")

    # ---- Tool routing -------------------------------------------------------
    def _activate_tool(self, key: str) -> None:
        tool = self._tools[key]
        self.viewport.set_active_tool(tool)
        action = self._tool_actions.get(key)
        if action is not None:
            action.setChecked(True)
        self._tool_label.setText(f"Tool: {tool.name}")

    def _cancel_tool(self) -> None:
        if self.viewport.active_tool is not None:
            self.viewport.active_tool.on_cancel(self.viewport)
