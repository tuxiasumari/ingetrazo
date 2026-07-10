# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""IngeTrazo main window: toolbar, viewport, side panels, status bar.

Owns the open document path and dispatches File menu actions (New, Open,
Save, Save As) onto :mod:`formats.igz`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStatusBar,
    QToolBar,
)

from core.i18n import available_languages, current_language, set_language, tr
from core.group import Group
from core.history import (
    ExplodeGroupCommand,
    HealOverlapsCommand,
    MakeGroupCommand,
    RebuildPlanarFacesCommand,
    SnapshotMutation,
)
from core.mesh import Edge, Face
from formats import igz as igz_format
from formats import obj as obj_format
from formats import stl as stl_format
from tools.arc import ArcTool, ThreePointArcTool
from tools.circle import CircleTool, PolygonTool
from tools.dimension import DimensionTool
from tools.geopath import GeoPathTool
from tools.line import LineTool
from tools.move import MoveTool
from tools.rotated_rectangle import RotatedRectangleTool
from tools.offset import OffsetTool
from tools.paint import PaintTool
from tools.paste import PasteTool
from tools.pushpull import PushPullTool
from tools.rectangle import RectangleTool
from tools.select import SelectTool
from views.tray import GeorefTray, Tray
from views.icons import tool_icon
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
            "rotated_rect": RotatedRectangleTool(),
            "circle": CircleTool(),
            "polygon": PolygonTool(),
            "arc": ArcTool(),
            "arc3": ThreePointArcTool(),
            "pushpull": PushPullTool(),
            "offset": OffsetTool(),
            "move": MoveTool(),
            "paint": PaintTool(),
            "dimension": DimensionTool(),
            # Georef trace (Track G) — draws a GeoPath, never mesh geometry.
            "geopath": GeoPathTool(),
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
        self._build_tray()
        self._build_menubar()
        self._build_statusbar()

        self._saved_version = self.viewport.scene.version
        self.viewport.sceneVersionChanged.connect(self._on_scene_version_changed)

    def _build_tray(self) -> None:
        # Two role-based right-side docks (tabbed): Properties (what you're
        # working with) and Georef (the location workspace).
        self.tray = Tray(self)
        self.georef_tray = GeorefTray(self)
        self.addDockWidget(Qt.RightDockWidgetArea, self.tray)
        self.addDockWidget(Qt.RightDockWidgetArea, self.georef_tray)
        self.tabifyDockWidget(self.tray, self.georef_tray)
        self.tray.raise_()
        self.viewport.sceneVersionChanged.connect(
            lambda _v: self.tray.on_scene_changed())
        self.viewport.sceneVersionChanged.connect(
            lambda _v: self.georef_tray.on_scene_changed())

        # Terrain profile dock (Track G, G4) — hidden until requested.
        from views.profile_panel import ProfileDock
        self.profile_dock = ProfileDock(self)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.profile_dock)
        self.profile_dock.hide()
        self.viewport.sceneVersionChanged.connect(
            lambda _v: self.profile_dock.on_scene_changed())
        self.viewport.sceneVersionChanged.connect(
            lambda _v: self._on_surfaces_scene_changed())
        self.viewport.tilesChanged.connect(self._build_terrain)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar(tr("Tools"), self)
        # Draggable + dockable to any edge or floating in its own window
        # (SketchUp-style). Left/right docking stacks the buttons vertically.
        toolbar.setMovable(True)
        toolbar.setFloatable(True)
        toolbar.setAllowedAreas(Qt.AllToolBarAreas)
        from PySide6.QtCore import QSize
        toolbar.setIconSize(QSize(24, 24))
        # Icons only (SketchUp-style) — stays compact when docked vertically on a
        # side; the name + shortcut live in each button's tooltip.
        toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.toolbar = toolbar
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        self._tool_group = QActionGroup(self)
        self._tool_group.setExclusive(True)

        # Tools grouped by task (separators between groups), like QGIS/SketchUp.
        # Select stands alone; then Draw, Modify, Annotate, Georef.
        groups = [
            ["select"],
            ["line", "rectangle", "rotated_rect", "circle", "polygon", "arc", "arc3"],
            ["pushpull", "offset", "move", "paint"],
            ["dimension"],
            ["geopath"],
        ]
        for gi, keys in enumerate(groups):
            if gi > 0:
                toolbar.addSeparator()
            for key in keys:
                tool = self._tools[key]
                name = tr(tool.name)
                action = QAction(name, self)
                action.setIcon(tool_icon(key))
                action.setCheckable(True)
                if tool.shortcut:
                    action.setShortcut(QKeySequence(tool.shortcut))
                    action.setToolTip(f"{name}  ({tool.shortcut})")
                else:
                    action.setToolTip(name)
                action.triggered.connect(
                    lambda _checked, k=key: self._activate_tool(k))
                self._tool_group.addAction(action)
                toolbar.addAction(action)
                self._tool_actions[key] = action

        # Spacebar returns to Select, like SketchUp's pointer. Keep "S" too.
        select_action = self._tool_actions["select"]
        select_action.setShortcuts([QKeySequence("S"), QKeySequence(Qt.Key_Space)])
        select_action.setToolTip(tr("Select (Space / S)"))

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
            action = QAction(tr(label), self)
            action.setIcon(tool_icon(key))
            action.setCheckable(True)
            action.setShortcut(QKeySequence(short))
            action.setToolTip(tr(tip))
            action.triggered.connect(lambda _checked, k=key: self._activate_nav(k))
            self._tool_group.addAction(action)
            toolbar.addAction(action)
            self._nav_actions[key] = action
        # Materials (colour swatches + textures) now live in the right-side Tray.

    def _build_menubar(self) -> None:
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu(tr("File"))
        for action in self._file_actions():
            file_menu.addAction(action)

        # Edit menu
        edit_menu = menubar.addMenu(tr("Edit"))

        self._undo_action = QAction(tr("Undo"), self)
        self._undo_action.setShortcut(QKeySequence.Undo)
        self._undo_action.triggered.connect(self._on_undo)
        edit_menu.addAction(self._undo_action)

        self._redo_action = QAction(tr("Redo"), self)
        # Cover both classic Windows (Ctrl+Y) and Linux/macOS (Ctrl+Shift+Z).
        self._redo_action.setShortcuts(
            [QKeySequence.Redo, QKeySequence("Ctrl+Shift+Z")]
        )
        self._redo_action.triggered.connect(self._on_redo)
        edit_menu.addAction(self._redo_action)

        edit_menu.addSeparator()

        copy_action = QAction(tr("Copy"), self)
        copy_action.setShortcut(QKeySequence.Copy)
        copy_action.triggered.connect(lambda: self.viewport.copy_selection())
        edit_menu.addAction(copy_action)

        cut_action = QAction(tr("Cut"), self)
        cut_action.setShortcut(QKeySequence.Cut)
        cut_action.triggered.connect(lambda: self.viewport.cut_selection())
        edit_menu.addAction(cut_action)

        paste_action = QAction(tr("Paste"), self)
        paste_action.setShortcut(QKeySequence.Paste)
        paste_action.triggered.connect(self._on_paste)
        edit_menu.addAction(paste_action)

        edit_menu.addSeparator()

        group_action = QAction(tr("Make Group"), self)
        group_action.setShortcut(QKeySequence("Ctrl+G"))
        group_action.triggered.connect(self._on_make_group)
        edit_menu.addAction(group_action)

        explode_action = QAction(tr("Explode Group"), self)
        explode_action.setShortcut(QKeySequence("Ctrl+Shift+G"))
        explode_action.triggered.connect(self._on_explode_group)
        edit_menu.addAction(explode_action)

        convert_path_action = QAction(tr("Convert Path to Geometry"), self)
        convert_path_action.triggered.connect(self._on_convert_geopath)
        edit_menu.addAction(convert_path_action)

        edit_menu.addSeparator()

        heal_action = QAction(tr("Heal Overlapping Faces"), self)
        heal_action.triggered.connect(self._on_heal_overlaps)
        edit_menu.addAction(heal_action)

        rebuild_action = QAction(tr("Rebuild Faces (Planar)"), self)
        rebuild_action.triggered.connect(self._on_rebuild_planar)
        edit_menu.addAction(rebuild_action)

        # View menu
        view_menu = menubar.addMenu(tr("View"))

        toggle_tray = self.tray.toggleViewAction()
        toggle_tray.setText(tr("Properties panel"))
        view_menu.addAction(toggle_tray)

        toggle_georef = self.georef_tray.toggleViewAction()
        toggle_georef.setText(tr("Georef panel"))
        view_menu.addAction(toggle_georef)

        toggle_profile = self.profile_dock.toggleViewAction()
        toggle_profile.setText(tr("Terrain profile"))
        view_menu.addAction(toggle_profile)
        view_menu.addSeparator()

        action_proj = QAction(tr("Toggle Perspective / Parallel"), self)
        action_proj.setShortcut(QKeySequence("P"))
        action_proj.triggered.connect(self.viewport.toggle_projection)
        view_menu.addAction(action_proj)

        view_menu.addSeparator()

        action_zoom_extents = QAction(tr("Zoom Extents"), self)
        action_zoom_extents.setShortcut(QKeySequence("F2"))
        action_zoom_extents.triggered.connect(self._on_zoom_extents)
        view_menu.addAction(action_zoom_extents)

        standard_menu = view_menu.addMenu(tr("Standard Views"))
        for label, key in [
            ("Top", "top"),
            ("Bottom", "bottom"),
            ("Front", "front"),
            ("Back", "back"),
            ("Left", "left"),
            ("Right", "right"),
            ("Isometric", "iso"),
        ]:
            action = QAction(tr(label), self)
            action.triggered.connect(lambda _checked, k=key: self._on_standard_view(k))
            standard_menu.addAction(action)

        view_menu.addSeparator()
        self._build_language_menu(view_menu)

        # Tools menu (mirrors the toolbar)
        tools_menu = menubar.addMenu(tr("Tools"))
        for action in self._tool_actions.values():
            tools_menu.addAction(action)
        tools_menu.addSeparator()
        for action in self._nav_actions.values():
            tools_menu.addAction(action)
        tools_menu.addSeparator()
        action_cancel = QAction(tr("Cancel current tool"), self)
        action_cancel.setShortcut(QKeySequence("Esc"))
        action_cancel.triggered.connect(self._cancel_tool)
        tools_menu.addAction(action_cancel)

        tools_menu.addSeparator()
        action_profile = QAction(tr("Terrain profile of selection"), self)
        action_profile.triggered.connect(self._on_terrain_profile)
        tools_menu.addAction(action_profile)

        help_menu = menubar.addMenu(tr("Help"))
        about_action = QAction(tr("About IngeTrazo"), self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    # ---- Language -----------------------------------------------------------
    _LANGUAGE_NAMES = {"en": "English", "es": "Español"}

    def _build_language_menu(self, parent_menu) -> None:
        lang_menu = parent_menu.addMenu(tr("Language"))
        group = QActionGroup(self)
        group.setExclusive(True)
        for code in available_languages():
            action = QAction(self._LANGUAGE_NAMES.get(code, code), self)
            action.setCheckable(True)
            action.setChecked(code == current_language())
            action.triggered.connect(lambda _checked, c=code: self._on_set_language(c))
            group.addAction(action)
            lang_menu.addAction(action)

    def _on_set_language(self, code: str) -> None:
        """Persist the chosen UI language (applied on next start)."""
        if code == current_language():
            return
        QSettings().setValue("language", code)
        set_language(code)
        QMessageBox.information(
            self,
            tr("Language changed"),
            tr("Restart IngeTrazo to apply the new language."),
        )

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            tr("About IngeTrazo"),
            "<h3>IngeTrazo</h3>"
            f"<p>{tr('Free 3D modeler for architecture, civil engineering and 3D printing.')}</p>"
            f"<p>{tr('Created by')} <b>Marco Sumari Tellez</b><br>"
            f"{tr('Civil Engineer — Lima, Peru')}</p>"
            f"<p>{tr('Licensed under GPL-3.0-or-later.')}<br>"
            "<a href='https://github.com/tuxiasumari/ingetrazo'>"
            "github.com/tuxiasumari/ingetrazo</a></p>"
            "<p><i>Trazá. Metrá. Presupuestá.</i></p>",
        )

    def _file_actions(self) -> list[QAction]:
        actions = []

        new_action = QAction(tr("New"), self)
        new_action.setShortcut(QKeySequence.New)
        new_action.triggered.connect(self._on_new)
        actions.append(new_action)

        open_action = QAction(tr("Open…"), self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self._on_open)
        actions.append(open_action)

        save_action = QAction(tr("Save"), self)
        save_action.setShortcut(QKeySequence.Save)
        save_action.triggered.connect(self._on_save)
        actions.append(save_action)

        save_as_action = QAction(tr("Save As…"), self)
        save_as_action.setShortcut(QKeySequence.SaveAs)
        save_as_action.triggered.connect(self._on_save_as)
        actions.append(save_as_action)

        actions.append(self._separator())

        import_obj_action = QAction(tr("Import OBJ…"), self)
        import_obj_action.triggered.connect(self._on_import_obj)
        actions.append(import_obj_action)

        import_geo_action = QAction(tr("Import georef (KML / GeoJSON)…"), self)
        import_geo_action.triggered.connect(self._on_import_georef)
        actions.append(import_geo_action)

        export_stl_action = QAction(tr("Export STL…"), self)
        export_stl_action.triggered.connect(self._on_export_stl)
        actions.append(export_stl_action)

        export_obj_action = QAction(tr("Export OBJ…"), self)
        export_obj_action.triggered.connect(self._on_export_obj)
        actions.append(export_obj_action)

        actions.append(self._separator())

        quit_action = QAction(tr("Quit"), self)
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
        bar.showMessage(tr(
            "Orbit (O) / Pan (H) buttons: left-drag to move the view  ·  "
            "MMB-drag: orbit  ·  Shift+MMB-drag: pan  ·  Wheel / 2-finger: zoom  ·  "
            "P: persp/parallel  ·  →←↑: lock X/Y/Z  ·  ↓: par/perp to ref  ·  "
            "Shift: lock inference  ·  Type N + Enter: exact length  ·  "
            "Rectangle: type W;H + Enter  ·  Type X;Y;Z + Enter: 3D delta"
        ))
        self._tool_label = QLabel(tr("Tool: none"))
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
        # A tool may vary its caption with state (Circle: "Lados" before the
        # centre, "Radio" after); fall back to the static label.
        dynamic = getattr(tool, "vcb_caption", None) if tool is not None else None
        caption = (dynamic() if callable(dynamic)
                   else getattr(tool, "vcb_label", None)) if tool is not None \
            else None
        self._vcb_name.setVisible(caption is not None)
        self._vcb_value.setVisible(caption is not None)
        if caption is None:
            return
        self._vcb_name.setText(tr(caption))
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
        self._tool_label.setText(tr("Tool: {name}", name=tr(tool.name)))
        self._refresh_vcb()

    def _activate_nav(self, key: str) -> None:
        self.viewport.set_nav_mode(key)
        action = self._nav_actions.get(key)
        if action is not None:
            action.setChecked(True)
        self._tool_label.setText(
            tr("Nav: {name}", name=tr(key.capitalize())))
        self._refresh_vcb()

    def _on_make_group(self) -> None:
        sel = self.viewport.scene.selection
        faces = [f for f in sel if isinstance(f, Face)]
        edges = [e for e in sel if isinstance(e, Edge)]
        if faces or edges:
            self.viewport.history.execute(MakeGroupCommand(faces, edges))
            self.viewport.update()

    def _on_explode_group(self) -> None:
        groups = [g for g in self.viewport.scene.selection if isinstance(g, Group)]
        for g in groups:
            self.viewport.history.execute(ExplodeGroupCommand(g))
        if groups:
            self.viewport.update()

    def _on_convert_geopath(self) -> None:
        """Bake selected georef paths into real mesh geometry (Track G bridge).

        The trace crosses from the georef subsystem into the modelling engine
        *on demand*: each segment becomes a welded edge (a closed path auto-faces,
        so a traced footprint is ready to push/pull into a building), and the
        GeoPath is consumed. One undoable step.
        """
        from georef.geopath import GeoPath
        from core.edits import build_add_edges
        from core.history import CompoundCommand, DeleteGeoPathsCommand

        scene = self.viewport.scene
        paths = [p for p in scene.selection if isinstance(p, GeoPath)]
        if not paths:
            self.statusBar().showMessage(
                tr("Select a path to convert to geometry."), 3000)
            return
        cmds = []
        for path in paths:
            segs = [(a, b) for a, b in path.segments()]
            if segs:
                cmds.append(build_add_edges(scene, segs, detect_faces=True))
        cmds.append(DeleteGeoPathsCommand(paths))
        cmd = cmds[0] if len(cmds) == 1 else CompoundCommand(cmds)
        self.viewport.history.execute(cmd)
        self.viewport.update()

    def _on_toggle_path_closed(self) -> None:
        from georef.geopath import GeoPath
        from core.history import ToggleGeoPathClosedCommand
        paths = [p for p in self.viewport.scene.selection if isinstance(p, GeoPath)]
        if paths:
            self.viewport.history.execute(ToggleGeoPathClosedCommand(paths))
            self.viewport.update()

    def _on_delete_selection(self) -> None:
        """Delete the current selection (any entity type), as one undoable step —
        the same logic the Select tool's Delete key runs, reachable from the
        context menu regardless of the active tool."""
        from core.mesh import Edge, Face
        from core.dimension import Dimension
        from georef.geopath import GeoPath
        from core.history import (
            CompoundCommand, DeleteDimensionsCommand, DeleteGeoPathsCommand,
            DeleteGroupCommand, EraseSelectionCommand,
        )
        sel = self.viewport.scene.selection
        if not sel:
            return
        edges = [e for e in sel if isinstance(e, Edge)]
        faces = [f for f in sel if isinstance(f, Face)]
        groups = [g for g in sel if isinstance(g, Group)]
        dims = [d for d in sel if isinstance(d, Dimension)]
        paths = [p for p in sel if isinstance(p, GeoPath)]
        cmds = []
        if edges or faces:
            cmds.append(EraseSelectionCommand(edges, faces))
        cmds.extend(DeleteGroupCommand(g) for g in groups)
        if dims:
            cmds.append(DeleteDimensionsCommand(dims))
        if paths:
            cmds.append(DeleteGeoPathsCommand(paths))
        if cmds:
            self.viewport.history.execute(
                cmds[0] if len(cmds) == 1 else CompoundCommand(cmds))
            self.viewport.update()

    def show_viewport_context_menu(self, global_pos) -> None:
        """SketchUp-style right-click menu, tailored to what's selected."""
        from core.mesh import Edge, Face
        from core.dimension import Dimension
        from georef.geopath import GeoPath

        sel = self.viewport.scene.selection
        has_geopath = any(isinstance(e, GeoPath) for e in sel)
        has_group = any(isinstance(e, Group) for e in sel)
        has_mesh = any(isinstance(e, (Edge, Face)) for e in sel)
        menu = QMenu(self)

        if has_geopath:
            menu.addAction(tr("Terrain profile"), self._on_terrain_profile)
            closed_paths = [e for e in sel
                            if isinstance(e, GeoPath) and len(e.points) >= 3]
            if closed_paths:
                surf = menu.addMenu(tr("Terrain surface"))
                surf.addAction(tr("Flat (single slope)"),
                               lambda: self._on_set_surface("flat"))
                surf.addAction(tr("Draped (follow relief)"),
                               lambda: self._on_set_surface("draped"))
                surf.addAction(tr("None (line only)"),
                               lambda: self._on_set_surface(None))
            menu.addAction(tr("Convert Path to Geometry"), self._on_convert_geopath)
            menu.addAction(tr("Open / Close path"), self._on_toggle_path_closed)
            menu.addSeparator()
        if has_mesh:
            menu.addAction(tr("Make Group"), self._on_make_group)
        if has_group:
            menu.addAction(tr("Explode Group"), self._on_explode_group)
        if sel:
            menu.addAction(tr("Delete"), self._on_delete_selection)
            act_clear = menu.addAction(tr("Clear selection"),
                                       self.viewport.scene.clear_selection)
            act_clear.triggered.connect(self.viewport.update)
            menu.addSeparator()

        if getattr(self.viewport, "clipboard", None):
            menu.addAction(tr("Paste"), self._on_paste)
        menu.addAction(tr("Zoom Extents"), self._on_zoom_extents)
        menu.addSeparator()
        undo = menu.addAction(tr("Undo"), self._on_undo)
        undo.setEnabled(bool(self.viewport.history.undo_stack))
        redo = menu.addAction(tr("Redo"), self._on_redo)
        redo.setEnabled(bool(self.viewport.history.redo_stack))

        menu.exec(global_pos)

    def _on_heal_overlaps(self) -> None:
        cmd = HealOverlapsCommand()
        self.viewport.history.execute(cmd)
        self.viewport.update()
        self.statusBar().showMessage(
            tr("Healed {n} overlapping face(s).", n=cmd.healed) if cmd.healed
            else tr("No overlapping faces found."), 3000)

    def _on_rebuild_planar(self) -> None:
        cmd = RebuildPlanarFacesCommand()
        self.viewport.history.execute(cmd)
        self.viewport.update()
        if not cmd.flat:
            msg = tr("Rebuild Faces only works on a flat (single-plane) drawing.")
        else:
            msg = tr("Rebuilt {n} face(s) from the edge graph.", n=cmd.rebuilt)
        self.statusBar().showMessage(msg, 3000)

    def _on_paste(self) -> None:
        if self.viewport.clipboard is None:
            return
        self.viewport.set_active_tool(PasteTool())
        for action in self._tool_actions.values():
            action.setChecked(False)
        self._tool_label.setText(tr("Tool: {name}", name=tr("Paste")))
        self._refresh_vcb()

    def _cancel_tool(self) -> None:
        if isinstance(self.viewport.active_tool, PasteTool):
            self._activate_tool("select")
            return
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

    def _on_terrain_profile(self) -> None:
        """Show the profile dock and profile the current selection (Track G)."""
        self.profile_dock.show()
        self.profile_dock.raise_()
        self.profile_dock.compute_from_selection()

    def on_viewport_hover(self, screen_x: float, screen_y: float) -> None:
        """Plan→profile link: mark the station of the route point under the
        cursor in the open profile (Track G)."""
        if self.profile_dock.isVisible():
            self.profile_dock.indicate_at_screen(screen_x, screen_y)

    # ---- Terrain-surface fill (Track G) -------------------------------------
    def _surface_dem(self, datum):
        """Shared DEM sampler for surface fills, rebuilt when the datum changes."""
        if getattr(self, "_surf_sampler", None) is not None \
                and self._surf_datum is datum:
            return self._surf_sampler
        from georef.dem import DEMSampler
        self._surf_sampler = DEMSampler(datum, parent=self)
        self._surf_datum = datum
        self._surf_sampler.changed.connect(self._rebuild_surfaces)
        self._surf_sampler.changed.connect(self._build_terrain)
        return self._surf_sampler

    def _on_set_surface(self, mode) -> None:
        from georef.geopath import GeoPath
        from core.history import SetGeoPathSurfaceCommand
        paths = [p for p in self.viewport.scene.selection
                 if isinstance(p, GeoPath) and len(p.points) >= 3]
        if not paths:
            return
        self.viewport.history.execute(SetGeoPathSurfaceCommand(paths, mode))
        self._rebuild_surfaces()

    def _rebuild_surfaces(self) -> None:
        """(Re)compute the 3D triangles of every surfaced path from the DEM."""
        from georef.surface import build_surface
        scene = self.viewport.scene
        datum = getattr(scene, "georef", None)
        surfaced = [p for p in scene.geo_paths if getattr(p, "surface", None)]
        if datum is None:
            for p in surfaced:
                p._surface_tris = None
            self.viewport.update()
            return
        sampler = self._surface_dem(datum)
        area = None
        for p in surfaced:
            xs = [pt.x() for pt in p.points]
            ys = [pt.y() for pt in p.points]
            lo = self._local_to_ll(datum, min(xs), min(ys))
            hi = self._local_to_ll(datum, max(xs), max(ys))
            sampler.ensure_area(min(lo[0], hi[0]), min(lo[1], hi[1]),
                                max(lo[0], hi[0]), max(lo[1], hi[1]))
            p._surface_tris = build_surface(p, sampler, datum)
        self.viewport.update()

    @staticmethod
    def _local_to_ll(datum, x, y):
        from PySide6.QtGui import QVector3D
        lat, lon, _ = datum.local_to_geodetic(QVector3D(x, y, 0.0))
        return lat, lon

    def _on_surfaces_scene_changed(self) -> None:
        """Re-drape surfaced paths when their nodes move (version bump)."""
        if any(getattr(p, "surface", None) for p in self.viewport.scene.geo_paths):
            self._rebuild_surfaces()

    # ---- 3D terrain (Track G, G2 full) --------------------------------------
    def set_terrain_enabled(self, on: bool) -> None:
        self._terrain_on = on
        if on:
            self._build_terrain()
        else:
            self.viewport.scene.terrain = None
            self.viewport.upload_terrain(None)
            self.viewport.update()

    @staticmethod
    def _capture_bbox(layer):
        """Local-metre bounding box ``(minx, miny, maxx, maxy)`` of the capture
        patches — the area the 3D terrain should cover."""
        patches = getattr(layer, "patches", None) or [(0, 0, layer.radius_m,
                                                        layer.radius_m)]
        minx = min(cx - hw for cx, cy, hw, hh in patches)
        maxx = max(cx + hw for cx, cy, hw, hh in patches)
        miny = min(cy - hh for cx, cy, hw, hh in patches)
        maxy = max(cy + hh for cx, cy, hw, hh in patches)
        return minx, miny, maxx, maxy

    def _build_terrain(self) -> None:
        """(Re)build the 3D terrain from the DEM + base-map tiles (async-ready)."""
        if not getattr(self, "_terrain_on", False):
            return
        from georef.terrain import build_mosaic, build_terrain
        from georef.surface import ground_reference
        scene = self.viewport.scene
        datum = getattr(scene, "georef", None)
        layer = getattr(scene, "tile_layer", None)
        if datum is None or layer is None:
            return
        sampler = self._surface_dem(datum)
        zoom = layer.zoom
        # The terrain covers the captured area (the drawn patches' bounding box),
        # not a fixed square — so the 3D matches what you captured.
        bbox = self._capture_bbox(layer)
        minx, miny, maxx, maxy = bbox
        lo = self._local_to_ll(datum, minx, miny)
        hi = self._local_to_ll(datum, maxx, maxy)
        sampler.ensure_area(min(lo[0], hi[0]), min(lo[1], hi[1]),
                            max(lo[0], hi[0]), max(lo[1], hi[1]))
        self.viewport.prefetch_tiles(layer.source, layer.flat_tiles(datum), zoom)
        ground = ground_reference(sampler, datum)
        if ground is None:
            return                         # DEM not ready — retry on changed
        terrain = build_terrain(datum, sampler, ground, bbox, zoom=zoom)
        if terrain is None:
            return                         # DEM grid not fully loaded yet
        first = scene.terrain is None
        terrain.texture_image = build_mosaic(terrain, layer.images)
        terrain.visible = True
        scene.terrain = terrain
        self.viewport.upload_terrain(terrain)
        # Frame the terrain only the first time it appears (not on async rebuilds).
        if first:
            mn, mx = terrain.bounds()
            if mn is not None:
                self.viewport.camera.set_view("iso")
                self.viewport.camera.fit_to(mn, mx)
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
        if not self._confirm_discard(tr("Discard current drawing?")):
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
        if not self._confirm_discard(
                tr("Discard current drawing and open another?")):
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            tr("Open IngeTrazo document"),
            "",
            tr(IGZ_FILE_FILTER),
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            igz_format.load_into(self.viewport.scene, path)
        except Exception as exc:  # noqa: BLE001 - surface any IO/parse error to the user
            QMessageBox.critical(self, tr("Open failed"), str(exc))
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
            tr("Save IngeTrazo document"),
            default_name,
            tr(IGZ_FILE_FILTER),
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
            QMessageBox.critical(self, tr("Save failed"), str(exc))
            return
        self._current_path = path
        self._saved_version = self.viewport.scene.version
        self._update_title()

    def _on_import_obj(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, tr("Import OBJ"), "", tr("Wavefront OBJ (*.obj);;All files (*)"))
        if not path_str:
            return
        path = Path(path_str)
        try:
            self.viewport.history.execute(SnapshotMutation(
                lambda scene: obj_format.load_obj(scene, path)))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, tr("Import OBJ failed"), str(exc))
            return
        self.viewport.update()
        self.statusBar().showMessage(tr("Imported {name}", name=path.name), 3000)

    def _on_import_georef(self) -> None:
        """Import a KML/KMZ/GeoJSON alignment as georeferenced GeoPath traces —
        located via the datum, ready to profile / measure (Track G)."""
        from georef.geoimport import load_features
        from georef.datum import SceneDatum
        from georef.geopath import GeoPath
        from core.history import AddGeoPathCommand, CompoundCommand

        path_str, _ = QFileDialog.getOpenFileName(
            self, tr("Import georef"), "",
            tr("Georef (*.kml *.kmz *.geojson *.json);;All files (*)"))
        if not path_str:
            return
        try:
            feats = load_features(Path(path_str))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, tr("Import failed"), str(exc))
            return
        feats = [f for f in feats if len(f.points) >= 2]
        if not feats:
            self.statusBar().showMessage(
                tr("No lines or polygons found in the file."), 4000)
            return

        scene = self.viewport.scene
        datum = getattr(scene, "georef", None)
        if datum is None:      # anchor the scene at the imported data's centre
            pts = [p for f in feats for p in f.points]
            datum = SceneDatum(sum(p[0] for p in pts) / len(pts),
                               sum(p[1] for p in pts) / len(pts))
            scene.georef = datum

        cmds = []
        for f in feats:
            local = [datum.geodetic_to_local(la, lo) for la, lo in f.points]
            cmds.append(AddGeoPathCommand(GeoPath(local, closed=f.closed,
                                                  name=f.name)))
        self.viewport.history.execute(
            cmds[0] if len(cmds) == 1 else CompoundCommand(cmds))

        # Sync the base-map panel + set a reference capture around the import.
        self.georef_tray.base_map.setup_for_import(datum, scene.geo_paths)
        self.georef_tray.raise_()
        # Frame the imported traces (top view).
        self._frame_geo_paths(scene.geo_paths)
        self.statusBar().showMessage(
            tr("Imported {n} feature(s) from {name}").format(
                n=len(feats), name=Path(path_str).name), 4000)

    def _frame_geo_paths(self, paths) -> None:
        from PySide6.QtGui import QVector3D
        pts = [p for gp in paths for p in gp.points]
        if not pts:
            return
        mn = QVector3D(min(p.x() for p in pts), min(p.y() for p in pts), 0.0)
        mx = QVector3D(max(p.x() for p in pts), max(p.y() for p in pts), 0.0)
        self.viewport.camera.set_view("top")
        self.viewport.camera.fit_to(mn, mx)
        self.viewport.update()

    def _on_export_stl(self) -> None:
        self._export("STL", "stl", tr("STL mesh (*.stl)"), stl_format.save_stl)

    def _on_export_obj(self) -> None:
        self._export("OBJ", "obj", tr("Wavefront OBJ (*.obj)"), obj_format.save_obj)

    def _export(self, label: str, suffix: str, file_filter, writer) -> None:
        base = (self._current_path.stem if self._current_path is not None
                else "untitled")
        path_str, _ = QFileDialog.getSaveFileName(
            self, tr("Export {label}", label=label), f"{base}.{suffix}", file_filter)
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != f".{suffix}":
            path = path.with_suffix(f".{suffix}")
        try:
            writer(self.viewport.scene, path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(
                self, tr("Export {label} failed", label=label), str(exc))
            return
        self.statusBar().showMessage(
            tr("Exported {label} → {name}", label=label, name=path.name), 3000)

    def _confirm_discard(self, prompt: str) -> bool:
        """Return True if it's safe to discard the current drawing."""
        if not self._is_dirty():
            return True
        answer = QMessageBox.question(
            self,
            tr("Unsaved changes"),
            tr("{prompt}\n\nUnsaved changes will be lost.", prompt=prompt),
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
        name = (self._current_path.name if self._current_path is not None
                else tr("Untitled"))
        marker = " *" if self._is_dirty() else ""
        self.setWindowTitle(f"IngeTrazo — {name}{marker}")

    # ---- Window lifecycle ---------------------------------------------------
    def closeEvent(self, event) -> None:
        if not self._confirm_discard(tr("Quit IngeTrazo?")):
            event.ignore()
            return
        super().closeEvent(event)
