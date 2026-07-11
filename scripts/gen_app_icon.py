# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Generate the IngeTrazo application icon (programmatic, licence-clean).

The mark IS the thesis: a hand-drawn orange TRAZO (stroke) laying down the
front edge of an isometric cube — drawing like on paper, becoming a solid.
Deep blue-grey rounded tile, paper-white wireframe, IngeTrazo-orange stroke.

    python scripts/gen_app_icon.py

Writes ``resources/icons/ingetrazo_<size>.png`` (16..512) — used as the
window icon and, at packaging time, for the .desktop entry.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "resources" / "icons"
SIZES = (512, 256, 128, 64, 48, 32, 16)

BG_TOP = QColor(52, 68, 92)       # deep blue-grey
BG_BOT = QColor(33, 44, 62)
INK = QColor(238, 240, 244)       # paper white
ORANGE = QColor(243, 115, 41)     # IngeTrazo orange


def _cube_points(cx, cy, r):
    """Isometric cube: hexagon corners (flat-top at ±90°) + centre."""
    pts = []
    for i in range(6):
        a = math.radians(60 * i - 90)
        pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts, QPointF(cx, cy)


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = size / 256.0

    # Rounded tile with a vertical gradient.
    g = QLinearGradient(0, 0, 0, size)
    g.setColorAt(0.0, BG_TOP)
    g.setColorAt(1.0, BG_BOT)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawRoundedRect(QRectF(0, 0, size, size), 56 * s, 56 * s)

    # Isometric cube wireframe, slightly high to leave room for the stroke.
    pts, c = _cube_points(128 * s, 118 * s, 78 * s)
    pen = QPen(INK, max(1.0, 10 * s))
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    hexa = QPainterPath()
    hexa.moveTo(pts[0])
    for q in pts[1:]:
        hexa.lineTo(q)
    hexa.closeSubpath()
    p.drawPath(hexa)
    for i in (0, 2, 4):
        p.drawLine(c, pts[i])

    # Subtle face tint on the top face so the volume reads at small sizes.
    top_face = QPainterPath()
    top_face.moveTo(pts[5])
    top_face.lineTo(pts[0])
    top_face.lineTo(pts[1])
    top_face.lineTo(c)
    top_face.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 26))
    p.drawPath(top_face)

    # The TRAZO: a freehand orange stroke sweeping in and laying down the
    # cube's front-bottom edge, ending in the draw-point dot.
    stroke = QPainterPath()
    start = QPointF(34 * s, 224 * s)
    stroke.moveTo(start)
    stroke.cubicTo(QPointF(78 * s, 236 * s), QPointF(96 * s, 210 * s),
                   QPointF(pts[4].x(), pts[4].y()))
    stroke.lineTo(pts[3])
    pen = QPen(ORANGE, max(1.5, 16 * s))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawPath(stroke)
    p.setPen(Qt.NoPen)
    p.setBrush(ORANGE)
    p.drawEllipse(pts[3], 13 * s, 13 * s)
    p.setBrush(QColor(255, 255, 255, 220))
    p.drawEllipse(pts[3], 5 * s, 5 * s)

    p.end()
    return img


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        img = draw(size)
        img.save(str(OUT / f"ingetrazo_{size}.png"))
        print("wrote", f"ingetrazo_{size}.png")


if __name__ == "__main__":
    main()
