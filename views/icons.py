# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Toolbar icons, drawn programmatically with QPainter (Track: UI).

The drawing tools are geometric primitives, so their icons *are* those shapes —
a line is a line, a rectangle a rectangle. Drawing them ourselves keeps the set
perfectly consistent, theme-aware (ink follows the palette), tiny, and free of
any third-party icon licence. No SVG files, no QtSvg, no assets.

``tool_icon(key)`` returns a :class:`QIcon` for a tool/nav key, or a null icon
for an unknown key (the action keeps its text label).
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QApplication

_PX = 48          # render size (QIcon scales down; big = crisp on HiDPI)
_M = 8.0          # margin — drawing happens in [_M, _PX-_M]


def _ink() -> QColor:
    """Icon ink colour — follows the current palette's text colour so the icons
    read on light and dark themes alike."""
    app = QApplication.instance()
    if app is not None:
        c = app.palette().windowText().color()
        # Nudge toward a medium ink so lines aren't harsh pure black.
        return QColor(c.red(), c.green(), c.blue())
    return QColor(40, 44, 52)


def _canvas():
    pm = QPixmap(_PX, _PX)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    ink = _ink()
    pen = QPen(ink, 3.0)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundStyle if hasattr(Qt, "RoundStyle") else Qt.RoundCap)
    p.setPen(pen)
    return pm, p, ink


def _accent() -> QColor:
    return QColor(243, 115, 41)   # IngeTrazo orange, for endpoint dots / handles


# ---- Per-tool drawings ---------------------------------------------------------
# Each takes (painter, ink) and draws into the _M.._PX-_M box.

def _dot(p, x, y, r=3.2, color=None):
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(color or _accent())
    p.drawEllipse(QPointF(x, y), r, r)
    p.restore()


def _select(p, ink):
    # Arrow cursor.
    path = QPainterPath()
    path.moveTo(16, 12)
    path.lineTo(16, 36)
    path.lineTo(23, 29)
    path.lineTo(28, 39)
    path.lineTo(32, 37)
    path.lineTo(27, 27)
    path.lineTo(36, 27)
    path.closeSubpath()
    p.setBrush(QBrush(ink))
    p.drawPath(path)


def _line(p, ink):
    p.drawLine(QPointF(12, 36), QPointF(36, 12))
    _dot(p, 12, 36)
    _dot(p, 36, 12)


def _rectangle(p, ink):
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(12, 14, 24, 20))


def _rotated_rect(p, ink):
    poly = QPolygonF([QPointF(12, 26), QPointF(26, 12),
                      QPointF(36, 22), QPointF(22, 36)])
    p.setBrush(Qt.NoBrush)
    p.drawPolygon(poly)


def _circle(p, ink):
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(24, 24), 13, 13)


def _polygon(p, ink):
    pts = []
    for i in range(6):
        a = math.radians(60 * i - 30)
        pts.append(QPointF(24 + 13 * math.cos(a), 24 + 13 * math.sin(a)))
    p.setBrush(Qt.NoBrush)
    p.drawPolygon(QPolygonF(pts))


def _arc(p, ink):
    path = QPainterPath()
    path.moveTo(12, 34)
    path.quadTo(24, 6, 36, 34)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    _dot(p, 12, 34)
    _dot(p, 36, 34)


def _arc3(p, ink):
    _arc(p, ink)
    _dot(p, 24, 15)


def _rotate(p, ink):
    # Protractor: a swept arc with an arrowhead around a centre dot.
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(11, 11, 26, 26), 210 * 16, -240 * 16)
    p.drawLine(QPointF(33.5, 11.5), QPointF(38, 14))
    p.drawLine(QPointF(33.5, 11.5), QPointF(33, 17))
    _dot(p, 24, 24)


def _center_arc(p, ink):
    # Compass arc: centre dot, radius arm, swept arc.
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(10, 10, 28, 28), 0, 105 * 16)
    p.drawLine(QPointF(24, 24), QPointF(38, 24))
    _dot(p, 24, 24)
    _dot(p, 38, 24, 2.6)
    _dot(p, 17, 12, 2.6)



def _scale(p, ink):
    # A small square growing to a large one along a diagonal arrow.
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(11, 27, 10, 10))
    p.drawRect(QRectF(17, 11, 20, 20))
    p.drawLine(QPointF(14, 34), QPointF(33, 15))
    p.drawLine(QPointF(33, 15), QPointF(27, 15))
    p.drawLine(QPointF(33, 15), QPointF(33, 21))



def _followme(p, ink):
    # A small profile square swept along a curved path.
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    path.moveTo(12, 36)
    path.quadTo(14, 16, 36, 14)
    p.drawPath(path)
    p.save()
    p.translate(12, 36)
    p.rotate(-75)
    p.drawRect(QRectF(-4.5, -4.5, 9, 9))
    p.restore()
    _dot(p, 36, 14)



def _protractor(p, ink):
    # A half-circle protractor with tick marks and an angled guide arm.
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(10, 10, 28, 28), 0, 180 * 16)
    p.drawLine(QPointF(10, 24), QPointF(38, 24))       # the base
    import math as _m
    for adeg in (30, 60, 90, 120, 150):
        a = _m.radians(adeg)
        x1, y1 = 24 + 11 * _m.cos(a), 24 - 11 * _m.sin(a)
        x2, y2 = 24 + 14 * _m.cos(a), 24 - 14 * _m.sin(a)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    pen = p.pen()
    pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.drawLine(QPointF(24, 24), QPointF(40, 11))       # the angled guide
    pen.setStyle(Qt.SolidLine)
    p.setPen(pen)
    _dot(p, 24, 24)


def _pushpull(p, ink):
    # A face with an up arrow (extrude).
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(12, 26, 18, 12))
    p.drawLine(QPointF(21, 26), QPointF(21, 10))
    p.drawLine(QPointF(21, 10), QPointF(16, 16))
    p.drawLine(QPointF(21, 10), QPointF(26, 16))


def _offset(p, ink):
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(10, 12, 28, 24))
    p.drawRect(QRectF(16, 18, 16, 12))


def _move(p, ink):
    p.drawLine(QPointF(24, 10), QPointF(24, 38))
    p.drawLine(QPointF(10, 24), QPointF(38, 24))
    for (x, y, dx1, dy1, dx2, dy2) in (
        (24, 10, -4, 5, 4, 5), (24, 38, -4, -5, 4, -5),
        (10, 24, 5, -4, 5, 4), (38, 24, -5, -4, -5, 4)):
        p.drawLine(QPointF(x, y), QPointF(x + dx1, y + dy1))
        p.drawLine(QPointF(x, y), QPointF(x + dx2, y + dy2))


def _paint(p, ink):
    # A bucket.
    p.setBrush(Qt.NoBrush)
    body = QPainterPath()
    body.moveTo(14, 20)
    body.lineTo(34, 20)
    body.lineTo(30, 38)
    body.lineTo(18, 38)
    body.closeSubpath()
    p.drawPath(body)
    p.drawLine(QPointF(24, 20), QPointF(24, 12))
    _dot(p, 30, 40, 2.6)


def _dimension(p, ink):
    p.drawLine(QPointF(12, 30), QPointF(36, 30))
    p.drawLine(QPointF(12, 24), QPointF(12, 36))
    p.drawLine(QPointF(36, 24), QPointF(36, 36))


def _geopath(p, ink):
    pen = p.pen()
    pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.drawPolyline(QPolygonF([QPointF(11, 34), QPointF(20, 16),
                              QPointF(30, 30), QPointF(38, 14)]))
    pen.setStyle(Qt.SolidLine)
    p.setPen(pen)
    for x, y in ((11, 34), (20, 16), (30, 30), (38, 14)):
        _dot(p, x, y, 2.8)


def _orbit(p, ink):
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(11, 11, 26, 26), 40 * 16, 280 * 16)
    p.drawLine(QPointF(33, 12), QPointF(37, 16))
    p.drawLine(QPointF(37, 16), QPointF(31, 18))


def _pan(p, ink):
    # Four-way arrows, thinner than move.
    _move(p, ink)


def _eraser(p, ink):
    # A tilted eraser block with a swipe line under it.
    p.save()
    p.translate(24, 22)
    p.rotate(-35)
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(-11, -6, 22, 12))
    p.drawLine(QPointF(-3, -6), QPointF(-3, 6))   # the sleeve
    p.restore()
    pen = p.pen()
    pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.drawLine(QPointF(12, 38), QPointF(36, 38))
    pen.setStyle(Qt.SolidLine)
    p.setPen(pen)


def _tape(p, ink):
    # A tape-measure body with the tape pulled out and a hook.
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(18, 20), 8.5, 8.5)
    p.drawEllipse(QPointF(18, 20), 2.6, 2.6)
    p.drawLine(QPointF(18, 28.5), QPointF(38, 28.5))   # the tape
    p.drawLine(QPointF(38, 25.5), QPointF(38, 31.5))   # end hook
    for x in (24, 29, 34):                              # tick marks
        p.drawLine(QPointF(x, 28.5), QPointF(x, 25.8))


def _zoom_extents(p, ink):
    # Corner brackets framing the extent (fit-to-view).
    for (cx, cy, sx, sy) in ((13, 13, 1, 1), (35, 13, -1, 1),
                             (35, 35, -1, -1), (13, 35, 1, -1)):
        p.drawLine(QPointF(cx, cy), QPointF(cx + 7 * sx, cy))
        p.drawLine(QPointF(cx, cy), QPointF(cx, cy + 7 * sy))


def _iso_cube():
    """Iso-cube geometry: outer hexagon + the 3 visible-face split points.

    Returns ``(hexagon_points, centre, top_face, left_face, right_face)`` where
    each face is a 4-point polygon of the cube's visible top/left/right face."""
    pts = []
    for i in range(6):
        a = math.radians(60 * i - 90)
        pts.append(QPointF(24 + 14 * math.cos(a), 24 + 14 * math.sin(a)))
    c = QPointF(24, 24)
    # Hexagon vertices (i): 0 top, 1 up-right, 2 down-right, 3 bottom,
    # 4 down-left, 5 up-left. Faces meet at the centre.
    top = QPolygonF([pts[5], pts[0], pts[1], c])
    right = QPolygonF([pts[1], pts[2], pts[3], c])
    left = QPolygonF([pts[3], pts[4], pts[5], c])
    return pts, c, top, left, right


def _view_iso(p, ink):
    pts, c, _, _, _ = _iso_cube()
    p.setBrush(Qt.NoBrush)
    p.drawPolygon(QPolygonF(pts))
    for i in (0, 2, 4):
        p.drawLine(c, pts[i])


def _view_cube(face, filled):
    """Cube-view icon: highlight one of the 3 iso-visible faces (top/left/right).

    ``filled`` marks the near view (solid accent face); a hollow accent outline
    marks the opposite/far view — so each axis pair (top↔bottom, front↔back,
    right↔left) is distinct and reads consistently."""
    def draw(p, ink):
        pts, c, top, left, right = _iso_cube()
        poly = {"top": top, "left": left, "right": right}[face]
        p.save()
        if filled:
            p.setPen(Qt.NoPen)
            p.setBrush(_accent())
        else:
            pen = QPen(_accent(), 2.4)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
        p.drawPolygon(poly)
        p.restore()
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(QPolygonF(pts))
        for i in (0, 2, 4):
            p.drawLine(c, pts[i])
    return draw


def _text(p, ink):
    # An "A" with a leader line pointing down-left (SketchUp's Text).
    f = p.font()
    f.setPixelSize(24)
    f.setBold(True)
    p.setFont(f)
    p.drawText(QPointF(20, 26), "A")
    p.drawLine(QPointF(10, 38), QPointF(19, 29))
    p.drawEllipse(QPointF(10, 38), 2.0, 2.0)


def _text3d(p, ink):
    # An extruded "A": the glyph plus a depth-shifted echo.
    f = p.font()
    f.setPixelSize(26)
    f.setBold(True)
    p.setFont(f)
    pen = p.pen()
    faded = QPen(pen)
    c = QColor(ink)
    c.setAlpha(110)
    faded.setColor(c)
    p.setPen(faded)
    p.drawText(QPointF(19, 25), "A")
    p.setPen(pen)
    p.drawText(QPointF(13, 33), "A")


_DRAW = {
    "select": _select, "line": _line, "rectangle": _rectangle,
    "rotated_rect": _rotated_rect, "circle": _circle, "polygon": _polygon,
    "arc": _arc, "arc3": _arc3, "center_arc": _center_arc,
    "rotate": _rotate, "scale": _scale, "followme": _followme, "pushpull": _pushpull, "offset": _offset,
    "move": _move, "paint": _paint, "dimension": _dimension,
    "geopath": _geopath, "orbit": _orbit, "pan": _pan,
    "text": _text, "text3d": _text3d,
    "eraser": _eraser, "tape": _tape, "protractor": _protractor,
    "zoom_extents": _zoom_extents, "view_iso": _view_iso,
    # Standard views — solid face = near, hollow face = opposite.
    "view_top": _view_cube("top", True),
    "view_bottom": _view_cube("top", False),
    "view_front": _view_cube("left", True),
    "view_back": _view_cube("left", False),
    "view_right": _view_cube("right", True),
    "view_left": _view_cube("right", False),
}


def tool_icon(key: str) -> QIcon:
    """Programmatic :class:`QIcon` for a tool/nav ``key`` (null if unknown)."""
    draw = _DRAW.get(key)
    if draw is None:
        return QIcon()
    pm, p, ink = _canvas()
    draw(p, ink)
    p.end()
    return QIcon(pm)
