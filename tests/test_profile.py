# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Longitudinal profile (Track G, G4): polyline ordering, chainage geometry,
and elevation sampling against a synthetic (stub) DEM. No network."""
from __future__ import annotations

import math

from PySide6.QtGui import QVector3D

from core.scene import Scene
from georef.datum import SceneDatum
from georef.geopath import GeoPath
from georef.profile import (
    point_at_station,
    polyline_length,
    profile_to_csv,
    sample_profile,
    selected_geopath,
)


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


class StubSampler:
    """Elevation = f(x, y) over a datum — no tiles, no network."""

    def __init__(self, datum, fn):
        self.datum = datum
        self._fn = fn
        self.ensured = []

    def ensure_area(self, lat_s, lon_w, lat_n, lon_e):
        self.ensured.append((lat_s, lon_w, lat_n, lon_e))

    def elevation_at_local(self, p):
        return self._fn(p.x(), p.y())


# ---- Path source (which GeoPath to profile) ------------------------------------

def test_selected_geopath_single_selection():
    scene = Scene()
    p1 = GeoPath([V(0, 0), V(10, 0)])
    p2 = GeoPath([V(0, 5), V(10, 5)])
    scene.geo_paths.extend([p1, p2])
    scene.selection.add(p1)
    assert selected_geopath(scene) is p1


def test_selected_geopath_falls_back_to_only_path():
    scene = Scene()
    only = GeoPath([V(0, 0), V(10, 0)])
    scene.geo_paths.append(only)
    assert selected_geopath(scene) is only        # no selection, one path


def test_selected_geopath_none_when_ambiguous():
    scene = Scene()
    scene.geo_paths.extend([GeoPath([V(0, 0), V(1, 0)]),
                            GeoPath([V(0, 1), V(1, 1)])])
    assert selected_geopath(scene) is None         # two paths, none selected


def test_sample_profile_from_geopath_points():
    datum = SceneDatum(-12.0464, -77.0428)
    sampler = StubSampler(datum, lambda x, y: x * 0.5)
    path = GeoPath([V(0, 0), V(100, 0)])
    profile = sample_profile(path.profile_points(), sampler, spacing=10.0)
    assert abs(profile.samples[-1].elevation - 50.0) < 1e-6


# ---- Chainage geometry ---------------------------------------------------------

def test_polyline_length_horizontal():
    pts = [V(0, 0), V(3, 4)]     # 3-4-5 triangle
    assert abs(polyline_length(pts) - 5.0) < 1e-9


def test_point_at_station_interpolates():
    pts = [V(0, 0), V(10, 0), V(10, 10)]
    assert point_at_station(pts, 0) == (0.0, 0.0)
    assert point_at_station(pts, 5) == (5.0, 0.0)
    x, y = point_at_station(pts, 15)   # 10 along first seg + 5 up second
    assert abs(x - 10) < 1e-9 and abs(y - 5) < 1e-9
    # Past the end clamps to the last vertex.
    assert point_at_station(pts, 999) == (10.0, 10.0)


# ---- Sampling ------------------------------------------------------------------

def test_sample_profile_follows_terrain():
    datum = SceneDatum(-12.0464, -77.0428)
    # Elevation rises 0.5 m per metre east.
    sampler = StubSampler(datum, lambda x, y: x * 0.5)
    profile = sample_profile([V(0, 0), V(100, 0)], sampler, spacing=10.0)
    assert profile.complete
    assert abs(profile.length - 100.0) < 1e-6
    assert abs(profile.samples[0].elevation - 0.0) < 1e-6
    assert abs(profile.samples[-1].elevation - 50.0) < 1e-6
    assert abs(profile.max_elevation() - 50.0) < 1e-6
    assert abs(profile.min_elevation() - 0.0) < 1e-6


def test_sample_profile_slopes_and_gain():
    datum = SceneDatum(-12.0464, -77.0428)
    sampler = StubSampler(datum, lambda x, y: x * 0.10)  # 10% grade east
    profile = sample_profile([V(0, 0), V(50, 0)], sampler, spacing=10.0)
    slopes = profile.slopes()
    assert slopes and all(abs(sp - 10.0) < 1e-6 for _, sp in slopes)
    assert abs(profile.total_gain() - 5.0) < 1e-6   # 50 m * 0.10

def test_sample_profile_requests_dem_area():
    datum = SceneDatum(-12.0464, -77.0428)
    sampler = StubSampler(datum, lambda x, y: 100.0)
    sample_profile([V(0, 0), V(200, 200)], sampler, spacing=50.0)
    assert len(sampler.ensured) == 1   # ensured the covering bbox once


def test_sample_profile_incomplete_when_tiles_missing():
    datum = SceneDatum(-12.0464, -77.0428)
    # Missing elevation past x=50 (tile not loaded).
    sampler = StubSampler(datum, lambda x, y: 100.0 if x <= 50 else None)
    profile = sample_profile([V(0, 0), V(100, 0)], sampler, spacing=10.0)
    assert profile.complete is False
    assert any(s.elevation is None for s in profile.samples)
    # min/max still computed over the loaded part.
    assert profile.max_elevation() == 100.0


def test_profile_csv_round_shape():
    datum = SceneDatum(-12.0464, -77.0428)
    sampler = StubSampler(datum, lambda x, y: x)
    profile = sample_profile([V(0, 0), V(20, 0)], sampler, spacing=10.0)
    csv = profile_to_csv(profile)
    lines = csv.strip().split("\n")
    assert lines[0] == "station_m,x_m,y_m,elevation_m"
    assert len(lines) == len(profile.samples) + 1
