# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Location lookup (Track G): URL building and response parsing. No network."""
from __future__ import annotations

from georef.geocode import Place, geocode_url, parse_ip, parse_places


def test_geocode_url_encodes_query():
    url = geocode_url("Cusco, Perú", limit=5)
    assert url.startswith("https://nominatim.openstreetmap.org/search?q=")
    assert "Cusco" in url and "%2C" in url        # comma percent-encoded
    assert "limit=5" in url and "format=jsonv2" in url


def test_parse_places_multiple_candidates():
    data = b'''[
      {"display_name": "Santiago, Region Metropolitana, Chile",
       "lat": "-33.4489", "lon": "-70.6693"},
      {"display_name": "Santiago del Estero, Argentina",
       "lat": "-27.7951", "lon": "-64.2615"}
    ]'''
    places = parse_places(data)
    assert len(places) == 2
    assert isinstance(places[0], Place)
    assert "Chile" in places[0].name
    assert abs(places[0].lat - (-33.4489)) < 1e-6


def test_parse_places_skips_bad_entries():
    data = b'[{"display_name": "ok", "lat": "1.0", "lon": "2.0"}, {"bad": true}]'
    places = parse_places(data)
    assert len(places) == 1


def test_parse_places_empty_and_malformed():
    assert parse_places(b"[]") == []
    assert parse_places(b"not json") == []


def test_parse_ip():
    data = b'{"latitude": -12.0464, "longitude": -77.0428, "city": "Lima", ' \
           b'"region": "Lima", "country_name": "Peru"}'
    res = parse_ip(data)
    assert res is not None
    lat, lon, label = res
    assert abs(lat - (-12.0464)) < 1e-6 and abs(lon - (-77.0428)) < 1e-6
    assert label == "Lima, Lima, Peru"


def test_parse_ip_missing_coords():
    assert parse_ip(b'{"city": "Nowhere"}') is None
    assert parse_ip(b"garbage") is None
