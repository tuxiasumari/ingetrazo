"""Extension inference — snap to the collinear continuation of a nearby edge.

SketchUp's dashed-extension guide: when the cursor lines up with the prolongation
of an existing edge (beyond its endpoints), the point snaps onto that line so you
can draw in line with it.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.scene import Scene
from core.snap import compute_snap


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(float(x), float(y), float(z))


def _w2p(p: QVector3D):
    return (p.x() * 100.0, p.y() * 100.0)


def _snap(scene, cand, **kw):
    return compute_snap(cand, _w2p(cand), scene, _w2p,
                        threshold_px=9.0, edge_threshold_px=14.0, **kw)


def test_extension_snaps_onto_the_edge_line():
    scene = Scene()
    scene.mesh.add_edge(V(0, 0, 0), V(2, 0, 0))     # edge along +X
    r = _snap(scene, V(3.0, 0.05, 0.0), start_point=V(2, 0, 0))
    assert r.kind == "extension"
    assert abs(r.point.y()) < 1e-9                  # projected onto the line
    assert abs(r.point.x() - 3.0) < 1e-9
    assert r.guide is not None


def test_extension_does_not_fire_off_the_line():
    scene = Scene()
    scene.mesh.add_edge(V(0, 0, 0), V(2, 0, 0))
    r = _snap(scene, V(3.0, 0.5, 0.0), start_point=V(2, 0, 0))
    assert r.kind != "extension"


def test_extension_not_on_the_segment_itself():
    # A point on the segment is an on-edge snap, not an extension.
    scene = Scene()
    scene.mesh.add_edge(V(0, 0, 0), V(2, 0, 0))
    r = _snap(scene, V(1.0, 0.02, 0.0), start_point=V(0, 0, 0))
    assert r.kind != "extension"


def test_endpoint_wins_over_extension():
    # Near a vertex, the endpoint snap takes priority over the extension line.
    scene = Scene()
    scene.mesh.add_edge(V(0, 0, 0), V(2, 0, 0))
    r = _snap(scene, V(2.01, 0.01, 0.0), start_point=V(0, 0, 0))
    assert r.kind == "endpoint"


def test_extension_snaps_to_crossing_with_perpendicular_edge():
    # Extend a 10 m line onto a perpendicular one 20 cm past its end: snapping
    # to the exact crossing, as a green connection point, with the dashed guide.
    scene = Scene()
    scene.mesh.add_edge(V(0, 0, 0), V(10, 0, 0))         # line A
    scene.mesh.add_edge(V(10.2, -1, 0), V(10.2, 1, 0))   # perpendicular B
    r = _snap(scene, V(10.15, 0.02, 0.0), start_point=V(10, 0, 0))
    assert r.kind == "intersection"
    assert abs(r.point.x() - 10.2) < 1e-4   # float32 precision
    assert abs(r.point.y()) < 1e-4
    assert r.guide is not None


def test_extension_needs_a_collinear_draw_direction():
    # Drawing away from the edge's direction must not trigger its extension.
    scene = Scene()
    scene.mesh.add_edge(V(0, 0, 0), V(10, 0, 0))
    r = _snap(scene, V(10.1, 0.5, 0.0), start_point=V(10, 0, 0))  # heading off +Y
    assert r.kind not in ("extension", "intersection")


# ---- perpendicular-to-edge inference ----------------------------------------

def _proj_factory(cand):
    def proj(start, direction):
        d = direction.normalized()
        t = QVector3D.dotProduct(cand - start, d)
        return start + d * t
    return proj


def test_perpendicular_to_angled_edge_locks():
    import math
    scene = Scene()
    scene.mesh.add_edge(V(0, 0, 0), V(3, 3, 0))     # 45° line
    scene.mesh.add_edge(V(1, -1, 0), V(4, 2, 0))    # parallel
    ang = math.radians(135 + 1.5)                   # ~perpendicular, 1.5° off
    cand = V(math.cos(ang) * 2, math.sin(ang) * 2, 0)
    r = compute_snap(cand, _w2p(cand), scene, _w2p, threshold_px=9.0,
                     edge_threshold_px=14.0, start_point=V(0, 0, 0),
                     project_onto_line=_proj_factory(cand), inference_angle_deg=3.0)
    assert r.kind == "reference"                    # perpendicular lock
    # The locked direction is exactly perpendicular to the 45° edge.
    dirn = (r.point - V(0, 0, 0)).normalized()
    assert abs(QVector3D.dotProduct(dirn, V(-1, 1, 0).normalized())) > 0.999


def test_no_perpendicular_lock_when_not_square():
    scene = Scene()
    scene.mesh.add_edge(V(0, 0, 0), V(3, 3, 0))
    cand = V(2.0, 0.3, 0.0)                          # not perpendicular
    r = compute_snap(cand, _w2p(cand), scene, _w2p, threshold_px=9.0,
                     edge_threshold_px=14.0, start_point=V(0, 0, 0),
                     project_onto_line=_proj_factory(cand), inference_angle_deg=3.0)
    assert r.kind != "reference"


def test_perpendicular_to_axis_aligned_wall_locks():
    # The wall is axis-aligned, so its perpendicular is an axis — but starting on
    # the wall and drawing square to it must still lock (it runs before the axis
    # inference). This is the "perpendicular between two parallel walls" case.
    scene = Scene()
    scene.mesh.add_edge(V(0, 0, 0), V(5, 0, 0))   # wall along X
    cand = V(2.0, 2.0, 0.0)                        # straight +Y from a point on it
    r = compute_snap(cand, _w2p(cand), scene, _w2p, threshold_px=9.0,
                     edge_threshold_px=14.0, start_point=V(2, 0, 0),
                     project_onto_line=_proj_factory(cand), inference_angle_deg=3.0)
    assert r.kind == "reference"
    assert abs((r.point - V(2, 0, 0)).normalized().y()) > 0.999  # exact +Y
