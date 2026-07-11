# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Construction guides (Tape Measure) — SketchUp guide lines and points.

A guide is scaffolding, not geometry: an infinite dashed construction line (or a
lone point) used to align real drawing. It never enters the topology mesh; it
lives in ``Scene.guides``, renders as a fine dashed overlay, and feeds the snap
engine so drawing tools can lock onto it — that is its whole purpose.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

# Half-length used when a conceptually infinite guide line needs segment form
# (render clipping and snap both work on segments).
GUIDE_HALF_LEN = 1.0e4


class Guide:
    """A construction guide: an infinite line (``point`` + ``direction``) or,
    when ``direction`` is None, a guide point."""

    def __init__(self, point: QVector3D, direction: QVector3D | None = None) -> None:
        self.point = QVector3D(point)
        self.direction = (QVector3D(direction).normalized()
                          if direction is not None and
                          QVector3D(direction).length() > 1e-12 else None)

    @property
    def is_line(self) -> bool:
        return self.direction is not None

    def segment(self) -> tuple[QVector3D, QVector3D]:
        """The guide as a long finite segment (for render clipping and snap)."""
        if self.direction is None:
            return QVector3D(self.point), QVector3D(self.point)
        d = self.direction * GUIDE_HALF_LEN
        return self.point - d, self.point + d

    # Snap-engine duck-typing: guides are fed to ``compute_snap`` as pseudo
    # edges, which only reads ``.a`` / ``.b``.
    @property
    def a(self) -> QVector3D:
        return self.segment()[0]

    @property
    def b(self) -> QVector3D:
        return self.segment()[1]

    # ---- Serialisation ------------------------------------------------------
    def to_dict(self) -> dict:
        entry = {"point": [self.point.x(), self.point.y(), self.point.z()]}
        if self.direction is not None:
            entry["direction"] = [self.direction.x(), self.direction.y(),
                                  self.direction.z()]
        return entry

    @classmethod
    def from_dict(cls, data: dict) -> "Guide":
        d = data.get("direction")
        return cls(QVector3D(*data["point"]),
                   QVector3D(*d) if d else None)
