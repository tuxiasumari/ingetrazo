"""Push/pull on a solid commits a *consistently oriented* watertight mesh.

Root-fix step 2: after a nested push the solid is closed and seam-free, but its
winding used to come out inconsistent (a fresh strip wound opposite its coplanar
neighbour, a flipped cap) — latent until you tried to push that face and it
extruded inward. ``pushpull._mutate`` now runs ``orient_outward`` on the closed
solid, so every committed face's normal points out.

Regression bench: the irregular triangle prism from the engine notes, pushed on
its three side walls in every order (the "60 combinations" validation, made
permanent). Each result must be closed, have no unmerged coplanar seam, carry a
positive signed volume, and already be consistently oriented (``orient_outward``
finds nothing to flip).
"""
from __future__ import annotations

import itertools

from PySide6.QtGui import QVector3D

from core.history import History
from core.orient import is_closed, orient_outward, signed_volume
from core.scene import Scene
from tools.pushpull import PushPullTool


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


class _StubVP:
    def __init__(self, scene, history):
        self.scene = scene
        self.history = history

    def set_hover(self, *a):
        pass

    def set_suppressed_faces(self, *a):
        pass

    def update(self):
        pass


def _push(scene, hist, face, dist):
    tool = PushPullTool()
    tool.base_face = face
    tool.extrusion = dist
    tool._normal = face.normal()
    tool._anchor = face.centroid()
    tool._attached, tool._prism_cap = tool._classify_base(scene)
    tool._cap_positions = tool._cap_loop_positions(face)
    tool._commit(_StubVP(scene, hist))


def _prism_solid():
    """The irregular triangle extruded into a prism with the real tool."""
    a, b, c = (-0.2, -2.8), (2.7, -7.2), (4.1, -2.7)
    scene = Scene()
    hist = History(scene)
    scene.mesh.add_face([V(*a), V(*b), V(*c)])
    _push(scene, hist, scene.mesh.faces[0], 3.0)
    return scene, hist


def _side_walls(scene):
    """The three vertical walls, in a stable order so an index means one wall."""
    walls = [f for f in scene.mesh.faces if abs(f.normal().z()) < 0.1]
    return sorted(walls, key=lambda f: (round(f.centroid().x(), 3),
                                        round(f.centroid().y(), 3)))


def _unmerged_coplanar_seams(mesh):
    """Edges whose two faces are coplanar (either winding) — a seam the merge
    should have dissolved. Zero in a clean result."""
    n = 0
    for e in mesh.edges:
        if len(e.faces) == 2:
            d = QVector3D.dotProduct(e.faces[0].normal().normalized(),
                                     e.faces[1].normal().normalized())
            if abs(d) > 0.999:
                n += 1
    return n


def _assert_clean_solid(scene):
    mesh = scene.mesh
    assert is_closed(mesh), "solid left open (a crack survived)"
    assert _unmerged_coplanar_seams(mesh) == 0, "coplanar seam not merged"
    assert signed_volume(mesh) > 0.0, "negative/zero signed volume (winding)"
    # The headline invariant: the committed solid is already outward-consistent.
    assert orient_outward(mesh) == [], "committed solid has inconsistent winding"


# ---- every order of the three side-wall pushes -----------------------------

_DISTS = [1.5, 1.0, 2.0]


def _seqs():
    for r in (1, 2, 3):
        yield from itertools.permutations(range(3), r)


import pytest


@pytest.mark.parametrize("perm", list(_seqs()))
def test_side_wall_push_orders_commit_oriented_solid(perm):
    scene, hist = _prism_solid()
    for k, wi in enumerate(perm):
        _push(scene, hist, _side_walls(scene)[wi], _DISTS[k])
    _assert_clean_solid(scene)


# ---- stacked bumps on the same wall ----------------------------------------

@pytest.mark.parametrize("perm", [(0, 0, 1), (1, 1, 2), (2, 2, 0), (0, 1, 0)])
def test_repeated_wall_push_commits_oriented_solid(perm):
    scene, hist = _prism_solid()
    for k, wi in enumerate(perm):
        _push(scene, hist, _side_walls(scene)[wi], _DISTS[k % len(_DISTS)])
    _assert_clean_solid(scene)


# ---- a single extrude is already consistent --------------------------------

def test_plain_prism_is_oriented():
    scene, _hist = _prism_solid()
    _assert_clean_solid(scene)


# ---- undo / redo round-trip ------------------------------------------------

def test_push_undo_redo_round_trip():
    # Redo must restore the committed result, not re-run the mutation closure
    # (whose tool state is reset right after commit) — the pre-existing crash.
    scene, hist = _prism_solid()
    _push(scene, hist, _side_walls(scene)[0], 1.5)
    after = len(scene.mesh.faces)
    assert hist.undo() is True
    assert hist.redo() is True
    assert len(scene.mesh.faces) == after
    _assert_clean_solid(scene)
