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
from core.version import __version__
from core.group import Group
from core.history import (
    ExplodeGroupCommand,
    HealOverlapsCommand,
    MakeGroupCommand,
    RebuildPlanarFacesCommand,
    SnapshotImport,
    SnapshotMutation,
)
from core.mesh import Edge, Face
from formats import igz as igz_format
from formats import dae as dae_format
from formats import obj as obj_format
from formats import ifc as ifc_format
from formats import stl as stl_format
from tools.arc import CenterArcTool, ArcTool, ThreePointArcTool
from tools.circle import CircleTool, PolygonTool
from tools.dimension import DimensionTool
from tools.eraser import EraserTool
from tools.geopath import GeoPathTool
from tools.line import LineTool
from tools.protractor import ProtractorTool
from tools.tape import TapeMeasureTool
from tools.move import MoveTool
from tools.rotate import RotateTool
from tools.scale import ScaleTool
from tools.followme import FollowMeTool
from tools.rotated_rectangle import RotatedRectangleTool
from tools.offset import OffsetTool
from tools.paint import PaintTool
from tools.paste import PasteTool
from tools.pushpull import PushPullTool
from tools.rectangle import RectangleTool
from tools.select import SelectTool
from views.tray import BimTray, GeorefTray, Tray
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
            "center_arc": CenterArcTool(),
            "pushpull": PushPullTool(),
            "offset": OffsetTool(),
            "move": MoveTool(),
            "rotate": RotateTool(),
            "scale": ScaleTool(),
            "followme": FollowMeTool(),
            "paint": PaintTool(),
            "dimension": DimensionTool(),
            "eraser": EraserTool(),
            "tape": TapeMeasureTool(),
            "protractor": ProtractorTool(),
            # Georef trace (Track G) — draws a GeoPath, never mesh geometry.
            "geopath": GeoPathTool(),
        }
        self._tool_actions: dict[str, QAction] = {}

        self._current_path: Optional[Path] = None
        self._saved_version: int = 0

        self._setup_ui()
        self._activate_tool("select")
        self._insert_scale_figure()
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
        self.bim_tray = BimTray(self)
        self.georef_tray = GeorefTray(self)
        self.addDockWidget(Qt.RightDockWidgetArea, self.tray)
        self.addDockWidget(Qt.RightDockWidgetArea, self.bim_tray)
        self.addDockWidget(Qt.RightDockWidgetArea, self.georef_tray)
        self.tabifyDockWidget(self.tray, self.bim_tray)
        self.tabifyDockWidget(self.bim_tray, self.georef_tray)
        self.tray.raise_()
        self.viewport.sceneVersionChanged.connect(
            lambda _v: self.tray.on_scene_changed())
        self.viewport.sceneVersionChanged.connect(
            lambda _v: self.bim_tray.on_scene_changed())
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

    def _new_toolbar(self, title: str, object_name: str) -> QToolBar:
        """A separate, independently draggable/floatable icons-only toolbar
        (SketchUp-style — Draw, Modify, View… each move on their own)."""
        from PySide6.QtCore import QSize
        tb = QToolBar(title, self)
        tb.setObjectName(object_name)
        tb.setMovable(True)
        tb.setFloatable(True)
        tb.setAllowedAreas(Qt.AllToolBarAreas)
        tb.setIconSize(QSize(24, 24))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.addToolBar(Qt.TopToolBarArea, tb)
        return tb

    def _add_tool_button(self, tb: QToolBar, key: str) -> QAction:
        tool = self._tools[key]
        name = tr(tool.name)
        action = QAction(tool_icon(key), name, self)
        action.setCheckable(True)
        if tool.shortcut:
            action.setShortcut(QKeySequence(tool.shortcut))
            action.setToolTip(f"{name}  ({tool.shortcut})")
        else:
            action.setToolTip(name)
        action.triggered.connect(lambda _c, k=key: self._activate_tool(k))
        self._tool_group.addAction(action)
        tb.addAction(action)
        self._tool_actions[key] = action
        return action

    def _build_toolbar(self) -> None:
        self._tool_group = QActionGroup(self)
        self._tool_group.setExclusive(True)
        self.toolbars: dict[str, QToolBar] = {}

        # One toolbar per task, each independently movable (SketchUp).
        layout = [
            ("main", tr("Main"), ["select", "eraser", "paint"]),
            ("draw", tr("Draw"),
             ["line", "rectangle", "rotated_rect", "circle", "polygon",
              "arc", "arc3", "center_arc"]),
            ("modify", tr("Modify"), ["move", "rotate", "scale", "pushpull", "followme", "offset"]),
            ("annotate", tr("Annotate"), ["tape", "protractor", "dimension", "geopath"]),
        ]
        for oname, title, keys in layout:
            tb = self._new_toolbar(title, oname)
            self.toolbars[oname] = tb
            for key in keys:
                self._add_tool_button(tb, key)

        # Spacebar returns to Select, like SketchUp's pointer ("S" now
        # belongs to Scale, matching SketchUp).
        select_action = self._tool_actions["select"]
        select_action.setShortcuts([QKeySequence(Qt.Key_Space)])
        select_action.setToolTip(tr("Select (Space)"))

        # View toolbar: camera nav (Orbit / Pan) + Zoom Extents + iso view.
        view_tb = self._new_toolbar(tr("View"), "view")
        self.toolbars["view"] = view_tb
        self._nav_actions: dict[str, QAction] = {}
        for key, label, short, tip in [
            ("orbit", "Orbit", "O", "Orbit (O) — left-drag to rotate the view"),
            ("pan", "Pan", "H", "Pan (H) — left-drag to slide the view"),
        ]:
            action = QAction(tool_icon(key), tr(label), self)
            action.setCheckable(True)
            action.setShortcut(QKeySequence(short))
            action.setToolTip(tr(tip))
            action.triggered.connect(lambda _c, k=key: self._activate_nav(k))
            self._tool_group.addAction(action)
            view_tb.addAction(action)
            self._nav_actions[key] = action
        view_tb.addSeparator()
        act_ze = QAction(tool_icon("zoom_extents"), tr("Zoom Extents"), self)
        act_ze.setShortcut(QKeySequence("F2"))
        act_ze.setToolTip(f"{tr('Zoom Extents')}  (F2)")
        act_ze.triggered.connect(self._on_zoom_extents)
        view_tb.addAction(act_ze)

        # Standard-views toolbar: one-shot camera orientations, icon-only.
        views_tb = self._new_toolbar(tr("Standard Views"), "views")
        self.toolbars["views"] = views_tb
        for key, label, icon in [
            ("iso", "Isometric", "view_iso"),
            ("top", "Top", "view_top"),
            ("bottom", "Bottom", "view_bottom"),
            ("front", "Front", "view_front"),
            ("back", "Back", "view_back"),
            ("left", "Left", "view_left"),
            ("right", "Right", "view_right"),
        ]:
            act = QAction(tool_icon(icon), tr(label), self)
            act.setToolTip(tr(label))
            act.triggered.connect(lambda _c, k=key: self._on_standard_view(k))
            views_tb.addAction(act)

    def _build_menubar(self) -> None:
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu(tr("File"))
        for action in self._file_actions():
            if isinstance(action, QMenu):
                file_menu.addMenu(action)
            else:
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

        select_all_action = QAction(tr("Select All"), self)
        select_all_action.setShortcut(QKeySequence.SelectAll)
        select_all_action.triggered.connect(self._on_select_all)
        edit_menu.addAction(select_all_action)

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

        delete_guides_action = QAction(tr("Delete Guides"), self)
        delete_guides_action.triggered.connect(self._on_delete_guides)
        edit_menu.addAction(delete_guides_action)

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
        toggle_georef.setText(tr("Terrain panel"))
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
        get_models_action = QAction(tr("Get more models and textures…"), self)
        get_models_action.triggered.connect(self._on_get_models)
        help_menu.addAction(get_models_action)
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
            f"<p>{tr('Version')} {__version__}</p>"
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

        components_menu = QMenu(tr("Insert component"), self)
        sumari_act = QAction(tr("Sumari (author, 1.65 m)"), self)
        sumari_act.triggered.connect(
            lambda: self._on_insert_person_2d("sumari.png", 1.65, "Sumari"))
        components_menu.addAction(sumari_act)
        person2d = QAction(tr("Person (2D, faces the camera)"), self)
        person2d.triggered.connect(self._on_insert_person_2d)
        components_menu.addAction(person2d)
        person_sil = QAction(tr("Person (silhouette)"), self)
        person_sil.triggered.connect(
            lambda: self._on_insert_person_2d("person_silhouette.png"))
        components_menu.addAction(person_sil)
        faceme_img = QAction(tr("Face-me image (PNG)…"), self)
        faceme_img.triggered.connect(self._on_insert_faceme_image)
        components_menu.addAction(faceme_img)
        for key, label in (("person", tr("Person 3D")),
                           ("tree", tr("Tree")),
                           ("bush", tr("Bush")),
                           ("car", tr("Car"))):
            act = QAction(label, self)
            act.triggered.connect(
                lambda _c, k=key: self._on_insert_component(k))
            components_menu.addAction(act)
        actions.append(components_menu)

        import_dae_action = QAction(tr("Import DAE…"), self)
        import_dae_action.triggered.connect(self._on_import_dae)
        actions.append(import_dae_action)

        import_geo_action = QAction(tr("Import georef (KML / GeoJSON)…"), self)
        import_geo_action.triggered.connect(self._on_import_georef)
        actions.append(import_geo_action)

        export_ifc_action = QAction(tr("Export IFC…"), self)
        export_ifc_action.triggered.connect(self._on_export_ifc)
        actions.append(export_ifc_action)

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
        if self.viewport.scene.edit_group is not None:
            self.viewport.flash_status(tr(
                "Leave the group first (Esc) — nested groups aren't "
                "supported yet"))
            return
        sel = self.viewport.scene.selection
        faces = [f for f in sel if isinstance(f, Face)]
        edges = [e for e in sel if isinstance(e, Edge)]
        if faces or edges:
            self.viewport.history.execute(MakeGroupCommand(faces, edges))
            self.viewport.update()

    def _on_explode_group(self) -> None:
        if self.viewport.scene.edit_group is not None:
            self.viewport.flash_status(tr(
                "Leave the group first (Esc) — nested groups aren't "
                "supported yet"))
            return
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

    def _on_delete_guides(self) -> None:
        """Remove every construction guide (SketchUp's Edit ▸ Delete Guides)."""
        from core.history import DeleteGuidesCommand
        guides = list(self.viewport.scene.guides)
        if guides:
            self.viewport.history.execute(DeleteGuidesCommand(guides))
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
        """Esc, escalating like the viewport: cancel an in-progress action
        first; with nothing in progress, clear the selection."""
        vp = self.viewport
        if isinstance(vp.active_tool, PasteTool):
            self._activate_tool("select")
            return
        if vp.active_tool is not None and vp._tool_busy(vp.active_tool):
            vp.active_tool.on_cancel(vp)
            return
        if vp.scene.selection:
            vp.scene.clear_selection()
            vp.update()
            return
        if vp.active_tool is not None:
            vp.active_tool.on_cancel(vp)

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

    def _on_select_all(self) -> None:
        """Select every entity (Ctrl+A) — edges (soft included), faces, groups
        and dimensions. The safe way to rotate/move/scale a WHOLE model: a
        window box-select that misses one protruding piece leaves it behind
        and the transform warps the boundary (the sigue.igz report)."""
        sel = self.viewport.scene.selection
        sel.clear()
        sel.update(self.viewport.scene.edges)
        sel.update(self.viewport.scene.faces)
        sel.update(self.viewport.scene.groups)
        sel.update(getattr(self.viewport.scene, "dimensions", []))
        self.viewport.update()
        self.statusBar().showMessage(
            tr("Selected everything ({n} entities)", n=len(sel)), 2500)

    # ---- Undo / redo --------------------------------------------------------
    def _on_undo(self) -> None:
        if self.viewport.history.undo():
            self.viewport.notify_scene_changed()

    def _on_redo(self) -> None:
        if self.viewport.history.redo():
            self.viewport.notify_scene_changed()

    # ---- File handling ------------------------------------------------------
    def _on_new(self) -> None:
        self.viewport.end_group_edit()
        if not self._confirm_discard(tr("Discard current drawing?")):
            return
        scene = self.viewport.scene
        scene.clear()
        scene.version += 1
        self.viewport.history.clear()
        self._current_path = None
        self._insert_scale_figure()
        self.viewport.notify_scene_changed()
        self._update_title()

    def _on_open(self) -> None:
        self.viewport.end_group_edit()
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
        self.viewport.end_group_edit()
        if self._current_path is None:
            self._on_save_as()
            return
        self._do_save(self._current_path)

    def _on_save_as(self) -> None:
        self.viewport.end_group_edit()
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

    def _insert_scale_figure(self) -> None:
        """Place the 1.75 m scale figure near the origin in a fresh document,
        SketchUp-style. A plain group — select and Delete removes it. Added
        outside the undo history and without dirtying the document."""
        group = self._make_billboard_person("sumari.png", height=1.65,
                                            name="Sumari")
        if group is None:
            group = self._make_billboard_person()
        if group is None:
            return
        scene = self.viewport.scene
        scene.groups.append(group)
        scene.version += 1
        self._saved_version = scene.version

    def _make_billboard_person(self, image: str = "person_billboard.png",
                               height: float = 1.75,
                               name: str | None = None):
        """A face-me scale figure (arch-viz cutout)."""
        from PySide6.QtGui import QImage
        from core.group import make_billboard_group
        path = (Path(__file__).resolve().parent.parent / "resources"
                / "components" / image)
        if not path.exists():
            return None
        img = QImage(str(path))
        if img.isNull() or img.height() == 0:
            return None
        return make_billboard_group(str(path), height, name or tr("Person"),
                                    img.width() / img.height())

    def _on_insert_person_2d(self, image: str = "person_billboard.png",
                             height: float = 1.75,
                             name: str | None = None) -> None:
        from core.history import InsertGroupCommand
        self.viewport.end_group_edit()
        group = self._make_billboard_person(image, height, name)
        if group is None:
            QMessageBox.warning(self, tr("Insert component"),
                                tr("Component file missing: {p}",
                                   p="person_billboard.png"))
            return
        self.viewport.history.execute(InsertGroupCommand(group))
        self._activate_tool("move")
        self.viewport.flash_status(
            tr("Component inserted at the origin — Move (M) places it"), 4000)
        self.viewport.update()

    def _on_insert_faceme_image(self) -> None:
        """Insert the user's own transparent PNG as a face-me billboard —
        a cutout person, a tree photo — scaled to a chosen real height."""
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QInputDialog
        from core.group import make_billboard_group
        from core.history import InsertGroupCommand
        self.viewport.end_group_edit()
        path_str, _ = QFileDialog.getOpenFileName(
            self, tr("Face-me image"), "",
            tr("Images (*.png *.webp);;All files (*)"))
        if not path_str:
            return
        img = QImage(path_str)
        if img.isNull() or img.height() == 0:
            QMessageBox.warning(self, tr("Face-me image"),
                                tr("Could not read the image."))
            return
        if not img.hasAlphaChannel():
            QMessageBox.information(
                self, tr("Face-me image"),
                tr("The image has no transparency — it will show as a "
                   "solid rectangle. A PNG with transparent background "
                   "works best."))
        height, ok = QInputDialog.getDouble(
            self, tr("Face-me image"), tr("Real height (m):"),
            1.75, 0.05, 500.0, 2)
        if not ok:
            return
        group = make_billboard_group(
            path_str, height, Path(path_str).stem,
            img.width() / img.height())
        self.viewport.history.execute(InsertGroupCommand(group))
        self._activate_tool("move")
        self.viewport.flash_status(
            tr("Component inserted at the origin — Move (M) places it"), 4000)
        self.viewport.update()

    def _on_insert_component(self, key: str) -> None:
        """Insert a bundled starter component as a Group at the origin,
        selected and ready to Move into place."""
        from core.history import InsertGroupCommand
        from core.group import Group
        from core.scene import Scene as _Scene
        from formats import obj as _obj
        self.viewport.end_group_edit()
        path = (Path(__file__).resolve().parent.parent / "resources"
                / "components" / f"{key}.obj")
        if not path.exists():
            QMessageBox.warning(self, tr("Insert component"),
                                tr("Component file missing: {p}", p=str(path)))
            return
        temp = _Scene()
        _obj.load_obj(temp, path)
        group = Group(temp.mesh, name=tr(key.capitalize()))
        self.viewport.history.execute(InsertGroupCommand(group))
        self._activate_tool("move")
        self.viewport.flash_status(
            tr("Component inserted at the origin — Move (M) places it"), 4000)
        self.viewport.update()

    def _on_get_models(self) -> None:
        QMessageBox.information(
            self, tr("Get more models and textures"),
            tr("Free sources that open directly in IngeTrazo:") + "<br><br>"
            "<b>3D Warehouse</b> — "
            "<a href='https://3dwarehouse.sketchup.com'>"
            "3dwarehouse.sketchup.com</a><br>"
            + tr("Download as COLLADA (.dae) and use File → Import DAE.")
            + "<br><br>"
            "<b>Poly Haven</b> — <a href='https://polyhaven.com'>"
            "polyhaven.com</a> " + tr("(CC0: models OBJ and PBR textures)")
            + "<br><b>ambientCG</b> — <a href='https://ambientcg.com'>"
            "ambientcg.com</a> " + tr("(CC0 textures — drop the PNG into "
                                      "resources/textures)")
            + "<br><b>Sketchfab</b> — <a href='https://sketchfab.com'>"
            "sketchfab.com</a> " + tr("(filter by CC licence, download OBJ)"))


    def _import_progress(self, title):
        """A modal progress dialog + the callback the loaders call at
        milestones (big imports take ~20 s; SketchUp shows a bar here too)."""
        from PySide6.QtWidgets import QApplication, QProgressDialog
        dlg = QProgressDialog(title, "", 0, 100, self)
        dlg.setCancelButton(None)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(400)
        dlg.setAutoClose(False)

        def cb(frac, text):
            dlg.setValue(int(frac * 100))
            dlg.setLabelText(tr(text))
            QApplication.processEvents()

        return dlg, cb

    def _prepare_import_display(self, cmd, cb) -> None:
        """Pre-build the render/pick caches of freshly imported groups while
        the progress dialog is still up — otherwise the first orbit after a
        big import freezes ~5 s building them."""
        for g in getattr(cmd, "added_groups", []):
            cb(0.97, "Preparing display…")
            try:
                self.viewport._group_chunk(g)
            except Exception:  # noqa: BLE001 — display cache only; never fatal
                pass
        cb(1.0, "Done")

    def _on_import_dae(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, tr("Import DAE"), "",
            tr("COLLADA (*.dae);;All files (*)"))
        if not path_str:
            return
        path = Path(path_str)
        dlg, cb = self._import_progress(tr("Importing {name}…", name=path.name))
        cmd = SnapshotImport(
            lambda scene: dae_format.load_dae(scene, path, progress=cb))
        try:
            self.viewport.history.execute(cmd)
        except Exception as exc:  # noqa: BLE001
            dlg.close()
            QMessageBox.critical(self, tr("Import DAE failed"), str(exc))
            return
        self._prepare_import_display(cmd, cb)
        dlg.close()
        self.viewport.update()
        self.statusBar().showMessage(tr("Imported {name}", name=path.name), 3000)

    def _on_import_obj(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, tr("Import OBJ"), "", tr("Wavefront OBJ (*.obj);;All files (*)"))
        if not path_str:
            return
        path = Path(path_str)
        dlg, cb = self._import_progress(tr("Importing {name}…", name=path.name))
        cmd = SnapshotImport(
            lambda scene: obj_format.load_obj(scene, path, progress=cb))
        try:
            self.viewport.history.execute(cmd)
        except Exception as exc:  # noqa: BLE001
            dlg.close()
            QMessageBox.critical(self, tr("Import OBJ failed"), str(exc))
            return
        self._prepare_import_display(cmd, cb)
        dlg.close()
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

    def _on_export_ifc(self) -> None:
        self.viewport.end_group_edit()
        from core.bim import collect_objects
        if not collect_objects(self.viewport.scene):
            QMessageBox.information(
                self, tr("Export IFC"),
                tr("Nothing to export: tag geometry in the BIM panel first "
                   "(only tagged objects go to IFC)."))
            return
        path_str, _ = QFileDialog.getSaveFileName(
            self, tr("Export IFC"), "model.ifc",
            tr("IFC4 (*.ifc);;All files (*)"))
        if not path_str:
            return
        try:
            count = ifc_format.save_ifc(self.viewport.scene, path_str)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, tr("Export IFC failed"), str(exc))
            return
        self.statusBar().showMessage(
            tr("{n} IFC elements exported to {path}",
               n=count, path=path_str), 5000)

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
