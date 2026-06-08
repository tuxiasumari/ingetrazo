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


CURRENT_FORMAT = 1


def save_scene(scene, path: Path) -> None:
    edges = [
        {
            "a": [e.a.x(), e.a.y(), e.a.z()],
            "b": [e.b.x(), e.b.y(), e.b.z()],
        }
        for e in scene.edges
    ]
    def _face_json(f):
        entry = {"vertices": [[v.x(), v.y(), v.z()] for v in f.vertices]}
        # Holes are written only when present, so simple documents stay terse
        # and older readers ignore the extra key gracefully.
        if getattr(f, "holes", None):
            entry["holes"] = [
                [[v.x(), v.y(), v.z()] for v in loop] for loop in f.holes
            ]
        return entry

    faces = [_face_json(f) for f in getattr(scene, "faces", [])]
    data = {
        "igz_format": CURRENT_FORMAT,
        "app_version": "0.0.1",
        "scene": {"edges": edges, "faces": faces},
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

    for raw in payload.get("edges", []):
        a = QVector3D(*raw["a"])
        b = QVector3D(*raw["b"])
        try:
            scene.mesh.add_edge(a, b)
        except ValueError:
            pass  # degenerate edge in the document — skip

    for raw in payload.get("faces", []):
        verts = [QVector3D(*v) for v in raw["vertices"]]
        holes = [
            [QVector3D(*v) for v in loop] for loop in raw.get("holes", [])
        ]
        scene.mesh.add_face(verts, holes)

    scene.version += 1
