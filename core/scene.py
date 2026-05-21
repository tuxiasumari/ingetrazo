"""Scene container: edges, selection, version counter.

The scene is a flat list at this stage. A proper scene graph with nested
groups and transforms lands once components are introduced.

``version`` bumps on every mutation so the viewport can cheaply decide
whether to rebuild its dynamic VBOs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from PySide6.QtGui import QVector3D

from core.geometry import Edge


@dataclass
class Scene:
    edges: list[Edge] = field(default_factory=list)
    selection: set[Edge] = field(default_factory=set)
    version: int = 0

    def add_edge(self, a: QVector3D, b: QVector3D) -> Edge:
        edge = Edge(a, b)
        self.edges.append(edge)
        self.version += 1
        return edge

    def select(self, edges: Iterable[Edge], additive: bool = False) -> None:
        if not additive:
            self.selection.clear()
        self.selection.update(edges)
        self.version += 1

    def clear_selection(self) -> None:
        if self.selection:
            self.selection.clear()
            self.version += 1

    def delete_selection(self) -> None:
        if not self.selection:
            return
        self.edges = [e for e in self.edges if e not in self.selection]
        self.selection.clear()
        self.version += 1

    def clear(self) -> None:
        if self.edges or self.selection:
            self.edges.clear()
            self.selection.clear()
            self.version += 1
