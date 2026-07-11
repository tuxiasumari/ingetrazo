# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""BIM tagging layer — semantics ON TOP of freeform geometry.

The project's non-negotiable principle: the modeller is SketchUp-style
freedom, and BIM lives as *metadata applied to selected geometry* (the
BlenderBIM pattern), never as rigid Revit-style primitives. A tag turns a set
of faces (or a whole group) into a named IFC object; tagged objects feed the
quantity takeoff — and later the IFC export to IngePresupuestos. Untagged
geometry stays plain drawing.

Storage:
- loose faces: ``face.attrs["ifc"] = {"id": n, "class": ..., "name": ...}``
  — the ``attrs`` dict already survives the engine's face churn (push/pull,
  rebuilds, heals) and serialises with the document, so tags are durable.
- groups: ``group.ifc = {"class": ..., "name": ...}`` (the group IS the
  object; its isolated mesh gives exact quantities).

Quantities are computed honestly: any face set yields its AREA; VOLUME is
reported only when the set is watertight on its own (every edge of the set
bordered by exactly two faces of the set) — the guarantee the whole engine
exists to provide.
"""
from __future__ import annotations

from PySide6.QtGui import QVector3D

#: Curated civil/architecture classes (IFC4 names).
IFC_CLASSES = [
    "IfcWall", "IfcSlab", "IfcColumn", "IfcBeam", "IfcFooting",
    "IfcRoof", "IfcStair", "IfcRamp", "IfcDoor", "IfcWindow",
    "IfcRailing", "IfcCovering", "IfcPile", "IfcMember", "IfcSpace",
]


def next_object_id(scene) -> int:
    """A fresh BIM object id (max existing + 1)."""
    top = 0
    for f in scene.loose_mesh.faces:
        tag = f.attrs.get("ifc")
        if tag and isinstance(tag.get("id"), int):
            top = max(top, tag["id"])
    return top + 1


def tag_faces(faces, ifc_class: str, name: str, obj_id: int) -> None:
    for f in faces:
        f.attrs["ifc"] = {"id": obj_id, "class": ifc_class, "name": name}


def untag_faces(faces) -> None:
    for f in faces:
        f.attrs.pop("ifc", None)


def tag_group(group, ifc_class: str, name: str) -> None:
    group.ifc = {"class": ifc_class, "name": name}


def untag_group(group) -> None:
    group.ifc = None


def face_set_area(faces) -> float:
    return sum(f.area() for f in faces)


def face_set_volume(faces):
    """Volume enclosed by ``faces``, or ``None`` when the set is not
    watertight by itself. Signed tetrahedron sum over the set's triangles;
    closure = every edge of the set borders exactly two faces of the set."""
    faces = list(faces)
    if not faces:
        return None
    fset = set(faces)
    counts: dict = {}
    for f in faces:
        for lp in (f.loop, *f.hole_loops):
            n = len(lp)
            for i in range(n):
                key = frozenset((id(lp[i]), id(lp[(i + 1) % n])))
                counts[key] = counts.get(key, 0) + 1
    if any(c != 2 for c in counts.values()):
        return None
    total = 0.0
    for f in faces:
        for t0, t1, t2 in f.triangulate():
            total += QVector3D.dotProduct(
                t0, QVector3D.crossProduct(t1, t2)) / 6.0
    return abs(total)


def collect_objects(scene) -> list[dict]:
    """Every tagged BIM object in the document, with live quantities:
    ``{"key", "class", "name", "area", "volume" (or None), "faces" | "group"}``.
    Loose-face objects group by tag id; each tagged group is one object."""
    out: list[dict] = []
    by_id: dict = {}
    for f in scene.loose_mesh.faces:
        tag = f.attrs.get("ifc")
        if tag:
            by_id.setdefault(tag.get("id"), (tag, []))[1].append(f)
    for obj_id, (tag, faces) in sorted(by_id.items(),
                                       key=lambda kv: kv[0] or 0):
        out.append({
            "key": ("faces", obj_id),
            "class": tag.get("class", ""),
            "name": tag.get("name", ""),
            "area": face_set_area(faces),
            "volume": face_set_volume(faces),
            "faces": faces,
        })
    for g in scene.groups:
        tag = getattr(g, "ifc", None)
        if not tag:
            continue
        faces = list(g.mesh.faces)
        out.append({
            "key": ("group", id(g)),
            "class": tag.get("class", ""),
            "name": tag.get("name", ""),
            "area": face_set_area(faces),
            "volume": face_set_volume(faces),
            "group": g,
        })
    return out


def quantities_csv(scene) -> str:
    """The takeoff table (metrado) as CSV — class, name, area m², volume m³.
    The pre-IFC bridge to IngePresupuestos: model → quantities in one export."""
    lines = ["class,name,area_m2,volume_m3"]
    for obj in collect_objects(scene):
        vol = "" if obj["volume"] is None else f"{obj['volume']:.4f}"
        name = (obj["name"] or "").replace('"', "'")
        lines.append(f"{obj['class']},\"{name}\",{obj['area']:.4f},{vol}")
    return "\n".join(lines) + "\n"
