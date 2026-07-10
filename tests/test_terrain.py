# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""3D terrain (Track G, G2 full): heightmesh geometry, UVs, and tile mosaic —
built from a synthetic DEM. Headless."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication, QImage, QVector3D

from georef.datum import SceneDatum
from georef.terrain import build_mosaic, build_terrain

_app = QGuiApplication.instance() or QGuiApplication([])


class StubSampler:
    def __init__(self, datum, fn):
        self.datum = datum
        self._fn = fn

    def elevation_at_local(self, p):
        return self._fn(p.x(), p.y())

    def elevation_at(self, lat, lon):
        return self._fn(0.0, 0.0)


DATUM = SceneDatum(-13.5320, -71.9675)


def test_grid_dimensions_and_triangles():
    sampler = StubSampler(DATUM, lambda x, y: 3000.0)
    t = build_terrain(DATUM, sampler, 3000.0, radius_m=500, grid_n=10, zoom=15)
    assert t is not None
    assert len(t.vertices) == 100                # 10×10 grid
    assert len(t.uvs) == 100
    assert len(t.triangles) == 2 * 9 * 9          # 2 tris per cell


def test_heights_are_ground_relative():
    # A central bump; ground_ref = 3000 (elevation at origin).
    def terrain(x, y):
        return 3000.0 + 40.0 * max(0.0, 1 - ((x * x + y * y) ** 0.5) / 400.0)
    sampler = StubSampler(DATUM, terrain)
    ground = sampler.elevation_at(DATUM.lat, DATUM.lon)   # 3040 at (0,0)!
    t = build_terrain(DATUM, sampler, ground, radius_m=400, grid_n=9, zoom=15)
    zs = [v.z() for v in t.vertices]
    # Centre (origin) is the highest → z ≈ 0 there; edges lower (negative).
    assert max(zs) <= 1e-6 + 0.0
    assert min(zs) < -10.0


def test_uv_corners_span_unit_range():
    sampler = StubSampler(DATUM, lambda x, y: 3000.0)
    t = build_terrain(DATUM, sampler, 3000.0, radius_m=500, grid_n=8, zoom=15)
    us = [u for u, v in t.uvs]
    vs = [v for u, v in t.uvs]
    assert 0.0 <= min(us) and max(us) <= 1.0
    assert 0.0 <= min(vs) and max(vs) <= 1.0
    # North row (first) has the smallest v (top of the mosaic).
    assert t.uvs[0][1] <= t.uvs[-1][1]


def test_build_terrain_none_when_dem_missing():
    sampler = StubSampler(DATUM, lambda x, y: None)
    assert build_terrain(DATUM, sampler, 0.0, radius_m=500, grid_n=8) is None


def test_mosaic_composites_tiles():
    sampler = StubSampler(DATUM, lambda x, y: 3000.0)
    t = build_terrain(DATUM, sampler, 3000.0, radius_m=300, grid_n=6, zoom=15)
    tx0, ty0, ntx, nty, zoom = t.tile_range
    # Provide a solid red image for each covering tile.
    red = QImage(256, 256, QImage.Format.Format_RGB32)
    red.fill(0xFFFF0000)
    images = {(tx0 + dx, ty0 + dy, zoom): red
              for dx in range(ntx) for dy in range(nty)}
    mosaic = build_mosaic(t, images)
    assert mosaic is not None
    assert mosaic.width() == ntx * 256 and mosaic.height() == nty * 256
    assert mosaic.pixelColor(10, 10).red() == 255


def test_height_at_bilinear():
    # Linear terrain z = 0.1x (ground-relative): height_at must recover it.
    sampler = StubSampler(DATUM, lambda x, y: 0.1 * x)
    t = build_terrain(DATUM, sampler, 0.0, radius_m=500, grid_n=21, zoom=15)
    assert abs(t.height_at(0, 0) - 0.0) < 1e-6
    assert abs(t.height_at(200, 0) - 20.0) < 0.5
    assert abs(t.height_at(-300, 100) - (-30.0)) < 0.5
    assert t.height_at(9999, 0) is None      # outside the patch


def test_mosaic_none_when_no_images():
    sampler = StubSampler(DATUM, lambda x, y: 3000.0)
    t = build_terrain(DATUM, sampler, 3000.0, radius_m=300, grid_n=6, zoom=15)
    assert build_mosaic(t, {}) is None
