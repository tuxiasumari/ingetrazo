# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""GeoPath (Track G): the traced-path entity, its commands, and .igz round-trip.
Headless — GeoPath is deliberately separate from the topology mesh."""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.history import (
    AddGeoPathCommand,
    DeleteGeoPathsCommand,
    History,
    MoveGeoPathNodeCommand,
)
from core.scene import Scene
from formats import igz
from georef.geopath import GeoPath


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


# ---- Entity --------------------------------------------------------------------

def test_length_open():
    p = GeoPath([V(0, 0), V(3, 0), V(3, 4)])
    assert abs(p.length() - 7.0) < 1e-9


def test_length_closed_includes_closing_edge():
    p = GeoPath([V(0, 0), V(4, 0), V(4, 3)], closed=True)
    # 4 + 3 + hypot(4,3)=5 = 12
    assert abs(p.length() - 12.0) < 1e-9


def test_profile_points_closes_loop():
    p = GeoPath([V(0, 0), V(4, 0), V(4, 3)], closed=True)
    pts = p.profile_points()
    assert len(pts) == 4
    assert (pts[-1].x(), pts[-1].y()) == (0.0, 0.0)


def test_open_profile_points_unchanged():
    p = GeoPath([V(0, 0), V(4, 0)])
    assert len(p.profile_points()) == 2


# ---- Commands ------------------------------------------------------------------

def test_add_and_undo():
    scene = Scene()
    hist = History(scene)
    path = GeoPath([V(0, 0), V(10, 0)])
    hist.execute(AddGeoPathCommand(path))
    assert scene.geo_paths == [path]
    hist.undo()
    assert scene.geo_paths == []
    hist.redo()
    assert scene.geo_paths == [path]


def test_delete_paths():
    scene = Scene()
    hist = History(scene)
    p1, p2 = GeoPath([V(0, 0), V(1, 0)]), GeoPath([V(2, 0), V(3, 0)])
    scene.geo_paths.extend([p1, p2])
    hist.execute(DeleteGeoPathsCommand([p1]))
    assert scene.geo_paths == [p2]
    hist.undo()
    assert scene.geo_paths == [p1, p2]


def test_move_node():
    scene = Scene()
    hist = History(scene)
    path = GeoPath([V(0, 0), V(10, 0), V(20, 0)])
    scene.geo_paths.append(path)
    hist.execute(MoveGeoPathNodeCommand(path, 1, V(10, 5)))
    assert (path.points[1].x(), path.points[1].y()) == (10.0, 5.0)
    hist.undo()
    assert (path.points[1].x(), path.points[1].y()) == (10.0, 0.0)


# ---- Serialisation -------------------------------------------------------------

def test_igz_round_trip(tmp_path):
    scene = Scene()
    scene.geo_paths.append(GeoPath([V(0, 0), V(10, 5), V(20, 0)], name="Road A"))
    scene.geo_paths.append(GeoPath([V(0, 0), V(4, 0), V(4, 4)], closed=True))
    path = tmp_path / "paths.igz"
    igz.save_scene(scene, path)

    loaded = Scene()
    igz.load_into(loaded, path)
    assert len(loaded.geo_paths) == 2
    a, b = loaded.geo_paths
    assert a.name == "Road A" and not a.closed and len(a.points) == 3
    assert b.closed and len(b.points) == 3


def test_plain_document_has_no_geo_paths(tmp_path):
    scene = Scene()
    scene.add_edge(V(0, 0), V(1, 0))
    path = tmp_path / "plain.igz"
    igz.save_scene(scene, path)
    assert '"geo_paths"' not in path.read_text()
    loaded = Scene()
    igz.load_into(loaded, path)
    assert loaded.geo_paths == []


def test_clear_resets_geo_paths():
    scene = Scene()
    scene.geo_paths.append(GeoPath([V(0, 0), V(1, 0)]))
    scene.clear()
    assert scene.geo_paths == []


# ---- Convert to geometry (the bridge into the modelling engine) -----------------

def _convert(scene, hist, path):
    from core.edits import build_add_edges
    from core.history import CompoundCommand
    segs = [(a, b) for a, b in path.segments()]
    hist.execute(CompoundCommand(
        [build_add_edges(scene, segs, detect_faces=True),
         DeleteGeoPathsCommand([path])]))


def test_convert_open_path_to_edges():
    scene = Scene()
    hist = History(scene)
    p = GeoPath([V(0, 0), V(10, 0), V(10, 10)])
    scene.geo_paths.append(p)
    _convert(scene, hist, p)
    assert len(scene.mesh.edges) == 2
    assert len(scene.mesh.faces) == 0
    assert scene.geo_paths == []
    hist.undo()
    assert len(scene.mesh.edges) == 0
    assert scene.geo_paths == [p]


def test_convert_closed_path_makes_face():
    scene = Scene()
    hist = History(scene)
    p = GeoPath([V(0, 0), V(10, 0), V(10, 10), V(0, 10)], closed=True)
    scene.geo_paths.append(p)
    _convert(scene, hist, p)
    assert len(scene.mesh.faces) == 1     # a traced lot → a face to push/pull
    assert scene.geo_paths == []
