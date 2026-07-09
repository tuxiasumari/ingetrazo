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
    polyline_from_selection,
    profile_to_csv,
    sample_profile,
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
        self._profile = None
        self._message = tr("Select a polyline and click “Profile”.")

    def set_profile(self, profile) -> None:
        self._profile = profile
        self._message = None
        self.update()

    def set_message(self, text: str) -> None:
        self._profile = None
        self._message = text
        self.update()

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


class ProfileDock(QDockWidget):
    """Bottom dock: the profile view plus compute / export controls."""

    def __init__(self, window) -> None:
        super().__init__(tr("Terrain profile"), window)
        self._window = window
        self.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)

        self._sampler = None
        self._sampler_datum = None
        self._polyline = None

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
        poly = polyline_from_selection(scene)
        if poly is None or len(poly) < 2:
            self.view.set_message(tr(
                "Draw a path with the Line tool (L), select one of its "
                "segments, then run Profile."))
            self._polyline = None
            return
        self._ensure_sampler(datum)
        self._polyline = poly
        self.view.set_message(tr("Loading terrain…"))
        self._recompute()

    def _recompute(self) -> None:
        if not self._polyline or self._sampler is None:
            return
        profile = sample_profile(self._polyline, self._sampler)
        self.view.set_profile(profile)

    def on_scene_changed(self) -> None:
        """Live update: re-profile the current selection when the trace edits.

        Only while the dock is visible and a polyline is being profiled; the
        selection is re-read so a moved vertex reshapes the profile.
        """
        if not self.isVisible() or self._polyline is None:
            return
        scene = self._window.viewport.scene
        datum = getattr(scene, "georef", None)
        if datum is None:
            return
        poly = polyline_from_selection(scene)
        if poly is not None and len(poly) >= 2:
            self._ensure_sampler(datum)
            self._polyline = poly
            self._recompute()

    # ---- Export -------------------------------------------------------------
    def _export_csv(self) -> None:
        if not self._polyline or self._sampler is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Export profile CSV"), "profile.csv", "CSV (*.csv)")
        if not path:
            return
        profile = sample_profile(self._polyline, self._sampler)
        with open(path, "w", encoding="utf-8") as f:
            f.write(profile_to_csv(profile))

    def _export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Export profile image"), "profile.png", "PNG (*.png)")
        if path:
            self.view.grab().save(path)
