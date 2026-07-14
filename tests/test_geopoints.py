# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Survey-point import (Track G, municipal flow): CSV parsing, UTM → local
through the datum, undoable command with datum anchoring, .igz persistence."""
from __future__ import annotations

import math

import pytest
from PySide6.QtGui import QVector3D

from core.history import (AddGeoPointsCommand, DeleteGeoPointsCommand,
                          History)
from core.scene import Scene
from formats import igz
from georef.datum import SceneDatum
from georef.points import (GeoPoint, datum_for_rows, parse_points_csv,
                           points_from_rows)

# A tiny plausible survey near Cusco (UTM 19S): station + two corners.
CSV_COMMA = """P,N,E,Z,DESC
E1,8503000.000,177000.000,3335.20,ESTACION
V1,8503012.500,177008.250,3336.10,VERTICE LOTE
V2,8503020.000,176995.000,3334.85,VERTICE LOTE
"""


def test_parse_comma_with_header():
    rows = parse_points_csv(CSV_COMMA)
    assert len(rows) == 3                           # header skipped
    assert rows[0] == {"name": "E1", "north": 8503000.0, "east": 177000.0,
                       "z": 3335.20, "desc": "ESTACION"}
    assert rows[1]["desc"] == "VERTICE LOTE"


def test_parse_semicolon_with_comma_decimals_and_whitespace():
    rows = parse_points_csv("E1;8503000,00;177000,00;3335,20;EST\n")
    assert rows[0]["north"] == 8503000.0 and rows[0]["z"] == 3335.2
    rows = parse_points_csv("P1  8503010.0  177005.0  3334.0\n")
    assert rows[0]["name"] == "P1" and rows[0]["desc"] == ""


def test_parse_garbage_raises():
    with pytest.raises(ValueError):
        parse_points_csv("this is not\na survey file\n")


def test_datum_anchors_first_point_and_preserves_distances():
    rows = parse_points_csv(CSV_COMMA)
    datum = datum_for_rows(rows, zone=19, northern=False)
    pts = points_from_rows(rows, datum)
    # First point IS the origin (and its elevation the datum altitude).
    assert pts[0].position.length() < 1e-3
    # Local frame preserves the survey's own UTM deltas exactly (offsets,
    # no reprojection).
    d01 = pts[1].position - pts[0].position
    assert abs(d01.x() - 8.25) < 1e-6               # east delta
    assert abs(d01.y() - 12.5) < 1e-6               # north delta
    assert abs(d01.z() - 0.9) < 1e-6
    # Round-trip: local → UTM gives back the CSV numbers.
    east, north, alt = datum.local_to_utm(pts[2].position)
    assert abs(east - 176995.0) < 1e-6
    assert abs(north - 8503020.0) < 1e-6
    assert abs(alt - 3334.85) < 1e-6


def test_command_sets_datum_and_undo_restores():
    scene = Scene()
    history = History(scene)
    rows = parse_points_csv(CSV_COMMA)
    datum = datum_for_rows(rows, 19, False)
    pts = points_from_rows(rows, datum)
    history.execute(AddGeoPointsCommand(pts, datum=datum))
    assert len(scene.geo_points) == 3
    assert scene.georef is datum
    history.undo()
    assert scene.geo_points == []
    assert scene.georef is None                     # datum travels with undo
    history.redo()
    assert len(scene.geo_points) == 3 and scene.georef is datum
    # Delete + undo restores order.
    history.execute(DeleteGeoPointsCommand(list(scene.geo_points)))
    assert scene.geo_points == []
    history.undo()
    assert [p.name for p in scene.geo_points] == ["E1", "V1", "V2"]


def test_snap_lands_exactly_on_survey_point():
    """The whole point of importing the survey: the pencil snaps EXACTLY to
    the surveyed coordinate. Points reach the snap engine as degenerate
    pseudo-edges (viewport._snap_scene); the endpoint snap fires and the
    direction-based inferences skip zero-length safely."""
    from types import SimpleNamespace
    from core.snap import compute_snap
    p = QVector3D(3, 4, 0.5)
    edge = SimpleNamespace(a=QVector3D(p), b=QVector3D(p))
    scene = SimpleNamespace(edges=[edge])

    def to_px(q):
        return (100.0 + (q.x() - 3) * 50, 100.0 - (q.y() - 4) * 50)

    cand = QVector3D(3.05, 4.02, 0.5)
    res = compute_snap(candidate_world=cand, candidate_pixel=to_px(cand),
                       scene=scene, world_to_pixel=to_px, threshold_px=9.0)
    assert res.kind == "endpoint"
    assert res.point == p                           # bit-exact, no drift


def test_igz_round_trip(tmp_path):
    scene = Scene()
    scene.georef = SceneDatum(-13.5, -71.9, alt=3335.2)
    scene.geo_points.append(GeoPoint(QVector3D(1.5, -2.25, 0.9),
                                     name="V1", desc="VERTICE"))
    p = tmp_path / "puntos.igz"
    igz.save_scene(scene, p)
    scene2 = Scene()
    igz.load_into(scene2, p)
    assert len(scene2.geo_points) == 1
    gp = scene2.geo_points[0]
    assert gp.name == "V1" and gp.desc == "VERTICE"
    assert (gp.position - QVector3D(1.5, -2.25, 0.9)).length() < 1e-6
    # clear() forgets them (File ▸ New leaves nothing behind)
    scene2.clear()
    assert scene2.geo_points == []
