"""Auto-merge of coincident edges — Phase 1, sub-step 1.

Covers the SketchUp-style weld: drawing an edge that already exists (in
either orientation, within the position tolerance) reuses it instead of
stacking a duplicate. Exercised at two levels:

- the ``find_duplicate_edge`` primitive in ``core.topology``;
- the ``AddEdgeCommand`` that wires it into the undo/redo history, including
  the regression that undo must never delete the edge it merged into.

These tests are pure data + Qt value types (``QVector3D``), so they run
headless without a ``QApplication``.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.geometry import Edge
from core.history import AddEdgeCommand, History
from core.scene import Scene
from core.topology import find_duplicate_edge, same_position


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(x, y, z)


# ---- find_duplicate_edge ----------------------------------------------------

def test_duplicate_same_orientation():
    edges = [Edge(V(0, 0), V(1, 0))]
    assert find_duplicate_edge(edges, V(0, 0), V(1, 0)) is edges[0]


def test_duplicate_reversed_orientation():
    edges = [Edge(V(0, 0), V(1, 0))]
    # Endpoint set is orientation-independent.
    assert find_duplicate_edge(edges, V(1, 0), V(0, 0)) is edges[0]


def test_duplicate_within_tolerance():
    edges = [Edge(V(0, 0), V(1, 0))]
    # ~0.01 mm offset (well under the 0.1 mm key tolerance) still welds.
    assert find_duplicate_edge(edges, V(0.00001, 0), V(1, 0)) is edges[0]


def test_not_duplicate_beyond_tolerance():
    edges = [Edge(V(0, 0), V(1, 0))]
    # 1 mm offset is beyond the weld tolerance.
    assert find_duplicate_edge(edges, V(0, 0), V(1, 0.001)) is None


def test_no_duplicate_for_different_edge():
    edges = [Edge(V(0, 0), V(1, 0))]
    assert find_duplicate_edge(edges, V(0, 1), V(1, 1)) is None


def test_degenerate_query_never_matches():
    edges = [Edge(V(0, 0), V(0, 0))]  # zero-length edge in the scene
    assert find_duplicate_edge(edges, V(0, 0), V(0, 0)) is None


def test_empty_scene():
    assert find_duplicate_edge([], V(0, 0), V(1, 0)) is None


def test_same_position_helper():
    assert same_position(V(1, 2, 3), V(1, 2, 3))
    assert same_position(V(1, 0, 0), V(1.00001, 0, 0))
    assert not same_position(V(1, 0, 0), V(1.01, 0, 0))


# ---- AddEdgeCommand merge behaviour ----------------------------------------

def _history() -> tuple[Scene, History]:
    scene = Scene()
    return scene, History(scene)


def test_merge_dedups_reversed_edge():
    scene, hist = _history()
    hist.execute(AddEdgeCommand(V(0, 0), V(1, 0)))
    hist.execute(AddEdgeCommand(V(1, 0), V(0, 0)))  # reverse of the first
    assert len(scene.edges) == 1


def test_mesh_never_duplicates_even_with_merge_off():
    # The shared-vertex mesh is the source of truth and cannot hold two edges
    # between the same vertices, so it dedups regardless of the merge flag —
    # stricter (and more correct) than the legacy model, which could stack one.
    scene, hist = _history()
    hist.execute(AddEdgeCommand(V(0, 0), V(1, 0)))
    hist.execute(AddEdgeCommand(V(0, 0), V(1, 0), merge=False))
    assert len(scene.edges) == 1


def test_undo_of_merged_noop_keeps_original():
    """The merged command added nothing, so undoing it must leave the
    pre-existing edge intact — the core regression auto-merge could cause."""
    scene, hist = _history()
    hist.execute(AddEdgeCommand(V(0, 0), V(1, 0)))
    original = scene.edges[0]
    hist.execute(AddEdgeCommand(V(1, 0), V(0, 0)))  # merged no-op
    assert hist.undo() is True
    assert scene.edges == [original]


def test_undo_redo_roundtrip_for_real_add():
    scene, hist = _history()
    cmd = AddEdgeCommand(V(0, 0), V(1, 0))
    hist.execute(cmd)
    edge = scene.edges[0]
    assert hist.undo() is True
    assert scene.edges == []
    assert hist.redo() is True
    # Same edge object is re-appended, and no spurious duplicate.
    assert scene.edges == [edge]


def test_undo_real_add_discards_from_selection():
    scene, hist = _history()
    cmd = AddEdgeCommand(V(0, 0), V(1, 0))
    hist.execute(cmd)
    scene.selection.add(cmd.edge)
    hist.undo()
    assert cmd.edge not in scene.selection


def test_merged_command_owns_no_edge():
    scene, hist = _history()
    hist.execute(AddEdgeCommand(V(0, 0), V(1, 0)))
    merged = AddEdgeCommand(V(0, 0), V(1, 0))
    hist.execute(merged)
    assert merged.edge is None  # never created its own edge


# ---- Integration: two rectangles sharing a border --------------------------

def _rect_edge_commands(corners):
    return [AddEdgeCommand(corners[i], corners[(i + 1) % 4]) for i in range(4)]


def test_two_rects_share_single_edge():
    """Two unit squares side by side share the border at x=1. The shared
    edge appears reversed between the two rectangles' windings, so this also
    exercises orientation-independent merging end to end."""
    scene, hist = _history()
    rect1 = [V(0, 0), V(1, 0), V(1, 1), V(0, 1)]
    rect2 = [V(1, 0), V(2, 0), V(2, 1), V(1, 1)]
    for cmd in _rect_edge_commands(rect1):
        hist.execute(cmd)
    for cmd in _rect_edge_commands(rect2):
        hist.execute(cmd)
    # 4 + 4 - 1 shared = 7 distinct edges.
    assert len(scene.edges) == 7
    # The shared border exists exactly once.
    shared = [
        e for e in scene.edges
        if find_duplicate_edge([e], V(1, 0), V(1, 1)) is e
    ]
    assert len(shared) == 1
