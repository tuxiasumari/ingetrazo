# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""DEM elevation (Track G, G2-minimal): RGB decode, bilinear sampling, and the
DEMSampler's datum-aware lookup — all without touching the network."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtGui import QColor, QGuiApplication, QImage, QVector3D

from georef.datum import SceneDatum
from georef.dem import (
    AWS_TERRAIN,
    DEMSampler,
    bilinear,
    decode_elevation,
    pixel_in_tile,
)


_app = QGuiApplication.instance() or QGuiApplication([])


def _solid(color: QColor) -> QImage:
    img = QImage(4, 4, QImage.Format.Format_RGBA8888)
    img.fill(color)
    return img


# ---- RGB → elevation decode ----------------------------------------------------

def test_decode_terrarium_known_height():
    # terrarium: h = R*256 + G + B/256 - 32768. Encode +100 m → (128,100,0).
    field = decode_elevation(_solid(QColor(128, 100, 0)), "terrarium")
    assert field.shape == (4, 4)
    assert np.allclose(field, 100.0, atol=1e-3)


def test_decode_terrarium_sub_metre_blue_channel():
    # B carries the sub-metre part: (128,100,128) → 100 + 128/256 = 100.5 m.
    field = decode_elevation(_solid(QColor(128, 100, 128)), "terrarium")
    assert np.allclose(field, 100.5, atol=1e-3)


def test_decode_mapbox_known_height():
    # mapbox: h = -10000 + (R*65536 + G*256 + B)*0.1. +100 m → (1,138,136).
    field = decode_elevation(_solid(QColor(1, 138, 136)), "mapbox")
    assert np.allclose(field, 100.0, atol=0.2)


def test_decode_sea_level():
    # terrarium zero → (128,0,0) = 128*256 - 32768 = 0 m.
    field = decode_elevation(_solid(QColor(128, 0, 0)), "terrarium")
    assert np.allclose(field, 0.0, atol=1e-3)


# ---- Bilinear ------------------------------------------------------------------

def test_bilinear_corners_and_centre():
    f = np.array([[0.0, 10.0], [20.0, 30.0]], dtype=np.float32)
    assert bilinear(f, 0, 0) == 0.0
    assert bilinear(f, 1, 0) == 10.0
    assert bilinear(f, 0, 1) == 20.0
    assert abs(bilinear(f, 0.5, 0.5) - 15.0) < 1e-4


def test_bilinear_clamps_out_of_range():
    f = np.array([[5.0, 5.0], [5.0, 5.0]], dtype=np.float32)
    assert bilinear(f, -3, -3) == 5.0
    assert bilinear(f, 99, 99) == 5.0


# ---- pixel_in_tile -------------------------------------------------------------

def test_pixel_in_tile_within_bounds():
    tx, ty, px, py = pixel_in_tile(-13.5320, -71.9675, 13)  # Cusco
    assert isinstance(tx, int) and isinstance(ty, int)
    assert 0 <= px < 256 and 0 <= py < 256


# ---- DEMSampler ----------------------------------------------------------------

class _StubFetcher:
    """A fetcher that never hits the network — every request is a cache miss."""

    def request(self, source, x, y, z):
        return None


def test_sampler_returns_none_when_tile_absent():
    datum = SceneDatum(-12.0464, -77.0428)
    s = DEMSampler(datum)
    s._fetcher = _StubFetcher()  # no network
    assert s.elevation_at(-12.0464, -77.0428) is None
    assert s.has_tile(-12.0464, -77.0428) is False


def test_sampler_reads_injected_field():
    datum = SceneDatum(-12.0464, -77.0428)
    s = DEMSampler(datum, zoom=13)
    s._fetcher = _StubFetcher()
    tx, ty, _, _ = pixel_in_tile(datum.lat, datum.lon, 13)
    s._fields[(tx, ty, 13)] = np.full((256, 256), 154.0, dtype=np.float32)
    assert abs(s.elevation_at(datum.lat, datum.lon) - 154.0) < 1e-3
    assert s.has_tile(datum.lat, datum.lon) is True


def test_sampler_elevation_at_local_uses_datum():
    datum = SceneDatum(-12.0464, -77.0428)
    s = DEMSampler(datum, zoom=13)
    s._fetcher = _StubFetcher()
    tx, ty, _, _ = pixel_in_tile(datum.lat, datum.lon, 13)
    s._fields[(tx, ty, 13)] = np.full((256, 256), 300.0, dtype=np.float32)
    # The scene origin maps back to the datum's lat/lon → same tile.
    assert abs(s.elevation_at_local(QVector3D(0, 0, 0)) - 300.0) < 1e-3


def test_aws_terrain_source_url():
    assert AWS_TERRAIN.url(1, 2, 3).endswith("/terrarium/3/1/2.png")
    assert AWS_TERRAIN.encoding == "terrarium"
