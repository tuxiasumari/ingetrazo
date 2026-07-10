# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Terrain-surface fill (Track G): flat best-fit plane + draped relief, built
from a synthetic DEM. Headless."""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from georef.datum import SceneDatum
from georef.geopath import GeoPath
from georef.surface import (
    _fit_plane,
    build_draped_surface,
    build_flat_surface,
    build_surface,
    ground_reference,
)


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


class StubSampler:
    def __init__(self, datum, fn):
        self.datum = datum
        self._fn = fn

    def elevation_at_local(self, p):
        return self._fn(p.x(), p.y())

    def elevation_at(self, lat, lon):
        return self._fn(0.0, 0.0)   # datum origin → (0,0) local


def _square(size=100.0):
    return GeoPath([V(0, 0), V(size, 0), V(size, size), V(0, size)],
                   closed=True)


# ---- Plane fit -----------------------------------------------------------------

def test_fit_plane_recovers_slope():
    pts = [V(0, 0), V(10, 0), V(10, 10), V(0, 10)]
    zs = [0.5 * p.x() + 0.2 * p.y() + 3 for p in pts]   # z = 0.5x + 0.2y + 3
    a, b, c = _fit_plane(pts, zs)
    assert abs(a - 0.5) < 1e-6 and abs(b - 0.2) < 1e-6 and abs(c - 3) < 1e-6


# ---- Flat surface --------------------------------------------------------------

def test_flat_surface_is_single_plane():
    datum = SceneDatum(-13.53, -71.96)
    sampler = StubSampler(datum, lambda x, y: 0.1 * x + 3000)  # slope east
    ground = ground_reference(sampler, datum)                  # 3000 at origin
    tris = build_flat_surface(_square(), sampler, ground)
    assert tris and len(tris) == 2                             # square → 2 tris
    # Every vertex lies on z = 0.1x (ground-relative), i.e. z - 0.1x ≈ 0.
    for tri in tris:
        for v in tri:
            assert abs(v.z() - 0.1 * v.x()) < 1e-3


def test_flat_surface_none_when_dem_missing():
    datum = SceneDatum(-13.53, -71.96)
    sampler = StubSampler(datum, lambda x, y: None)
    assert build_flat_surface(_square(), sampler, 0.0) is None


# ---- Draped surface ------------------------------------------------------------

def test_draped_follows_relief_and_subdivides():
    datum = SceneDatum(-13.53, -71.96)
    # A bump in the middle → draped must place interior points above the corners.
    def terrain(x, y):
        return 3000 + 20.0 * max(0.0, 1 - (((x - 50) ** 2 + (y - 50) ** 2) ** 0.5) / 50)
    sampler = StubSampler(datum, terrain)
    ground = ground_reference(sampler, datum)
    tris = build_draped_surface(_square(), sampler, ground)
    assert tris and len(tris) > 2                    # subdivided (100 m > 12 m)
    zs = [v.z() for tri in tris for v in tri]
    assert max(zs) > 5.0                             # the central bump shows

def test_draped_flat_terrain_matches_ground():
    datum = SceneDatum(-13.53, -71.96)
    sampler = StubSampler(datum, lambda x, y: 2500.0)   # perfectly flat
    ground = ground_reference(sampler, datum)
    tris = build_draped_surface(_square(20), sampler, ground)
    assert tris
    for tri in tris:
        for v in tri:
            assert abs(v.z()) < 1e-6                 # flat terrain → z ≈ 0


# ---- Dispatch ------------------------------------------------------------------

def test_build_surface_dispatch():
    datum = SceneDatum(-13.53, -71.96)
    sampler = StubSampler(datum, lambda x, y: 100.0)
    flat = _square(); flat.surface = "flat"
    draped = _square(); draped.surface = "draped"
    line = _square(); line.surface = None
    assert build_surface(flat, sampler, datum) is not None
    assert build_surface(draped, sampler, datum) is not None
    assert build_surface(line, sampler, datum) is None
