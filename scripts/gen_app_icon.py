# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Generate the IngeTrazo application icon — the author's SUMARI mark,
re-vectorised.

The mark Marco designed for his practice: three chevron blades in brand blue
forming a triangle with 120° rotational symmetry, a white isometric cube at
the centre. The geometry here was TRACED from his original artwork
(boundary-follow + Douglas-Peucker on the raster; each piece turned out to be
a clean 6-vertex chevron whose slit edge lies exactly on the symmetry axis
and whose outer edge sits at a perfect 60°), then symmetrised — so this
renders his design crisp at every size, free of JPEG artefacts.

    python scripts/gen_app_icon.py

Writes ``resources/icons/ingetrazo_<size>.png`` (16..512, transparent).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
    QPolygonF,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "resources" / "icons"
SIZES = (512, 256, 128, 64, 48, 32, 16)

BLUE = QColor(54, 137, 231)        # sampled from the original artwork
CUBE_FILL = QColor(252, 252, 253)
CUBE_LINE = QColor(150, 156, 166)

# One chevron blade, traced from the original (units: mark radius = 300,
# origin at the mark centre; y grows downward). Slit edge on the +Y axis,
# outer edge at exactly 60°.
_PIECE = [
    (0.0, -300.0),     # apex tip
    (0.0, -71.0),      # slit edge, down the symmetry axis
    (-36.0, -50.0),    # short-limb end cut
    (-36.0, -164.0),   # inner slit edge back up
    (-161.0, 51.0),    # inner side of the long limb (parallel to the outer)
    (-224.0, 88.0),    # far tip
]
_DESIGN_R = 300.0
_MARGIN = 1.06                     # small breathing room inside the canvas


def _rot(x: float, y: float, deg: float) -> tuple[float, float]:
    a = math.radians(deg)
    return (x * math.cos(a) - y * math.sin(a),
            x * math.sin(a) + y * math.cos(a))


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = size / (2.0 * _DESIGN_R * _MARGIN)
    cx = size * 0.5
    # Optical centring: the triangle's bbox spans y ∈ [-300, +150] around the
    # centroid, so shift down half the difference to centre the visual mass.
    cy = size * 0.5 + 75.0 * s

    p.setPen(Qt.NoPen)
    p.setBrush(BLUE)
    for k in range(3):
        pts = []
        for x, y in _PIECE:
            rx, ry = _rot(x, y, 120 * k)
            pts.append(QPointF(cx + rx * s, cy + ry * s))
        p.drawPolygon(QPolygonF(pts))

    # Centre cube: white iso cube — enlarged from the original artwork's
    # ≈0.21 R to 0.32 R so it reads at dock sizes (author's call).
    r = 96.0 * s
    hexa = []
    for i in range(6):
        a = math.radians(60 * i - 90)
        hexa.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    p.setBrush(CUBE_FILL)
    pen = QPen(CUBE_LINE, max(0.7, 2.2 * s * 2.4))
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawPolygon(QPolygonF(hexa))
    for i in (0, 2, 4):
        p.drawLine(QPointF(cx, cy), hexa[i])

    p.end()
    return img


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        draw(size).save(str(OUT / f"ingetrazo_{size}.png"))
        print("wrote", f"ingetrazo_{size}.png")
    # Refresh the master too, at high resolution.
    draw(1024).save(str(OUT / "ingetrazo_master.png"))
    print("master refreshed (1024, vector-clean)")


if __name__ == "__main__":
    main()
