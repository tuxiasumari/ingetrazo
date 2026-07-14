# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Scene datum — the bridge between geographic coordinates and the scene.

Everything the modelling engine touches lives in **local metres** relative to a
scene origin (:class:`SceneDatum`). This is non-negotiable: ``QVector3D`` is
float32, so feeding raw UTM eastings (~500 km) straight into the mesh drops
precision to centimetres. The datum anchors the scene at one geographic point
and projects every other point into a local, continuous, metre-based frame:

    X = easting  (east,  metres from anchor)
    Y = northing (north, metres from anchor)
    Z = altitude (metres from anchor altitude)

matching IngeTrazo's Z-up / X-east / Y-north convention.

Projection is UTM (WGS84 Transverse Mercator). The datum **freezes the UTM
zone** at construction and projects every point through that single zone, so
local coordinates stay continuous even if the model spills across a zone
boundary — never recompute the zone per point. The forward series is ported
from IngePresupuestos' ``latlon_a_utm``; the inverse is the standard Snyder
Transverse Mercator expansion. Both round-trip to well under a millimetre at
city scale (see ``tests/test_datum.py``).

No external dependencies — plain ``math``, so it runs anywhere PySide6 does.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtGui import QVector3D


# WGS84 ellipsoid + UTM constants.
_A = 6378137.0                 # semi-major axis
_F = 1 / 298.257223563         # flattening
_E2 = _F * (2 - _F)            # first eccentricity squared
_EP2 = _E2 / (1 - _E2)         # second eccentricity squared
_K0 = 0.9996                   # UTM scale factor
_FALSE_EASTING = 500000.0
_FALSE_NORTHING = 10000000.0   # applied in the southern hemisphere


def zone_for_lon(lon: float) -> int:
    """UTM zone number (1..60) containing longitude ``lon`` (degrees)."""
    return int((lon + 180) / 6) + 1


def _lon0(zone: int) -> float:
    """Central-meridian longitude of ``zone`` in radians."""
    return math.radians((zone - 1) * 6 - 180 + 3)


def utm_forward(lat: float, lon: float, zone: int) -> tuple[float, float]:
    """Project geodetic ``lat``/``lon`` (degrees) onto UTM ``zone``.

    Returns ``(easting, northing)`` in metres with the standard false easting
    (500 km) and — for southern latitudes — false northing (10 000 km) applied,
    so the values match what surveying tools report. The zone is forced (not
    derived from ``lon``) to keep the datum's frame continuous across borders.
    """
    latr = math.radians(lat)
    lonr = math.radians(lon)
    n = _A / math.sqrt(1 - _E2 * math.sin(latr) ** 2)
    t = math.tan(latr) ** 2
    c = _EP2 * math.cos(latr) ** 2
    a = math.cos(latr) * (lonr - _lon0(zone))
    m = _A * ((1 - _E2 / 4 - 3 * _E2**2 / 64 - 5 * _E2**3 / 256) * latr
              - (3 * _E2 / 8 + 3 * _E2**2 / 32 + 45 * _E2**3 / 1024) * math.sin(2 * latr)
              + (15 * _E2**2 / 256 + 45 * _E2**3 / 1024) * math.sin(4 * latr)
              - (35 * _E2**3 / 3072) * math.sin(6 * latr))
    easting = (_K0 * n * (a + (1 - t + c) * a**3 / 6
               + (5 - 18 * t + t**2 + 72 * c - 58 * _EP2) * a**5 / 120)
               + _FALSE_EASTING)
    northing = _K0 * (m + n * math.tan(latr) * (a**2 / 2
               + (5 - t + 9 * c + 4 * c**2) * a**4 / 24
               + (61 - 58 * t + t**2 + 600 * c - 330 * _EP2) * a**6 / 720))
    if lat < 0:
        northing += _FALSE_NORTHING
    return easting, northing


def utm_inverse(easting: float, northing: float, zone: int,
                northern: bool) -> tuple[float, float]:
    """Inverse of :func:`utm_forward`: UTM → geodetic ``(lat, lon)`` in degrees."""
    x = easting - _FALSE_EASTING
    y = northing if northern else northing - _FALSE_NORTHING
    m = y / _K0
    mu = m / (_A * (1 - _E2 / 4 - 3 * _E2**2 / 64 - 5 * _E2**3 / 256))
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    phi1 = (mu
            + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
            + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
            + (151 * e1**3 / 96) * math.sin(6 * mu)
            + (1097 * e1**4 / 512) * math.sin(8 * mu))
    c1 = _EP2 * math.cos(phi1) ** 2
    t1 = math.tan(phi1) ** 2
    n1 = _A / math.sqrt(1 - _E2 * math.sin(phi1) ** 2)
    r1 = _A * (1 - _E2) / (1 - _E2 * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * _K0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * _EP2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * _EP2 - 3 * c1**2) * d**6 / 720)
    lon = _lon0(zone) + (
        d - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * _EP2 + 24 * t1**2) * d**5 / 120) / math.cos(phi1)
    return math.degrees(lat), math.degrees(lon)


@dataclass
class SceneDatum:
    """Anchors the scene at a geographic point; converts geodetic ↔ local metres.

    ``lat``/``lon`` (degrees, WGS84) and ``alt`` (metres) are the sole source of
    truth — the UTM zone, hemisphere and false-origin offsets are derived and
    kept in sync in :meth:`__post_init__`. Serialise only ``lat``/``lon``/``alt``.
    """

    lat: float
    lon: float
    alt: float = 0.0

    def __post_init__(self) -> None:
        self.lat = float(self.lat)
        self.lon = float(self.lon)
        self.alt = float(self.alt)
        self.zone = zone_for_lon(self.lon)
        self.northern = self.lat >= 0
        # UTM coordinates of the anchor — the local-frame origin.
        self._east0, self._north0 = utm_forward(self.lat, self.lon, self.zone)

    @property
    def hemisphere(self) -> str:
        return "N" if self.northern else "S"

    # ---- Conversions --------------------------------------------------------
    def geodetic_to_local(self, lat: float, lon: float,
                          alt: float = 0.0) -> QVector3D:
        """Geodetic (degrees + metres) → local scene metres (X east, Y north)."""
        east, north = utm_forward(lat, lon, self.zone)
        return QVector3D(east - self._east0, north - self._north0, alt - self.alt)

    def local_to_geodetic(self, point: QVector3D) -> tuple[float, float, float]:
        """Local scene metres → geodetic ``(lat, lon, alt)`` (degrees + metres)."""
        lat, lon = utm_inverse(point.x() + self._east0,
                               point.y() + self._north0,
                               self.zone, self.northern)
        return lat, lon, point.z() + self.alt

    def utm_to_local(self, east: float, north: float,
                     alt: float = 0.0) -> QVector3D:
        """UTM metres (in the datum's frozen zone — survey CSVs report these
        directly) → local scene metres. Pure offsets, no reprojection."""
        return QVector3D(east - self._east0, north - self._north0,
                         alt - self.alt)

    def local_to_utm(self, point: QVector3D) -> tuple[float, float, float]:
        """Local scene metres → UTM ``(east, north, alt)`` in the frozen zone."""
        return (point.x() + self._east0, point.y() + self._north0,
                point.z() + self.alt)

    # ---- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict:
        return {"lat": self.lat, "lon": self.lon, "alt": self.alt}

    @classmethod
    def from_dict(cls, data: dict) -> "SceneDatum":
        return cls(data["lat"], data["lon"], data.get("alt", 0.0))
