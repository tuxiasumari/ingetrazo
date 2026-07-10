# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Location lookup for the project locator (Track G).

Two no-API-key services, so the user reaches a site without typing coordinates:

- **Geocoding** by place name via **Nominatim** (OpenStreetMap) — returns a list
  of candidates with full context so the user disambiguates ("Santiago, Chile"
  vs "Santiago del Estero, Argentina"). Search only *gets you near*; the map
  pin is the ground truth, so an unnamed rural site is reached by panning from
  the nearest named place.
- **IP geolocation** via **ipapi.co** — a "locate me" starting point (city-level,
  approximate — refine on the map afterwards).

Pure URL builders + response parsers (tested offline) plus thin async QObject
wrappers over ``QNetworkAccessManager``. Nominatim's usage policy requires a real
User-Agent and ≤1 request/second — callers search on Enter, not per keystroke.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)

NOMINATIM = "https://nominatim.openstreetmap.org/search"
IPAPI = "https://ipapi.co/json/"
_USER_AGENT = b"IngeTrazo/0.0.1 (https://github.com/tuxiasumari/ingetrazo)"


@dataclass(frozen=True)
class Place:
    """A geocoding candidate."""
    name: str
    lat: float
    lon: float


def geocode_url(query: str, limit: int = 8) -> str:
    return f"{NOMINATIM}?q={quote(query)}&format=jsonv2&limit={int(limit)}"


def parse_places(data: bytes) -> list[Place]:
    """Parse a Nominatim jsonv2 response into candidates (best-effort)."""
    try:
        arr = json.loads(bytes(data))
    except (ValueError, TypeError):
        return []
    out = []
    for r in arr if isinstance(arr, list) else []:
        try:
            out.append(Place(r.get("display_name", ""),
                             float(r["lat"]), float(r["lon"])))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def parse_ip(data: bytes):
    """Parse an ipapi.co response into ``(lat, lon, label)`` or ``None``."""
    try:
        d = json.loads(bytes(data))
    except (ValueError, TypeError):
        return None
    lat, lon = d.get("latitude"), d.get("longitude")
    if lat is None or lon is None:
        return None
    label = ", ".join(str(x) for x in (d.get("city"), d.get("region"),
                                       d.get("country_name")) if x)
    try:
        return float(lat), float(lon), label
    except (ValueError, TypeError):
        return None


class _Fetcher(QObject):
    """Shared async GET helper with the required User-Agent."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)

    def _get(self, url: str, on_bytes, on_error) -> None:
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent", _USER_AGENT)
        req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                         QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self._nam.get(req)

        def done():
            try:
                if reply.error() != QNetworkReply.NetworkError.NoError:
                    on_error(reply.errorString())
                else:
                    on_bytes(bytes(reply.readAll()))
            finally:
                reply.deleteLater()
        reply.finished.connect(done)


class Geocoder(_Fetcher):
    """Async place-name search (Nominatim)."""

    resultsReady = Signal(list)   # list[Place]
    failed = Signal(str)

    def search(self, query: str) -> None:
        query = (query or "").strip()
        if not query:
            self.resultsReady.emit([])
            return
        self._get(geocode_url(query),
                  lambda data: self.resultsReady.emit(parse_places(data)),
                  self.failed.emit)


class IpLocator(_Fetcher):
    """Async approximate location from the client's IP (ipapi.co)."""

    located = Signal(float, float, str)
    failed = Signal(str)

    def locate(self) -> None:
        def on_bytes(data):
            res = parse_ip(data)
            if res is None:
                self.failed.emit("no location in response")
            else:
                self.located.emit(*res)
        self._get(IPAPI, on_bytes, self.failed.emit)
