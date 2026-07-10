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


SQUARE = (-500.0, -500.0, 500.0, 500.0)     # 1 km × 1 km capture bbox


def test_grid_dimensions_and_triangles():
    sampler = StubSampler(DATUM, lambda x, y: 3000.0)
    # target_cell 100 over 1000 m → 11×11 grid.
    t = build_terrain(DATUM, sampler, 3000.0, SQUARE, target_cell_m=100, zoom=15)
    assert t is not None
    assert t.nx == 11 and t.ny == 11
    assert len(t.vertices) == 121
    assert len(t.triangles) == 2 * 10 * 10


def test_strip_grid_is_long_and_thin():
    sampler = StubSampler(DATUM, lambda x, y: 3000.0)
    # A 200 m × 10 km strip → many rows, few columns.
    t = build_terrain(DATUM, sampler, 3000.0, (-100, -5000, 100, 5000),
                      target_cell_m=100, max_grid=180, zoom=15)
    assert t.ny > t.nx                          # long (north-south) and thin


def test_heights_are_ground_relative():
    def terrain(x, y):
        return 3000.0 + 40.0 * max(0.0, 1 - ((x * x + y * y) ** 0.5) / 400.0)
    sampler = StubSampler(DATUM, terrain)
    ground = sampler.elevation_at(DATUM.lat, DATUM.lon)
    t = build_terrain(DATUM, sampler, ground, (-400, -400, 400, 400),
                      target_cell_m=100, zoom=15)
    zs = [v.z() for v in t.vertices]
    assert max(zs) <= 1e-6
    assert min(zs) < -10.0


def test_height_at_covers_bbox():
    sampler = StubSampler(DATUM, lambda x, y: 0.1 * x)     # z = 0.1x
    t = build_terrain(DATUM, sampler, 0.0, SQUARE, target_cell_m=50, zoom=15)
    assert abs(t.height_at(0, 0) - 0.0) < 1e-6
    assert abs(t.height_at(400, 0) - 40.0) < 0.5
    assert t.height_at(9999, 0) is None                    # outside the capture


def test_uv_corners_span_unit_range():
    sampler = StubSampler(DATUM, lambda x, y: 3000.0)
    t = build_terrain(DATUM, sampler, 3000.0, SQUARE, target_cell_m=120, zoom=15)
    us = [u for u, v in t.uvs]
    vs = [v for u, v in t.uvs]
    assert 0.0 <= min(us) and max(us) <= 1.0
    assert 0.0 <= min(vs) and max(vs) <= 1.0
    assert t.uvs[0][1] <= t.uvs[-1][1]


def test_build_terrain_none_when_dem_missing():
    sampler = StubSampler(DATUM, lambda x, y: None)
    assert build_terrain(DATUM, sampler, 0.0, SQUARE, zoom=15) is None


def test_mosaic_composites_tiles():
    sampler = StubSampler(DATUM, lambda x, y: 3000.0)
    t = build_terrain(DATUM, sampler, 3000.0, (-300, -300, 300, 300),
                      target_cell_m=100, zoom=15)
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


def test_mosaic_none_when_no_images():
    sampler = StubSampler(DATUM, lambda x, y: 3000.0)
    t = build_terrain(DATUM, sampler, 3000.0, (-300, -300, 300, 300),
                      target_cell_m=100, zoom=15)
    assert build_mosaic(t, {}) is None
