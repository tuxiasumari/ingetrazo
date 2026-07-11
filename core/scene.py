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
    # Layers / tags (SketchUp): labels with visibility + lock. The default
    # layer always exists; entities reference layers by name.
    layers: list = field(default_factory=lambda: [
        __import__("core.layers", fromlist=["Layer"]).Layer(
            __import__("core.layers", fromlist=["DEFAULT_LAYER"]).DEFAULT_LAYER)
    ])
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
    # Group-edit context (Groups v2): while set, ``mesh`` POINTS AT the edited
    # group's mesh so every tool/command works inside the group transparently;
    # ``_loose_mesh`` keeps the real loose mesh for render and restore.
    edit_group: object | None = None
    _loose_mesh: object | None = None

    # ---- Geometry views (read-only over the *loose* mesh) -------------------
    # Tools, edits and topology operate on this (the loose geometry); groups are
    # walled off so drawing never welds to them.
    @property
    def edges(self) -> list[Edge]:
        return self.mesh.edges

    @property
    def faces(self) -> list[Face]:
        return self.mesh.faces

    # ---- Layers --------------------------------------------------------------
    def layer(self, name: str):
        for ly in self.layers:
            if ly.name == name:
                return ly
        return None

    def _layer_state(self, entity) -> tuple[bool, bool]:
        """(visible, locked) of the layer ``entity`` carries; unknown layer
        names read as the default (visible, unlocked)."""
        from core.layers import layer_of
        ly = self.layer(layer_of(entity))
        if ly is None:
            return True, False
        return ly.visible, ly.locked

    def entity_visible(self, entity) -> bool:
        return self._layer_state(entity)[0]

    def entity_selectable(self, entity) -> bool:
        visible, locked = self._layer_state(entity)
        return visible and not locked

    # ---- Group-edit context (Groups v2) --------------------------------------
    def begin_group_edit(self, group) -> None:
        """Enter a group: tools and commands now edit ITS mesh (SketchUp's
        double-click-into-group). Nested groups are not supported yet."""
        if self.edit_group is not None:
            self.end_group_edit()
        self._loose_mesh = self.mesh
        self.mesh = group.mesh
        self.edit_group = group
        self.selection.clear()
        self.version += 1

    def end_group_edit(self) -> None:
        """Leave the group-edit context, restoring the loose mesh."""
        if self.edit_group is None:
            return
        self.mesh = self._loose_mesh
        self._loose_mesh = None
        self.edit_group = None
        self.selection.clear()
        self.version += 1

    @property
    def loose_mesh(self):
        """The real loose mesh regardless of the edit context."""
        return self._loose_mesh if self.edit_group is not None else self.mesh

    # ---- Render views (loose + every group) ---------------------------------
    def render_edges(self):
        for e in self.loose_mesh.edges:
            if self.entity_visible(e):
                yield e
        for g in self.groups:
            if self.entity_visible(g):
                yield from g.mesh.edges

    def render_faces(self):
        for f in self.loose_mesh.faces:
            if self.entity_visible(f):
                yield f
        for g in self.groups:
            if self.entity_visible(g):
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
        self.end_group_edit()
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
            from core.layers import DEFAULT_LAYER, Layer
            self.layers = [Layer(DEFAULT_LAYER)]
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
