# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Terrain elevation (DEM) sampling — Track G, G2 (minimal: sample only).

Fetches raster DEM tiles (elevation encoded in RGB), decodes them to metre
heightfields with NumPy, and samples elevation at any geographic point. This is
the **minimal** slice of G2 the roadmap calls for: no 3D terrain mesh, no
render — just ``elevation_at(lat, lon)``, which is exactly what the G4 profile
tool needs. The full drape-over-terrain render is the rest of G2, later.

Source: **AWS Terrain Tiles** (``terrarium`` encoding) — an open, no-API-key
global DEM (SRTM / NED / etc.), so IngeTrazo pulls elevation with zero
credentials and zero new services. Encoding is provider-declared, so a Mapbox
Terrain-RGB URL (with a token) also works if the user pastes one.

Precision is honest (invariant #6): global ~30 m DEM = anteproyecto /
visualisation, **not** survey-grade. G6 will import the user's own
photogrammetry/LiDAR for fine work.

NumPy is used here for the first time in the project (anticipated in the Stack
notes). Tiles are cached on disk via the same :class:`~georef.tiles.TileCache`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage, QVector3D

from georef.tiles import TileCache, deg2num


TILE_PX = 256  # DEM tiles, like map tiles, are 256×256


@dataclass(frozen=True)
class DEMSource:
    """A raster DEM provider: an ``{z}/{x}/{y}`` URL plus its RGB encoding.

    ``encoding`` is ``"terrarium"`` (AWS Terrain Tiles / Mapzen) or ``"mapbox"``
    (Mapbox Terrain-RGB). Duck-compatible with :class:`~georef.tiles.TileSource`
    (``id``/``url``/``max_zoom``), so it rides the same fetcher and cache.
    """

    id: str
    name: str
    url_template: str
    encoding: str = "terrarium"
    max_zoom: int = 15
    attribution: str = ""

    def url(self, x: int, y: int, z: int) -> str:
        return (self.url_template
                .replace("{z}", str(z))
                .replace("{x}", str(x))
                .replace("{y}", str(y)))


AWS_TERRAIN = DEMSource(
    id="aws_terrarium",
    name="AWS Terrain Tiles (SRTM/NED)",
    url_template="https://s3.amazonaws.com/elevation-tiles-prod/terrarium/"
                 "{z}/{x}/{y}.png",
    encoding="terrarium",
    max_zoom=15,
    attribution="Terrain Tiles hosted on AWS by Mapzen/Nextzen — sources "
                "include SRTM, NED, Copernicus. https://registry.opendata.aws/terrain-tiles/",
)


def decode_elevation(image: QImage, encoding: str = "terrarium") -> np.ndarray:
    """Decode an RGB-encoded DEM tile to a ``(H, W)`` float32 metre heightfield.

    - ``terrarium``: ``h = R*256 + G + B/256 - 32768``
    - ``mapbox``:    ``h = -10000 + (R*65536 + G*256 + B) * 0.1``
    """
    img = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    buf = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(h, bpl)
    a = buf[:, : w * 4].reshape(h, w, 4).astype(np.float32)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    if encoding == "mapbox":
        return -10000.0 + (r * 65536.0 + g * 256.0 + b) * 0.1
    # terrarium (default)
    return (r * 256.0 + g + b / 256.0) - 32768.0


def bilinear(field: np.ndarray, px: float, py: float) -> float:
    """Bilinearly sample ``field`` at fractional pixel ``(px, py)``.

    Coordinates are clamped to the array, so edge samples degrade gracefully
    (a G2-minimal simplification: cross-tile seams read the nearest in-tile
    pixel; the ~pixel-scale error is negligible for a profile).
    """
    h, w = field.shape
    x0 = int(math.floor(px))
    y0 = int(math.floor(py))
    fx = px - x0
    fy = py - y0
    x0 = min(max(x0, 0), w - 1)
    y0 = min(max(y0, 0), h - 1)
    x1 = min(x0 + 1, w - 1)
    y1 = min(y0 + 1, h - 1)
    top = field[y0, x0] * (1 - fx) + field[y0, x1] * fx
    bot = field[y1, x0] * (1 - fx) + field[y1, x1] * fx
    return float(top * (1 - fy) + bot * fy)


def pixel_in_tile(lat: float, lon: float, zoom: int) -> tuple[int, int, float, float]:
    """Locate ``(lat, lon)`` as ``(tile_x, tile_y, px, py)`` at ``zoom``.

    ``px``/``py`` are pixel coordinates within the tile (``0..TILE_PX``).
    """
    xf, yf = deg2num(lat, lon, zoom)
    tx, ty = int(math.floor(xf)), int(math.floor(yf))
    return tx, ty, (xf - tx) * TILE_PX, (yf - ty) * TILE_PX


class DEMSampler(QObject):
    """Elevation sampler over a scene datum.

    Decoded heightfields are cached in memory (and their PNGs on disk); missing
    tiles download asynchronously, emitting :attr:`changed` as each lands so a
    consumer (the G4 profile) can recompute. ``elevation_at`` returns ``None``
    for a not-yet-loaded tile — call :meth:`ensure_area` first, then react to
    :attr:`changed`.
    """

    #: Emitted when a newly decoded tile becomes available.
    changed = Signal()

    def __init__(self, datum, source: DEMSource = AWS_TERRAIN, zoom: int = 13,
                 cache: TileCache | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.datum = datum
        self.source = source
        self.zoom = int(zoom)
        self._fields: dict[tuple[int, int, int], np.ndarray] = {}
        self._fetcher = None
        self._cache = cache

    # ---- Fetching -----------------------------------------------------------
    def _ensure_fetcher(self):
        if self._fetcher is None:
            from georef.tile_fetcher import TileFetcher, default_cache_dir
            cache = self._cache or TileCache(default_cache_dir().parent / "dem")
            self._fetcher = TileFetcher(cache, parent=self)
            self._fetcher.tileReady.connect(self._on_tile)
        return self._fetcher

    def _on_tile(self, source_id, x, y, z, image) -> None:
        if source_id == self.source.id and z == self.zoom:
            self._fields[(x, y, z)] = decode_elevation(image, self.source.encoding)
            self.changed.emit()

    def _field_for(self, tx: int, ty: int):
        """Heightfield for tile ``(tx, ty)`` — from memory, or kick off a fetch."""
        key = (tx, ty, self.zoom)
        if key in self._fields:
            return self._fields[key]
        img = self._ensure_fetcher().request(self.source, tx, ty, self.zoom)
        if img is not None:  # disk-cache hit → decode now
            field = decode_elevation(img, self.source.encoding)
            self._fields[key] = field
            return field
        return None  # downloading; caller reacts to `changed`

    def ensure_area(self, lat_s: float, lon_w: float,
                    lat_n: float, lon_e: float) -> None:
        """Request every DEM tile covering the geodetic bbox (idempotent)."""
        from georef.tiles import tiles_covering
        for tx, ty in tiles_covering(lat_s, lon_w, lat_n, lon_e, self.zoom):
            self._field_for(tx, ty)

    # ---- Sampling -----------------------------------------------------------
    def elevation_at(self, lat: float, lon: float) -> float | None:
        """Elevation (metres) at ``(lat, lon)``, or ``None`` if not yet loaded."""
        tx, ty, px, py = pixel_in_tile(lat, lon, self.zoom)
        field = self._field_for(tx, ty)
        if field is None:
            return None
        return bilinear(field, px, py)

    def elevation_at_local(self, point: QVector3D) -> float | None:
        """Elevation at a local-metre scene point (via the datum)."""
        lat, lon, _ = self.datum.local_to_geodetic(point)
        return self.elevation_at(lat, lon)

    def has_tile(self, lat: float, lon: float) -> bool:
        tx, ty, _, _ = pixel_in_tile(lat, lon, self.zoom)
        return (tx, ty, self.zoom) in self._fields
