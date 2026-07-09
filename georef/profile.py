# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Longitudinal terrain profile (Track G, G4) — the Google-Earth parity.

Given a traced polyline (a road, a boundary, a pipeline) and a
:class:`~georef.dem.DEMSampler`, this samples the **terrain elevation** under
the trace at a regular chainage and returns a :class:`Profile`: station /
elevation pairs, total length and per-segment slopes. It's a headless action
(``build``-style, invariant #3) — the QPainter panel and the live-update wiring
sit on top; the maths here has no GUI.

Chainage is horizontal (XY) distance along the trace — the trace itself is flat
on the base map (Z=0); the profile is the ground *under* it, sampled from the
DEM. When the DEM tiles for the area aren't loaded yet, the affected samples
come back ``None`` and ``Profile.complete`` is ``False``; the caller re-runs
once the sampler emits ``changed``.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from PySide6.QtGui import QVector3D


@dataclass
class ProfileSample:
    """One sampled point along the trace."""
    station: float                  # horizontal distance from the start (m)
    x: float                        # local scene metres
    y: float
    elevation: float | None         # terrain elevation (m), None if not loaded


@dataclass
class Profile:
    """A sampled terrain profile along a polyline."""
    samples: list[ProfileSample] = field(default_factory=list)
    length: float = 0.0
    complete: bool = True           # every sample has an elevation

    def elevations(self) -> list[float]:
        return [s.elevation for s in self.samples if s.elevation is not None]

    def min_elevation(self) -> float | None:
        es = self.elevations()
        return min(es) if es else None

    def max_elevation(self) -> float | None:
        es = self.elevations()
        return max(es) if es else None

    def total_gain(self) -> float:
        """Cumulative uphill elevation gain (m)."""
        gain = 0.0
        prev = None
        for s in self.samples:
            if s.elevation is None:
                continue
            if prev is not None and s.elevation > prev:
                gain += s.elevation - prev
            prev = s.elevation
        return gain

    def slopes(self) -> list[tuple[float, float]]:
        """``(station, slope_percent)`` between consecutive sampled points."""
        out = []
        for a, b in zip(self.samples, self.samples[1:]):
            if a.elevation is None or b.elevation is None:
                continue
            ds = b.station - a.station
            if ds > 1e-9:
                out.append((a.station, (b.elevation - a.elevation) / ds * 100.0))
        return out


# ---- Polyline ordering ---------------------------------------------------------

def order_polyline(edges) -> list[QVector3D] | None:
    """Order a set of mesh edges into a single polyline of positions.

    Adjacency is by shared-vertex identity (``Edge.v0``/``v1``). Returns the
    ordered vertex positions for a simple open chain (two endpoints) or closed
    loop (no endpoints); ``None`` if the selection branches or is disconnected —
    a profile needs an unambiguous path.
    """
    edges = list(edges)
    if not edges:
        return None
    adj: dict = defaultdict(list)
    verts = set()
    for e in edges:
        adj[e.v0].append(e.v1)
        adj[e.v1].append(e.v0)
        verts.add(e.v0)
        verts.add(e.v1)
    # Any vertex touching more than two edges → ambiguous path.
    if any(len(adj[v]) > 2 for v in verts):
        return None
    endpoints = [v for v in verts if len(adj[v]) == 1]
    if len(endpoints) == 2:
        start = endpoints[0]
    elif not endpoints:
        start = next(iter(verts))          # closed loop
    else:
        return None                        # dangling / disconnected
    ordered = [start]
    prev, curr = None, start
    for _ in range(len(edges)):
        nbrs = [n for n in adj[curr] if n is not prev]
        if not nbrs:
            break
        nxt = nbrs[0]
        ordered.append(nxt)
        prev, curr = curr, nxt
        if curr is start:                  # closed the loop
            break
    positions = [v.position for v in ordered]
    # A disconnected selection walks only its first component — reject if we
    # didn't reach every vertex.
    if len(set(id(v) for v in ordered)) < len(verts):
        return None
    return positions


def polyline_from_selection(scene) -> list[QVector3D] | None:
    """Ordered polyline from the edges currently selected, or ``None``."""
    from core.mesh import Edge
    edges = [e for e in scene.selection if isinstance(e, Edge)]
    return order_polyline(edges)


# ---- Geometry helpers ----------------------------------------------------------

def polyline_length(pts: list[QVector3D]) -> float:
    """Horizontal (XY) length of the polyline."""
    total = 0.0
    for a, b in zip(pts, pts[1:]):
        total += math.hypot(b.x() - a.x(), b.y() - a.y())
    return total


def point_at_station(pts: list[QVector3D], s: float) -> tuple[float, float]:
    """Interpolate the ``(x, y)`` at horizontal chainage ``s`` along ``pts``."""
    if not pts:
        return 0.0, 0.0
    if s <= 0:
        return pts[0].x(), pts[0].y()
    acc = 0.0
    for a, b in zip(pts, pts[1:]):
        seg = math.hypot(b.x() - a.x(), b.y() - a.y())
        if acc + seg >= s:
            t = (s - acc) / seg if seg > 1e-12 else 0.0
            return a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t
        acc += seg
    return pts[-1].x(), pts[-1].y()


# ---- The action ----------------------------------------------------------------

def sample_profile(polyline: list[QVector3D], sampler,
                   spacing: float | None = None) -> Profile:
    """Sample terrain elevation along ``polyline`` using ``sampler``.

    ``spacing`` is the chainage step in metres; when ``None`` it defaults to
    length/256 (clamped to ≥1 m), giving a bounded, smooth profile. Requests the
    DEM tiles covering the trace first (idempotent); samples that fall on
    not-yet-loaded tiles come back ``None`` (``complete=False``).
    """
    pts = list(polyline)
    if len(pts) < 2:
        return Profile([], 0.0, True)
    length = polyline_length(pts)
    if length <= 1e-9:
        return Profile([], 0.0, True)
    if spacing is None:
        spacing = max(length / 256.0, 1.0)
    n = max(int(math.ceil(length / spacing)), 1)

    # Make sure the DEM covering the trace is (being) fetched.
    lats, lons = [], []
    for p in pts:
        la, lo, _ = sampler.datum.local_to_geodetic(p)
        lats.append(la)
        lons.append(lo)
    sampler.ensure_area(min(lats), min(lons), max(lats), max(lons))

    samples, complete = [], True
    for i in range(n + 1):
        s = min(length, i * spacing)
        x, y = point_at_station(pts, s)
        elev = sampler.elevation_at_local(QVector3D(x, y, 0.0))
        if elev is None:
            complete = False
        samples.append(ProfileSample(s, x, y, elev))
    return Profile(samples, length, complete)


def profile_to_csv(profile: Profile) -> str:
    """Render the profile as CSV text (station, x, y, elevation)."""
    lines = ["station_m,x_m,y_m,elevation_m"]
    for s in profile.samples:
        elev = "" if s.elevation is None else f"{s.elevation:.3f}"
        lines.append(f"{s.station:.3f},{s.x:.3f},{s.y:.3f},{elev}")
    return "\n".join(lines) + "\n"
