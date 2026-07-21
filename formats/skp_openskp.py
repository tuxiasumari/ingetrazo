# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Adapter: an OpenSKP parse → an IngeTrazo geometry payload.

Kept in its own module so ``import openskp`` happens lazily (only when this
backend actually runs) — ``formats/skp.py`` must stay importable without the
optional parser installed.

OpenSKP 0.8-era data model (v0.2.0), discovered by introspection:

* ``SkpFile.open(path).parse()`` → ``SkpModel`` with ``definitions`` (dict:
  id → ``Definition``), ``materials``, ``layers``, ``version``.
* ``Definition``: ``id``, ``name``, ``vertices`` (dict id → ``Vertex(x,y,z)``),
  ``edges`` (dict id → ``Edge(v1_id, v2_id)``), ``faces`` (dict id → ``Face``),
  ``instances`` (list of ``Instance``).
* ``Face``: ``loops`` — a list of loops, each ``[(edge_id, sense), …]``; the
  first loop is the outer boundary, the rest are holes. ``sense`` 1 walks the
  edge ``v1→v2``, 0 walks ``v2→v1``. Plus ``normal`` and ``material_id``.
* ``Instance``: ``matrix`` (a 3×3 rotation/scale row-major + a translation, 13
  floats), ``ref_idx`` (→ the placed definition's id), ``children``.

SketchUp stores lengths in **inches** and is **Z-up** — same up axis as
IngeTrazo, so we only scale (inches → metres); no axis swap. The instance tree
is flattened to world-space polygons (reference geometry, like the big-DAE
import path). Per-face materials resolve through ``SkpModel.materials_by_id``
(our upstream PR iamahsanmehmood/openskp#3): plain colours become
``attrs["color"]``, and textured materials (``Material.texture``, PR openskp#4)
become ``attrs["texture"]`` — image bytes extracted to ``<stem>/`` next to the
``.skp``, tile size in metres, rendered with IngeTrazo's planar projection
(SketchUp's default texture behaviour; per-face UVs from the TLV are a later
refinement). Both joins are guarded, so PyPI 0.2.0 still imports (uncoloured).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QMatrix4x4, QVector3D

_INCH = 0.0254          # SketchUp internal unit → metres
_MAX_DEPTH = 32         # guard against pathological instance nesting


def _ring(defn, loop):
    """Resolve one ``[(edge_id, sense), …]`` loop to a list of local-space
    ``QVector3D`` (metres). Returns ``None`` on any dangling reference."""
    pts = []
    for eid, sense in loop:
        edge = defn.edges.get(eid)
        if edge is None:
            return None
        vid = edge.v1_id if sense else edge.v2_id
        v = defn.vertices.get(vid)
        if v is None:
            return None
        pts.append(QVector3D(v.x * _INCH, v.y * _INCH, v.z * _INCH))
    return pts


def _matrix(m) -> QMatrix4x4:
    """An OpenSKP instance ``matrix`` (row-major 3×3 + translation, in inches)
    as a ``QMatrix4x4`` whose translation is already in metres."""
    return QMatrix4x4(
        m[0], m[1], m[2], m[9] * _INCH,
        m[3], m[4], m[5], m[10] * _INCH,
        m[6], m[7], m[8], m[11] * _INCH,
        0.0, 0.0, 0.0, 1.0)


def _texture_dir(skp_path) -> Path:
    """Directory for extracted texture images: ``<stem>/`` next to the
    ``.skp`` — the SketchUp-export convention skp2dae also follows, so texture
    paths stay valid for the session and for saved ``.igz`` documents. Falls
    back to a temp dir when the .skp's folder is read-only."""
    d = Path(skp_path).parent / Path(skp_path).stem
    try:
        d.mkdir(exist_ok=True)
        return d
    except OSError:
        import tempfile
        return Path(tempfile.mkdtemp(prefix="ingetrazo-skp-tex-"))


def _material_attrs(model, skp_path):
    """Map ``material_id`` → IngeTrazo ``Face.attrs`` dict.

    A textured material (``Material.texture``, our upstream PR openskp#4)
    becomes ``{"texture": {"path", "sw", "sh"}}`` — image bytes written once
    to :func:`_texture_dir`, tile size converted inches → metres (defaulting
    to 1 m when the file omits it). A plain material becomes
    ``{"color": [r, g, b]}`` in 0..1 (PR openskp#3). Empty when the installed
    OpenSKP predates the joins."""
    attrs: dict = {}
    tex_dir = None
    for mid, mat in (getattr(model, "materials_by_id", None) or {}).items():
        tex = getattr(mat, "texture", None)
        if tex is not None and getattr(tex, "data", None):
            if tex_dir is None:
                tex_dir = _texture_dir(skp_path)
            # SketchUp often stores the author's FULL original path as the
            # texture filename ("C:\Users\...\toro.png", "P:/SketchUp
            # projects/.../x.png") — reduce to a safe basename or the image
            # lands in nonexistent subdirectories (or an unwritable name on
            # Windows). On a basename collision with different bytes, prefix
            # the material id.
            safe = (tex.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
            img = tex_dir / (safe or f"material_{mid}.png")
            try:
                if img.exists() and img.stat().st_size != len(tex.data):
                    img = tex_dir / f"{mid}_{img.name}"
                if not img.exists() or img.stat().st_size != len(tex.data):
                    img.write_bytes(tex.data)
            except OSError:
                img = None
            if img is not None:
                attrs[mid] = {"texture": {
                    "path": str(img),
                    "sw": (tex.width or 1.0 / _INCH) * _INCH,
                    "sh": (tex.height or 1.0 / _INCH) * _INCH,
                }}
                continue
        color = getattr(mat, "color", None)
        if color is not None and len(color) >= 3:
            attrs[mid] = {"color": [color[0] / 255.0, color[1] / 255.0,
                                    color[2] / 255.0]}
    return attrs


def _face_attrs(face, attr_map, inherited=None):
    """IngeTrazo ``Face.attrs`` for an OpenSKP face, or ``None``.

    A face with no material of its own inherits ``inherited`` — the material
    painted on the nearest enclosing instance (SketchUp's "paint the
    component" rule; ``Instance.material_id``, our upstream PR openskp#5)."""
    mid = getattr(face, "material_id", None)
    if mid is None:
        mid = inherited
    return attr_map.get(mid) if mid is not None else None


def _collect(defn, xform, by_id, attr_map, out, depth, stack,
             proto_ids=frozenset(), proto_uses=None, inherited=None) -> None:
    """Append ``(outer, holes, attrs)`` faces for ``defn`` (transformed by
    ``xform``) and, recursively, for every definition its instances place.

    ``inherited`` is the material of the nearest enclosing painted instance —
    faces with no material of their own take it (SketchUp inheritance).

    When an instance references a definition in ``proto_ids``, its geometry is
    NOT flattened here — the composed placement matrix is recorded in
    ``proto_uses[(def_id, inherited)]`` instead, so the shared prototype is
    built once per inherited material and every copy becomes an O(1) instance
    (``Group.xform``)."""
    if depth > _MAX_DEPTH or id(defn) in stack:
        return
    stack = stack | {id(defn)}
    for face in defn.faces.values():
        loops = getattr(face, "loops", None)
        if not loops:
            continue
        outer = _ring(defn, loops[0])
        if not outer or len(outer) < 3:
            continue
        outer = [xform.map(p) for p in outer]
        holes = []
        for lp in loops[1:]:
            h = _ring(defn, lp)
            if h and len(h) >= 3:
                holes.append([xform.map(p) for p in h])
        out.append((outer, holes, _face_attrs(face, attr_map, inherited)))
    for ins in getattr(defn, "instances", []):
        rid = getattr(ins, "ref_idx", None)
        child = by_id.get(rid)
        if child is None:
            continue
        placed = xform * _matrix(ins.matrix)
        child_inherited = getattr(ins, "material_id", None) or inherited
        cid = getattr(child, "id", None)
        if proto_uses is not None and cid in proto_ids:
            proto_uses.setdefault((cid, child_inherited), []).append(placed)
            continue
        _collect(child, placed, by_id, attr_map, out, depth + 1, stack,
                 proto_ids, proto_uses, child_inherited)


def _subtree_polys(defn, by_id, memo, stack) -> int:
    """Total polygon count of ``defn``'s subtree (own faces + instanced)."""
    cid = id(defn)
    if cid in stack:
        return 0
    cached = memo.get(cid)
    if cached is not None:
        return cached
    stack = stack | {cid}
    n = len(getattr(defn, "faces", {}) or {})
    for ins in getattr(defn, "instances", []):
        child = by_id.get(getattr(ins, "ref_idx", None))
        if child is not None:
            n += _subtree_polys(child, by_id, memo, stack)
    memo[cid] = n
    return n


def _census(defn, by_id, uses, depth, stack) -> None:
    """Count how many times each definition id is placed, walking only the
    tree actually reachable from ``defn`` (dangling library definitions and
    their internal references don't inflate the counts)."""
    if depth > _MAX_DEPTH or id(defn) in stack:
        return
    stack = stack | {id(defn)}
    for ins in getattr(defn, "instances", []):
        rid = getattr(ins, "ref_idx", None)
        child = by_id.get(rid)
        if child is None:
            continue
        uses[getattr(child, "id", None)] = uses.get(
            getattr(child, "id", None), 0) + 1
        _census(child, by_id, uses, depth + 1, stack)


def _adapt(model, name: str, skp_path=None):
    """An ``SkpModel`` → a payload ``{"backend", "groups", "protos"}`` or
    ``None`` when it yields no geometry (so the seam can fall back to skp2dae).

    SketchUp-style structure, mirroring the DAE reference import:

    * the root's loose faces → one group named after the file;
    * each top-level instance → its own group (its subtree flattened into it),
      so every placed component is selectable/movable on its own;
    * a definition placed ≥2 times whose subtree is worth sharing (same
      thresholds as the DAE import) → ONE prototype, extracted at ANY depth,
      each copy an O(1) placement matrix (``Group.xform``).

    Definitions with faces that nothing instances are library entries not
    placed in the model — SketchUp does not render those, and neither do we.
    ``skp_path`` anchors where extracted texture images land; the material
    joins are guarded so PyPI 0.2.0 still imports (uncoloured)."""
    from formats.dae import _INST_MIN_POLYS, _INST_MIN_SAVED

    defs = getattr(model, "definitions", {}) or {}
    attr_map = _material_attrs(model, skp_path or name)
    by_id = {}
    root = None
    for d in defs.values():
        by_id[getattr(d, "id", None)] = d
        if getattr(d, "name", None) == "ROOT_MODEL":
            root = d
    roots = [root] if root is not None else list(defs.values())

    # Shared-prototype census over the reachable tree.
    uses: dict = {}
    memo: dict = {}
    for r in roots:
        _census(r, by_id, uses, 0, set())
    proto_ids = set()
    for did, cnt in uses.items():
        d = by_id.get(did)
        if d is None or cnt < 2:
            continue
        polys = _subtree_polys(d, by_id, memo, set())
        if polys >= _INST_MIN_POLYS and polys * (cnt - 1) >= _INST_MIN_SAVED:
            proto_ids.add(did)

    groups: list = []
    proto_uses: dict = {}
    for r in roots:
        # The root's own loose faces (no instance recursion).
        loose: list = []
        for face in r.faces.values():
            loops = getattr(face, "loops", None)
            if not loops:
                continue
            outer = _ring(r, loops[0])
            if not outer or len(outer) < 3:
                continue
            holes = [h for lp in loops[1:]
                     if (h := _ring(r, lp)) and len(h) >= 3]
            loose.append((outer, holes, _face_attrs(face, attr_map)))
        if loose:
            groups.append({"name": name, "faces": loose})
        # Each top-level instance → its own group (or a shared-proto use).
        for ins in getattr(r, "instances", []):
            child = by_id.get(getattr(ins, "ref_idx", None))
            if child is None:
                continue
            placed = _matrix(ins.matrix)
            inh = getattr(ins, "material_id", None)
            if getattr(child, "id", None) in proto_ids:
                proto_uses.setdefault((child.id, inh), []).append(placed)
                continue
            sub: list = []
            _collect(child, placed, by_id, attr_map, sub, 0, set(),
                     proto_ids, proto_uses, inh)
            if sub:
                groups.append({"name": getattr(child, "name", None) or name,
                               "faces": sub})

    # Build each shared prototype ONCE, in local coordinates — per inherited
    # material, so a component painted red and green as a whole yields two
    # prototypes, not one wrongly shared. Nested proto references inside a
    # prototype flatten into it (no proto-in-proto).
    protos: list = []
    for (did, inh), xforms in proto_uses.items():
        d = by_id.get(did)
        if d is None or not xforms:
            continue
        local: list = []
        _collect(d, QMatrix4x4(), by_id, attr_map, local, 0, set(),
                 inherited=inh)
        if local:
            protos.append({"name": getattr(d, "name", None) or name,
                           "faces": local, "instances": xforms})

    if not groups and not protos:
        return None
    return {"backend": "openskp", "groups": groups, "protos": protos}


def parse(path, progress=None):
    """Parse ``path`` with OpenSKP and adapt it to a payload, or ``None`` when
    no geometry comes out. Raises whatever OpenSKP raises on a file it cannot
    read (the caller treats that as "fall back to the converter")."""
    import openskp
    if progress is not None:
        progress(0.1, "Parsing .skp (OpenSKP)…")
    model = openskp.SkpFile.open(str(path)).parse()
    if progress is not None:
        progress(0.6, "Building geometry…")
    return _adapt(model, Path(path).stem, skp_path=path)
