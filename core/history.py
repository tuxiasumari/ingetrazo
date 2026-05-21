"""Undo / redo history using the command pattern.

Every mutation that should be reversible goes through a :class:`Command`
subclass. The viewport owns a :class:`History` that maintains the undo and
redo stacks. Tools call ``viewport.history.execute(...)`` rather than
mutating the scene directly.

Why a command stack and not snapshots? Snapshots scale poorly with large
scenes (every push/pull would copy the whole model). Commands store only
the delta, so memory cost stays proportional to the action.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Optional

from PySide6.QtGui import QVector3D

from core.geometry import Edge, Face


class Command(ABC):
    """Abstract reversible operation against a :class:`Scene`."""

    @abstractmethod
    def do(self, scene) -> None:
        """Apply the operation."""

    @abstractmethod
    def undo(self, scene) -> None:
        """Reverse the operation."""


class History:
    def __init__(self, scene) -> None:
        self.scene = scene
        self.undo_stack: list[Command] = []
        self.redo_stack: list[Command] = []

    def execute(self, cmd: Command) -> None:
        cmd.do(self.scene)
        self.undo_stack.append(cmd)
        self.redo_stack.clear()

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        cmd = self.undo_stack.pop()
        cmd.undo(self.scene)
        self.redo_stack.append(cmd)
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        cmd = self.redo_stack.pop()
        cmd.do(self.scene)
        self.undo_stack.append(cmd)
        return True

    def clear(self) -> None:
        self.undo_stack.clear()
        self.redo_stack.clear()


# ---- Concrete commands ------------------------------------------------------

class AddEdgeCommand(Command):
    def __init__(self, a: QVector3D, b: QVector3D) -> None:
        self.a = a
        self.b = b
        self.edge: Optional[Edge] = None

    def do(self, scene) -> None:
        if self.edge is None:
            self.edge = Edge(self.a, self.b)
        scene.edges.append(self.edge)
        scene.version += 1

    def undo(self, scene) -> None:
        try:
            scene.edges.remove(self.edge)
        except ValueError:
            pass
        if self.edge is not None:
            scene.selection.discard(self.edge)
        scene.version += 1


class DeleteEdgesCommand(Command):
    def __init__(self, edges: Iterable[Edge]) -> None:
        self.edges: list[Edge] = list(edges)

    def do(self, scene) -> None:
        edges_set = set(self.edges)
        scene.edges[:] = [e for e in scene.edges if e not in edges_set]
        for e in self.edges:
            scene.selection.discard(e)
        scene.version += 1

    def undo(self, scene) -> None:
        for edge in self.edges:
            if edge not in scene.edges:
                scene.edges.append(edge)
        # The original selection is not restored — SketchUp behaves the same.
        scene.version += 1


class AddFaceCommand(Command):
    def __init__(self, vertices: Iterable[QVector3D]) -> None:
        self.vertices = [QVector3D(v) for v in vertices]
        self.face: Optional[Face] = None

    def do(self, scene) -> None:
        if self.face is None:
            self.face = Face(list(self.vertices))
        scene.faces.append(self.face)
        scene.version += 1

    def undo(self, scene) -> None:
        if self.face is not None:
            try:
                scene.faces.remove(self.face)
            except ValueError:
                pass
        scene.version += 1


class CompoundCommand(Command):
    """A list of commands executed and reverted as one atomic step."""

    def __init__(self, commands: Iterable[Command]) -> None:
        self.commands: list[Command] = list(commands)

    def do(self, scene) -> None:
        for cmd in self.commands:
            cmd.do(scene)

    def undo(self, scene) -> None:
        for cmd in reversed(self.commands):
            cmd.undo(scene)
