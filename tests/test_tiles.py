# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Slippy-map tiles (Track G, G1) headless core: tile math, source URL
templating, and the disk LRU cache. No GUI, no network."""
from __future__ import annotations

from georef.datum import SceneDatum
from georef.tiles import (
    PRESETS,
    TileCache,
    TileLayer,
    TileSource,
    custom_source,
    deg2num,
    num2deg,
    tile_bbox,
    tiles_covering,
)


# ---- Slippy math ---------------------------------------------------------------

def test_deg2num_known_value():
    # Well-known reference: (0,0) at zoom 0 maps to the single tile (0,0).
    x, y = deg2num(0.0, 0.0, 0)
    assert int(x) == 0 and int(y) == 0
    # The equator/prime-meridian point sits at the tile's centre.
    assert abs(x - 0.5) < 1e-9 and abs(y - 0.5) < 1e-9


def test_deg2num_zoom_scales():
    # At zoom z there are 2**z tiles per axis.
    x, y = deg2num(0.0, 179.999, 3)
    assert 0 <= x < 8 and 0 <= y < 8


def test_deg2num_num2deg_round_trip():
    for lat, lon in ((-12.0464, -77.0428), (59.9139, 10.7522), (0.0, 0.0)):
        for z in (2, 10, 18):
            x, y = deg2num(lat, lon, z)
            rlat, rlon = num2deg(x, y, z)
            assert abs(rlat - lat) < 1e-6
            assert abs(rlon - lon) < 1e-6


def test_latitude_clamped_to_mercator_limit():
    # Beyond ~85.05° Web Mercator is undefined; deg2num must not blow up.
    x, y = deg2num(89.9, 0.0, 5)
    assert y > -1e-6  # clamped to the top edge (~0), finite, not NaN
    assert y == y     # not NaN


def test_tile_bbox_orientation():
    lat_s, lon_w, lat_n, lon_e = tile_bbox(0, 0, 1)
    assert lat_n > lat_s   # north above south
    assert lon_e > lon_w   # east right of west
    # Zoom-1 top-left tile spans the NW quadrant of the world.
    assert abs(lon_w - (-180.0)) < 1e-6
    assert abs(lon_e - 0.0) < 1e-6


def test_tiles_covering_single_tile_at_zoom0():
    tiles = tiles_covering(-80, -179, 80, 179, 0)
    assert tiles == [(0, 0)]


def test_tiles_covering_small_bbox_is_contiguous_block():
    # A ~1 km box around Lima at zoom 16 covers a small contiguous block.
    lat, lon = -12.0464, -77.0428
    d = 0.005  # ~500 m
    tiles = tiles_covering(lat - d, lon - d, lat + d, lon + d, 16)
    assert len(tiles) >= 1
    xs = {t[0] for t in tiles}
    ys = {t[1] for t in tiles}
    # Contiguous: index ranges have no gaps.
    assert max(xs) - min(xs) + 1 == len(xs)
    assert max(ys) - min(ys) + 1 == len(ys)


# ---- Tile sources --------------------------------------------------------------

def test_source_url_templating_order():
    # ArcGIS serves z/y/x — templating must honour placeholder order.
    esri = PRESETS["esri_imagery"]
    url = esri.url(x=3, y=5, z=7)
    assert url.endswith("/7/5/3")


def test_osm_url():
    url = PRESETS["osm"].url(x=1, y=2, z=3)
    assert url == "https://tile.openstreetmap.org/3/1/2.png"


def test_subdomain_rotation():
    src = TileSource("t", "T", "https://{s}.example.com/{z}/{x}/{y}.png",
                     subdomains=("a", "b", "c"))
    seen = {src.url(x, 0, 0).split(".")[0].split("//")[1] for x in range(3)}
    assert seen == {"a", "b", "c"}


def test_no_google_tile_service_in_presets():
    # Invariant #5: Google's tile *service* is never a shipped default. (A path
    # segment like "GoogleMapsCompatible" is the WMTS CRS name — not Google.)
    from urllib.parse import urlparse
    banned = ("google.com", "googleapis.com", "gstatic.com", "ggpht.com")
    for src in PRESETS.values():
        host = urlparse(src.url_template.replace("{s}", "a")).netloc.lower()
        assert not any(b in host for b in banned)


def test_custom_source_carries_url():
    c = custom_source("https://mytiles.example/{z}/{x}/{y}.jpg", max_zoom=20)
    assert c.id == "custom"
    assert c.max_zoom == 20
    assert c.url(1, 1, 1) == "https://mytiles.example/1/1/1.jpg"


# ---- Disk cache ----------------------------------------------------------------

def test_cache_put_get_round_trip(tmp_path):
    cache = TileCache(tmp_path)
    assert cache.get("osm", 1, 2, 3) is None
    cache.put("osm", 1, 2, 3, b"PNGDATA")
    assert cache.get("osm", 1, 2, 3) == b"PNGDATA"


def test_cache_path_layout(tmp_path):
    cache = TileCache(tmp_path)
    p = cache.path_for("esri_imagery", 4, 5, 6)
    assert p == tmp_path / "esri_imagery" / "6" / "4" / "5"


def test_cache_survives_reopen(tmp_path):
    TileCache(tmp_path).put("osm", 0, 0, 0, b"X")
    # A fresh cache over the same root reads the persisted tile (offline reopen).
    assert TileCache(tmp_path).get("osm", 0, 0, 0) == b"X"


# ---- Tile layer (projection to local metres) ----------------------------------

def test_layer_visible_tiles_nonempty_and_contiguous():
    datum = SceneDatum(-12.0464, -77.0428)
    layer = TileLayer(PRESETS["esri_imagery"], zoom=16, radius_m=1000)
    tiles = layer.visible_tiles(datum)
    assert len(tiles) >= 4
    xs = {t[0] for t in tiles}
    ys = {t[1] for t in tiles}
    assert max(xs) - min(xs) + 1 == len(xs)
    assert max(ys) - min(ys) + 1 == len(ys)


def test_layer_quad_local_geometry():
    datum = SceneDatum(-12.0464, -77.0428)
    layer = TileLayer(PRESETS["esri_imagery"], zoom=16, radius_m=1000)
    x, y = layer.visible_tiles(datum)[0]
    quad = layer.quad_local(datum, x, y)
    assert len(quad) == 6                      # two triangles
    positions = [p for p, _ in quad]
    assert all(abs(p.z()) < 1e-6 for p in positions)   # flat at Z=0 (G1)
    # A z16 tile is ~600 m wide near the equator; corners span a sane size.
    xs = [p.x() for p in positions]
    ys = [p.y() for p in positions]
    assert 50 < (max(xs) - min(xs)) < 5000
    assert 50 < (max(ys) - min(ys)) < 5000


def test_layer_north_edge_maps_to_higher_y():
    # UV (u,0) is the tile's north edge; it must project to a larger local Y
    # than the south edge (u,1) — the map is north-up.
    datum = SceneDatum(-12.0464, -77.0428)
    layer = TileLayer(PRESETS["esri_imagery"], zoom=16, radius_m=1000)
    x, y = layer.visible_tiles(datum)[0]
    quad = layer.quad_local(datum, x, y)
    north = [p for p, uv in quad if uv[1] == 0.0]
    south = [p for p, uv in quad if uv[1] == 1.0]
    assert min(p.y() for p in north) > max(p.y() for p in south)


# ---- Capture patches (multi-region base map) -----------------------------------

def test_default_patch_matches_radius_square():
    datum = SceneDatum(-12.0464, -77.0428)
    layer = TileLayer(PRESETS["esri_imagery"], zoom=16, radius_m=1000)
    assert set(layer.flat_tiles(datum)) == set(layer.visible_tiles(datum))


def test_strip_covers_far_fewer_tiles_than_square():
    datum = SceneDatum(-12.0464, -77.0428)
    layer = TileLayer(PRESETS["esri_imagery"], zoom=15)
    # A 500 m × 20 km strip vs a 20 km × 20 km square at the same zoom.
    layer.set_rectangle(width_m=500, length_m=20000)
    strip = layer.flat_tiles(datum)
    layer.set_rectangle(width_m=20000, length_m=20000)
    square = layer.flat_tiles(datum)
    assert len(strip) < len(square) / 5      # the strip is a small fraction
    assert len(strip) < 60                     # bounded — a road stays cheap


def test_multiple_patches_union_and_dedup():
    datum = SceneDatum(-12.0464, -77.0428)
    layer = TileLayer(PRESETS["esri_imagery"], zoom=15)
    layer.set_rectangle(width_m=600, length_m=600, cx=0, cy=0)
    one = set(layer.flat_tiles(datum))
    layer.add_patch(5000, 5000, 300, 300)     # a second site 5 km away
    two = set(layer.flat_tiles(datum))
    assert two > one                            # grew
    # Overlapping patch adds nothing new (dedup).
    layer.add_patch(0, 0, 300, 300)
    assert set(layer.flat_tiles(datum)) == two


def test_cache_lru_eviction(tmp_path):
    cache = TileCache(tmp_path, max_bytes=30)
    for i in range(10):
        cache.put("osm", i, 0, 5, b"0123456789")  # 10 bytes each
    # Budget is 30 B → at most ~3 tiles remain; the newest survive.
    remaining = [i for i in range(10) if cache.get("osm", i, 0, 5) is not None]
    total = sum(len(b"0123456789") for _ in remaining)
    assert total <= 30
    assert 9 in remaining  # most-recently written kept
