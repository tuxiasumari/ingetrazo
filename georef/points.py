# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Survey points — imported GPS / total-station control points (Track G).

The municipal-flow entry: the engineer walks the site with a GPS or total
station and comes back with a CSV of points in UTM — the classic surveying
``P,N,E,Z,D`` layout (point id, northing, easting, elevation, description).
Those points are the skeleton the whole trace hangs from, so they become
first-class georef entities in ``Scene.geo_points`` (the heterogeneous-Scene
principle: reference data never enters the topology mesh) — rendered as
markers with their names, and fed to the snap engine so lines, paths and
dimensions land EXACTLY on the surveyed coordinates.

Per the datum invariant, positions are stored in local scene metres. The
import converts UTM → local through the scene's :class:`SceneDatum`; when the
scene has no datum yet, one is anchored at the first point (its UTM zone and
hemisphere supplied by the user, since a bare CSV doesn't carry them).
"""
from __future__ import annotations

import csv
import io

from PySide6.QtGui import QVector3D


class GeoPoint:
    """One surveyed control point, in local scene metres."""

    def __init__(self, position, name: str = "", desc: str = "") -> None:
        self.position = QVector3D(position)
        self.name = name
        self.desc = desc

    # ---- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict:
        p = self.position
        entry: dict = {"position": [p.x(), p.y(), p.z()]}
        if self.name:
            entry["name"] = self.name
        if self.desc:
            entry["desc"] = self.desc
        return entry

    @classmethod
    def from_dict(cls, data: dict) -> "GeoPoint":
        return cls(QVector3D(*data.get("position", [0, 0, 0])),
                   name=data.get("name", ""),
                   desc=data.get("desc", ""))


def _num(cell: str) -> float:
    """Parse a coordinate cell, tolerating the comma decimal separator that
    Latin-American station software often emits (with ';' or tab delimiters)."""
    return float(cell.strip().replace(",", "."))


def parse_points_csv(text: str) -> list[dict]:
    """Parse survey-CSV text into rows of
    ``{"name", "north", "east", "z", "desc"}``.

    Accepts the standard ``P,N,E,Z[,D]`` column order with ',', ';', tab or
    whitespace delimiters; a header row (any non-numeric N/E) is skipped, as
    are blank/comment lines. Raises ``ValueError`` when no point parses —
    a wrong file should fail loudly, not import an empty set."""
    rows: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ";" in line:
            cells = next(csv.reader(io.StringIO(line), delimiter=";"))
        elif "," in line:
            cells = next(csv.reader(io.StringIO(line)))
        else:
            cells = line.split()
        cells = [c.strip() for c in cells]
        if len(cells) < 4:
            continue
        try:
            north, east, z = _num(cells[1]), _num(cells[2]), _num(cells[3])
        except ValueError:
            continue                        # header (or stray text) row
        rows.append({
            "name": cells[0],
            "north": north,
            "east": east,
            "z": z,
            "desc": ",".join(cells[4:]).strip() if len(cells) > 4 else "",
        })
    if not rows:
        raise ValueError("No survey points found (expected P,N,E,Z[,desc])")
    return rows


def points_from_rows(rows: list[dict], datum) -> list[GeoPoint]:
    """UTM rows → :class:`GeoPoint` list in the datum's local frame."""
    return [
        GeoPoint(datum.utm_to_local(r["east"], r["north"], r["z"]),
                 name=r["name"], desc=r["desc"])
        for r in rows
    ]


def datum_for_rows(rows: list[dict], zone: int, northern: bool):
    """A :class:`SceneDatum` anchored at the first point — used when the scene
    has none yet. The anchor altitude is the point's own elevation, so the
    imported site sits near Z=0 and absolute cotas stay recoverable through
    the datum."""
    from georef.datum import SceneDatum, utm_forward, utm_inverse
    first = rows[0]
    lat, lon = utm_inverse(first["east"], first["north"], zone, northern)
    datum = SceneDatum(lat, lon, alt=first["z"])
    if datum.zone != zone:
        # Near a zone boundary the CSV's forced zone can differ from the one
        # the anchor longitude derives — honour the survey's frame (the
        # "freeze the zone" doctrine) so offsets stay pure.
        datum.zone = zone
        datum.northern = northern
        datum._east0, datum._north0 = utm_forward(lat, lon, zone)
    return datum
