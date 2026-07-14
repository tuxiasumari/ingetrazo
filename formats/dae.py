# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""COLLADA (.dae) import — open the models SketchUp exports natively.

COLLADA is Khronos' open XML interchange format; parsing with the stdlib
``xml.etree`` keeps the project dependency-free. Scope (matching what
SketchUp emits): ``library_geometries`` meshes (``triangles`` / ``polylist``
/ ``polygons``), the ``visual_scene`` node tree with baked transforms
(``matrix`` / ``translate`` / ``rotate`` / ``scale``), component instancing
via ``library_nodes`` / ``instance_node``, lambert/phong diffuse colours,
``up_axis`` (Y_UP → Z-up conversion) and the ``unit`` metre scale (SketchUp
exports inches). Textures are skipped (colour only).

Like the OBJ importer, triangles are fused back into clean editable polygons
(coplanar merge) and closed results get a consistent outward orientation.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6.QtGui import QMatrix4x4, QVector3D

_NS = "{http://www.collada.org/2005/11/COLLADASchema}"

def _add_fused(mesh, fused) -> None:
    """Add ``fuse_coplanar_loops`` output to ``mesh``, falling back to a
    region's original loops when the fused polygon is rejected."""
    for outer, holes, attrs, originals in fused:
        try:
            f = mesh.add_face(outer, holes or None)
            if attrs:
                f.attrs.update(attrs)
        except Exception:  # noqa: BLE001 — irregular region: keep its pieces
            for pts in originals:
                try:
                    f = mesh.add_face(pts)
                    if attrs:
                        f.attrs.update(attrs)
                except Exception:  # noqa: BLE001
                    continue


# Imports up to this many polygons get the full editable-import pipeline
# (coplanar fusion + outward orientation). Bigger models come from asset
# libraries (3D Warehouse buildings, furniture) and import as-is: the fusion
# pass is O(F²)-ish and measured minutes-to-hours at that scale (a 17k-tri
# building froze the app), while the result is reference geometry anyway.
# Mirrored in formats/obj.py — keep the two in sync.
_MAX_FUSE_LOOPS = 400


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _floats(text) -> list[float]:
    return [float(t) for t in (text or "").split()]


def _ints(text) -> list[int]:
    return [int(t) for t in (text or "").split()]


class _Dae:
    """One parsed document: id-indexed libraries + resolved world geometry."""

    def __init__(self, root, base_dir=None) -> None:
        self.root = root
        self.base_dir = base_dir
        self._img_colors: dict = {}
        # Parsed <source> float arrays and UV tuple lists, keyed by element
        # identity: sources are shared across primitives and instanced
        # geometry, and re-splitting a big TEXCOORD array text per primitive
        # turned a 19 s import into a 4-minute one.
        self._src_cache: dict = {}
        self._uv_cache: dict = {}
        self._img_alpha: dict = {}
        self.by_id: dict = {}
        for el in root.iter():
            i = el.get("id")
            if i is not None:
                self.by_id[i] = el
        asset = root.find(f"{_NS}asset")
        self.scale = 1.0
        self.up = "Z_UP"
        if asset is not None:
            unit = asset.find(f"{_NS}unit")
            if unit is not None and unit.get("meter"):
                self.scale = float(unit.get("meter"))
            up = asset.find(f"{_NS}up_axis")
            if up is not None and up.text:
                self.up = up.text.strip()

    def ref(self, url: str):
        return self.by_id.get((url or "").lstrip("#"))

    def to_zup(self, p: QVector3D) -> QVector3D:
        s = self.scale
        if self.up == "Y_UP":
            return QVector3D(p.x() * s, -p.z() * s, p.y() * s)
        if self.up == "X_UP":
            return QVector3D(p.y() * s, p.x() * s, p.z() * s)
        return QVector3D(p.x() * s, p.y() * s, p.z() * s)


def _source_floats(dae: _Dae, source_el) -> tuple[list[float], int]:
    cached = dae._src_cache.get(id(source_el))
    if cached is not None:
        return cached
    arr = source_el.find(f"{_NS}float_array")
    data = _floats(arr.text if arr is not None else "")
    stride = 3
    tc = source_el.find(f"{_NS}technique_common")
    if tc is not None:
        acc = tc.find(f"{_NS}accessor")
        if acc is not None and acc.get("stride"):
            stride = int(acc.get("stride"))
    dae._src_cache[id(source_el)] = (data, stride)
    return data, stride


def _positions(dae: _Dae, mesh_el) -> list[QVector3D]:
    verts = mesh_el.find(f"{_NS}vertices")
    if verts is None:
        return []
    for inp in verts.findall(f"{_NS}input"):
        if inp.get("semantic") == "POSITION":
            src = dae.ref(inp.get("source"))
            if src is None:
                return []
            data, stride = _source_floats(dae, src)
            return [QVector3D(data[i], data[i + 1], data[i + 2])
                    for i in range(0, len(data) - 2, stride)]
    return []


def _prim_inputs(prim_el) -> tuple[int, int | None, str | None, int]:
    """``(vertex_offset, texcoord_offset, texcoord_source_url, stride)`` of
    the interleaved ``<p>`` stream (stride = max offset + 1)."""
    v_off, uv_off, uv_src, max_off = 0, None, None, 0
    for inp in prim_el.findall(f"{_NS}input"):
        off = int(inp.get("offset", "0"))
        max_off = max(max_off, off)
        sem = inp.get("semantic")
        if sem == "VERTEX":
            v_off = off
        elif sem == "TEXCOORD" and uv_off is None:
            uv_off = off
            uv_src = inp.get("source")
    return v_off, uv_off, uv_src, max_off + 1


def _prim_loops(prim_el, positions, uv_count: int = 0) -> list:
    """``(vertex_loop, uv_loop_or_None)`` pairs of one ``triangles`` /
    ``polylist`` / ``polygons`` primitive. UV loops are only produced when
    the primitive has a TEXCOORD input and ``uv_count`` > 0."""
    kind = _tag(prim_el)
    v_off, uv_off, _src, stride = _prim_inputs(prim_el)
    take_uv = uv_off is not None and uv_count > 0
    loops: list = []

    def emit(idx, start, nverts):
        vl = [idx[start + j * stride + v_off] for j in range(nverts)]
        ul = ([idx[start + j * stride + uv_off] for j in range(nverts)]
              if take_uv else None)
        loops.append((vl, ul))

    if kind == "triangles":
        p = prim_el.find(f"{_NS}p")
        idx = _ints(p.text if p is not None else "")
        verts_per = 3 * stride
        for k in range(0, len(idx) - verts_per + 1, verts_per):
            emit(idx, k, 3)
    elif kind == "polylist":
        vc = prim_el.find(f"{_NS}vcount")
        p = prim_el.find(f"{_NS}p")
        counts = _ints(vc.text if vc is not None else "")
        idx = _ints(p.text if p is not None else "")
        pos = 0
        for c in counts:
            if pos + c * stride <= len(idx):
                emit(idx, pos, c)
            pos += c * stride
    elif kind == "polygons":
        for p in prim_el.findall(f"{_NS}p"):
            idx = _ints(p.text)
            emit(idx, 0, len(idx) // stride)
    out = []
    for vl, ul in loops:
        if len(vl) < 3 or not all(0 <= i < len(positions) for i in vl):
            continue
        if ul is not None and not all(0 <= i < uv_count for i in ul):
            ul = None
        out.append((vl, ul))
    return out


def _image_color(dae: _Dae, image_el):
    """Representative RGB of a texture image: the average colour when the
    file exists next to the ``.dae`` (SketchUp exports a ``<name>/`` folder),
    else a stable light tint derived from the path — either way, distinct
    materials get distinct colours, so the fusion pass keeps their
    boundaries (a plaza's curb lines must not melt into the paving)."""
    init = image_el.find(f"{_NS}init_from")
    ref = (init.text or "").strip() if init is not None else ""
    if not ref:
        return None
    cached = dae._img_colors.get(ref)
    if cached is not None:
        return cached
    color = None
    if dae.base_dir is not None:
        from urllib.parse import unquote
        candidate = Path(dae.base_dir) / unquote(ref)
        if candidate.is_file():
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QImage
            img = QImage(str(candidate))
            if not img.isNull():
                px = img.scaled(1, 1, Qt.IgnoreAspectRatio,
                                Qt.SmoothTransformation)
                c = px.pixelColor(0, 0)
                color = [c.redF(), c.greenF(), c.blueF()]
    if color is None:
        # Missing image: a deterministic light tint keeps the material
        # boundary visible without inventing loud colours.
        h = hash(ref)
        color = [0.78 + ((h >> s) & 15) / 100.0 for s in (0, 4, 8)]
    dae._img_colors[ref] = color
    return color


def _effect_diffuse(dae: _Dae, material_el):
    """``(rgb_or_None, image_el_or_None)`` of a material's diffuse: the
    literal colour, or the diffuse texture's ``<image>`` element."""
    ie = material_el.find(f"{_NS}instance_effect")
    effect = dae.ref(ie.get("url")) if ie is not None else None
    if effect is None:
        return None, None
    for shader in ("lambert", "phong", "blinn", "constant"):
        for el in effect.iter(f"{_NS}{shader}"):
            diffuse = el.find(f"{_NS}diffuse")
            if diffuse is None:
                continue
            col = diffuse.find(f"{_NS}color")
            if col is not None:
                vals = _floats(col.text)
                if len(vals) >= 3:
                    return vals[:3], None
            tex = diffuse.find(f"{_NS}texture")
            if tex is not None:
                # Chase sampler → surface → image (SketchUp sometimes puts
                # the image id directly in texture/@texture).
                sid = tex.get("texture") or ""
                target = dae.by_id.get(sid)
                if target is None or _tag(target) != "image":
                    for np_el in effect.iter(f"{_NS}newparam"):
                        if np_el.get("sid") != sid:
                            continue
                        s2d = np_el.find(f"{_NS}sampler2D")
                        src = (s2d.find(f"{_NS}source")
                               if s2d is not None else None)
                        surf_sid = (src.text or "").strip() if src is not None else ""
                        for np2 in effect.iter(f"{_NS}newparam"):
                            if np2.get("sid") != surf_sid:
                                continue
                            surf = np2.find(f"{_NS}surface")
                            init = (surf.find(f"{_NS}init_from")
                                    if surf is not None else None)
                            if init is not None and init.text:
                                target = dae.by_id.get(init.text.strip())
                        break
                if target is not None and _tag(target) == "image":
                    return None, target
    return None, None


def _image_file(dae: _Dae, image_el):
    """Absolute path of a texture image when it exists next to the ``.dae``
    (SketchUp exports a ``<name>/`` folder), else ``None``."""
    init = image_el.find(f"{_NS}init_from")
    ref = (init.text or "").strip() if init is not None else ""
    if not ref or dae.base_dir is None:
        return None
    from urllib.parse import unquote
    candidate = Path(dae.base_dir) / unquote(ref)
    return str(candidate) if candidate.is_file() else None


def _material_map(dae: _Dae, inst_geom_el) -> dict:
    """symbol → ``{"color": rgb_or_None, "path": image_path_or_None}`` for one
    ``instance_geometry``'s bound materials. ``path`` is set only when the
    image file actually exists on disk; the representative colour always
    rides along as the fallback (missing TEXCOORD, missing file)."""
    out: dict = {}
    for im in inst_geom_el.iter(f"{_NS}instance_material"):
        mat = dae.ref(im.get("target"))
        if mat is None:
            continue
        color, img = _effect_diffuse(dae, mat)
        path = _image_file(dae, img) if img is not None else None
        if color is None and img is not None:
            color = _image_color(dae, img)
        if color is not None or path is not None:
            out[im.get("symbol")] = {"color": color, "path": path}
    return out


def _face_attrs(zpts, color, tex):
    """Face attrs for one imported polygon: a real texture (world→UV affine
    map fitted from the file's own texture coordinates, evaluated in final
    Z-up world space) when the image exists, else the diffuse colour."""
    if tex is not None:
        path, uvs = tex
        from core.texture import fit_uv_affine
        m = fit_uv_affine(zpts, uvs)
        if m is not None:
            import math as _math
            glu = _math.hypot(m[0], m[1], m[2])
            glv = _math.hypot(m[4], m[5], m[6])
            return {"texture": {
                "path": path, "uvw": m,
                # Display/export tile size derived from the UV gradients.
                "sw": (1.0 / glu) if glu > 1e-9 else 1.0,
                "sh": (1.0 / glv) if glv > 1e-9 else 1.0,
            }}
    if color is not None:
        return {"color": [float(c) for c in color[:3]]}
    return None


def _node_matrix(node_el) -> QMatrix4x4:
    m = QMatrix4x4()
    for el in node_el:
        t = _tag(el)
        if t == "matrix":
            vals = _floats(el.text)
            if len(vals) == 16:
                mm = QMatrix4x4(*vals)     # COLLADA matrices are row-major
                m = m * mm
        elif t == "translate":
            v = _floats(el.text)
            if len(v) >= 3:
                m.translate(v[0], v[1], v[2])
        elif t == "rotate":
            v = _floats(el.text)
            if len(v) >= 4:
                m.rotate(v[3], v[0], v[1], v[2])
        elif t == "scale":
            v = _floats(el.text)
            if len(v) >= 3:
                m.scale(v[0], v[1], v[2])
    return m


def _prim_uvs(dae: _Dae, prim_el) -> list:
    """The TEXCOORD source of a primitive as ``(s, t)`` tuples (empty when
    the primitive carries no texture coordinates)."""
    _v, uv_off, uv_src, _s = _prim_inputs(prim_el)
    if uv_off is None or not uv_src:
        return []
    src = dae.ref(uv_src)
    if src is None:
        return []
    cached = dae._uv_cache.get(id(src))
    if cached is None:
        data, stride = _source_floats(dae, src)
        stride = max(stride, 2)
        cached = dae._uv_cache[id(src)] = [
            (data[i], data[i + 1])
            for i in range(0, len(data) - stride + 1, stride)]
    return cached


def _collect_direct(dae: _Dae, node_el, m: QMatrix4x4, out: list) -> None:
    """The ``instance_geometry`` carried directly by ``node_el`` (no
    recursion), transformed by the already-composed matrix ``m``. Emits
    ``(points, color, tex)`` — ``tex`` is ``(image_path, uvs)`` when the
    material has an on-disk image and the primitive carries TEXCOORDs."""
    for el in node_el:
        if _tag(el) != "instance_geometry":
            continue
        geom = dae.ref(el.get("url"))
        if geom is None:
            continue
        mats = _material_map(dae, el)
        mesh_el = geom.find(f"{_NS}mesh")
        if mesh_el is None:
            continue
        positions = _positions(dae, mesh_el)
        world = [m.map(p) for p in positions]
        for prim in mesh_el:
            kind = _tag(prim)
            if kind not in ("triangles", "polylist", "polygons"):
                continue
            mat = mats.get(prim.get("material")) or {}
            color = mat.get("color")
            path = mat.get("path")
            uvpts = _prim_uvs(dae, prim) if path is not None else []
            for lp, ul in _prim_loops(prim, world, uv_count=len(uvpts)):
                tex = None
                if ul is not None and path is not None:
                    tex = (path, [uvpts[i] for i in ul])
                out.append(([world[i] for i in lp], color, tex))


def _image_has_cutout(dae: _Dae, path: str) -> bool:
    """Whether the image carries REAL transparency (some pixels see-through)
    — the signature of a photo sprite (a person/animal/tree cutout PNG),
    versus an opaque photo panel (a sign or mural)."""
    cached = dae._img_alpha.get(path)
    if cached is not None:
        return cached
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    img = QImage(path)
    ok = False
    if not img.isNull() and img.hasAlphaChannel():
        small = img.scaled(32, 32, Qt.IgnoreAspectRatio,
                           Qt.FastTransformation)
        for yy in range(small.height()):
            for xx in range(small.width()):
                if small.pixelColor(xx, yy).alpha() < 32:
                    ok = True
                    break
            if ok:
                break
    dae._img_alpha[path] = ok
    return ok


def _looks_faceme(dae: _Dae, loops) -> bool:
    """Whether a component subtree reads as a SketchUp face-me sprite: every
    polygon textured with the SAME image, all coplanar on ONE near-vertical
    plane (in final Z-up coordinates), and either a cut silhouette (more
    than 4 distinct corners) or a rectangle whose image has REAL
    transparency (SketchUp people/animals/trees are alpha-cutout PNGs on a
    rectangle; an opaque photo panel — a sign, a mural — stays static).
    COLLADA drops the 'always face camera' flag, so these are the only
    signals left to keep sprites turning toward the camera."""
    from formats.fuse import _key, _newell
    if not 1 <= len(loops) <= 400:
        return False
    path = None
    n_ref = None
    corners: set = set()
    for pts, _color, tex in loops:
        if tex is None:
            return False
        if path is None:
            path = tex[0]
        elif tex[0] != path:
            return False
        zpts = [dae.to_zup(p) for p in pts]
        n = _newell(zpts)
        ln = n.length()
        if ln < 1e-12:
            continue
        n = n / ln
        if abs(n.z()) > 0.08:
            return False                     # not vertical
        if n_ref is None:
            n_ref = n
        elif abs(QVector3D.dotProduct(n, n_ref)) < 0.99:
            return False                     # not one plane
        for p in zpts:
            corners.add(_key(p))
    if n_ref is None:
        return False
    return len(corners) > 4 or _image_has_cutout(dae, path)


def _collect(dae: _Dae, node_el, xform: QMatrix4x4, out: list,
             depth: int = 0, faceme: list | None = None) -> None:
    """Walk a node tree, baking transforms; instances recurse into
    ``library_nodes`` (SketchUp components). With ``faceme`` given, a child
    component whose geometry is a face-me silhouette is pulled out into it
    as ``(name, loops)`` instead of flattening into the parent."""
    if depth > 32:
        return                                     # cyclic instance guard
    m = xform * _node_matrix(node_el)
    _collect_direct(dae, node_el, m, out)
    for el in node_el:
        t = _tag(el)
        target = None
        d2 = depth
        if t == "node":
            target = el
        elif t == "instance_node":
            target = dae.ref(el.get("url"))
            d2 = depth + 1
        if target is None:
            continue
        if faceme is not None:
            sub: list = []
            _collect(dae, target, m, sub, d2, faceme)
            if sub and _looks_faceme(dae, sub):
                faceme.append((el.get("name") or _node_label(target), sub))
            else:
                out.extend(sub)
        else:
            _collect(dae, target, m, out, d2)


def _prim_count(prim) -> int:
    """Cheap polygon count of a primitive from its attributes (no ``<p>``
    parsing) — used only to decide where to split groups."""
    kind = _tag(prim)
    if kind in ("triangles", "polylist"):
        try:
            return int(prim.get("count", "0"))
        except ValueError:
            return 0
    if kind == "polygons":
        return len(prim.findall(f"{_NS}p"))
    return 0


def _direct_count(dae: _Dae, node_el) -> int:
    n = 0
    for el in node_el:
        if _tag(el) != "instance_geometry":
            continue
        geom = dae.ref(el.get("url"))
        mesh_el = geom.find(f"{_NS}mesh") if geom is not None else None
        if mesh_el is not None:
            n += sum(_prim_count(p) for p in mesh_el)
    return n


def _subtree_count(dae: _Dae, node_el, depth: int = 0) -> int:
    if depth > 32:
        return 0
    n = _direct_count(dae, node_el)
    for el in node_el:
        t = _tag(el)
        if t == "node":
            n += _subtree_count(dae, el, depth)
        elif t == "instance_node":
            target = dae.ref(el.get("url"))
            if target is not None:
                n += _subtree_count(dae, target, depth + 1)
    return n


# Reference imports mirror SketchUp's group structure: every DAE assembly
# (group / component instance) can become its own Group, so the user selects,
# moves and edits a farola or a pérgola as a unit instead of one monolithic
# 160k-face blob (exploding THAT into the loose mesh melts the editing
# engine). The splitter is greedy — always split the largest splittable
# bucket — bounded by _MAX_GROUPS (per-group render/pick overhead is real)
# and _SPLIT_MIN (a small bench stays one piece even if internally grouped).
_MAX_GROUPS = 250
_SPLIT_MIN = 4000


def _node_label(node_el) -> str:
    return node_el.get("name") or node_el.get("id") or "node"


def _bucketize(dae: _Dae, top_nodes, faceme: list | None = None) -> list:
    """Split the visual scene into ``(name, loops)`` buckets along the DAE
    node hierarchy (SketchUp groups/components). Returns at most
    ``_MAX_GROUPS`` buckets, largest assemblies split first. Face-me
    silhouettes found anywhere in the tree land in ``faceme`` instead."""
    import heapq
    from itertools import count as _count
    tie = _count()
    # Heap entries: (-tris, tie, kind, node_el, xform, depth, name)
    # kind "subtree": xform is the PRE-entry matrix (node's own applies at
    # collect). kind "direct": xform is the composed matrix, no recursion.
    heap = []
    for nd in top_nodes:
        n = _subtree_count(dae, nd)
        if n > 0:
            heapq.heappush(heap, (-n, next(tie), "subtree", nd,
                                  QMatrix4x4(), 0, _node_label(nd)))
    final: list = []
    while heap and (len(final) + len(heap)) < _MAX_GROUPS:
        entry = heapq.heappop(heap)
        neg, _t, kind, node_el, xform, depth, name = entry
        if -neg <= _SPLIT_MIN:
            heapq.heappush(heap, entry)
            break                      # largest left is small: all are done
        kids = [el for el in node_el if _tag(el) == "node"]
        insts = [el for el in node_el if _tag(el) == "instance_node"]
        if kind == "direct" or depth > 32 or (not kids and not insts):
            final.append(entry)        # big but unsplittable: keep as-is
            continue
        m = xform * _node_matrix(node_el)
        dg = _direct_count(dae, node_el)
        if dg > 0:
            heapq.heappush(heap, (-dg, next(tie), "direct", node_el, m,
                                  depth, name))
        for el in kids:
            n = _subtree_count(dae, el, depth)
            if n > 0:
                heapq.heappush(heap, (-n, next(tie), "subtree", el, m,
                                      depth, _node_label(el)))
        for el in insts:
            target = dae.ref(el.get("url"))
            if target is None:
                continue
            n = _subtree_count(dae, target, depth + 1)
            if n > 0:
                nm = el.get("name") or _node_label(target)
                heapq.heappush(heap, (-n, next(tie), "subtree", target, m,
                                      depth + 1, nm))
    buckets = []
    for neg, _t, kind, node_el, xform, depth, name in sorted(final + heap):
        loops: list = []
        if kind == "direct":
            _collect_direct(dae, node_el, xform, loops)
        else:
            _collect(dae, node_el, xform, loops, depth, faceme)
            if faceme is not None and loops and _looks_faceme(dae, loops):
                faceme.append((name, loops))   # the bucket itself is a sprite
                continue
        if loops:
            buckets.append((name, loops))
    return buckets


def load_dae(scene, path, progress=None) -> None:
    """Add the geometry of a COLLADA file at ``path`` to ``scene``.

    Small models (≤ ``_MAX_FUSE_LOOPS`` polygons) go into the loose mesh and
    get the full editable pipeline — triangle fusion + outward orientation.
    Bigger models are *reference* geometry: they land in their own
    :class:`~core.group.Group` (isolated mesh, SketchUp-style), so the
    editing engine — snap, edge splitting, auto-face, heals — never scans
    their thousands of triangles while the user draws beside them. The
    caller wraps this for undo (``SnapshotImport`` handles the group).

    ``progress``, when given, is called as ``progress(fraction, text)`` at
    milestones so the UI can show an import progress bar."""
    from core.history import run_stitch
    from core.orient import orient_outward
    from core.topology import _key

    def tick(frac, text):
        if progress is not None:
            progress(frac, text)

    tick(0.02, "Reading file…")
    root = ET.parse(Path(path)).getroot()
    dae = _Dae(root, base_dir=Path(path).parent)
    top_nodes: list = []
    for vs in root.iter(f"{_NS}visual_scene"):
        top_nodes.extend(vs.findall(f"{_NS}node"))
    total_polys = sum(_subtree_count(dae, nd) for nd in top_nodes)

    if total_polys > _MAX_FUSE_LOOPS:
        # Reference import, SketchUp-structured: one Group per DAE assembly
        # (group / component instance) via the greedy splitter, so elements
        # stay individually selectable/movable/editable and the loose-mesh
        # engine never has to swallow the whole model.
        from core.group import Group
        from core.mesh import Mesh
        from formats.fuse import fuse_coplanar_loops, soften_smooth_edges
        tick(0.1, "Collecting geometry…")
        faceme: list = []
        buckets = _bucketize(dae, top_nodes, faceme)
        if not buckets and not faceme:
            raise ValueError("No geometry found in the COLLADA file")
        total_loops = max(sum(len(lp) for _n, lp in buckets + faceme), 1)
        done = 0
        for is_sprite, name, loops in (
                [(False, n, lp) for n, lp in buckets]
                + [(True, n, lp) for n, lp in faceme]):
            tick(0.15 + 0.8 * done / total_loops, f"Building {name}…")
            raw = []
            for loop, color, tex in loops:
                zpts = [dae.to_zup(p) for p in loop]
                raw.append((zpts, _face_attrs(zpts, color, tex)))
            fused = fuse_coplanar_loops(raw)
            target = Mesh()
            for item in fused:
                _add_fused(target, [item])
            soften_smooth_edges(target)
            if target.faces:
                g = Group(target, name=name)
                if is_sprite:
                    g.billboard = "mesh"   # geometry turns toward the camera
                scene.groups.append(g)
            done += len(loops)
        scene.version += 1
        tick(1.0, "Done")
        return

    pending: list = []
    faceme: list = []
    for node in top_nodes:
        sub: list = []
        _collect(dae, node, QMatrix4x4(), sub, faceme=faceme)
        if sub and _looks_faceme(dae, sub):
            faceme.append((_node_label(node), sub))   # top node IS a sprite
        else:
            pending.extend(sub)
    if faceme:
        # Face-me sprites become billboard groups even in a small import.
        from core.group import Group
        from core.mesh import Mesh
        from formats.fuse import fuse_coplanar_loops, soften_smooth_edges
        for name, loops in faceme:
            raw = []
            for loop, color, tex in loops:
                zpts = [dae.to_zup(p) for p in loop]
                raw.append((zpts, _face_attrs(zpts, color, tex)))
            target = Mesh()
            for item in fuse_coplanar_loops(raw):
                _add_fused(target, [item])
            soften_smooth_edges(target)
            if target.faces:
                g = Group(target, name=name)
                g.billboard = "mesh"
                scene.groups.append(g)
    if not pending and not faceme:
        # No visual scene (bare geometry library): import it un-instanced.
        for geom in root.iter(f"{_NS}geometry"):
            mesh_el = geom.find(f"{_NS}mesh")
            if mesh_el is None:
                continue
            positions = _positions(dae, mesh_el)
            for prim in mesh_el:
                if _tag(prim) in ("triangles", "polylist", "polygons"):
                    for lp, _ul in _prim_loops(prim, positions):
                        pending.append(([positions[i] for i in lp],
                                        None, None))
    if not pending:
        if faceme:
            scene.version += 1
            tick(1.0, "Done")
            return
        raise ValueError("No geometry found in the COLLADA file")

    if len(pending) > _MAX_FUSE_LOOPS:
        # Bare geometry library (no visual scene) too big for the editable
        # pipeline: import as ONE reference group.
        from core.group import Group
        from core.mesh import Mesh
        from formats.fuse import fuse_coplanar_loops, soften_smooth_edges
        tick(0.35, "Transforming…")
        raw = []
        for loop, color, tex in pending:
            zpts = [dae.to_zup(p) for p in loop]
            raw.append((zpts, _face_attrs(zpts, color, tex)))
        tick(0.5, "Merging coplanar faces…")
        fused = fuse_coplanar_loops(raw)
        target = Mesh()
        n = max(len(fused), 1)
        for k, item in enumerate(fused):
            if progress is not None and k % 8192 == 0:
                tick(0.6 + 0.3 * k / n, "Building the model…")
            _add_fused(target, [item])
        tick(0.92, "Smoothing edges…")
        soften_smooth_edges(target)
        scene.groups.append(Group(target, name=Path(path).stem))
        scene.version += 1
        tick(1.0, "Done")
        return

    target = scene.mesh
    seed: set = set()
    new_faces = set()
    for loop, color, tex in pending:
        pts = [dae.to_zup(p) for p in loop]
        try:
            face = target.add_face(pts)
        except Exception:  # noqa: BLE001 — skip a degenerate polygon
            continue
        new_faces.add(face)
        attrs = _face_attrs(pts, color, tex)
        if attrs:
            face.attrs.update(attrs)
        for p in pts:
            seed.add(_key(p))

    # Fuse the exported triangles back into clean polygons and give a closed
    # result a consistent outward orientation — same pipeline as OBJ import.
    # (Big models took the reference-group path above with the fast fusion.)
    run_stitch(scene.mesh, seed, new_faces, coplanar_merge=True)
    orient_outward(scene.mesh)
    scene.version += 1
