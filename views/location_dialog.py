# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Project locator (Track G) — the SketchUp-style "Add Location" flow.

Start near you (IP), search a place to get close, then pan/zoom the map under a
fixed centre pin to nail the exact spot — even an unnamed rural site — and
confirm. The centre of the map is the selected point (like a phone map: move the
map, the pin stays put). Falls back to manual coordinates, which always work.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from georef.geocode import Geocoder, IpLocator
from georef.tiles import PRESETS, deg2num, num2deg

TILE_PX = 256


class MapPicker(QWidget):
    """A pannable/zoomable slippy map; its centre is the selected point."""

    centerChanged = Signal(float, float)   # lat, lon

    def __init__(self, source, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(480, 340)
        self.setCursor(Qt.OpenHandCursor)
        self._source = source
        self._lat, self._lon = -12.0464, -77.0428
        self._zoom = 5
        self._drag = None
        # Capture-rectangle drawing: when on, a left-drag draws the area to
        # capture instead of panning. Committed as geographic corners so it
        # stays anchored to the ground as you pan/zoom.
        self._rect_mode = False
        self._rect_screen = None       # (start_pt, cur_pt) while dragging
        self._rect_ll = None           # (lat1, lon1, lat2, lon2) committed
        from georef.tile_fetcher import TileFetcher
        self._fetcher = TileFetcher(parent=self)
        self._fetcher.tileReady.connect(lambda *_: self.update())
        self._images: dict = {}

    def set_center(self, lat: float, lon: float, zoom: int | None = None) -> None:
        self._lat = max(-85.0, min(85.0, float(lat)))
        self._lon = ((float(lon) + 180) % 360) - 180
        if zoom is not None:
            self._zoom = max(1, min(self._source.max_zoom, int(zoom)))
        self.centerChanged.emit(self._lat, self._lon)
        self.update()

    def center(self):
        return self._lat, self._lon

    def zoom(self):
        return self._zoom

    def set_source(self, source) -> None:
        self._source = source
        self._images.clear()
        self.update()

    def set_rect_mode(self, on: bool) -> None:
        self._rect_mode = bool(on)
        self.setCursor(Qt.CrossCursor if on else Qt.OpenHandCursor)

    # ---- Screen ↔ geographic -------------------------------------------------
    def _screen_to_ll(self, sx, sy):
        w, h = self.width(), self.height()
        cxf, cyf = deg2num(self._lat, self._lon, self._zoom)
        ox = cxf * TILE_PX - w / 2
        oy = cyf * TILE_PX - h / 2
        return num2deg((ox + sx) / TILE_PX, (oy + sy) / TILE_PX, self._zoom)

    def _ll_to_screen(self, lat, lon):
        w, h = self.width(), self.height()
        cxf, cyf = deg2num(self._lat, self._lon, self._zoom)
        xf, yf = deg2num(lat, lon, self._zoom)
        return (xf * TILE_PX - (cxf * TILE_PX - w / 2),
                yf * TILE_PX - (cyf * TILE_PX - h / 2))

    def capture_rect(self):
        """The drawn capture area as ``(center_lat, center_lon, width_m,
        length_m)``, or ``None`` if none was drawn."""
        if self._rect_ll is None:
            return None
        import math
        la1, lo1, la2, lo2 = self._rect_ll
        clat, clon = (la1 + la2) / 2, (lo1 + lo2) / 2
        width_m = abs(lo2 - lo1) * 111320.0 * math.cos(math.radians(clat))
        length_m = abs(la2 - la1) * 111320.0
        return clat, clon, max(200.0, width_m), max(200.0, length_m)

    # ---- Rendering ----------------------------------------------------------
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(230, 232, 236))
        w, h = self.width(), self.height()
        n = 2 ** self._zoom
        cxf, cyf = deg2num(self._lat, self._lon, self._zoom)
        ox = cxf * TILE_PX - w / 2       # world pixel at widget's top-left
        oy = cyf * TILE_PX - h / 2
        tx0 = int(math.floor(ox / TILE_PX))
        ty0 = int(math.floor(oy / TILE_PX))
        tx1 = int(math.floor((ox + w) / TILE_PX))
        ty1 = int(math.floor((oy + h) / TILE_PX))
        for ty in range(ty0, ty1 + 1):
            if ty < 0 or ty >= n:
                continue
            for tx in range(tx0, tx1 + 1):
                twx = tx % n                       # wrap horizontally
                img = self._tile(twx, ty)
                sx = tx * TILE_PX - ox
                sy = ty * TILE_PX - oy
                if img is not None:
                    p.drawImage(QPointF(sx, sy), img)
                else:
                    p.fillRect(int(sx), int(sy), TILE_PX, TILE_PX,
                               QColor(214, 217, 222))

        # Capture rectangle: the drawn area to import.
        rect_pts = None
        if self._rect_screen is not None:
            (p0, p1) = self._rect_screen
            rect_pts = (p0.x(), p0.y(), p1.x(), p1.y())
        elif self._rect_ll is not None:
            la1, lo1, la2, lo2 = self._rect_ll
            x0, y0 = self._ll_to_screen(la1, lo1)
            x1, y1 = self._ll_to_screen(la2, lo2)
            rect_pts = (x0, y0, x1, y1)
        if rect_pts is not None:
            x0, y0, x1, y1 = rect_pts
            rx, ry = min(x0, x1), min(y0, y1)
            rw, rh = abs(x1 - x0), abs(y1 - y0)
            p.setBrush(QColor(255, 200, 40, 40))
            p.setPen(QPen(QColor(255, 190, 30), 2))
            p.drawRect(int(rx), int(ry), int(rw), int(rh))
            p.setBrush(Qt.NoBrush)

        # Centre pin — the selected point.
        cx, cy = w / 2, h / 2
        p.setPen(QPen(QColor(255, 255, 255), 3))
        p.drawLine(QPointF(cx - 11, cy), QPointF(cx + 11, cy))
        p.drawLine(QPointF(cx, cy - 11), QPointF(cx, cy + 11))
        p.setPen(QPen(QColor(230, 60, 40), 1.5))
        p.setBrush(QColor(230, 60, 40))
        p.drawEllipse(QPointF(cx, cy), 4, 4)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255), 1))
        p.drawLine(QPointF(cx - 11, cy), QPointF(cx + 11, cy))
        p.drawLine(QPointF(cx, cy - 11), QPointF(cx, cy + 11))

    def _tile(self, x: int, y: int):
        key = (x, y, self._zoom)
        img = self._images.get(key)
        if img is None:
            img = self._fetcher.request(self._source, x, y, self._zoom)
            if img is not None:
                self._images[key] = img
        return img

    # ---- Interaction --------------------------------------------------------
    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            return
        if self._rect_mode:
            self._rect_screen = (ev.position(), ev.position())
        else:
            self._drag = ev.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, ev) -> None:
        if self._rect_mode and self._rect_screen is not None:
            self._rect_screen = (self._rect_screen[0], ev.position())
            self.update()
            return
        if self._drag is None:
            return
        dx = ev.position().x() - self._drag.x()
        dy = ev.position().y() - self._drag.y()
        self._drag = ev.position()
        cxf, cyf = deg2num(self._lat, self._lon, self._zoom)
        lat, lon = num2deg(cxf - dx / TILE_PX, cyf - dy / TILE_PX, self._zoom)
        self.set_center(lat, lon)

    def mouseReleaseEvent(self, _ev) -> None:
        if self._rect_mode and self._rect_screen is not None:
            (p0, p1) = self._rect_screen
            if abs(p1.x() - p0.x()) > 4 and abs(p1.y() - p0.y()) > 4:
                la1, lo1 = self._screen_to_ll(p0.x(), p0.y())
                la2, lo2 = self._screen_to_ll(p1.x(), p1.y())
                self._rect_ll = (la1, lo1, la2, lo2)
            self._rect_screen = None
            self.update()
            return
        self._drag = None
        self.setCursor(Qt.OpenHandCursor if not self._rect_mode else Qt.CrossCursor)

    def wheelEvent(self, ev) -> None:
        step = 1 if ev.angleDelta().y() > 0 else -1
        z = max(1, min(self._source.max_zoom, self._zoom + step))
        if z != self._zoom:
            self._zoom = z
            self.update()


class LocationDialog(QDialog):
    """Search / locate-me / pan-and-zoom to pick a project location."""

    def __init__(self, source, lat=-12.0464, lon=-77.0428, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Set project location"))
        self.resize(640, 560)
        self._geocoder = Geocoder(self)
        self._geocoder.resultsReady.connect(self._on_results)
        self._geocoder.failed.connect(
            lambda msg: self._status.setText(tr("Search failed: {msg}").format(msg=msg)))
        self._iploc = IpLocator(self)
        self._iploc.located.connect(self._on_located)
        self._iploc.failed.connect(
            lambda msg: self._status.setText(tr("Locate me failed: {msg}").format(msg=msg)))

        root = QVBoxLayout(self)

        row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("Search a place (city, address, landmark)…"))
        self._search.returnPressed.connect(self._do_search)
        btn_search = QPushButton(tr("Search"))
        btn_search.clicked.connect(self._do_search)
        btn_me = QPushButton(tr("Locate me"))
        btn_me.clicked.connect(self._locate_me)
        row.addWidget(self._search, 1)
        row.addWidget(btn_search)
        row.addWidget(btn_me)
        root.addLayout(row)

        self._results = QListWidget()
        self._results.setMaximumHeight(96)
        self._results.itemClicked.connect(self._on_pick_result)
        root.addWidget(self._results)

        self._map = MapPicker(source)
        self._map.centerChanged.connect(self._on_center)
        root.addWidget(self._map, 1)

        self._draw_rect = QCheckBox(
            tr("Draw capture area (drag a rectangle on the map)"))
        self._draw_rect.toggled.connect(self._map.set_rect_mode)
        root.addWidget(self._draw_rect)

        coords = QHBoxLayout()
        coords.addWidget(QLabel(tr("Lat:")))
        self._lat_box = QDoubleSpinBox()
        self._lat_box.setRange(-85.0, 85.0)
        self._lat_box.setDecimals(6)
        self._lat_box.editingFinished.connect(self._on_coords_typed)
        coords.addWidget(self._lat_box)
        coords.addWidget(QLabel(tr("Lon:")))
        self._lon_box = QDoubleSpinBox()
        self._lon_box.setRange(-180.0, 180.0)
        self._lon_box.setDecimals(6)
        self._lon_box.editingFinished.connect(self._on_coords_typed)
        coords.addWidget(self._lon_box)
        coords.addStretch(1)
        root.addLayout(coords)

        self._status = QLabel(tr("Move the map so the centre pin is on your site."))
        self._status.setStyleSheet("color:#7a828f; font-size:11px;")
        root.addWidget(self._status)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._map.set_center(lat, lon, zoom=12)

    # ---- Slots --------------------------------------------------------------
    def _do_search(self) -> None:
        self._status.setText(tr("Searching…"))
        self._geocoder.search(self._search.text())

    def _on_results(self, places) -> None:
        self._results.clear()
        if not places:
            self._status.setText(tr("No matches. Pan the map to your site, or "
                                    "type coordinates."))
            return
        self._status.setText(tr("{n} matches — pick one, then fine-tune on the "
                                "map.").format(n=len(places)))
        for pl in places:
            item = QListWidgetItem(pl.name)
            item.setData(Qt.UserRole, (pl.lat, pl.lon))
            self._results.addItem(item)

    def _on_pick_result(self, item) -> None:
        lat, lon = item.data(Qt.UserRole)
        self._map.set_center(lat, lon, zoom=15)

    def _locate_me(self) -> None:
        self._status.setText(tr("Locating…"))
        self._iploc.locate()

    def _on_located(self, lat, lon, label) -> None:
        self._status.setText(tr("Near {label} (approximate — refine on the map).")
                             .format(label=label))
        self._map.set_center(lat, lon, zoom=11)

    def _on_center(self, lat, lon) -> None:
        for box, val in ((self._lat_box, lat), (self._lon_box, lon)):
            box.blockSignals(True)
            box.setValue(val)
            box.blockSignals(False)

    def _on_coords_typed(self) -> None:
        self._map.set_center(self._lat_box.value(), self._lon_box.value())

    def selected(self):
        """``(lat, lon, width_m, length_m)``. If a capture rectangle was drawn,
        the location is its centre and the size comes from it; otherwise the map
        centre with ``None`` sizes (the tray keeps its typed capture size)."""
        rect = self._map.capture_rect()
        if rect is not None:
            clat, clon, wm, lm = rect
            return clat, clon, wm, lm
        lat, lon = self._map.center()
        return lat, lon, None, None


def pick_location(source, lat, lon, parent=None):
    """Modal locator. Returns ``(lat, lon, width_m, length_m)`` on accept
    (sizes may be ``None`` if no capture rectangle was drawn), else ``None``."""
    src = source if source is not None else PRESETS["esri_imagery"]
    dlg = LocationDialog(src, lat, lon, parent)
    if dlg.exec() == QDialog.Accepted:
        return dlg.selected()
    return None
