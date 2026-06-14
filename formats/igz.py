"""Native IngeTrazo document format (``.igz``).

Plain JSON, schema-versioned. Trivial to inspect, edit by hand, and diff
in source control. Will grow as new entity types (faces, groups,
components, materials) land — old documents must keep loading.

Layout::

    {
      "igz_format": 1,
      "app_version": "0.0.1",
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

from PySide6.QtGui import QVector3D

from core.group import Group


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
    return entry


def _mesh_json(mesh) -> dict:
    return {
        "edges": [{"a": [e.a.x(), e.a.y(), e.a.z()],
                   "b": [e.b.x(), e.b.y(), e.b.z()]} for e in mesh.edges],
        "faces": [_face_json(f) for f in mesh.faces],
    }


def save_scene(scene, path: Path) -> None:
    # Accept a Scene or a bare Mesh (the M1 read-compat path feeds a Mesh).
    mesh = scene.mesh if hasattr(scene, "mesh") else scene
    payload = _mesh_json(mesh)
    groups = getattr(scene, "groups", None)
    if groups:
        payload["groups"] = [_mesh_json(g.mesh) for g in groups]
    data = {
        "igz_format": CURRENT_FORMAT,
        "app_version": "0.0.1",
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

    _load_mesh(scene.mesh, payload)
    for raw in payload.get("groups", []):
        group = Group()
        _load_mesh(group.mesh, raw)
        scene.groups.append(group)

    scene.version += 1


def _load_mesh(mesh, payload) -> None:
    for raw in payload.get("edges", []):
        try:
            mesh.add_edge(QVector3D(*raw["a"]), QVector3D(*raw["b"]))
        except ValueError:
            pass  # degenerate edge in the document — skip
    for raw in payload.get("faces", []):
        verts = [QVector3D(*v) for v in raw["vertices"]]
        holes = [[QVector3D(*v) for v in loop] for loop in raw.get("holes", [])]
        face = mesh.add_face(verts, holes)
        color = raw.get("color")
        if color is not None and face is not None:
            face.attrs["color"] = list(color)
