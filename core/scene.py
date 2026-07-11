# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Scene container, backed by a shared-vertex :class:`~core.mesh.Mesh`.

``edges`` and ``faces`` are read-only views onto the mesh (lists of
``mesh.Edge`` / ``mesh.Face``), so render, bounds and ``.igz`` save consume them
unchanged. Every mutation goes through mesh methods (via the ``Command`` layer),
which keep shared-vertex connectivity and incidence in sync — no more
position-matching to rediscover topology.

``version`` bumps on every mutation so the viewport can cheaply decide whether
to rebuild its dynamic VBOs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from PySide6.QtGui import QVector3D

from core.mesh import Edge, Face, Mesh


@dataclass
class Scene:
    mesh: Mesh = field(default_factory=Mesh)
    selection: set = field(default_factory=set)
    version: int = 0
    # Encapsulated chunks (own meshes), isolated from the main mesh's welding.
    groups: list = field(default_factory=list)
    # Annotation entities (static dimensions) — not geometry, drawn as overlays.
    dimensions: list = field(default_factory=list)
    # Georef traced paths (roads / boundaries / alignments) — first-class georef
    # entities, kept out of the topology mesh entirely (Track G).
    geo_paths: list = field(default_factory=list)
    # Construction guides (Tape Measure): infinite dashed lines / points used to
    # align real drawing. Scaffolding, never part of the mesh.
    guides: list = field(default_factory=list)
    # Display style for dimension annotations (edited from the Tray).
    dimension_style: dict = field(default_factory=lambda: {
        "decimals": 2, "units": "m", "font_size": 9, "color": [45, 55, 75]})
    # Georeferencing anchor (Track G). ``None`` until the user sets a datum;
    # once set, geodetic ↔ local-metre conversion goes through it. Terrain and
    # tiles are separate display-only objects added in later phases.
    georef: object | None = None
    # Base-map tile layer (Track G, G1) — display-only, never welded into the
    # mesh. Runtime state (not serialised as geometry); requires ``georef``.
    tile_layer: object | None = None
    # 3D draped terrain (Track G, G2 full) — display-only relief mesh, runtime.
    terrain: object | None = None

    # ---- Geometry views (read-only over the *loose* mesh) -------------------
    # Tools, edits and topology operate on this (the loose geometry); groups are
    # walled off so drawing never welds to them.
    @property
    def edges(self) -> list[Edge]:
        return self.mesh.edges

    @property
    def faces(self) -> list[Face]:
        return self.mesh.faces

    # ---- Render views (loose + every group) ---------------------------------
    def render_edges(self):
        yield from self.mesh.edges
        for g in self.groups:
            yield from g.mesh.edges

    def render_faces(self):
        yield from self.mesh.faces
        for g in self.groups:
            yield from g.mesh.faces

    # ---- Mutations ----------------------------------------------------------
    def add_edge(self, a: QVector3D, b: QVector3D) -> Edge:
        edge = self.mesh.add_edge(a, b)
        self.version += 1
        return edge

    def select(self, edges: Iterable, additive: bool = False) -> None:
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
        for ent in list(self.selection):
            if isinstance(ent, Edge):
                self.mesh.remove_edge(ent)
            elif isinstance(ent, Face):
                self.mesh.remove_face(ent)
        self.selection.clear()
        self.version += 1

    def clear(self) -> None:
        if (self.mesh.edges or self.mesh.faces or self.selection
                or self.groups or self.dimensions or self.georef
                or self.tile_layer or self.geo_paths or self.terrain
                or self.guides):
            self.mesh.clear()
            self.groups.clear()
            self.dimensions.clear()
            self.geo_paths.clear()
            self.guides.clear()
            self.selection.clear()
            self.georef = None
            self.tile_layer = None
            self.terrain = None
            self.version += 1

    # ---- Queries ------------------------------------------------------------
    def bounds(self) -> tuple[QVector3D, QVector3D] | tuple[None, None]:
        """Axis-aligned bounding box of all geometry. ``(None, None)`` if empty."""
        edges = list(self.render_edges())
        faces = list(self.render_faces())
        if not edges and not faces:
            return None, None
        inf = float("inf")
        minx = miny = minz = inf
        maxx = maxy = maxz = -inf

        def absorb(v: QVector3D) -> None:
            nonlocal minx, miny, minz, maxx, maxy, maxz
            x, y, z = v.x(), v.y(), v.z()
            if x < minx: minx = x
            if y < miny: miny = y
            if z < minz: minz = z
            if x > maxx: maxx = x
            if y > maxy: maxy = y
            if z > maxz: maxz = z

        for edge in edges:
            absorb(edge.a)
            absorb(edge.b)
        for face in faces:
            for v in face.vertices:
                absorb(v)
        return QVector3D(minx, miny, minz), QVector3D(maxx, maxy, maxz)
