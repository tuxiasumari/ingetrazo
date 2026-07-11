# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Generate the bundled starter components (procedural, licence-clean).

SketchUp ships a scale figure, trees and props; IngeTrazo's equivalents are
built here as low-poly OBJ+MTL — a 1.75 m scale person (flat cutout, the
architectural classic), a deciduous tree, a bush and a sedan — real metres,
Z-up, coloured via Kd materials so our own OBJ importer brings them in ready.

    python scripts/gen_components.py

Writes ``resources/components/<name>.obj`` (+ .mtl).
"""
from __future__ import annotations

import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "resources" / "components"


class Obj:
    def __init__(self, name: str) -> None:
        self.name = name
        self.v: list[tuple] = []
        self.faces: list[tuple[str, list[int]]] = []   # (material, 1-based idx)
        self.mats: dict[str, tuple] = {}

    def add_v(self, x, y, z) -> int:
        self.v.append((x, y, z))
        return len(self.v)

    def face(self, mat: str, idxs) -> None:
        self.faces.append((mat, list(idxs)))

    def poly_prism(self, mat: str, outline, y0: float, y1: float) -> None:
        """Extrude a flat XZ outline along Y: front, back and side walls."""
        front = [self.add_v(x, y0, z) for x, z in outline]
        back = [self.add_v(x, y1, z) for x, z in outline]
        self.face(mat, front)
        self.face(mat, list(reversed(back)))
        n = len(outline)
        for i in range(n):
            j = (i + 1) % n
            self.face(mat, [front[i], front[j], back[j], back[i]])

    def ring(self, r: float, z: float, n: int = 8, squash: float = 1.0):
        return [self.add_v(r * math.cos(2 * math.pi * k / n),
                           r * squash * math.sin(2 * math.pi * k / n), z)
                for k in range(n)]

    def loft(self, mat: str, ring_a, ring_b) -> None:
        n = len(ring_a)
        for i in range(n):
            j = (i + 1) % n
            self.face(mat, [ring_a[i], ring_a[j], ring_b[j], ring_b[i]])

    def cap(self, mat: str, ring, up: bool) -> None:
        self.face(mat, list(ring) if up else list(reversed(ring)))

    def save(self) -> None:
        OUT.mkdir(parents=True, exist_ok=True)
        mtl_lines = []
        for name, (r, g, b) in self.mats.items():
            mtl_lines += [f"newmtl {name}", f"Kd {r:.4f} {g:.4f} {b:.4f}", ""]
        (OUT / f"{self.name}.mtl").write_text("\n".join(mtl_lines))
        lines = [f"mtllib {self.name}.mtl"]
        lines += [f"v {x:.4f} {y:.4f} {z:.4f}" for x, y, z in self.v]
        cur = None
        for mat, idxs in self.faces:
            if mat != cur:
                lines.append(f"usemtl {mat}")
                cur = mat
            lines.append("f " + " ".join(str(i) for i in idxs))
        (OUT / f"{self.name}.obj").write_text("\n".join(lines) + "\n")
        print("wrote", f"{self.name}.obj",
              f"({len(self.v)} v, {len(self.faces)} f)")


def person() -> Obj:
    """A 1.75 m low-poly 3D figure — reads from every angle (no face-me
    billboard needed): head, torso, arms and legs as tapered boxes."""
    o = Obj("person")
    o.mats["skin"] = (0.85, 0.68, 0.55)
    o.mats["shirt"] = (0.25, 0.45, 0.60)
    o.mats["pants"] = (0.30, 0.32, 0.38)
    o.mats["shoes"] = (0.15, 0.15, 0.17)

    def tbox(mat, cx, cy, z0, z1, w0, d0, w1, d1):
        """Tapered box: rectangle (w0×d0) at z0 lofted to (w1×d1) at z1."""
        def rect(z, w, d, cxx):
            return [o.add_v(cxx - w / 2, cy - d / 2, z),
                    o.add_v(cxx + w / 2, cy - d / 2, z),
                    o.add_v(cxx + w / 2, cy + d / 2, z),
                    o.add_v(cxx - w / 2, cy + d / 2, z)]
        b = rect(z0, w0, d0, cx)
        t = rect(z1, w1, d1, cx)
        o.face(mat, list(reversed(b)))
        o.face(mat, t)
        for i in range(4):
            j = (i + 1) % 4
            o.face(mat, [b[i], b[j], t[j], t[i]])

    # Legs (slight stance) + shoes.
    for sx in (-0.09, 0.09):
        tbox("shoes", sx, 0.035, 0.00, 0.07, 0.11, 0.28, 0.10, 0.24)
        tbox("pants", sx, 0.0, 0.07, 0.52, 0.12, 0.15, 0.13, 0.16)   # shin
        tbox("pants", sx, 0.0, 0.52, 0.95, 0.13, 0.16, 0.15, 0.18)   # thigh
    # Pelvis.
    tbox("pants", 0.0, 0.0, 0.95, 1.08, 0.34, 0.20, 0.33, 0.19)
    # Torso, tapering out to the shoulders.
    tbox("shirt", 0.0, 0.0, 1.08, 1.45, 0.33, 0.19, 0.42, 0.21)
    # Shoulders → arms (slightly outward) → hands.
    for sx in (-0.245, 0.245):
        tbox("shirt", sx, 0.0, 1.17, 1.45, 0.09, 0.11, 0.10, 0.12)  # upper arm
        tbox("skin", sx * 1.06, 0.0, 0.88, 1.17, 0.075, 0.09, 0.08, 0.10)
    # Neck + head.
    tbox("skin", 0.0, 0.0, 1.45, 1.52, 0.10, 0.10, 0.10, 0.10)
    tbox("skin", 0.0, 0.0, 1.52, 1.75, 0.17, 0.19, 0.15, 0.17)
    return o


def tree() -> Obj:
    o = Obj("tree")
    o.mats["trunk"] = (0.42, 0.30, 0.20)
    o.mats["leaf"] = (0.30, 0.50, 0.25)
    base = o.ring(0.16, 0.0)
    top = o.ring(0.12, 2.3)
    o.cap("trunk", base, up=False)
    o.loft("trunk", base, top)
    o.cap("trunk", top, up=True)
    r0 = o.ring(0.85, 2.1)
    r1 = o.ring(1.45, 3.0)
    r2 = o.ring(1.15, 3.9)
    r3 = o.ring(0.55, 4.5)
    o.cap("leaf", r0, up=False)
    o.loft("leaf", r0, r1)
    o.loft("leaf", r1, r2)
    o.loft("leaf", r2, r3)
    o.cap("leaf", r3, up=True)
    return o


def bush() -> Obj:
    o = Obj("bush")
    o.mats["leaf"] = (0.33, 0.52, 0.27)
    r0 = o.ring(0.35, 0.0)
    r1 = o.ring(0.55, 0.35)
    r2 = o.ring(0.40, 0.70)
    r3 = o.ring(0.15, 0.90)
    o.cap("leaf", r0, up=False)
    o.loft("leaf", r0, r1)
    o.loft("leaf", r1, r2)
    o.loft("leaf", r2, r3)
    o.cap("leaf", r3, up=True)
    return o


def car() -> Obj:
    """A low-poly sedan, 4.3 m long (X), 1.7 m wide (Y)."""
    o = Obj("car")
    o.mats["body"] = (0.72, 0.15, 0.14)
    o.mats["glass"] = (0.62, 0.72, 0.80)
    o.mats["tire"] = (0.12, 0.12, 0.13)

    def box(mat, x0, x1, y0, y1, z0, z1):
        a = o.add_v(x0, y0, z0); b = o.add_v(x1, y0, z0)
        c = o.add_v(x1, y1, z0); d = o.add_v(x0, y1, z0)
        e = o.add_v(x0, y0, z1); f = o.add_v(x1, y0, z1)
        g = o.add_v(x1, y1, z1); h = o.add_v(x0, y1, z1)
        o.face(mat, [d, c, b, a])
        o.face(mat, [e, f, g, h])
        o.face(mat, [a, b, f, e])
        o.face(mat, [b, c, g, f])
        o.face(mat, [c, d, h, g])
        o.face(mat, [d, a, e, h])

    # Body slab.
    box("body", -2.15, 2.15, -0.85, 0.85, 0.32, 0.85)
    # Cabin: a lofted trapezoid (windscreens raked both ways).
    yb, yt = 0.80, 0.68
    b1 = o.add_v(-1.45, -yb, 0.85); b2 = o.add_v(0.95, -yb, 0.85)
    b3 = o.add_v(0.95, yb, 0.85); b4 = o.add_v(-1.45, yb, 0.85)
    t1 = o.add_v(-0.85, -yt, 1.42); t2 = o.add_v(0.40, -yt, 1.42)
    t3 = o.add_v(0.40, yt, 1.42); t4 = o.add_v(-0.85, yt, 1.42)
    o.face("glass", [b1, b2, t2, t1])
    o.face("glass", [b2, b3, t3, t2])
    o.face("glass", [b3, b4, t4, t3])
    o.face("glass", [b4, b1, t1, t4])
    o.face("body", [t1, t2, t3, t4])
    # Wheels: 8-gon cylinders across Y.
    for wx in (-1.35, 1.35):
        for wy in (-0.88, 0.70):
            ring_out, ring_in = [], []
            for k in range(8):
                a = 2 * math.pi * k / 8
                z = 0.32 + 0.30 * math.sin(a)
                x = wx + 0.30 * math.cos(a)
                ring_out.append(o.add_v(x, wy + 0.18, z))
                ring_in.append(o.add_v(x, wy, z))
            o.loft("tire", ring_in, ring_out)
            o.cap("tire", ring_out, up=True)
            o.cap("tire", ring_in, up=False)
    return o




def person_billboard() -> None:
    """The face-me scale figure: a clean arch-viz silhouette PNG (flat slate,
    real anthropometric proportions), tight-cropped so the billboard quad's
    aspect maps exactly to 1.75 m tall."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QImage,
                               QPainter, QPainterPath)
    QGuiApplication.instance() or QGuiApplication([])
    W, H = 640, 1024
    img = QImage(W, H, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    ink = QColor(58, 66, 82)
    SC = H * 0.97 / 175.0

    def pt(x, y):
        return QPointF(W / 2 + x * SC, H * 0.985 - y * SC)

    p.setBrush(QBrush(ink))
    p.setPen(Qt.NoPen)
    p.drawEllipse(pt(0, 163.5), 10.5 * SC, 11.5 * SC)
    b = QPainterPath()
    b.moveTo(pt(4.5, 152))
    b.cubicTo(pt(13, 150), pt(20, 148), pt(22.5, 144))
    b.cubicTo(pt(24.5, 140), pt(24.5, 134), pt(23.5, 126))
    b.cubicTo(pt(22.5, 112), pt(21.5, 96), pt(20.5, 82))
    b.cubicTo(pt(20.2, 77), pt(20.5, 73), pt(19.5, 69))
    b.cubicTo(pt(18.0, 66), pt(16.5, 67), pt(16.3, 70))
    b.cubicTo(pt(16.4, 74), pt(16.6, 78), pt(16.6, 82))
    b.cubicTo(pt(17.0, 98), pt(17.4, 116), pt(18.2, 133))
    b.cubicTo(pt(17.0, 136), pt(16.2, 133), pt(15.8, 124))
    b.cubicTo(pt(15.2, 114), pt(14.8, 108), pt(15.2, 100))
    b.cubicTo(pt(16.4, 94), pt(17.2, 90), pt(17.2, 85))
    b.cubicTo(pt(16.4, 72), pt(13.0, 60), pt(11.0, 48))
    b.cubicTo(pt(9.4, 36), pt(8.0, 20), pt(7.4, 7))
    b.lineTo(pt(9.6, 1.2))
    b.lineTo(pt(2.6, 0))
    b.cubicTo(pt(2.9, 12), pt(3.1, 26), pt(3.4, 44))
    b.cubicTo(pt(3.0, 58), pt(1.6, 68), pt(0.0, 76))
    b.cubicTo(pt(-1.6, 68), pt(-3.0, 58), pt(-3.4, 44))
    b.cubicTo(pt(-3.1, 26), pt(-2.9, 12), pt(-2.6, 0))
    b.lineTo(pt(-9.6, 1.2))
    b.lineTo(pt(-7.4, 7))
    b.cubicTo(pt(-8.0, 20), pt(-9.4, 36), pt(-11.0, 48))
    b.cubicTo(pt(-13.0, 60), pt(-16.4, 72), pt(-17.2, 85))
    b.cubicTo(pt(-17.2, 90), pt(-16.4, 94), pt(-15.2, 100))
    b.cubicTo(pt(-14.8, 108), pt(-15.2, 114), pt(-15.8, 124))
    b.cubicTo(pt(-16.2, 133), pt(-17.0, 136), pt(-18.2, 133))
    b.cubicTo(pt(-17.4, 116), pt(-17.0, 98), pt(-16.6, 82))
    b.cubicTo(pt(-16.6, 78), pt(-16.4, 74), pt(-16.3, 70))
    b.cubicTo(pt(-16.5, 67), pt(-18.0, 66), pt(-19.5, 69))
    b.cubicTo(pt(-20.5, 73), pt(-20.2, 77), pt(-20.5, 82))
    b.cubicTo(pt(-21.5, 96), pt(-22.5, 112), pt(-23.5, 126))
    b.cubicTo(pt(-24.5, 134), pt(-24.5, 140), pt(-22.5, 144))
    b.cubicTo(pt(-20, 148), pt(-13, 150), pt(-4.5, 152))
    b.closeSubpath()
    p.drawPath(b)
    p.end()
    # Tight crop so quad aspect = real proportions.
    import numpy as np
    buf = np.frombuffer(img.constBits(), np.uint8).reshape(H, W, 4)
    alpha = buf[:, :, 3]
    ys, xs = np.where(alpha > 8)
    m = 4
    crop = img.copy(int(xs.min()) - m, int(ys.min()) - m,
                    int(xs.max() - xs.min()) + 2 * m,
                    int(ys.max() - ys.min()) + 2 * m)
    OUT.mkdir(parents=True, exist_ok=True)
    crop.save(str(OUT / "person_billboard.png"))
    print("wrote person_billboard.png",
          f"({crop.width()}x{crop.height()})")


def main() -> None:
    for build in (person, tree, bush, car):
        build().save()
    person_billboard()


if __name__ == "__main__":
    main()
