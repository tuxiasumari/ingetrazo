# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""History.execute must be transactional: a command that throws mid-mutation
rolls the mesh back to its pre-command state and lands nowhere on the undo
stack. An aborted draw once left a quarter circle, an unsplit face and a
duplicated edge behind (aas.igz), with the traceback swallowed by Qt."""
from __future__ import annotations

from PySide6.QtGui import QVector3D

from core.history import AddEdgeCommand, Command, History
from core.scene import Scene


class _ExplodesMidway(Command):
    """Mutates the mesh, then throws — simulating a mid-commit failure."""

    def do(self, scene) -> None:
        scene.mesh.add_edge(QVector3D(0, 0, 0), QVector3D(1, 0, 0))
        scene.mesh.add_edge(QVector3D(1, 0, 0), QVector3D(2, 5, 0))
        raise ValueError("degenerate edge")

    def undo(self, scene) -> None:  # pragma: no cover - never reached
        raise AssertionError("undo of a failed command must never run")


def test_failed_command_rolls_back_and_stays_off_the_undo_stack(tmp_path):
    scene = Scene()
    hist = History(scene)
    hist.error_log = str(tmp_path / "errors.log")
    hist.execute(AddEdgeCommand(QVector3D(5, 5, 0), QVector3D(9, 5, 0)))
    assert len(scene.mesh.edges) == 1

    hist.execute(_ExplodesMidway())
    assert len(scene.mesh.edges) == 1              # partial mutation reverted
    assert len(hist.undo_stack) == 1               # failed cmd not recorded
    assert hist.last_error and "degenerate edge" in hist.last_error
    assert "ValueError" in (tmp_path / "errors.log").read_text()

    # The surviving history still works.
    assert hist.undo()
    assert len(scene.mesh.edges) == 0
    assert hist.redo()
    assert len(scene.mesh.edges) == 1


def test_successful_command_clears_last_error():
    scene = Scene()
    hist = History(scene)
    hist.last_error = "stale"
    hist.execute(AddEdgeCommand(QVector3D(0, 0, 0), QVector3D(1, 1, 0)))
    assert hist.last_error is None
