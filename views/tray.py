# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Right-side dockable tray (SketchUp-style), built from QDockWidget.

Holds collapsible sections:
- **Materiales** — a palette of colour + texture swatches ("En el modelo" and a
  bundled "Biblioteca"). Clicking a swatch makes it the active Paint material
  and switches to the Paint tool. ``+ Textura…`` adds an image with a tile size.
- **Estilo de cota** — precision, unit, font size and colour of dimensions,
  applied live to ``scene.dimension_style``.
- **Info de entidad** — read-only facts about the current selection (face area,
  edge length, dimension value, material).

A ``QDockWidget`` gives docking/floating/closing for free; the sections are a
vertical stack of lightweight collapsibles inside a scroll area.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from core.mesh import Edge, Face
from core.group import Group
from core.dimension import Dimension
from georef.datum import SceneDatum
from georef.geopath import GeoPath
from georef.tiles import DEFAULT_SOURCE_ID, PRESETS, TileLayer, custom_source
from tools.paint import PaintTool

_TEX_DIR = Path(__file__).resolve().parent.parent / "resources" / "textures"
_SWATCH = 44  # swatch pixel size

# A small starter colour set for the library row.
_LIBRARY_COLORS = [
    (0.96, 0.95, 0.925), (0.80, 0.45, 0.30), (0.20, 0.45, 0.75),
    (0.45, 0.62, 0.35), (0.85, 0.78, 0.45), (0.55, 0.55, 0.58),
    (0.30, 0.30, 0.33), (0.95, 0.95, 0.95),
]


class _Section(QWidget):
    """A collapsible section: a header button that toggles its content."""

    def __init__(self, title: str, content: QWidget) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._btn = QToolButton()
        self._btn.setText(f"  {title}")
        self._btn.setCheckable(True)
        self._btn.setChecked(True)
        self._btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._btn.setArrowType(Qt.DownArrow)
        # Clean, light header (QGIS-style): plain bold title on the panel
        # background with a subtle underline — no dark bar. Uses palette() roles
        # so it adapts to light and dark themes.
        self._btn.setStyleSheet(
            "QToolButton { font-weight: bold; padding: 6px 4px; border: none;"
            " border-bottom: 1px solid palette(mid); text-align: left; }"
            "QToolButton:hover { background: palette(midlight); }")
        self._btn.toggled.connect(self._on_toggle)
        self._content = content
        lay.addWidget(self._btn)
        lay.addWidget(content)

    def _on_toggle(self, on: bool) -> None:
        self._content.setVisible(on)
        self._btn.setArrowType(Qt.DownArrow if on else Qt.RightArrow)


def _color_pixmap(rgb, size=_SWATCH) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(QColor.fromRgbF(*rgb))
    return pm


def _texture_pixmap(path, size=_SWATCH) -> QPixmap | None:
    img = QImage(str(path))
    if img.isNull():
        return None
    return QPixmap.fromImage(img.scaled(size, size, Qt.IgnoreAspectRatio,
                                        Qt.SmoothTransformation))


def _swatch_button(pm: QPixmap, tip: str) -> QToolButton:
    b = QToolButton()
    b.setIcon(QIcon(pm))
    b.setIconSize(QSize(_SWATCH, _SWATCH))
    b.setToolTip(tip)
    b.setAutoRaise(True)
    return b


class BaseMapPanel(QWidget):
    """Satellite/street base map (Track G): pick a source, go to a place.

    Setting a location anchors the scene datum (if unset) at that lat/lon and
    shows the tile layer around the origin. The tiles are display-only — they
    never enter the modelling mesh.
    """

    _CUSTOM = "__custom__"

    def __init__(self, window) -> None:
        super().__init__()
        self._window = window
        grid = QGridLayout(self)
        grid.setContentsMargins(8, 6, 8, 8)

        grid.addWidget(QLabel(tr("Source:")), 0, 0)
        self._source = QComboBox()
        for sid, src in PRESETS.items():
            self._source.addItem(tr(src.name), sid)
        self._source.addItem(tr("Custom XYZ…"), self._CUSTOM)
        self._source.setCurrentIndex(
            self._source.findData(DEFAULT_SOURCE_ID))
        self._source.currentIndexChanged.connect(self._on_source_changed)
        grid.addWidget(self._source, 0, 1)

        self._custom_url = QLineEdit()
        self._custom_url.setPlaceholderText("https://…/{z}/{x}/{y}.png")
        self._custom_url.setToolTip(tr(
            "Paste any XYZ tile URL. You assume responsibility for its terms."))
        self._custom_url.editingFinished.connect(self._apply_source)
        self._custom_url.setVisible(False)
        grid.addWidget(self._custom_url, 1, 0, 1, 2)

        grid.addWidget(QLabel(tr("Latitude:")), 2, 0)
        self._lat = QDoubleSpinBox()
        self._lat.setRange(-85.0, 85.0)
        self._lat.setDecimals(6)
        self._lat.setValue(-12.046400)
        grid.addWidget(self._lat, 2, 1)

        grid.addWidget(QLabel(tr("Longitude:")), 3, 0)
        self._lon = QDoubleSpinBox()
        self._lon.setRange(-180.0, 180.0)
        self._lon.setDecimals(6)
        self._lon.setValue(-77.042800)
        grid.addWidget(self._lon, 3, 1)

        grid.addWidget(QLabel(tr("Zoom:")), 4, 0)
        self._zoom = QSpinBox()
        self._zoom.setRange(1, 21)
        self._zoom.setValue(16)
        self._zoom.valueChanged.connect(self._on_zoom_changed)
        grid.addWidget(self._zoom, 4, 1)

        # Capture area (metres): set by drawing a rectangle in the locator
        # dialog. A square for a site, a long strip for a road. Kept as state,
        # not tray fields (the locator is where you define it).
        self._capture_w = 2400.0
        self._capture_l = 2400.0

        self._find = QPushButton(tr("Search location…"))
        self._find.clicked.connect(self._open_locator)
        grid.addWidget(self._find, 5, 0, 1, 2)

        self._go = QPushButton(tr("Go to location"))
        self._go.clicked.connect(self._go_to)
        grid.addWidget(self._go, 6, 0, 1, 2)

        self._show = QCheckBox(tr("Show base map"))
        self._show.setChecked(True)
        self._show.toggled.connect(self._on_toggle_visible)
        grid.addWidget(self._show, 7, 0, 1, 2)

        self._terrain3d = QCheckBox(tr("3D terrain"))
        self._terrain3d.toggled.connect(self._on_toggle_terrain)
        grid.addWidget(self._terrain3d, 8, 0, 1, 2)

        self._attribution = QLabel("")
        self._attribution.setWordWrap(True)
        self._attribution.setStyleSheet("color:#9aa3b2; font-size:10px; margin-top:4px;")
        grid.addWidget(self._attribution, 9, 0, 1, 2)

        self._sync_from_scene()

    # ---- Source -------------------------------------------------------------
    def _current_source(self):
        sid = self._source.currentData()
        if sid == self._CUSTOM:
            url = self._custom_url.text().strip()
            if not url:
                return None
            return custom_source(url, max_zoom=self._zoom.maximum())
        return PRESETS[sid]

    def _on_source_changed(self) -> None:
        self._custom_url.setVisible(self._source.currentData() == self._CUSTOM)
        self._apply_source()

    def _apply_source(self) -> None:
        src = self._current_source()
        if src is None:
            return
        self._attribution.setText(src.attribution)
        layer = getattr(self._window.viewport.scene, "tile_layer", None)
        if layer is not None:
            layer.source = src
            self._window.viewport.reset_tiles()

    def _on_zoom_changed(self, z: int) -> None:
        layer = getattr(self._window.viewport.scene, "tile_layer", None)
        if layer is not None:
            layer.zoom = z
            self._window.viewport.reset_tiles()

    # ---- Location -----------------------------------------------------------
    def _open_locator(self) -> None:
        """Open the map locator; on accept, drop the chosen lat/lon and go."""
        from views.location_dialog import pick_location
        src = self._current_source() or PRESETS[DEFAULT_SOURCE_ID]
        result = pick_location(src, self._lat.value(), self._lon.value(), self)
        if result is not None:
            lat, lon, width_m, length_m = result
            self._lat.setValue(lat)
            self._lon.setValue(lon)
            if width_m and length_m:      # a capture rectangle was drawn
                self._capture_w = width_m
                self._capture_l = length_m
            self._go_to()

    def _go_to(self) -> None:
        src = self._current_source()
        if src is None:
            return
        scene = self._window.viewport.scene
        datum = SceneDatum(self._lat.value(), self._lon.value())
        scene.georef = datum
        layer = TileLayer(src, zoom=self._zoom.value())
        layer.set_rectangle(self._capture_w, self._capture_l)
        # Guard: a very large capture is capped in detail so it stays bounded.
        if layer.cap_detail(datum, max_tiles=500):
            self._window.viewport.flash_status(tr(
                "Large capture — detail reduced to zoom {z} to stay fast.")
                .format(z=layer.zoom))
        layer.visible = self._show.isChecked()
        scene.tile_layer = layer
        self._attribution.setText(src.attribution)
        self._window.viewport.reset_tiles()
        self._frame_camera(max(self._capture_w, self._capture_l) / 2.0)

    def setup_for_import(self, datum, geo_paths) -> None:
        """After a georef import: anchor the base map at the imported data with a
        reference capture covering it, so 'Show base map' verifies the location."""
        from PySide6.QtCore import QSignalBlocker
        src = self._current_source() or PRESETS[DEFAULT_SOURCE_ID]
        scene = self._window.viewport.scene
        pts = [p for gp in geo_paths for p in gp.points]
        if not pts:
            return
        minx = min(p.x() for p in pts)
        maxx = max(p.x() for p in pts)
        miny = min(p.y() for p in pts)
        maxy = max(p.y() for p in pts)
        margin = 0.15 * max(maxx - minx, maxy - miny, 200.0)
        cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
        self._capture_w = (maxx - minx) + 2 * margin
        self._capture_l = (maxy - miny) + 2 * margin
        layer = TileLayer(src, zoom=self._zoom.value())
        layer.set_rectangle(self._capture_w, self._capture_l, cx=cx, cy=cy)
        layer.cap_detail(datum, max_tiles=500)
        layer.visible = self._show.isChecked()
        scene.tile_layer = layer
        self._attribution.setText(src.attribution)
        blockers = [QSignalBlocker(w) for w in (self._lat, self._lon)]
        self._lat.setValue(datum.lat)
        self._lon.setValue(datum.lon)
        del blockers
        self._window.viewport.reset_tiles()

    def _frame_camera(self, radius: float) -> None:
        from PySide6.QtGui import QVector3D
        vp = self._window.viewport
        vp.camera.set_view("top")
        vp.camera.fit_to(QVector3D(-radius, -radius, 0.0),
                         QVector3D(radius, radius, 0.0))
        vp.update()

    def _on_toggle_visible(self, on: bool) -> None:
        layer = getattr(self._window.viewport.scene, "tile_layer", None)
        if layer is not None:
            layer.visible = on
            self._window.viewport.update()

    def _on_toggle_terrain(self, on: bool) -> None:
        self._window.set_terrain_enabled(on)

    def _sync_from_scene(self) -> None:
        """Reflect a datum/layer already on the scene (e.g. loaded from .igz).

        Widgets are updated with signals blocked so this passive sync never
        kicks off a tile reset or a camera move — it only mirrors state.
        """
        from PySide6.QtCore import QSignalBlocker
        scene = self._window.viewport.scene
        datum = getattr(scene, "georef", None)
        layer = getattr(scene, "tile_layer", None)
        blockers = [QSignalBlocker(w) for w in
                    (self._source, self._lat, self._lon, self._zoom, self._show)]
        if datum is not None:
            self._lat.setValue(datum.lat)
            self._lon.setValue(datum.lon)
        if layer is not None:
            idx = self._source.findData(layer.source.id)
            if idx >= 0:
                self._source.setCurrentIndex(idx)
            self._zoom.setValue(layer.zoom)
            self._show.setChecked(layer.visible)
            self._attribution.setText(layer.source.attribution)
        else:
            self._attribution.setText(self._current_source().attribution
                                      if self._current_source() else "")
        del blockers  # release the signal blockers
        self._custom_url.setVisible(self._source.currentData() == self._CUSTOM)

    def on_scene_changed(self) -> None:
        self._sync_from_scene()


class MaterialsPanel(QWidget):
    """Swatch palette: pick a colour/texture to paint with."""

    COLS = 5

    def __init__(self, window) -> None:
        super().__init__()
        self._window = window
        self._tile_size = 1.0
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 8)

        # Active material preview.
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Active:")))
        self._preview = QLabel()
        self._preview.setFixedSize(_SWATCH, _SWATCH)
        self._preview.setFrameShape(QFrame.Box)
        row.addWidget(self._preview)
        row.addStretch(1)
        root.addLayout(row)

        root.addWidget(self._heading(tr("In model")))
        self._in_model_grid = QGridLayout()
        self._in_model_grid.setSpacing(3)
        root.addLayout(self._in_model_grid)

        root.addWidget(self._heading(tr("Library")))
        lib_grid = QGridLayout()
        lib_grid.setSpacing(3)
        root.addLayout(lib_grid)
        self._fill_library(lib_grid)

        btns = QHBoxLayout()
        add_color = QPushButton(tr("+ Color…"))
        add_color.clicked.connect(self._add_color)
        add_tex = QPushButton(tr("+ Texture…"))
        add_tex.clicked.connect(self._add_texture)
        btns.addWidget(add_color)
        btns.addWidget(add_tex)
        root.addLayout(btns)
        root.addStretch(1)

        self._refresh_preview()
        self.refresh_in_model()

    def _heading(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#9aa3b2; margin-top:6px; font-size:11px;")
        return lbl

    # ---- Library + in-model swatches ---------------------------------------
    def _fill_library(self, grid: QGridLayout) -> None:
        i = 0
        for rgb in _LIBRARY_COLORS:
            b = _swatch_button(_color_pixmap(rgb), tr("Color"))
            b.clicked.connect(lambda _=False, c=rgb: self._apply_color(c))
            grid.addWidget(b, i // self.COLS, i % self.COLS)
            i += 1
        for path in sorted(_TEX_DIR.glob("*.png")):
            pm = _texture_pixmap(path)
            if pm is None:
                continue
            b = _swatch_button(pm, path.stem)
            b.clicked.connect(lambda _=False, p=str(path): self._apply_texture(p))
            grid.addWidget(b, i // self.COLS, i % self.COLS)
            i += 1

    def refresh_in_model(self) -> None:
        """Rebuild the 'En el modelo' swatches from the materials in use."""
        while self._in_model_grid.count():
            item = self._in_model_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        colors: dict = {}
        textures: dict = {}
        for face in self._window.viewport.scene.render_faces():
            tex = face.attrs.get("texture")
            if tex and tex.get("path"):
                textures.setdefault(tex["path"], tex)
            else:
                col = face.attrs.get("color")
                if col is not None:
                    colors[tuple(col)] = col
        i = 0
        for col in colors.values():
            b = _swatch_button(_color_pixmap(tuple(col)), tr("Color"))
            b.clicked.connect(lambda _=False, c=tuple(col): self._apply_color(c))
            self._in_model_grid.addWidget(b, i // self.COLS, i % self.COLS)
            i += 1
        for path, tex in textures.items():
            pm = _texture_pixmap(path)
            if pm is None:
                continue
            b = _swatch_button(pm, Path(path).stem)
            b.clicked.connect(
                lambda _=False, t=dict(tex): self._apply_texture(
                    t["path"], t.get("sw", 1.0)))
            self._in_model_grid.addWidget(b, i // self.COLS, i % self.COLS)
            i += 1

    # ---- Apply / add --------------------------------------------------------
    def _apply_color(self, rgb) -> None:
        PaintTool.current_color = tuple(rgb)
        PaintTool.current_texture = None
        self._window._activate_tool("paint")
        self._refresh_preview()

    def _apply_texture(self, path: str, size: float | None = None) -> None:
        sz = self._tile_size if size is None else size
        PaintTool.current_texture = {"path": path, "sw": sz, "sh": sz}
        self._window._activate_tool("paint")
        self._refresh_preview()

    def _add_color(self) -> None:
        r, g, b = PaintTool.current_color
        chosen = QColorDialog.getColor(QColor.fromRgbF(r, g, b), self, tr("Color"))
        if chosen.isValid():
            self._apply_color((chosen.redF(), chosen.greenF(), chosen.blueF()))

    def _add_texture(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, tr("Choose texture"), str(_TEX_DIR),
            tr("Images (*.png *.jpg *.jpeg *.bmp);;All (*)"))
        if not path_str:
            return
        size, ok = QInputDialog.getDouble(
            self, tr("Texture size"), tr("Real tile size (meters):"),
            self._tile_size, 0.001, 1000.0, 3)
        if not ok:
            return
        self._tile_size = size
        self._apply_texture(path_str, size)

    def _refresh_preview(self) -> None:
        if PaintTool.current_texture is not None:
            pm = _texture_pixmap(PaintTool.current_texture["path"])
            if pm is not None:
                self._preview.setPixmap(pm)
                return
        self._preview.setPixmap(_color_pixmap(PaintTool.current_color))


class DimensionStylePanel(QWidget):
    """Live editor for ``scene.dimension_style``."""

    def __init__(self, window) -> None:
        super().__init__()
        self._window = window
        grid = QGridLayout(self)
        grid.setContentsMargins(8, 6, 8, 8)
        style = self._style()

        grid.addWidget(QLabel(tr("Decimals:")), 0, 0)
        self._decimals = QSpinBox()
        self._decimals.setRange(0, 4)
        self._decimals.setValue(int(style.get("decimals", 2)))
        self._decimals.valueChanged.connect(self._apply)
        grid.addWidget(self._decimals, 0, 1)

        grid.addWidget(QLabel(tr("Unit:")), 1, 0)
        self._units = QComboBox()
        self._units.addItems(["m", "cm", "mm"])
        self._units.setCurrentText(style.get("units", "m"))
        self._units.currentTextChanged.connect(self._apply)
        grid.addWidget(self._units, 1, 1)

        grid.addWidget(QLabel(tr("Font:")), 2, 0)
        self._font = QSpinBox()
        self._font.setRange(6, 28)
        self._font.setValue(int(style.get("font_size", 9)))
        self._font.valueChanged.connect(self._apply)
        grid.addWidget(self._font, 2, 1)

        grid.addWidget(QLabel(tr("Color:")), 3, 0)
        self._color_btn = QPushButton()
        self._color_btn.clicked.connect(self._pick_color)
        grid.addWidget(self._color_btn, 3, 1)
        self._refresh_color_btn()

    def _style(self) -> dict:
        return self._window.viewport.scene.dimension_style

    def _apply(self) -> None:
        style = self._style()
        style["decimals"] = self._decimals.value()
        style["units"] = self._units.currentText()
        style["font_size"] = self._font.value()
        self._window.viewport.scene.version += 1
        self._window.viewport.update()

    def _pick_color(self) -> None:
        c = self._style().get("color", [45, 55, 75])
        chosen = QColorDialog.getColor(QColor(c[0], c[1], c[2]), self,
                                       tr("Dimension color"))
        if chosen.isValid():
            self._style()["color"] = [chosen.red(), chosen.green(), chosen.blue()]
            self._refresh_color_btn()
            self._apply()

    def _refresh_color_btn(self) -> None:
        c = self._style().get("color", [45, 55, 75])
        self._color_btn.setStyleSheet(
            f"background: rgb({c[0]},{c[1]},{c[2]}); min-height: 18px;")


class EntityInfoPanel(QWidget):
    """Read-only facts about the current selection."""

    def __init__(self, window) -> None:
        super().__init__()
        self._window = window
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        self._label = QLabel(tr("Nothing selected"))
        self._label.setWordWrap(True)
        self._label.setTextFormat(Qt.RichText)
        self._label.setStyleSheet("font-size: 12px;")
        lay.addWidget(self._label)

    def refresh(self) -> None:
        sel = list(self._window.viewport.scene.selection)
        self._label.setText(self._describe(sel))

    def _describe(self, sel: list) -> str:
        if not sel:
            return tr("Nothing selected")
        if len(sel) == 1:
            e = sel[0]
            if isinstance(e, Face):
                mat = self._material_of(e)
                return (f"<b>{tr('Face')}</b><br>{tr('Area')}: {e.area():.3f} m²<br>"
                        f"{tr('Vertices')}: {len(e.vertices)}<br>"
                        f"{tr('Material')}: {mat}")
            if isinstance(e, Edge):
                return (f"<b>{tr('Edge')}</b><br>"
                        f"{tr('Length')}: {(e.b - e.a).length():.3f} m")
            if isinstance(e, Dimension):
                return f"<b>{tr('Dimension')}</b><br>{tr('Measure')}: {e.value():.3f} m"
            if isinstance(e, GeoPath):
                return self._describe_geopath(e)
            if isinstance(e, Group):
                return f"<b>{tr('Group')}</b><br>{tr('Faces')}: {len(e.mesh.faces)}"
            return f"<b>{tr('1 entity')}</b>"
        counts = {"faces": 0, "edges": 0, "dimensions": 0, "groups": 0}
        for e in sel:
            if isinstance(e, Face):
                counts["faces"] += 1
            elif isinstance(e, Edge):
                counts["edges"] += 1
            elif isinstance(e, Dimension):
                counts["dimensions"] += 1
            elif isinstance(e, Group):
                counts["groups"] += 1
        parts = [f"{n} {tr(k)}" for k, n in counts.items() if n]
        return f"<b>{tr('Selection')}</b><br>" + ", ".join(parts)

    @staticmethod
    def _describe_geopath(path) -> str:
        kind = tr("Polygon") if path.closed else tr("Route")
        rows = [f"<b>{kind}</b>",
                f"{tr('Vertices')}: {len(path.points)}",
                f"{tr('Perimeter')}: {path.perimeter():.2f} m"]
        if path.closed:
            area = path.area()
            rows.append(f"{tr('Area (plan)')}: {area:.2f} m² "
                        f"({area / 10000:.4f} ha)")
            sa = path.surface_area()
            if sa is not None:
                rows.append(f"{tr('Area (3D terrain)')}: {sa:.2f} m²")
        return "<br>".join(rows)

    @staticmethod
    def _material_of(face) -> str:
        tex = face.attrs.get("texture")
        if tex and tex.get("path"):
            return Path(tex["path"]).stem
        col = face.attrs.get("color")
        if col is not None:
            return f"color {tuple(round(c, 2) for c in col)}"
        return "—"


def _scrolled(sections) -> QScrollArea:
    """A scroll area wrapping a vertical stack of collapsible sections."""
    inner = QWidget()
    col = QVBoxLayout(inner)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(2)
    for title, widget in sections:
        col.addWidget(_Section(title, widget))
    col.addStretch(1)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(inner)
    scroll.setMinimumWidth(240)
    return scroll


class Tray(QDockWidget):
    """Right-side **Properties** dock: what you're working with — the selection's
    info, materials, and annotation styles (context, not geo workspace)."""

    def __init__(self, window) -> None:
        super().__init__(tr("Properties"), window)
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetMovable
                         | QDockWidget.DockWidgetFloatable)

        self.entity_info = EntityInfoPanel(window)
        self.materials = MaterialsPanel(window)
        self.dim_style = DimensionStylePanel(window)
        self.setWidget(_scrolled([
            (tr("Entity info"), self.entity_info),
            (tr("Materials"), self.materials),
            (tr("Dimension style"), self.dim_style),
        ]))

    def on_scene_changed(self) -> None:
        self.entity_info.refresh()
        self.materials.refresh_in_model()


class GeorefTray(QDockWidget):
    """Right-side **Georef** dock: the location workspace — base map source,
    search/locate, capture area, 3D terrain (kept apart from properties)."""

    def __init__(self, window) -> None:
        super().__init__(tr("Georef"), window)
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetMovable
                         | QDockWidget.DockWidgetFloatable)
        self.base_map = BaseMapPanel(window)
        self.setWidget(_scrolled([(tr("Base map"), self.base_map)]))

    def on_scene_changed(self) -> None:
        self.base_map.on_scene_changed()
