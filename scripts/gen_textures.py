# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Generate the bundled texture library (procedural, tileable, ours).

SketchUp ships a categorised material library; its images are copyrighted, so
IngeTrazo's are generated here with QPainter — deterministic (fixed seed),
seamlessly tileable (every speckle/line is drawn with toroidal wrap), and
licence-clean. Re-run to regenerate:

    python scripts/gen_textures.py

Writes ``resources/textures/library/<category>/<name>.png`` (256 px) and the
manifest ``resources/textures/library.json`` with per-texture real-world tile
sizes (metres) and category/display names (translated at display time).
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPen

PX = 256
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "resources" / "textures" / "library"

rng = random.Random(20260711)


def _canvas(base) -> tuple[QImage, QPainter]:
    img = QImage(PX, PX, QImage.Format_RGB32)
    img.fill(QColor(*base))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    return img, p


def _wrap_rect(p, x, y, w, h, color):
    """Fill a rect with toroidal wrap so the tile stays seamless."""
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    for dx in (-PX, 0, PX):
        for dy in (-PX, 0, PX):
            p.drawRect(QRectF(x + dx, y + dy, w, h))


def _speckle(p, count, size, colors, alpha=255):
    for _ in range(count):
        x, y = rng.uniform(0, PX), rng.uniform(0, PX)
        s = rng.uniform(size * 0.5, size * 1.5)
        c = QColor(*rng.choice(colors))
        c.setAlpha(alpha)
        _wrap_rect(p, x, y, s, s, c)


def _tone(base, spread):
    """Luminance-only variation: ONE delta for all channels (independent
    per-channel noise reads as pastel hue shifts on bricks/stone)."""
    d = rng.randint(-spread, spread)
    return [min(255, max(0, v + d)) for v in base]


def _hline(p, y, color, width=1.0):
    p.setPen(QPen(QColor(*color), width))
    p.drawLine(QPointF(0, y), QPointF(PX, y))


def _vline(p, x, color, width=1.0):
    p.setPen(QPen(QColor(*color), width))
    p.drawLine(QPointF(x, 0), QPointF(x, PX))


# ---- Recipes ------------------------------------------------------------------

def brick(base, mortar, dark):
    img, p = _canvas(mortar)
    rows, cols = 8, 4
    bh, bw, gap = PX / rows, PX / cols, 3
    for r in range(rows):
        off = (bw / 2) if r % 2 else 0
        for c in range(-1, cols + 1):
            tone = _tone(base, 14)
            x, y = c * bw + off, r * bh
            _wrap_rect(p, x + gap / 2, y + gap / 2, bw - gap, bh - gap,
                       QColor(*tone))
    _speckle(p, 300, 2, [dark], alpha=40)
    p.end()
    return img


def concrete(base, speck_dark, speck_light, panels=False):
    img, p = _canvas(base)
    _speckle(p, 900, 2, [speck_dark], alpha=28)
    _speckle(p, 600, 2, [speck_light], alpha=30)
    if panels:
        for y in (0, PX / 2):
            _hline(p, y, speck_dark, 2)
        _vline(p, 0, speck_dark, 2)
        for cx, cy in ((PX / 4, PX / 4), (3 * PX / 4, PX / 4),
                       (PX / 4, 3 * PX / 4), (3 * PX / 4, 3 * PX / 4)):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(*speck_dark))
            p.drawEllipse(QPointF(cx, cy), 3.5, 3.5)
    p.end()
    return img


def blocks(base, joint):
    img, p = _canvas(joint)
    rows, cols = 4, 2
    bh, bw, gap = PX / rows, PX / cols, 4
    for r in range(rows):
        off = (bw / 2) if r % 2 else 0
        for c in range(-1, cols + 1):
            tone = _tone(base, 8)
            _wrap_rect(p, c * bw + off + gap / 2, r * bh + gap / 2,
                       bw - gap, bh - gap, QColor(*tone))
    _speckle(p, 500, 2, [(90, 90, 92)], alpha=22)
    p.end()
    return img


def ashlar(base, joint):
    img, p = _canvas(joint)
    rng2 = random.Random(7)
    rows = 4
    bh = PX / rows
    for r in range(rows):
        x = -rng2.uniform(0, 40)
        while x < PX:
            w = rng2.uniform(45, 95)
            d = rng2.randint(-16, 16)
            tone = [min(255, max(0, v + d)) for v in base]
            _wrap_rect(p, x + 2, r * bh + 2, w - 4, bh - 4, QColor(*tone))
            x += w
    _speckle(p, 400, 2, [(70, 66, 60)], alpha=30)
    p.end()
    return img


def wood(base, grain, dark):
    img, p = _canvas(base)
    planks = 6
    pw = PX / planks
    for i in range(planks):
        tone = _tone(base, 12)
        _wrap_rect(p, i * pw + 1, 0, pw - 2, PX, QColor(*tone))
        for _ in range(7):
            x = i * pw + rng.uniform(3, pw - 3)
            c = QColor(*grain)
            c.setAlpha(70)
            p.setPen(QPen(c, rng.uniform(0.6, 1.4)))
            p.drawLine(QPointF(x, -PX), QPointF(x + rng.uniform(-6, 6), 2 * PX))
        _vline(p, i * pw, dark, 1.6)
    p.end()
    return img


def roof_colonial(base, shade):
    img, p = _canvas(shade)
    rows, cols = 4, 4
    th, tw = PX / rows, PX / cols
    for r in range(rows):
        off = (tw / 2) if r % 2 else 0
        for c in range(-1, cols + 1):
            tone = _tone(base, 14)
            for dx in (-PX, 0, PX):
                rect = QRectF(c * tw + off + dx, r * th - th * 0.15,
                              tw - 2, th * 1.15)
                p.setPen(QPen(QColor(*shade), 2))
                p.setBrush(QColor(*tone))
                p.drawRoundedRect(rect, tw * 0.45, th * 0.35)
    p.end()
    return img


def corrugated(base, dark, light):
    img, p = _canvas(base)
    waves = 8
    ww = PX / waves
    for i in range(waves):
        g = QLinearGradient(i * ww, 0, (i + 1) * ww, 0)
        g.setColorAt(0.0, QColor(*dark))
        g.setColorAt(0.5, QColor(*light))
        g.setColorAt(1.0, QColor(*dark))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawRect(QRectF(i * ww, 0, ww, PX))
    p.end()
    return img


def floor_tiles(base, joint, n):
    img, p = _canvas(joint)
    tw = PX / n
    for r in range(n):
        for c in range(n):
            tone = _tone(base, 6)
            _wrap_rect(p, c * tw + 1.5, r * tw + 1.5, tw - 3, tw - 3,
                       QColor(*tone))
    p.end()
    return img


def checker(a, b, n=4):
    img, p = _canvas(a)
    tw = PX / n
    for r in range(n):
        for c in range(n):
            if (r + c) % 2:
                _wrap_rect(p, c * tw, r * tw, tw, tw, QColor(*b))
    p.end()
    return img


def pavers(base, joint):
    img, p = _canvas(joint)
    rows, cols = 8, 4
    bh, bw = PX / rows, PX / cols
    for r in range(rows):
        off = (bw / 2) if r % 2 else 0
        for c in range(-1, cols + 1):
            tone = _tone(base, 12)
            _wrap_rect(p, c * bw + off + 1.5, r * bh + 1.5,
                       bw - 3, bh - 3, QColor(*tone))
    _speckle(p, 400, 2, [(96, 96, 96)], alpha=26)
    p.end()
    return img


def noise(base, tones, count=1400, size=3):
    img, p = _canvas(base)
    _speckle(p, count, size, tones, alpha=60)
    p.end()
    return img


def metal_brushed(base, line):
    img, p = _canvas(base)
    for _ in range(220):
        y = rng.uniform(0, PX)
        c = QColor(*line)
        c.setAlpha(rng.randint(14, 40))
        p.setPen(QPen(c, rng.uniform(0.5, 1.2)))
        p.drawLine(QPointF(0, y), QPointF(PX, y))
    p.end()
    return img


def glass(base, hi):
    img, p = _canvas(base)
    g = QLinearGradient(0, 0, PX, PX)
    g.setColorAt(0.0, QColor(*hi))
    g.setColorAt(0.5, QColor(*base))
    g.setColorAt(1.0, QColor(*hi))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRect(QRectF(0, 0, PX, PX))
    p.end()
    return img


# category → [(name, recipe, tile_w, tile_h)]
LIBRARY = {
    "brick": [
        ("brick_red", lambda: brick((150, 62, 46), (196, 188, 178), (60, 24, 18)), 1.0, 0.6),
        ("brick_clay", lambda: brick((186, 120, 78), (204, 196, 184), (92, 52, 30)), 1.0, 0.6),
        ("brick_gray", lambda: brick((136, 134, 130), (190, 188, 184), (66, 64, 62)), 1.0, 0.6),
    ],
    "concrete": [
        ("concrete_smooth", lambda: concrete((178, 178, 176), (120, 120, 120), (210, 210, 208)), 1.5, 1.5),
        ("concrete_exposed", lambda: concrete((168, 168, 166), (110, 110, 110), (205, 205, 203), panels=True), 1.2, 1.2),
        ("concrete_blocks", lambda: blocks((172, 172, 170), (140, 140, 138)), 0.8, 0.8),
    ],
    "stone": [
        ("stone_ashlar", lambda: ashlar((158, 148, 132), (110, 102, 90)), 1.2, 0.9),
        ("stone_cobble", lambda: pavers((142, 136, 126), (98, 94, 86)), 0.8, 0.8),
    ],
    "wood": [
        ("wood_planks_light", lambda: wood((198, 162, 116), (150, 112, 70), (140, 106, 66)), 1.0, 1.0),
        ("wood_planks_dark", lambda: wood((124, 86, 56), (86, 56, 34), (70, 46, 28)), 1.0, 1.0),
    ],
    "roof": [
        ("roof_tiles_colonial", lambda: roof_colonial((176, 96, 66), (110, 56, 38)), 0.9, 0.6),
        ("roof_corrugated", lambda: corrugated((172, 176, 180), (120, 124, 130), (216, 220, 224)), 0.9, 0.9),
    ],
    "floor": [
        ("floor_ceramic_white", lambda: floor_tiles((228, 226, 220), (180, 178, 172), 2), 0.6, 0.6),
        ("floor_ceramic_gray", lambda: floor_tiles((176, 178, 180), (130, 132, 134), 2), 0.6, 0.6),
        ("floor_checker", lambda: checker((232, 230, 226), (52, 52, 56)), 0.8, 0.8),
        ("floor_pavers", lambda: pavers((168, 148, 128), (120, 104, 88)), 0.8, 0.8),
    ],
    "metal": [
        ("metal_brushed", lambda: metal_brushed((176, 180, 186), (120, 124, 130)), 1.0, 1.0),
        ("metal_dark", lambda: metal_brushed((92, 96, 102), (50, 54, 60)), 1.0, 1.0),
    ],
    "ground": [
        ("grass", lambda: noise((96, 138, 74), [(70, 110, 52), (120, 160, 90), (84, 124, 62)]), 1.5, 1.5),
        ("gravel", lambda: noise((166, 160, 150), [(120, 114, 104), (196, 190, 180), (140, 134, 124)], count=1800), 1.0, 1.0),
        ("asphalt", lambda: noise((72, 72, 74), [(50, 50, 52), (98, 98, 100), (60, 60, 62)], count=2000, size=2), 1.5, 1.5),
    ],
    "glass": [
        ("glass_blue", lambda: glass((168, 198, 214), (208, 228, 238)), 1.0, 1.0),
    ],
}


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841
    manifest: dict = {"categories": []}
    for cat, entries in LIBRARY.items():
        (OUT / cat).mkdir(parents=True, exist_ok=True)
        items = []
        for name, recipe, sw, sh in entries:
            img = recipe()
            rel = f"{cat}/{name}.png"
            img.save(str(OUT / rel))
            items.append({"file": rel, "name": name, "sw": sw, "sh": sh})
            print("wrote", rel)
        manifest["categories"].append({"id": cat, "items": items})
    (ROOT / "resources" / "textures" / "library.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")
    print("manifest ok:",
          sum(len(c["items"]) for c in manifest["categories"]), "textures")


if __name__ == "__main__":
    main()
