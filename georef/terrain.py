# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""3D terrain — DEM relief with the satellite ortophoto draped on top (Track G,
G2 full).

Display-only, like the flat tile layer: it is **never** part of ``Scene.mesh``
(the topology engine would choke on DEM density and it isn't editable geometry).
A regular grid of local-metre vertices is lifted to the DEM relief and textured
with a mosaic of the base-map tiles, so you see the imagery draped over the
ground. Heights are ground-relative to the datum (reference-plane model), so the
terrain shares Z≈0 with the flat map and the traced surfaces.

Two pure pieces: :func:`build_terrain` (grid geometry + UVs) and
:func:`build_mosaic` (composite the covering tiles into one image). The GL
upload/render lives in the viewport.
"""
from __future__ import annotations

from PySide6.QtGui import QImage, QPainter, QVector3D

from georef.tiles import deg2num, tiles_covering

TILE_PX = 256


class TerrainObject:
    """A draped-terrain patch: grid vertices, UVs, triangles, and its texture."""

    def __init__(self, vertices, uvs, triangles, tile_range,
                 nx=0, ny=0, bbox=(0.0, 0.0, 0.0, 0.0)) -> None:
        self.vertices = vertices          # list[QVector3D] local metres
        self.uvs = uvs                    # list[(u, v)] into the mosaic
        self.triangles = triangles        # list[(i, j, k)] indices
        self.tile_range = tile_range      # (tx0, ty0, ntx, nty, zoom)
        self.nx = nx                      # grid columns (x / east)
        self.ny = ny                      # grid rows (y / north)
        self.bbox = bbox                  # (minx, miny, maxx, maxy) local metres
        self.visible = True
        self.texture_image = None         # QImage mosaic (set by build_mosaic)

    def height_at(self, x: float, y: float) -> float | None:
        """Bilinearly interpolated terrain Z at local ``(x, y)``, or ``None`` if
        outside the captured area. Used to drape routes/markers onto the relief."""
        nx, ny = self.nx, self.ny
        minx, miny, maxx, maxy = self.bbox
        if nx < 2 or ny < 2 or maxx <= minx or maxy <= miny:
            return None
        if not (minx <= x <= maxx and miny <= y <= maxy):
            return None
        fi = (x - minx) / (maxx - minx) * (nx - 1)     # column (east)
        fj = (maxy - y) / (maxy - miny) * (ny - 1)     # row 0 = north (+y)
        i0 = max(0, min(nx - 2, int(fi)))
        j0 = max(0, min(ny - 2, int(fj)))
        tx, ty = fi - i0, fj - j0
        z = self.vertices
        z00 = z[j0 * nx + i0].z()
        z10 = z[j0 * nx + i0 + 1].z()
        z01 = z[(j0 + 1) * nx + i0].z()
        z11 = z[(j0 + 1) * nx + i0 + 1].z()
        top = z00 * (1 - tx) + z10 * tx
        bot = z01 * (1 - tx) + z11 * tx
        return top * (1 - ty) + bot * ty

    def bounds(self):
        if not self.vertices:
            return None, None
        xs = [v.x() for v in self.vertices]
        ys = [v.y() for v in self.vertices]
        zs = [v.z() for v in self.vertices]
        return (QVector3D(min(xs), min(ys), min(zs)),
                QVector3D(max(xs), max(ys), max(zs)))


def _bbox_ll(datum, bbox):
    """Geodetic bbox of a local-metre ``(minx, miny, maxx, maxy)`` rectangle."""
    minx, miny, maxx, maxy = bbox
    lats, lons = [], []
    for lx, ly in ((minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)):
        la, lo, _ = datum.local_to_geodetic(QVector3D(lx, ly, 0.0))
        lats.append(la)
        lons.append(lo)
    return min(lats), min(lons), max(lats), max(lons)


def build_terrain(datum, sampler, ground_ref: float, bbox,
                  target_cell_m: float = 40.0, max_grid: int = 180, zoom: int = 15):
    """DEM-lifted terrain grid over the captured ``bbox`` (local-metre
    ``(minx, miny, maxx, maxy)``), so the 3D terrain matches the area you drew.

    Grid density follows the extent at ~``target_cell_m`` per cell, capped at
    ``max_grid`` per axis (bounds the vertex count — a long strip gets a long
    thin grid). Returns ``None`` if the DEM isn't fully loaded.
    """
    minx, miny, maxx, maxy = bbox
    w, h = maxx - minx, maxy - miny
    if w <= 0 or h <= 0:
        return None
    nx = max(2, min(max_grid, int(round(w / target_cell_m)) + 1))
    ny = max(2, min(max_grid, int(round(h / target_cell_m)) + 1))

    lat_s, lon_w, lat_n, lon_e = _bbox_ll(datum, bbox)
    tiles = tiles_covering(lat_s, lon_w, lat_n, lon_e, zoom)
    if not tiles:
        return None
    tx0 = min(t[0] for t in tiles)
    ty0 = min(t[1] for t in tiles)
    ntx = max(t[0] for t in tiles) - tx0 + 1
    nty = max(t[1] for t in tiles) - ty0 + 1

    verts, uvs = [], []
    for j in range(ny):
        y = maxy - h * j / (ny - 1)            # row 0 = north edge (+Y)
        for i in range(nx):
            x = minx + w * i / (nx - 1)
            e = sampler.elevation_at_local(QVector3D(x, y, 0.0))
            if e is None:
                return None                    # DEM not ready
            verts.append(QVector3D(x, y, e - ground_ref))
            lat, lon, _ = datum.local_to_geodetic(QVector3D(x, y, 0.0))
            xf, yf = deg2num(lat, lon, zoom)
            uvs.append(((xf - tx0) / ntx, (yf - ty0) / nty))

    tris = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a = j * nx + i
            b = a + 1
            c = a + nx
            d = c + 1
            tris.append((a, c, b))
            tris.append((b, c, d))
    return TerrainObject(verts, uvs, tris, (tx0, ty0, ntx, nty, zoom),
                         nx=nx, ny=ny, bbox=bbox)


def build_mosaic(terrain: TerrainObject, images: dict) -> QImage | None:
    """Composite the covering tiles into one image for ``terrain``.

    ``images`` maps ``(x, y, zoom)`` → ``QImage``. Missing tiles are left blank;
    returns ``None`` if none are available yet.
    """
    tx0, ty0, ntx, nty, zoom = terrain.tile_range
    mosaic = QImage(ntx * TILE_PX, nty * TILE_PX, QImage.Format.Format_RGB32)
    mosaic.fill(0xFF8A8A8A)   # neutral grey so still-loading tiles don't read black
    painter = QPainter(mosaic)
    any_tile = False
    for (x, y, z), img in images.items():
        if z != zoom:
            continue
        if tx0 <= x < tx0 + ntx and ty0 <= y < ty0 + nty:
            painter.drawImage((x - tx0) * TILE_PX, (y - ty0) * TILE_PX, img)
            any_tile = True
    painter.end()
    return mosaic if any_tile else None
