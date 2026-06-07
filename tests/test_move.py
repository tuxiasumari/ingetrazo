"""Move — translate positions and let connected geometry follow.

Moving works on positions, not entities: every edge endpoint and face vertex
coincident with a moved point shifts together, so faces sharing that point
deform instead of tearing. This is the mechanic behind raising a ridge into a
gable roof.

Headless: ``QVector3D`` values + commands against a ``Scene``.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.edits import build_add_edges
from core.geometry import Face
from core.history import AddFaceCommand, History, MoveVerticesCommand
from core.scene import Scene


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(float(x), float(y), float(z))


def test_move_drags_all_coincident_points():
    # Two faces sharing the edge (1,0)-(1,1): moving that edge's points must
    # move the matching corner of *both* faces (they stay connected).
    scene = Scene()
    hist = History(scene)
    left = Face([V(0, 0), V(1, 0), V(1, 1), V(0, 1)])
    right = Face([V(1, 0), V(2, 0), V(2, 1), V(1, 1)])
    scene.faces.extend([left, right])

    hist.execute(MoveVerticesCommand([V(1, 0), V(1, 1)], V(0, 0, 1)))

    # The shared corners rose in both faces.
    assert any(abs(v.z() - 1) < 1e-9 for v in left.vertices)
    assert any(abs(v.z() - 1) < 1e-9 for v in right.vertices)
    moved = [v for v in left.vertices if abs(v.z() - 1) < 1e-9]
    assert {(round(v.x()), round(v.y())) for v in moved} == {(1, 0), (1, 1)}


def test_move_undo_restores_positions():
    scene = Scene()
    hist = History(scene)
    f = Face([V(0, 0), V(2, 0), V(2, 2), V(0, 2)])
    scene.faces.append(f)
    hist.execute(MoveVerticesCommand([V(2, 0), V(2, 2)], V(1, 0, 0)))
    assert any(abs(v.x() - 3) < 1e-9 for v in f.vertices)
    assert hist.undo() is True
    assert all(abs(v.x() - 3) > 1e-9 for v in f.vertices)  # back to x in {0,2}
    assert max(v.x() for v in f.vertices) == 2.0


def test_ridge_move_deforms_roof_slopes():
    # Box top split by a ridge line into two halves; raising the ridge tilts
    # both halves up (they share the ridge points), forming the roof slopes.
    scene = Scene()
    hist = History(scene)
    ground = [V(0, 0), V(4, 0), V(4, 2), V(0, 2)]
    hist.execute(build_add_edges(
        scene, [(ground[i], ground[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(ground))]))
    # Fake an extruded top face at z=2 for the test (skip push machinery).
    top = Face([V(0, 0, 2), V(4, 0, 2), V(4, 2, 2), V(0, 2, 2)])
    scene.faces.append(top)
    # Ridge line splits the top into two halves at y=1.
    hist.execute(build_add_edges(scene, [(V(0, 1, 2), V(4, 1, 2))], detect_faces=True))
    halves = [f for f in scene.faces if all(abs(v.z() - 2) < 1e-9 for v in f.vertices)]
    assert len(halves) == 2  # top split into two slopes

    hist.execute(MoveVerticesCommand([V(0, 1, 2), V(4, 1, 2)], V(0, 0, 1)))
    # Each half now has its ridge edge at z=3.
    for half in halves:
        assert any(abs(v.z() - 3) < 1e-9 for v in half.vertices)
        assert any(abs(v.z() - 2) < 1e-9 for v in half.vertices)  # eaves stay down
