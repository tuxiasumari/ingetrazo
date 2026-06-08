"""Shared-vertex, non-manifold connectivity mesh (core.mesh).

Phase M0 of the topology migration (docs/halfedge-migration-plan.md): the new
model is built and tested in parallel; the running app is not wired to it yet.

The point of the new model: vertices are shared objects, edges carry a radial
list of incident faces (which may exceed two), and moving a vertex moves every
edge/face referencing it for free — no position matching, no float tolerance.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.mesh import Edge, Face, Mesh, Vertex


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(float(x), float(y), float(z))


# ---- welding ----------------------------------------------------------------

def test_coincident_positions_weld_to_one_vertex():
    m = Mesh()
    a = m.vertex(V(1, 1, 1))
    b = m.vertex(V(1, 1, 1))
    assert a is b
    assert len(m.vertices) == 1


def test_weld_within_tolerance():
    m = Mesh()
    a = m.vertex(V(1, 0, 0))
    b = m.vertex(V(1.00001, 0, 0))  # < 0.1 mm
    assert a is b


def test_two_edges_sharing_a_corner_share_the_vertex():
    m = Mesh()
    e1 = m.add_edge(V(0, 0), V(1, 0))
    e2 = m.add_edge(V(1, 0), V(1, 1))
    # The shared corner is a single Vertex object, referenced by both edges.
    assert e1.v1 is e2.v0
    assert len(m.vertices) == 3
    assert e1.v1.edges == {e1, e2}


def test_add_edge_dedups():
    m = Mesh()
    e1 = m.add_edge(V(0, 0), V(2, 0))
    e2 = m.add_edge(V(2, 0), V(0, 0))  # reversed — same edge
    assert e1 is e2
    assert len(m.edges) == 1


def test_degenerate_edge_rejected():
    m = Mesh()
    try:
        m.add_edge(V(0, 0), V(0, 0))
    except ValueError:
        return
    raise AssertionError("degenerate edge should raise")


# ---- radial incidence (non-manifold) ----------------------------------------

def test_edge_between_two_faces_has_both():
    m = Mesh()
    m.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    m.add_face([V(1, 0), V(2, 0), V(2, 1), V(1, 1)])
    shared = m.find_edge(m.vertex_at(V(1, 0)), m.vertex_at(V(1, 1)))
    assert shared is not None
    assert len(shared.faces) == 2


def test_non_manifold_edge_carries_three_faces():
    # Three faces meeting along the edge (0,0,0)-(0,0,1) — like two walls and a
    # floor sharing a corner edge. The legacy model can't express this cleanly.
    m = Mesh()
    e = (V(0, 0, 0), V(0, 0, 1))
    m.add_face([e[0], e[1], V(1, 0, 1), V(1, 0, 0)])   # wall in +x
    m.add_face([e[0], e[1], V(0, 1, 1), V(0, 1, 0)])   # wall in +y
    m.add_face([e[0], e[1], V(-1, 0, 1), V(-1, 0, 0)])  # wall in -x
    shared = m.find_edge(m.vertex_at(V(0, 0, 0)), m.vertex_at(V(0, 0, 1)))
    assert len(shared.faces) == 3


def test_remove_face_detaches_incidence():
    m = Mesh()
    f1 = m.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    m.add_face([V(1, 0), V(2, 0), V(2, 1), V(1, 1)])
    shared = m.find_edge(m.vertex_at(V(1, 0)), m.vertex_at(V(1, 1)))
    assert len(shared.faces) == 2
    m.remove_face(f1)
    assert f1 not in m.faces
    assert len(shared.faces) == 1            # only the survivor remains
    assert shared in m.edges                 # shared edge still there


def test_vertex_faces_query():
    m = Mesh()
    m.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    m.add_face([V(1, 0), V(2, 0), V(2, 1), V(1, 1)])
    corner = m.vertex_at(V(1, 0))
    assert len(corner.faces()) == 2


# ---- the headline: move follows for free ------------------------------------

def test_move_vertex_drags_all_incident_geometry():
    # Two faces sharing the edge (1,0)-(1,1). Moving those shared vertices up
    # must lift the matching corner of *both* faces — they hold the same
    # objects, so it is automatic (this is the gable-ridge mechanic, now free).
    m = Mesh()
    left = m.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    right = m.add_face([V(1, 0), V(2, 0), V(2, 1), V(1, 1)])
    m.move_vertex(m.vertex_at(V(1, 0)), V(0, 0, 1))
    m.move_vertex(m.vertex_at(V(1, 1)), V(0, 0, 1))

    def raised(face):
        return {(round(p.x()), round(p.y()), round(p.z())) for p in face.vertices
                if abs(p.z() - 1) < 1e-6}

    assert raised(left) == {(1, 0, 1), (1, 1, 1)}
    assert raised(right) == {(1, 0, 1), (1, 1, 1)}


def test_move_vertex_rekeys_registry():
    m = Mesh()
    v = m.vertex(V(0, 0, 0))
    m.move_vertex(v, V(5, 0, 0))
    assert m.vertex_at(V(0, 0, 0)) is None
    assert m.vertex_at(V(5, 0, 0)) is v
    # A later weld at the new spot reuses it.
    assert m.vertex(V(5, 0, 0)) is v


# ---- face geometry (parity with legacy Face) --------------------------------

def test_face_geometry_basics():
    m = Mesh()
    f = m.add_face([V(0, 0), V(2, 0), V(2, 2), V(0, 2)])
    assert abs(f.area() - 4.0) < 1e-6
    n = f.normal()
    assert abs(n.x()) < 1e-6 and abs(n.y()) < 1e-6 and abs(abs(n.z()) - 1) < 1e-6
    c = f.centroid()
    assert abs(c.x() - 1) < 1e-6 and abs(c.y() - 1) < 1e-6
    assert len(f.triangulate()) == 2  # a quad → two triangles


def test_add_face_creates_boundary_edges_and_reuses_them():
    m = Mesh()
    m.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    assert len(m.edges) == 4
    # An adjacent face reuses the shared edge instead of stacking a duplicate.
    m.add_face([V(1, 0), V(2, 0), V(2, 1), V(1, 1)])
    assert len(m.edges) == 7  # 4 + 3 new (one shared)


# ---- M1: legacy read compatibility ------------------------------------------

def test_mesh_face_exposes_positions_like_legacy():
    m = Mesh()
    f = m.add_face(
        [V(0, 0), V(2, 0), V(2, 2), V(0, 2)],
        [[V(0.5, 0.5), V(1.5, 0.5), V(1.5, 1.5), V(0.5, 1.5)]],
    )
    # .vertices / .holes read as plain positions (not Vertex objects), matching
    # the legacy core.geometry.Face interface the renderer/save consume.
    assert all(isinstance(p, QVector3D) for p in f.vertices)
    assert all(isinstance(p, QVector3D) for loop in f.holes for p in loop)
    # connectivity is still vertices underneath
    assert all(isinstance(v, Vertex) for v in f.loop)
    # a holed face triangulates as a donut (more than a plain quad's 2 tris)
    assert len(f.triangulate()) > 2


def test_mesh_is_read_compatible_with_igz_save(tmp_path):
    import json
    from formats.igz import save_scene

    m = Mesh()
    m.add_face(
        [V(0, 0), V(4, 0), V(4, 4), V(0, 4)],
        [[V(1, 1), V(3, 1), V(3, 3), V(1, 3)]],
    )
    m.add_edge(V(0, 0), V(0, 5))

    out = tmp_path / "mesh.igz"
    save_scene(m, out)  # reads mesh.edges (.a/.b) + mesh.faces (.vertices/.holes)
    data = json.loads(out.read_text())

    assert data["scene"]["faces"][0]["vertices"][0] == [0.0, 0.0, 0.0]
    assert len(data["scene"]["faces"][0]["holes"][0]) == 4
    assert len(data["scene"]["edges"]) >= 1


# ---- M2: split_edge (the operation that needed hacks in the legacy model) ----

def test_split_edge_propagates_to_both_faces():
    # Two faces share the edge (1,0)-(1,1). Splitting it at the midpoint must
    # insert that vertex into BOTH faces' loops — the gable-propagation case,
    # which in the legacy model needed split_edge_in_faces + the holes patch.
    m = Mesh()
    left = m.add_face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    right = m.add_face([V(1, 0), V(2, 0), V(2, 1), V(1, 1)])
    shared = m.find_edge(m.vertex_at(V(1, 0)), m.vertex_at(V(1, 1)))

    e0, e1 = m.split_edge(shared, V(1, 0.5))
    mid = m.vertex_at(V(1, 0.5))

    assert mid in left.loop and mid in right.loop      # both faces gained it
    assert len(left.loop) == 5 and len(right.loop) == 5
    assert shared not in m.edges                        # old edge gone
    # both sub-edges border both faces
    assert set(e0.faces) == {left, right}
    assert set(e1.faces) == {left, right}


def test_split_edge_non_manifold_propagates_to_all():
    # Three faces along one edge — the split lands in all three.
    m = Mesh()
    a, b = V(0, 0, 0), V(0, 0, 2)
    m.add_face([a, b, V(1, 0, 2), V(1, 0, 0)])
    m.add_face([a, b, V(0, 1, 2), V(0, 1, 0)])
    m.add_face([a, b, V(-1, 0, 2), V(-1, 0, 0)])
    e = m.find_edge(m.vertex_at(a), m.vertex_at(b))

    e0, e1 = m.split_edge(e, V(0, 0, 1))
    mid = m.vertex_at(V(0, 0, 1))

    assert all(len(f.loop) == 5 for f in mid.faces())
    assert len(mid.faces()) == 3
    assert len(e0.faces) == 3 and len(e1.faces) == 3


def test_split_edge_then_move_forms_a_gable():
    # Split the shared ridge edge, then raise the midpoint: both slopes deform —
    # the whole gable mechanic, now two trivial mesh ops.
    m = Mesh()
    left = m.add_face([V(0, 0, 2), V(2, 0, 2), V(2, 2, 2), V(0, 2, 2)])
    # second slope sharing the (2,0,2)-(2,2,2) edge
    right = m.add_face([V(2, 0, 2), V(4, 0, 2), V(4, 2, 2), V(2, 2, 2)])
    ridge = m.find_edge(m.vertex_at(V(2, 0, 2)), m.vertex_at(V(2, 2, 2)))
    m.split_edge(ridge, V(2, 1, 2))
    apex = m.vertex_at(V(2, 1, 2))
    m.move_vertex(apex, V(0, 0, 1))  # raise the ridge point
    assert any(abs(p.z() - 3) < 1e-6 for p in left.vertices)
    assert any(abs(p.z() - 3) < 1e-6 for p in right.vertices)
