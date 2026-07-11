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
    """The 1.75 m scale figure — a flat architectural cutout."""
    o = Obj("person")
    o.mats["figure"] = (0.42, 0.47, 0.55)
    # Silhouette in the XZ plane (x = width, z = height), half-outline mirrored.
    right = [
        (0.09, 0.00), (0.11, 0.02), (0.12, 0.42),   # foot + outer leg
        (0.10, 0.80), (0.16, 0.84),                 # hip
        (0.185, 1.18),                              # arm down, hand
        (0.205, 1.42),                              # shoulder outer
        (0.14, 1.50),                               # neck side
        (0.115, 1.56), (0.115, 1.68), (0.06, 1.75), # head side
    ]
    left = [(-x, z) for x, z in reversed(right)]
    inner_right = [(0.02, 0.42), (0.035, 0.00)]     # inseam right leg
    inner_left = [(-0.035, 0.00), (-0.02, 0.42)]
    outline = right + left + inner_left + [(0.0, 0.5)] + inner_right
    o.poly_prism("figure", outline, -0.02, 0.02)
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


def main() -> None:
    for build in (person, tree, bush, car):
        build().save()


if __name__ == "__main__":
    main()
