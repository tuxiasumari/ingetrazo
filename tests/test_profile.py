# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Longitudinal profile (Track G, G4): polyline ordering, chainage geometry,
and elevation sampling against a synthetic (stub) DEM. No network."""
from __future__ import annotations

import math

from PySide6.QtGui import QVector3D

from core.mesh import Mesh
from core.scene import Scene
from georef.datum import SceneDatum
from georef.profile import (
    order_polyline,
    point_at_station,
    polyline_from_selection,
    polyline_length,
    profile_to_csv,
    sample_profile,
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


# ---- Polyline ordering ---------------------------------------------------------

def test_order_open_chain():
    m = Mesh()
    m.add_edge(V(0, 0), V(10, 0))
    m.add_edge(V(10, 0), V(10, 10))  # shares the welded (10,0) vertex
    pts = order_polyline(m.edges)
    assert pts is not None
    xs = [(round(p.x()), round(p.y())) for p in pts]
    assert xs[0] == (0, 0) and xs[-1] == (10, 10)
    assert len(xs) == 3


def test_order_rejects_branch():
    m = Mesh()
    m.add_edge(V(0, 0), V(5, 0))
    m.add_edge(V(5, 0), V(10, 0))
    m.add_edge(V(5, 0), V(5, 5))   # a T-branch at (5,0)
    assert order_polyline(m.edges) is None


def test_order_rejects_disconnected():
    m = Mesh()
    m.add_edge(V(0, 0), V(1, 0))
    m.add_edge(V(5, 5), V(6, 5))   # separate component
    assert order_polyline(m.edges) is None


def test_polyline_from_selection():
    scene = Scene()
    e1 = scene.mesh.add_edge(V(0, 0), V(10, 0))
    e2 = scene.mesh.add_edge(V(10, 0), V(20, 0))
    scene.selection.update({e1, e2})
    pts = polyline_from_selection(scene)
    assert pts is not None and len(pts) == 3


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
