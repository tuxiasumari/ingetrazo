# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Longitudinal terrain profile panel (Track G, G4) — the Google-Earth parity.

A bottom dock that draws the terrain profile under the selected polyline. The
maths is in :mod:`georef.profile`; this is the QPainter view plus the wiring:
compute from the current selection, fetch the DEM asynchronously and repaint as
tiles arrive, recompute live when the trace is edited, and export CSV / PNG.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from georef.profile import (
    profile_to_csv,
    sample_profile,
    selected_geopath,
)


def _nice_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    """A short list of round tick values spanning ``[lo, hi]``."""
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / target
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if raw <= step:
            break
    start = math.ceil(lo / step) * step
    ticks, v = [], start
    while v <= hi + 1e-6:
        ticks.append(round(v, 6))
        v += step
    return ticks or [lo, hi]


class ProfileView(QWidget):
    """Paints a :class:`~georef.profile.Profile` — station vs. terrain elevation.

    Auto-exaggerates the vertical scale (a road profile is near-flat to scale)
    and reports the factor, like Google Earth / civil profile sheets.
    """

    _M_LEFT, _M_RIGHT, _M_TOP, _M_BOTTOM = 60, 14, 26, 30

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(170)
        self.setMouseTracking(True)   # hover the profile to read the station
        self._profile = None
        self._message = tr("Select a polyline and click “Profile”.")
        self._plot = None             # last plot geometry, for cursor mapping
        self._cursor_station = None   # station (m) under the mouse, or None

    def set_profile(self, profile) -> None:
        self._profile = profile
        self._message = None
        self.update()

    def set_message(self, text: str) -> None:
        self._profile = None
        self._message = text
        self.update()

    # ---- Progresiva (station) readout on hover ------------------------------
    def mouseMoveEvent(self, ev) -> None:
        if self._plot is None or self._profile is None:
            return
        left, right, length = self._plot["left"], self._plot["right"], self._plot["length"]
        x = ev.position().x()
        if left <= x <= right and right > left:
            self._cursor_station = (x - left) / (right - left) * length
        else:
            self._cursor_station = None
        self.update()

    def leaveEvent(self, _ev) -> None:
        self._cursor_station = None
        self.update()

    def _elevation_at_station(self, s: float):
        """Interpolated elevation at chainage ``s`` from the sampled profile."""
        samples = self._profile.samples
        for a, b in zip(samples, samples[1:]):
            if a.station <= s <= b.station and a.elevation is not None \
                    and b.elevation is not None:
                span = b.station - a.station
                t = (s - a.station) / span if span > 1e-9 else 0.0
                return a.elevation + (b.elevation - a.elevation) * t
        return None

    @staticmethod
    def _fmt_station(s: float) -> str:
        """Civil chainage format, e.g. 1450 m → ``1+450``."""
        return f"{int(s // 1000)}+{s % 1000:06.2f}"

    # ---- Painting -----------------------------------------------------------
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect()
        p.fillRect(r, QColor(247, 248, 250))

        prof = self._profile
        if prof is None or not prof.samples or prof.max_elevation() is None:
            p.setPen(QColor(120, 128, 140))
            msg = self._message or tr("Loading terrain…")
            p.drawText(r, Qt.AlignCenter, msg)
            return

        left = r.left() + self._M_LEFT
        right = r.right() - self._M_RIGHT
        top = r.top() + self._M_TOP
        bottom = r.bottom() - self._M_BOTTOM
        pw, ph = right - left, bottom - top
        if pw < 20 or ph < 20:
            return

        length = prof.length or 1.0
        elo, ehi = prof.min_elevation(), prof.max_elevation()
        if ehi - elo < 1.0:                     # flat: pad so the line isn't a seam
            elo, ehi = elo - 1.0, ehi + 1.0
        pad = (ehi - elo) * 0.1
        elo, ehi = elo - pad, ehi + pad

        def sx(station):
            return left + station / length * pw

        def sy(elev):
            return bottom - (elev - elo) / (ehi - elo) * ph

        # Remember the plot geometry so hover can map cursor → station.
        self._plot = {"left": left, "right": right, "top": top,
                      "bottom": bottom, "length": length, "elo": elo, "ehi": ehi}

        # Grid + labels.
        p.setPen(QPen(QColor(210, 214, 220), 1))
        p.setFont(self.font())
        for e in _nice_ticks(elo, ehi, 5):
            y = sy(e)
            p.setPen(QPen(QColor(222, 226, 232), 1))
            p.drawLine(QPointF(left, y), QPointF(right, y))
            p.setPen(QColor(90, 98, 110))
            p.drawText(r.left() + 4, y + 4, f"{e:.0f}")
        for s in _nice_ticks(0, length, 6):
            x = sx(s)
            p.setPen(QPen(QColor(232, 235, 240), 1))
            p.drawLine(QPointF(x, top), QPointF(x, bottom))
            p.setPen(QColor(90, 98, 110))
            label = f"{s:.0f}" if length < 2000 else f"{s/1000:.2f}k"
            p.drawText(QPointF(x - 10, bottom + 16), label)

        # Terrain fill + line (split across gaps where the DEM is missing).
        runs, cur = [], []
        for smp in prof.samples:
            if smp.elevation is None:
                if cur:
                    runs.append(cur)
                    cur = []
            else:
                cur.append(QPointF(sx(smp.station), sy(smp.elevation)))
        if cur:
            runs.append(cur)
        for run in runs:
            if len(run) < 2:
                continue
            poly = QPolygonF(run + [QPointF(run[-1].x(), bottom),
                                    QPointF(run[0].x(), bottom)])
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(120, 170, 110, 70))
            p.drawPolygon(poly)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(70, 120, 60), 2))
            p.drawPolyline(QPolygonF(run))

        # Axis frame.
        p.setPen(QPen(QColor(150, 156, 165), 1))
        p.drawLine(QPointF(left, top), QPointF(left, bottom))
        p.drawLine(QPointF(left, bottom), QPointF(right, bottom))

        # Header: length, elevation span, vertical exaggeration.
        exag = (ph / (ehi - elo)) / (pw / length) if (ehi - elo) > 0 else 1.0
        span = prof.max_elevation() - prof.min_elevation()
        p.setPen(QColor(60, 66, 76))
        head = tr("Length {len} m · Δh {span} m · gain {gain} m · vert. exag. ×{exag}").format(
            len=f"{length:.0f}", span=f"{span:.1f}",
            gain=f"{prof.total_gain():.1f}", exag=f"{exag:.0f}")
        if not prof.complete:
            head += "  " + tr("(loading DEM…)")
        p.drawText(QPointF(left, top - 8), head)

        # Progresiva cursor: vertical line + station/elevation readout on hover.
        s = self._cursor_station
        if s is not None:
            cx = sx(s)
            elev = self._elevation_at_station(s)
            p.setPen(QPen(QColor(243, 115, 41), 1, Qt.DashLine))
            p.drawLine(QPointF(cx, top), QPointF(cx, bottom))
            if elev is not None:
                cy = sy(elev)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(243, 115, 41))
                p.drawEllipse(QPointF(cx, cy), 3.5, 3.5)
                p.setBrush(Qt.NoBrush)
                label = tr("Prog {sta} · {elev} m").format(
                    sta=self._fmt_station(s), elev=f"{elev:.1f}")
                fm = p.fontMetrics()
                tw = fm.horizontalAdvance(label)
                tx = min(max(cx + 6, left), right - tw)
                p.setPen(QColor(255, 255, 255))
                p.fillRect(int(tx) - 2, top + 1, tw + 4, fm.height() + 2,
                           QColor(243, 115, 41))
                p.drawText(QPointF(tx, top + fm.height() - 1), label)


class ProfileDock(QDockWidget):
    """Bottom dock: the profile view plus compute / export controls."""

    def __init__(self, window) -> None:
        super().__init__(tr("Terrain profile"), window)
        self._window = window
        self.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)

        self._sampler = None
        self._sampler_datum = None
        self._geopath = None      # the GeoPath currently being profiled

        self.view = ProfileView()
        bar = QHBoxLayout()
        btn_profile = QPushButton(tr("Profile selection"))
        btn_profile.clicked.connect(self.compute_from_selection)
        self._csv = QPushButton(tr("Export CSV…"))
        self._csv.clicked.connect(self._export_csv)
        self._png = QPushButton(tr("Export image…"))
        self._png.clicked.connect(self._export_png)
        bar.addWidget(btn_profile)
        bar.addStretch(1)
        bar.addWidget(self._csv)
        bar.addWidget(self._png)

        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(6, 4, 6, 6)
        col.addLayout(bar)
        col.addWidget(self.view, 1)
        self.setWidget(inner)

    # ---- Compute ------------------------------------------------------------
    def _ensure_sampler(self, datum):
        """(Re)build the DEM sampler when the datum changes."""
        if self._sampler is not None and self._sampler_datum is datum:
            return self._sampler
        from georef.dem import DEMSampler
        self._sampler = DEMSampler(datum, parent=self)
        self._sampler_datum = datum
        self._sampler.changed.connect(self._recompute)
        return self._sampler

    def compute_from_selection(self) -> None:
        scene = self._window.viewport.scene
        datum = getattr(scene, "georef", None)
        if datum is None:
            self.view.set_message(tr("Set a base map location first (Tray ▸ Base map)."))
            return
        path = selected_geopath(scene)
        if path is None or len(path.points) < 2:
            self.view.set_message(tr(
                "Trace a path with the Path tool (T), select it, then run "
                "Profile."))
            self._geopath = None
            return
        self._ensure_sampler(datum)
        self._geopath = path
        self.view.set_message(tr("Loading terrain…"))
        self._recompute()

    def _recompute(self) -> None:
        if self._geopath is None or self._sampler is None:
            return
        profile = sample_profile(self._geopath.profile_points(), self._sampler)
        self.view.set_profile(profile)

    def on_scene_changed(self) -> None:
        """Live update: re-profile the active path when its nodes move.

        Only while the dock is visible and a path is being profiled — moving a
        node bumps the scene version and reshapes the profile in place.
        """
        if not self.isVisible() or self._geopath is None:
            return
        scene = self._window.viewport.scene
        if self._geopath in scene.geo_paths and self._sampler is not None:
            self._recompute()

    # ---- Export -------------------------------------------------------------
    def _export_csv(self) -> None:
        if self._geopath is None or self._sampler is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Export profile CSV"), "profile.csv", "CSV (*.csv)")
        if not path:
            return
        profile = sample_profile(self._geopath.profile_points(), self._sampler)
        with open(path, "w", encoding="utf-8") as f:
            f.write(profile_to_csv(profile))

    def _export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Export profile image"), "profile.png", "PNG (*.png)")
        if path:
            self.view.grab().save(path)
