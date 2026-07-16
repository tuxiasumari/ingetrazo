# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Native IngeTrazo document format (``.igz``).

Plain JSON, schema-versioned. Trivial to inspect, edit by hand, and diff
in source control. Will grow as new entity types (faces, groups,
components, materials) land — old documents must keep loading.

Layout::

    {
      "igz_format": 1,
      "app_version": __version__,
      "scene": {
        "edges": [
          {"a": [x, y, z], "b": [x, y, z]},
          ...
        ],
        "faces": [
          {"vertices": [[x, y, z], ...]}
        ]
      }
    }
"""
from __future__ import annotations

import json
from pathlib import Path

from core.version import __version__

from PySide6.QtGui import QVector3D

from core.dimension import Dimension
from core.group import Group
from core.mesh import Mesh
from georef.datum import SceneDatum
from georef.geopath import GeoPath


CURRENT_FORMAT = 1


def _face_json(f) -> dict:
    entry = {"vertices": [[v.x(), v.y(), v.z()] for v in f.vertices]}
    # Holes are written only when present, so simple documents stay terse and
    # older readers ignore the extra key gracefully.
    if getattr(f, "holes", None):
        entry["holes"] = [
            [[v.x(), v.y(), v.z()] for v in loop] for loop in f.holes
        ]
    # Material colour (the Paint tool), written only when set.
    color = getattr(f, "attrs", {}).get("color")
    if color is not None:
        entry["color"] = list(color)
    texture = getattr(f, "attrs", {}).get("texture")
    if texture is not None:
        entry["texture"] = dict(texture)
    layer = getattr(f, "attrs", {}).get("layer")
    if layer is not None:
        entry["layer"] = layer
    ifc = getattr(f, "attrs", {}).get("ifc")
    if ifc is not None:
        entry["ifc"] = dict(ifc)
    return entry


def _edge_json(e) -> dict:
    entry = {"a": [e.a.x(), e.a.y(), e.a.z()],
             "b": [e.b.x(), e.b.y(), e.b.z()]}
    if getattr(e, "soft", False):
        entry["soft"] = True
    if getattr(e, "curve", None) is not None:
        entry["curve"] = e.curve
    if getattr(e, "layer", None) is not None:
        entry["layer"] = e.layer
    return entry


def _mesh_json(mesh) -> dict:
    return {
        "edges": [_edge_json(e) for e in mesh.edges],
        "faces": [_face_json(f) for f in mesh.faces],
    }


def save_scene(scene, path: Path) -> None:
    # Accept a Scene or a bare Mesh (the M1 read-compat path feeds a Mesh).
    mesh = scene.mesh if hasattr(scene, "mesh") else scene
    payload = _mesh_json(mesh)
    groups = getattr(scene, "groups", None)
    if groups:
        payload["groups"] = []
        # Component prototypes: each shared instance mesh is written ONCE.
        proto_index: dict = {}
        protos: list = []
        for g in groups:
            if getattr(g, "xform", None) is not None \
                    and id(g.mesh) not in proto_index:
                proto_index[id(g.mesh)] = len(protos)
                protos.append(_mesh_json(g.mesh))
        if protos:
            payload["protos"] = protos
        for g in groups:
            if getattr(g, "xform", None) is not None:
                entry = {"proto": proto_index[id(g.mesh)],
                         "xform": [float(x) for x in g.xform.data()]}
            else:
                entry = _mesh_json(g.mesh)
            if getattr(g, "layer", None) is not None:
                entry["layer"] = g.layer
            if getattr(g, "ifc", None):
                entry["ifc"] = dict(g.ifc)
            if getattr(g, "billboard", False):
                # True = legacy textured-quad face-me; "mesh" = imported
                # silhouette whose real geometry turns toward the camera.
                entry["billboard"] = g.billboard
            payload["groups"].append(entry)
    layers = getattr(scene, "layers", None)
    if layers is not None and (len(layers) > 1 or any(
            not ly.visible or ly.locked for ly in layers)):
        payload["layers"] = [ly.to_dict() for ly in layers]
    dims = getattr(scene, "dimensions", None)
    if dims:
        payload["dimensions"] = [
            {"a": [d.a.x(), d.a.y(), d.a.z()],
             "b": [d.b.x(), d.b.y(), d.b.z()],
             "offset": [d.offset.x(), d.offset.y(), d.offset.z()]}
            for d in dims
        ]
    style = getattr(scene, "dimension_style", None)
    if style:
        payload["dimension_style"] = dict(style)
    # Georeferencing datum (Track G) — optional block, written only when set so
    # ungeoreferenced documents stay terse and older readers ignore it.
    datum = getattr(scene, "georef", None)
    if datum is not None:
        payload["georef"] = {"datum": datum.to_dict()}
    paths = getattr(scene, "geo_paths", None)
    if paths:
        payload["geo_paths"] = [p.to_dict() for p in paths]
    points = getattr(scene, "geo_points", None)
    if points:
        payload["geo_points"] = [p.to_dict() for p in points]
    labels = getattr(scene, "text_labels", None)
    if labels:
        payload["text_labels"] = [t.to_dict() for t in labels]
    guides = getattr(scene, "guides", None)
    if guides:
        payload["guides"] = [g.to_dict() for g in guides]
    data = {
        "igz_format": CURRENT_FORMAT,
        "app_version": __version__,
        "scene": payload,
    }
    path.write_text(json.dumps(data, indent=2))


def load_into(scene, path: Path) -> None:
    """Replace ``scene`` contents with what's stored at ``path``."""
    data = json.loads(path.read_text())
    fmt = data.get("igz_format", 1)
    if fmt > CURRENT_FORMAT:
        raise ValueError(
            f"Document format v{fmt} is newer than this build (v{CURRENT_FORMAT})."
        )

    payload = data.get("scene", {})

    scene.mesh.clear()
    scene.selection.clear()
    scene.groups.clear()
    scene.geo_paths.clear()
    scene.geo_points.clear()
    scene.text_labels.clear()
    scene.georef = None

    _load_mesh(scene.mesh, payload)
    raw_layers = payload.get("layers")
    if raw_layers:
        from core.layers import DEFAULT_LAYER, Layer
        scene.layers = [Layer.from_dict(r) for r in raw_layers]
        if not any(ly.name == DEFAULT_LAYER for ly in scene.layers):
            scene.layers.insert(0, Layer(DEFAULT_LAYER))
    proto_meshes: list = []
    for raw in payload.get("protos", []):
        m = Mesh()
        _load_mesh(m, raw)
        proto_meshes.append(m)
    for raw in payload.get("groups", []):
        if raw.get("xform") is not None and "proto" in raw:
            from PySide6.QtGui import QMatrix4x4
            group = Group(proto_meshes[int(raw["proto"])])
            vals = [float(x) for x in raw["xform"]]
            # data() is column-major; the constructor takes row-major.
            rm = [vals[col * 4 + row] for row in range(4) for col in range(4)]
            group.xform = QMatrix4x4(*rm)
        else:
            group = Group()
            _load_mesh(group.mesh, raw)
        if raw.get("layer"):
            group.layer = raw["layer"]
        if raw.get("ifc"):
            group.ifc = dict(raw["ifc"])
        if raw.get("billboard"):
            group.billboard = raw["billboard"]   # True | "mesh"
        scene.groups.append(group)

    for raw in payload.get("dimensions", []):
        scene.dimensions.append(Dimension(
            QVector3D(*raw["a"]), QVector3D(*raw["b"]),
            QVector3D(*raw["offset"])))

    style = payload.get("dimension_style")
    if isinstance(style, dict):
        scene.dimension_style.update(style)

    georef = payload.get("georef")
    if isinstance(georef, dict) and isinstance(georef.get("datum"), dict):
        scene.georef = SceneDatum.from_dict(georef["datum"])

    for raw in payload.get("geo_paths", []):
        scene.geo_paths.append(GeoPath.from_dict(raw))

    from georef.points import GeoPoint
    for raw in payload.get("geo_points", []):
        scene.geo_points.append(GeoPoint.from_dict(raw))

    from core.textlabel import TextLabel
    for raw in payload.get("text_labels", []):
        scene.text_labels.append(TextLabel.from_dict(raw))

    from core.guide import Guide
    scene.guides.clear()
    for raw in payload.get("guides", []):
        scene.guides.append(Guide.from_dict(raw))

    scene.version += 1


def _load_mesh(mesh, payload) -> None:
    import core.mesh as _mesh_mod
    for raw in payload.get("edges", []):
        try:
            edge = mesh.add_edge(QVector3D(*raw["a"]), QVector3D(*raw["b"]))
            if raw.get("soft"):
                edge.soft = True
            if raw.get("layer"):
                edge.layer = raw["layer"]
            cid = raw.get("curve")
            if cid is not None:
                edge.curve = cid
                # Keep new curves unique after loading stored ids.
                if cid >= _mesh_mod._CURVE_COUNTER:
                    _mesh_mod._CURVE_COUNTER = cid + 1
        except ValueError:
            pass  # degenerate edge in the document — skip
    for raw in payload.get("faces", []):
        verts = [QVector3D(*v) for v in raw["vertices"]]
        holes = [[QVector3D(*v) for v in loop] for loop in raw.get("holes", [])]
        face = mesh.add_face(verts, holes)
        if face is not None:
            color = raw.get("color")
            if color is not None:
                face.attrs["color"] = list(color)
            texture = raw.get("texture")
            if texture is not None:
                face.attrs["texture"] = dict(texture)
            if raw.get("layer"):
                face.attrs["layer"] = raw["layer"]
            if raw.get("ifc"):
                face.attrs["ifc"] = dict(raw["ifc"])
