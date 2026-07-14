# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""IFC4 export — tagged BIM objects to a STEP Physical File, by hand.

Only what the thesis needs, written without dependencies: IFC4 is a text
format (ISO 10303-21) and our export scope is narrow — the spatial skeleton
(project → site → building → storey), one element per tagged BIM object with
its REAL IFC class, faceted-BRep geometry in world metres, and BaseQuantities
(area, and volume when the object is watertight) so downstream takeoff tools
read the numbers straight from the file. Untagged geometry is not exported —
the tag IS the opt-in, per the project's freeform-first principle.

ifcopenshell stays out deliberately: heavy native wheels lag Python releases
(the ARM/3.14 risk noted in CLAUDE.md), and import can be added later without
it constraining the writer.
"""
from __future__ import annotations

import datetime
import uuid
from pathlib import Path

from core.bim import class_quantities, collect_objects, face_set_volume

_GUID_CHARS = ("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
               "abcdefghijklmnopqrstuvwxyz_$")

#: IFC4 total attribute counts per element class (the first 8 are the common
#: GlobalId..Tag layout; the rest are padded with ``$``). Classes with quirky
#: layouts (doors/windows carry OverallHeight/Width…, IfcSpace has LongName
#: where others have Tag) just get the right pad width.
_ATTR_COUNT = {"IfcPile": 10, "IfcDoor": 13, "IfcWindow": 13, "IfcSpace": 11}
_DEFAULT_ATTRS = 9


def ifc_guid() -> str:
    """A 22-character IFC compressed GUID (base-64 over the IFC alphabet)."""
    n = uuid.uuid4().int
    return "".join(_GUID_CHARS[(n >> (6 * i)) & 63] for i in range(21, -1, -1))


def _s(text: str) -> str:
    """IFC string literal: apostrophes doubled, non-ASCII as \\X2\\…\\X0\\."""
    out = []
    run: list[str] = []

    def flush():
        if run:
            out.append("\\X2\\" + "".join(f"{ord(c):04X}" for c in run)
                       + "\\X0\\")
            run.clear()

    for ch in text or "":
        if ord(ch) < 128:
            flush()
            out.append("''" if ch == "'" else ch)
        else:
            run.append(ch)
    flush()
    return "'" + "".join(out) + "'"


def _f(v: float) -> str:
    return f"{v:.6f}"


class _Writer:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self._next = 1

    def add(self, entity: str, *attrs) -> int:
        eid = self._next
        self._next += 1
        self.lines.append(f"#{eid}={entity}({','.join(attrs)});")
        return eid


def _brep(w: _Writer, faces, context_id: int) -> int:
    """Faceted geometry for a face set → IfcShapeRepresentation id. Closed
    sets become an IfcFacetedBrep; open ones an IfcShellBasedSurfaceModel."""
    point_ids: dict = {}

    def pt(v) -> int:
        key = (round(v.x(), 6), round(v.y(), 6), round(v.z(), 6))
        pid = point_ids.get(key)
        if pid is None:
            pid = w.add("IFCCARTESIANPOINT",
                        f"({_f(key[0])},{_f(key[1])},{_f(key[2])})")
            point_ids[key] = pid
        return pid

    face_ids = []
    for f in faces:
        bounds = []
        outer = w.add("IFCPOLYLOOP",
                      "(" + ",".join(f"#{pt(v.position)}" for v in f.loop)
                      + ")")
        bounds.append(f"#{w.add('IFCFACEOUTERBOUND', f'#{outer}', '.T.')}")
        for h in f.hole_loops:
            loop = w.add("IFCPOLYLOOP",
                         "(" + ",".join(f"#{pt(v.position)}" for v in h) + ")")
            bounds.append(f"#{w.add('IFCFACEBOUND', f'#{loop}', '.T.')}")
        face_ids.append(w.add("IFCFACE", "(" + ",".join(bounds) + ")"))

    face_list = "(" + ",".join(f"#{i}" for i in face_ids) + ")"
    closed = face_set_volume(faces) is not None
    if closed:
        shell = w.add("IFCCLOSEDSHELL", face_list)
        item = w.add("IFCFACETEDBREP", f"#{shell}")
        rep_type = "'Brep'"
    else:
        shell = w.add("IFCOPENSHELL", face_list)
        item = w.add("IFCSHELLBASEDSURFACEMODEL", f"(#{shell})")
        rep_type = "'SurfaceModel'"
    return w.add("IFCSHAPEREPRESENTATION", f"#{context_id}", "'Body'",
                 rep_type, f"(#{item})")


def save_ifc(scene, path, project_name: str = "IngeTrazo project") -> int:
    """Write every tagged BIM object to ``path`` as IFC4. Returns how many
    elements were exported; raises ``ValueError`` when nothing is tagged."""
    objects = collect_objects(scene)
    if not objects:
        raise ValueError("No tagged BIM objects to export")

    w = _Writer()
    origin = w.add("IFCCARTESIANPOINT", "(0.,0.,0.)")
    axis = w.add("IFCAXIS2PLACEMENT3D", f"#{origin}", "$", "$")
    context = w.add("IFCGEOMETRICREPRESENTATIONCONTEXT",
                    "$", "'Model'", "3", "1.E-5", f"#{axis}", "$")
    units = [
        w.add("IFCSIUNIT", "*", ".LENGTHUNIT.", "$", ".METRE."),
        w.add("IFCSIUNIT", "*", ".AREAUNIT.", "$", ".SQUARE_METRE."),
        w.add("IFCSIUNIT", "*", ".VOLUMEUNIT.", "$", ".CUBIC_METRE."),
    ]
    unit_assign = w.add("IFCUNITASSIGNMENT",
                        "(" + ",".join(f"#{u}" for u in units) + ")")
    project = w.add("IFCPROJECT", _s(ifc_guid()), "$", _s(project_name),
                    "$", "$", "$", "$", f"(#{context})", f"#{unit_assign}")
    placement = w.add("IFCLOCALPLACEMENT", "$", f"#{axis}")
    site = w.add("IFCSITE", _s(ifc_guid()), "$", "'Site'", "$", "$",
                 f"#{placement}", "$", "$", ".ELEMENT.",
                 "$", "$", "$", "$", "$")
    building = w.add("IFCBUILDING", _s(ifc_guid()), "$", "'Building'", "$",
                     "$", f"#{placement}", "$", "$", ".ELEMENT.",
                     "$", "$", "$")
    storey = w.add("IFCBUILDINGSTOREY", _s(ifc_guid()), "$", "'Storey'", "$",
                   "$", f"#{placement}", "$", "$", ".ELEMENT.", "0.")
    w.add("IFCRELAGGREGATES", _s(ifc_guid()), "$", "$", "$",
          f"#{project}", f"(#{site})")
    w.add("IFCRELAGGREGATES", _s(ifc_guid()), "$", "$", "$",
          f"#{site}", f"(#{building})")
    w.add("IFCRELAGGREGATES", _s(ifc_guid()), "$", "$", "$",
          f"#{building}", f"(#{storey})")

    element_ids = []
    for obj in objects:
        faces = obj.get("faces") or list(obj["group"].mesh.faces)
        if not faces:
            continue
        rep = _brep(w, faces, context)
        pds = w.add("IFCPRODUCTDEFINITIONSHAPE", "$", "$", f"(#{rep})")
        from core.bim import IFC_CLASSES
        cls = obj["class"] if obj["class"] in IFC_CLASSES \
            else "IfcBuildingElementProxy"
        qset_name, qentries, _metrado = class_quantities(obj["class"], faces)
        total = _ATTR_COUNT.get(cls, _DEFAULT_ATTRS)
        attrs = [_s(ifc_guid()), "$", _s(obj["name"] or cls), "$", "$",
                 f"#{placement}", f"#{pds}", "'IngeTrazo'"]
        if cls in ("IfcDoor", "IfcWindow"):
            # Attributes 9/10 are OverallHeight/OverallWidth — viewers and
            # schedules read the leaf dimensions from here.
            dims = {name: val for kind, name, val in qentries
                    if kind == "length"}
            attrs.append(_f(dims["Height"]) if "Height" in dims else "$")
            attrs.append(_f(dims["Width"]) if "Width" in dims else "$")
        attrs += ["$"] * (total - len(attrs))
        elem = w.add(cls.upper(), *attrs)
        element_ids.append(elem)

        # Per-class BaseQuantities (Qto_* naming): the takeoff numbers in
        # the budget's own measures, straight in the file.
        _Q_ENTITY = {"area": "IFCQUANTITYAREA", "volume": "IFCQUANTITYVOLUME",
                     "length": "IFCQUANTITYLENGTH"}
        quantities = [
            f"#{w.add(_Q_ENTITY[kind], _s(name), '$', '$', _f(val), '$')}"
            for kind, name, val in qentries
        ]
        if quantities:
            eq = w.add("IFCELEMENTQUANTITY", _s(ifc_guid()), "$",
                       _s(qset_name), "$", "$",
                       "(" + ",".join(quantities) + ")")
            w.add("IFCRELDEFINESBYPROPERTIES", _s(ifc_guid()), "$", "$", "$",
                  f"(#{elem})", f"#{eq}")

    if not element_ids:
        raise ValueError("No tagged BIM objects to export")
    w.add("IFCRELCONTAINEDINSPATIALSTRUCTURE", _s(ifc_guid()), "$", "$", "$",
          "(" + ",".join(f"#{e}" for e in element_ids) + ")", f"#{storey}")

    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    name = Path(path).name.replace("'", "''")
    header = (
        "ISO-10303-21;\n"
        "HEADER;\n"
        "FILE_DESCRIPTION((''),'2;1');\n"
        f"FILE_NAME('{name}','{stamp}',('IngeTrazo'),('IngeTrazo'),"
        "'IngeTrazo','IngeTrazo','');\n"
        "FILE_SCHEMA(('IFC4'));\n"
        "ENDSEC;\n"
        "DATA;\n"
    )
    footer = "ENDSEC;\nEND-ISO-10303-21;\n"
    Path(path).write_text(header + "\n".join(w.lines) + "\n" + footer,
                          encoding="ascii")
    return len(element_ids)
