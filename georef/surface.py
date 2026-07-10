# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Terrain-surface fill for a closed :class:`~georef.geopath.GeoPath` (Track G).

Two flavours, both built from the DEM (no full 3D terrain needed):

- **flat**  — a single best-fit slope plane through the polygon's terrain
  elevations (a graded pad / platform, one slope).
- **draped** — the polygon interior subdivided and lifted to the DEM relief, so
  it follows the ground.

Heights are made **ground-relative**: every Z is offset by the terrain elevation
at the scene origin (the datum), so the surface undulates around Z=0 where the
flat base map sits — the "reference plane" model. Absolute cotas still come from
the profile; this is only for the 3D preview.
"""
from __future__ import annotations

import math

from PySide6.QtGui import QVector3D

from core.triangulate import earcut

Tri = tuple  # (QVector3D, QVector3D, QVector3D)

_TARGET_EDGE_M = 12.0   # subdivide draped triangles down to ~this edge length
_MAX_DEPTH = 5          # cap: 4**5 = 1024 sub-triangles per earcut triangle


def ground_reference(sampler, datum) -> float | None:
    """Terrain elevation at the scene origin — the Z offset for the surface."""
    return sampler.elevation_at(datum.lat, datum.lon)


def _fit_plane(pts, zs):
    """Least-squares plane ``z = a*x + b*y + c`` through ``(x, y, z)`` samples."""
    n = len(pts)
    sx = sy = sxx = syy = sxy = sz = sxz = syz = 0.0
    for p, z in zip(pts, zs):
        x, y = p.x(), p.y()
        sx += x; sy += y; sxx += x * x; syy += y * y; sxy += x * y
        sz += z; sxz += x * z; syz += y * z
    # Normal equations, 3×3 solve via Cramer's rule.
    A = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, float(n)]]
    b = [sxz, syz, sz]

    def det3(m):
        return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))

    d = det3(A)
    if abs(d) < 1e-12:
        avg = sz / n if n else 0.0
        return 0.0, 0.0, avg          # degenerate → horizontal plane
    def col(i):
        return [[b[r] if c == i else A[r][c] for c in range(3)] for r in range(3)]
    return det3(col(0)) / d, det3(col(1)) / d, det3(col(2)) / d


def build_flat_surface(path, sampler, ground_ref: float):
    """Best-fit single-slope plane over the polygon (list of 3D triangles)."""
    pts = path.points
    if len(pts) < 3:
        return None
    zs = [sampler.elevation_at_local(QVector3D(p.x(), p.y(), 0.0)) for p in pts]
    if any(z is None for z in zs):
        return None                    # DEM not loaded yet
    a, b, c = _fit_plane(pts, zs)
    ring = [(p.x(), p.y()) for p in pts]
    verts = [QVector3D(p.x(), p.y(), a * p.x() + b * p.y() + c - ground_ref)
             for p in pts]
    return [(verts[i], verts[j], verts[k]) for i, j, k in earcut(ring)]


def _subdivide(tri, depth, out):
    """Uniform 4-way midpoint subdivision — matching edge splits keep adjacent
    earcut triangles watertight (no T-junctions) at a shared depth."""
    if depth <= 0:
        out.append(tri)
        return
    (ax, ay), (bx, by), (cx, cy) = tri
    ab = ((ax + bx) / 2, (ay + by) / 2)
    bc = ((bx + cx) / 2, (by + cy) / 2)
    ca = ((cx + ax) / 2, (cy + ay) / 2)
    _subdivide(((ax, ay), ab, ca), depth - 1, out)
    _subdivide((ab, (bx, by), bc), depth - 1, out)
    _subdivide((ca, bc, (cx, cy)), depth - 1, out)
    _subdivide((ab, bc, ca), depth - 1, out)


def build_draped_surface(path, sampler, ground_ref: float):
    """Polygon lifted to the DEM relief (list of 3D triangles)."""
    pts = path.points
    if len(pts) < 3:
        return None
    ring = [(p.x(), p.y()) for p in pts]
    base = [tuple(ring[i] for i in tri) for tri in earcut(ring)]
    if not base:
        return None
    # One shared subdivision depth from the longest edge → watertight relief.
    longest = 0.0
    for (x0, y0), (x1, y1), (x2, y2) in base:
        longest = max(longest, math.hypot(x1 - x0, y1 - y0),
                      math.hypot(x2 - x1, y2 - y1), math.hypot(x0 - x2, y0 - y2))
    depth = 0
    if longest > _TARGET_EDGE_M:
        depth = min(_MAX_DEPTH, int(math.ceil(math.log2(longest / _TARGET_EDGE_M))))
    tris2d = []
    for tri in base:
        _subdivide(tri, depth, tris2d)

    # Sample each unique 2D point once (rounded key), lift to relief.
    cache: dict = {}

    def lift(pt):
        key = (round(pt[0], 3), round(pt[1], 3))
        v = cache.get(key)
        if v is None:
            e = sampler.elevation_at_local(QVector3D(pt[0], pt[1], 0.0))
            if e is None:
                return None
            v = QVector3D(pt[0], pt[1], e - ground_ref)
            cache[key] = v
        return v

    out = []
    for a, b, c in tris2d:
        va, vb, vc = lift(a), lift(b), lift(c)
        if va is None or vb is None or vc is None:
            return None                # DEM not fully loaded → try again later
        out.append((va, vb, vc))
    return out


def build_surface(path, sampler, datum):
    """Build ``path``'s surface triangles for its mode, or ``None`` if not ready."""
    if not path.surface or len(path.points) < 3:
        return None
    ground = ground_reference(sampler, datum)
    if ground is None:
        return None
    if path.surface == "flat":
        return build_flat_surface(path, sampler, ground)
    if path.surface == "draped":
        return build_draped_surface(path, sampler, ground)
    return None
