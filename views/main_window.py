"""IngeTrazo main window: toolbar, viewport, side panels, status bar.

Owns the open document path and dispatches File menu actions (New, Open,
Save, Save As) onto :mod:`formats.igz`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QToolBar,
)

from formats import igz as igz_format
from tools.line import LineTool
from tools.move import MoveTool
from tools.offset import OffsetTool
from tools.pushpull import PushPullTool
from tools.rectangle import RectangleTool
from tools.select import SelectTool
from views.viewport import Viewport


IGZ_FILE_FILTER = "IngeTrazo document (*.igz);;All files (*)"


class MainWindow(QMainWindow):
    """Top-level IngeTrazo window."""

    def __init__(self) -> None:
        super().__init__()
        self.resize(1280, 800)

        self._tools = {
            "select": SelectTool(),
            "line": LineTool(),
            "rectangle": RectangleTool(),
            "pushpull": PushPullTool(),
            "offset": OffsetTool(),
            "move": MoveTool(),
        }
        self._tool_actions: dict[str, QAction] = {}

        self._current_path: Optional[Path] = None
        self._saved_version: int = 0

        self._setup_ui()
        self._activate_tool("select")
        self._update_title()

    # ---- Layout -------------------------------------------------------------
    def _setup_ui(self) -> None:
        self.viewport = Viewport(self)
        self.setCentralWidget(self.viewport)

        self._build_toolbar()
        self._build_menubar()
        self._build_statusbar()

        self._saved_version = self.viewport.scene.version
        self.viewport.sceneVersionChanged.connect(self._on_scene_version_changed)

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

        # Spacebar returns to Select, like SketchUp's pointer. Keep "S" too.
        select_action = self._tool_actions["select"]
        select_action.setShortcuts([QKeySequence("S"), QKeySequence(Qt.Key_Space)])
        select_action.setToolTip("Select (Space / S)")

        # Camera-navigation buttons (SketchUp Orbit / Pan). Essential on a
        # trackpad with no middle mouse button: click one, then left-drag to
        # move the view. They live in the same exclusive group as the drawing
        # tools, so entering nav unchecks the active tool and vice versa.
        toolbar.addSeparator()
        self._nav_actions: dict[str, QAction] = {}
        for key, label, short, tip in [
            ("orbit", "Orbit", "O", "Orbit (O) — left-drag to rotate the view"),
            ("pan", "Pan", "H", "Pan (H) — left-drag to slide the view"),
        ]:
            action = QAction(f"{label} ({short})", self)
            action.setCheckable(True)
            action.setShortcut(QKeySequence(short))
            action.setToolTip(tip)
            action.triggered.connect(lambda _checked, k=key: self._activate_nav(k))
            self._tool_group.addAction(action)
            toolbar.addAction(action)
            self._nav_actions[key] = action

    def _build_menubar(self) -> None:
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")
        for action in self._file_actions():
            file_menu.addAction(action)

        # Edit menu
        edit_menu = menubar.addMenu("Edit")

        self._undo_action = QAction("Undo", self)
        self._undo_action.setShortcut(QKeySequence.Undo)
        self._undo_action.triggered.connect(self._on_undo)
        edit_menu.addAction(self._undo_action)

        self._redo_action = QAction("Redo", self)
        # Cover both classic Windows (Ctrl+Y) and Linux/macOS (Ctrl+Shift+Z).
        self._redo_action.setShortcuts(
            [QKeySequence.Redo, QKeySequence("Ctrl+Shift+Z")]
        )
        self._redo_action.triggered.connect(self._on_redo)
        edit_menu.addAction(self._redo_action)

        # View menu
        view_menu = menubar.addMenu("View")

        action_proj = QAction("Toggle Perspective / Parallel", self)
        action_proj.setShortcut(QKeySequence("P"))
        action_proj.triggered.connect(self.viewport.toggle_projection)
        view_menu.addAction(action_proj)

        view_menu.addSeparator()

        action_zoom_extents = QAction("Zoom Extents", self)
        action_zoom_extents.setShortcut(QKeySequence("F2"))
        action_zoom_extents.triggered.connect(self._on_zoom_extents)
        view_menu.addAction(action_zoom_extents)

        standard_menu = view_menu.addMenu("Standard Views")
        for label, key in [
            ("Top", "top"),
            ("Bottom", "bottom"),
            ("Front", "front"),
            ("Back", "back"),
            ("Left", "left"),
            ("Right", "right"),
            ("Isometric", "iso"),
        ]:
            action = QAction(label, self)
            action.triggered.connect(lambda _checked, k=key: self._on_standard_view(k))
            standard_menu.addAction(action)

        # Tools menu (mirrors the toolbar)
        tools_menu = menubar.addMenu("Tools")
        for action in self._tool_actions.values():
            tools_menu.addAction(action)
        tools_menu.addSeparator()
        for action in self._nav_actions.values():
            tools_menu.addAction(action)
        tools_menu.addSeparator()
        action_cancel = QAction("Cancel current tool", self)
        action_cancel.setShortcut(QKeySequence("Esc"))
        action_cancel.triggered.connect(self._cancel_tool)
        tools_menu.addAction(action_cancel)

    def _file_actions(self) -> list[QAction]:
        actions = []

        new_action = QAction("New", self)
        new_action.setShortcut(QKeySequence.New)
        new_action.triggered.connect(self._on_new)
        actions.append(new_action)

        open_action = QAction("Open…", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self._on_open)
        actions.append(open_action)

        save_action = QAction("Save", self)
        save_action.setShortcut(QKeySequence.Save)
        save_action.triggered.connect(self._on_save)
        actions.append(save_action)

        save_as_action = QAction("Save As…", self)
        save_as_action.setShortcut(QKeySequence.SaveAs)
        save_as_action.triggered.connect(self._on_save_as)
        actions.append(save_as_action)

        actions.append(self._separator())

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        actions.append(quit_action)

        return actions

    def _separator(self) -> QAction:
        sep = QAction(self)
        sep.setSeparator(True)
        return sep

    def _build_statusbar(self) -> None:
        bar = QStatusBar(self)
        self.setStatusBar(bar)
        bar.showMessage(
            "Orbit (O) / Pan (H) buttons: left-drag to move the view  ·  "
            "MMB-drag: orbit  ·  Shift+MMB-drag: pan  ·  Wheel / 2-finger: zoom  ·  "
            "P: persp/parallel  ·  →←↑: lock X/Y/Z  ·  ↓: par/perp to ref  ·  "
            "Shift: lock inference  ·  Type N + Enter: exact length  ·  "
            "Rectangle: type W;H + Enter  ·  Type X;Y;Z + Enter: 3D delta"
        )
        self._tool_label = QLabel("Tool: none")
        bar.addPermanentWidget(self._tool_label)

        # SketchUp-style Measurements box (VCB), pinned bottom-right: a caption
        # ("Length" / "Dimensions" / "Distance") plus a boxed field showing the
        # live measurement, or what you're typing (highlighted while typing).
        self._vcb_buffer = ""
        self._vcb_live = ""
        self._vcb_name = QLabel("")
        self._vcb_name.setStyleSheet("color:#5a6472; padding:0 4px;")
        self._vcb_value = QLabel("")
        self._vcb_value.setMinimumWidth(130)
        self._vcb_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._vcb_value.setStyleSheet(self._VCB_IDLE_STYLE)
        bar.addPermanentWidget(self._vcb_name)
        bar.addPermanentWidget(self._vcb_value)

        self.viewport.valueBufferChanged.connect(self._on_value_buffer)
        self.viewport.measurementChanged.connect(self._on_measurement)

    _VCB_IDLE_STYLE = (
        "color:#0F141B; background:#FFFFFF; border:1px solid #9aa3ad;"
        "border-radius:3px; padding:2px 8px;"
    )
    _VCB_ACTIVE_STYLE = (
        "color:#0F141B; background:#FFF3C4; border:1px solid #E0A800;"
        "border-radius:3px; padding:2px 8px;"
    )

    def _on_value_buffer(self, text: str) -> None:
        self._vcb_buffer = text
        self._refresh_vcb()

    def _on_measurement(self, text: str) -> None:
        self._vcb_live = text
        self._refresh_vcb()

    def _refresh_vcb(self) -> None:
        tool = self.viewport.active_tool
        caption = getattr(tool, "vcb_label", None) if tool is not None else None
        self._vcb_name.setVisible(caption is not None)
        self._vcb_value.setVisible(caption is not None)
        if caption is None:
            return
        self._vcb_name.setText(caption)
        if self._vcb_buffer:
            self._vcb_value.setText(f"{self._vcb_buffer}")
            self._vcb_value.setStyleSheet(self._VCB_ACTIVE_STYLE)
        else:
            self._vcb_value.setText(self._vcb_live)
            self._vcb_value.setStyleSheet(self._VCB_IDLE_STYLE)

    # ---- Tool routing -------------------------------------------------------
    def _activate_tool(self, key: str) -> None:
        tool = self._tools[key]
        self.viewport.set_active_tool(tool)
        action = self._tool_actions.get(key)
        if action is not None:
            action.setChecked(True)
        self._tool_label.setText(f"Tool: {tool.name}")
        self._refresh_vcb()

    def _activate_nav(self, key: str) -> None:
        self.viewport.set_nav_mode(key)
        action = self._nav_actions.get(key)
        if action is not None:
            action.setChecked(True)
        self._tool_label.setText(f"Nav: {key.capitalize()}")
        self._refresh_vcb()

    def _cancel_tool(self) -> None:
        if self.viewport.active_tool is not None:
            self.viewport.active_tool.on_cancel(self.viewport)

    # ---- View navigation ----------------------------------------------------
    def _on_zoom_extents(self) -> None:
        bounds = self.viewport.scene.bounds()
        if bounds[0] is None:
            return
        self.viewport.camera.fit_to(bounds[0], bounds[1])
        self.viewport.update()

    def _on_standard_view(self, key: str) -> None:
        self.viewport.camera.set_view(key)
        self.viewport.update()

    # ---- Undo / redo --------------------------------------------------------
    def _on_undo(self) -> None:
        if self.viewport.history.undo():
            self.viewport.notify_scene_changed()

    def _on_redo(self) -> None:
        if self.viewport.history.redo():
            self.viewport.notify_scene_changed()

    # ---- File handling ------------------------------------------------------
    def _on_new(self) -> None:
        if not self._confirm_discard("Discard current drawing?"):
            return
        scene = self.viewport.scene
        scene.mesh.clear()
        scene.selection.clear()
        scene.version += 1
        self.viewport.history.clear()
        self._current_path = None
        self._saved_version = scene.version
        self.viewport.notify_scene_changed()
        self._update_title()

    def _on_open(self) -> None:
        if not self._confirm_discard("Discard current drawing and open another?"):
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Open IngeTrazo document",
            "",
            IGZ_FILE_FILTER,
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            igz_format.load_into(self.viewport.scene, path)
        except Exception as exc:  # noqa: BLE001 - surface any IO/parse error to the user
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        self.viewport.history.clear()
        self._current_path = path
        self._saved_version = self.viewport.scene.version
        self.viewport.notify_scene_changed()
        self._update_title()

    def _on_save(self) -> None:
        if self._current_path is None:
            self._on_save_as()
            return
        self._do_save(self._current_path)

    def _on_save_as(self) -> None:
        default_name = (
            self._current_path.name if self._current_path is not None else "untitled.igz"
        )
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save IngeTrazo document",
            default_name,
            IGZ_FILE_FILTER,
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != ".igz":
            path = path.with_suffix(".igz")
        self._do_save(path)

    def _do_save(self, path: Path) -> None:
        try:
            igz_format.save_scene(self.viewport.scene, path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._current_path = path
        self._saved_version = self.viewport.scene.version
        self._update_title()

    def _confirm_discard(self, prompt: str) -> bool:
        """Return True if it's safe to discard the current drawing."""
        if not self._is_dirty():
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            f"{prompt}\n\nUnsaved changes will be lost.",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Save:
            self._on_save()
            return not self._is_dirty()
        return answer == QMessageBox.Discard

    def _is_dirty(self) -> bool:
        return self.viewport.scene.version != self._saved_version

    def _on_scene_version_changed(self, _version: int) -> None:
        self._update_title()

    def _update_title(self) -> None:
        name = self._current_path.name if self._current_path is not None else "Untitled"
        marker = " *" if self._is_dirty() else ""
        self.setWindowTitle(f"IngeTrazo — {name}{marker}")

    # ---- Window lifecycle ---------------------------------------------------
    def closeEvent(self, event) -> None:
        if not self._confirm_discard("Quit IngeTrazo?"):
            event.ignore()
            return
        super().closeEvent(event)
