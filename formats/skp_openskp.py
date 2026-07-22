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


def _ring_raw(defn, loop):
    """Resolve one ``[(edge_id, sense), …]`` loop to raw local ``(x, y, z)``
    tuples in INCHES. Returns ``None`` on any dangling reference."""
    pts = []
    for eid, sense in loop:
        edge = defn.edges.get(eid)
        if edge is None:
            return None
        vid = edge.v1_id if sense else edge.v2_id
        v = defn.vertices.get(vid)
        if v is None:
            return None
        pts.append((v.x, v.y, v.z))
    return pts


def _ring(defn, loop):
    """Resolve one ``[(edge_id, sense), …]`` loop to a list of local-space
    ``QVector3D`` (metres). Returns ``None`` on any dangling reference."""
    raw = _ring_raw(defn, loop)
    if raw is None:
        return None
    return [QVector3D(x * _INCH, y * _INCH, z * _INCH) for x, y, z in raw]


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


def _rgb_to_hls(rgb):
    """Vectorized colorsys.rgb_to_hls over an (..., 3) float array in 0..1."""
    import numpy as np
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maxc = rgb.max(axis=-1)
    minc = rgb.min(axis=-1)
    lum = (maxc + minc) / 2.0
    delta = maxc - minc
    nz = delta > 1e-12
    denom = np.where(lum <= 0.5, maxc + minc, 2.0 - maxc - minc)
    sat = np.where(nz & (denom > 1e-12), delta / np.where(denom > 1e-12, denom, 1.0), 0.0)
    safe = np.where(nz, delta, 1.0)
    rc = (maxc - r) / safe
    gc = (maxc - g) / safe
    bc = (maxc - b) / safe
    hue = np.where(maxc == r, bc - gc,
                   np.where(maxc == g, 2.0 + rc - bc, 4.0 + gc - rc))
    hue = np.where(nz, (hue / 6.0) % 1.0, 0.0)
    return hue, lum, sat


def _hls_to_rgb(hue, lum, sat):
    """Vectorized colorsys.hls_to_rgb; returns an (..., 3) float array."""
    import numpy as np
    m2 = np.where(lum <= 0.5, lum * (1.0 + sat), lum + sat - lum * sat)
    m1 = 2.0 * lum - m2

    def channel(h):
        h = h % 1.0
        return np.where(h < 1.0 / 6.0, m1 + (m2 - m1) * h * 6.0,
               np.where(h < 0.5, m2,
               np.where(h < 2.0 / 3.0, m1 + (m2 - m1) * (2.0 / 3.0 - h) * 6.0,
                        m1)))

    return np.stack([channel(hue + 1.0 / 3.0), channel(hue),
                     channel(hue - 1.0 / 3.0)], axis=-1)


def _needs_tint(data, declared_rgb) -> bool:
    """Whether a colourized-flagged texture actually needs re-tinting: only
    when the declared material colour differs from the image's (alpha-
    weighted) average — i.e. the user really recoloured it. The legacy
    format flags more materials than were ever tinted."""
    if not data or declared_rgb is None:
        return False
    from PySide6.QtGui import QImage
    img = QImage.fromData(data)
    if img.isNull():
        return False
    img = img.convertToFormat(QImage.Format_ARGB32)
    small = img.scaled(16, 16)
    sr = sg = sb = sa = 0
    for y in range(small.height()):
        for x in range(small.width()):
            c = small.pixelColor(x, y)
            a = c.alpha()
            sr += c.red() * a
            sg += c.green() * a
            sb += c.blue() * a
            sa += a
    if sa == 0:
        return False
    avg = (sr / sa, sg / sa, sb / sa)
    d = max(abs(avg[i] - declared_rgb[i]) for i in range(3))
    return d > 24.0


def _colorize_image(data, target_rgb, ctype):
    """Re-tint a shared texture the way SketchUp renders a colourized
    material copy ("[Name]1", ``type="2"``).

    ``ctype`` 0 ("shift") moves every pixel's hue/lightness/saturation by
    the delta between the image average and the material colour; ``1``
    ("tint") replaces hue/saturation outright, keeping the per-pixel
    lightness variation. Alpha is preserved (the chain-link cutout must
    survive). Returns PNG bytes, or ``data`` unchanged on any failure."""
    try:
        import numpy as np
        from PySide6.QtCore import QBuffer
        from PySide6.QtGui import QImage
        img = QImage.fromData(data)
        if img.isNull():
            return data
        img = img.convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = img.width(), img.height()
        buf = np.frombuffer(img.constBits(), dtype=np.uint8,
                            count=h * img.bytesPerLine())
        px = buf.reshape(h, img.bytesPerLine())[:, : w * 4].reshape(h, w, 4)
        rgb = px[..., :3].astype(np.float64) / 255.0
        alpha = px[..., 3]
        vis = alpha > 0
        avg = rgb[vis].mean(axis=0) if vis.any() else rgb.mean(axis=(0, 1))
        h0, l0, s0 = _rgb_to_hls(avg.reshape(1, 3))
        ht, lt, st = _rgb_to_hls(
            np.array([[c / 255.0 for c in target_rgb[:3]]]))
        hue, lum, sat = _rgb_to_hls(rgb)
        if ctype == 1:
            hue = np.full_like(hue, float(ht[0]))
            sat = np.full_like(sat, float(st[0]))
        else:
            hue = (hue + (float(ht[0]) - float(h0[0]))) % 1.0
            sat = np.clip(sat + (float(st[0]) - float(s0[0])), 0.0, 1.0)
        lum = np.clip(lum + (float(lt[0]) - float(l0[0])), 0.0, 1.0)
        out = np.clip(_hls_to_rgb(hue, lum, sat) * 255.0 + 0.5,
                      0, 255).astype(np.uint8)
        result = np.dstack([out, alpha])
        tinted = QImage(result.tobytes(), w, h, w * 4,
                        QImage.Format.Format_RGBA8888)
        qbuf = QBuffer()
        qbuf.open(QBuffer.OpenModeFlag.WriteOnly)
        tinted.save(qbuf, "PNG")
        return bytes(qbuf.data())
    except Exception:
        return data


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
            data = tex.data
            if getattr(mat, "colorized", False) and \
                    _needs_tint(data, getattr(mat, "color", None)):
                # Colourized copy ("[Name]1"): the stored image is SHARED
                # with the source material — re-tint it toward the material
                # colour (SketchUp shift/tint) and keep it under its own
                # name so the base texture stays untouched. The _needs_tint
                # guard skips materials whose declared colour already IS the
                # image average (the legacy colourized flag is greedy —
                # e.g. geolocation snapshots would go greyscale otherwise).
                data = _colorize_image(
                    data, getattr(mat, "color", (128, 128, 128)),
                    getattr(mat, "colorize_type", 0))
                safe = f"{mid}_{safe or 'material.png'}"
            img = tex_dir / (safe or f"material_{mid}.png")
            try:
                if img.exists() and img.stat().st_size != len(data):
                    img = tex_dir / f"{mid}_{img.name}"
                if not img.exists() or img.stat().st_size != len(data):
                    img.write_bytes(data)
            except OSError:
                img = None
            if img is not None:
                entry = {"texture": {
                    "path": str(img),
                    "sw": (tex.width or 1.0 / _INCH) * _INCH,
                    "sh": (tex.height or 1.0 / _INCH) * _INCH,
                }}
                op = getattr(mat, "transparency", 1.0)
                if op < 0.999:
                    entry["opacity"] = float(op)
                attrs[mid] = entry
                continue
        color = getattr(mat, "color", None)
        if color is not None and len(color) >= 3:
            entry = {"color": [color[0] / 255.0, color[1] / 255.0,
                               color[2] / 255.0]}
            op = getattr(mat, "transparency", 1.0)
            if op < 0.999:
                entry["opacity"] = float(op)
            attrs[mid] = entry
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


def _plane_basis(normal):
    """SketchUp's canonical in-plane axes for a face normal — the basis its
    per-face texture mapping is expressed in: ``xr = normalize(Z × n)``,
    ``yr = n × xr``; for a vertical normal, ``xr = X`` and ``yr = ±Y``."""
    n = QVector3D(*normal)
    n.normalize()
    if abs(n.x()) < 1e-9 and abs(n.y()) < 1e-9:
        xr = QVector3D(1.0, 0.0, 0.0)
        yr = QVector3D(0.0, 1.0 if n.z() > 0 else -1.0, 0.0)
        return xr, yr
    xr = QVector3D.crossProduct(QVector3D(0.0, 0.0, 1.0), n)
    xr.normalize()
    return xr, QVector3D.crossProduct(n, xr)


def _positioned_uvs(face, raw_ring, tex, matrix=None, projected=False):
    """Per-vertex UVs for a face whose texture was positioned / photo-fitted
    (``Face.uv_transform``, our upstream PR openskp#6), or ``None``.

    The stored 3×3 row-major matrix maps texture space → face plane; the UV
    of a local point ``p`` (INCHES) is ``[p·xr, p·yr, 1] @ inv(M)``, then
    ``/q`` and ``/tile`` — the recipe calibrated against SDK ground truth
    (exact for rotated and 4-pin distorted mappings alike). ``matrix``
    overrides the face's front transform (e.g. ``uv_transform_back`` when
    rendering a back-painted face)."""
    mat = matrix if matrix is not None else getattr(face, "uv_transform", None)
    if mat is None or len(mat) != 9:
        return None
    tw = (tex.get("sw", 0.0) or 0.0) / _INCH   # tile back to inches
    th = (tex.get("sh", 0.0) or 0.0) / _INCH
    if tw <= 0 or th <= 0:
        return None
    m = [list(mat[0:3]), list(mat[3:6]), list(mat[6:9])]
    # Invert the 3×3 (adjugate / determinant).
    det = (m[0][0]*(m[1][1]*m[2][2]-m[1][2]*m[2][1])
           - m[0][1]*(m[1][0]*m[2][2]-m[1][2]*m[2][0])
           + m[0][2]*(m[1][0]*m[2][1]-m[1][1]*m[2][0]))
    if abs(det) < 1e-15:
        return None
    inv = [[(m[1][1]*m[2][2]-m[1][2]*m[2][1])/det,
            (m[0][2]*m[2][1]-m[0][1]*m[2][2])/det,
            (m[0][1]*m[1][2]-m[0][2]*m[1][1])/det],
           [(m[1][2]*m[2][0]-m[1][0]*m[2][2])/det,
            (m[0][0]*m[2][2]-m[0][2]*m[2][0])/det,
            (m[0][2]*m[1][0]-m[0][0]*m[1][2])/det],
           [(m[1][0]*m[2][1]-m[1][1]*m[2][0])/det,
            (m[0][1]*m[2][0]-m[0][0]*m[2][1])/det,
            (m[0][0]*m[1][1]-m[0][1]*m[1][0])/det]]
    if projected:
        # PROJECTED texture (Add Location terrain drape): the mapping runs
        # in the projection plane — plan XY for the vertical drape — so
        # every face of the terrain samples one continuous image regardless
        # of its tilt. (Non-vertical projection axes are not decoded yet.)
        xr = yr = None
    else:
        xr, yr = _plane_basis(face.normal or (0.0, 0.0, 1.0))
    uvs = []
    for x, y, z in raw_ring:                    # local INCHES
        if projected:
            x2, y2 = x, y
        else:
            p = QVector3D(x, y, z)
            x2 = QVector3D.dotProduct(p, xr)
            y2 = QVector3D.dotProduct(p, yr)
        # row-vector: uvq = [x2, y2, 1] @ inv
        u = x2*inv[0][0] + y2*inv[1][0] + inv[2][0]
        v = x2*inv[0][1] + y2*inv[1][1] + inv[2][1]
        q = x2*inv[0][2] + y2*inv[1][2] + inv[2][2]
        if abs(q) < 1e-12:
            return None
        uvs.append((u/q/tw, v/q/th))
    return uvs


def _face_entry(defn, face, xform, attr_map, inherited=None):
    """One payload face ``(outer, holes, attrs)`` for ``face`` transformed by
    ``xform``, or ``None`` when degenerate. Bakes a positioned / photo-fitted
    texture's exact per-face UVs (``Face.uv_transform``, upstream PR
    openskp#6; computed in local inches) into a world→UV affine — the
    ``"uvw"`` IngeTrazo's renderer and exporters already consume. Exact for
    triangles; a per-face affine fit of the projective map otherwise."""
    from core.texture import fit_uv_affine

    loops = getattr(face, "loops", None)
    if not loops:
        return None
    raw = _ring_raw(defn, loops[0])
    if not raw or len(raw) < 3:
        return None
    # Material precedence, matching SketchUp: the face's OWN material wins —
    # front side first, then back side (flipping the face so the painted
    # side fronts, what "Reverse Faces + paint" produces) — and only a face
    # with no material of its own inherits the enclosing instance's paint.
    # (An instance-painted group whose faces carry their own back materials —
    # e.g. a bullring painted blue as a group but with grey/red faces —
    # must show the faces' colours, not the blue.)
    uv_mat = getattr(face, "uv_transform", None)
    flipped = False
    mid = getattr(face, "material_id", None)
    attrs = attr_map.get(mid) if mid is not None else None
    if attrs is None:
        battrs = attr_map.get(getattr(face, "back_material_id", None))
        if battrs is not None:
            attrs = battrs
            uv_mat = getattr(face, "uv_transform_back", None)
            raw = list(reversed(raw))
            flipped = True
        elif inherited is not None:
            attrs = attr_map.get(inherited)
    outer = [xform.map(QVector3D(x * _INCH, y * _INCH, z * _INCH))
             for x, y, z in raw]
    holes = []
    for lp in loops[1:]:
        h = _ring(defn, lp)
        if h and len(h) >= 3:
            if flipped:
                h = list(reversed(h))
            holes.append([xform.map(p) for p in h])
    def _bake_uvs(entry, uv_matrix, projected=False):
        """Return ``entry`` with the texture's per-face ``uvw`` baked in."""
        if not entry or "texture" not in entry:
            return entry
        if uv_matrix is not None:
            uvs = _positioned_uvs(face, raw, entry["texture"], matrix=uv_matrix,
                                  projected=projected)
        else:
            # SketchUp's DEFAULT mapping runs in the face's LOCAL frame:
            # u = (p·xr)/tile, plane basis from the local normal (the recipe
            # calibrated with the controlled textura.skp). Baking it per face
            # keeps every slat of a component sampling the same patch — a
            # world-space projection would give each slat (and each copy) a
            # different slice of the tile.
            tex = entry["texture"]
            tw = (tex.get("sw", 0.0) or 0.0) / _INCH
            th = (tex.get("sh", 0.0) or 0.0) / _INCH
            uvs = None
            if tw > 0 and th > 0:
                xr, yr = _plane_basis(face.normal or (0.0, 0.0, 1.0))
                uvs = []
                for x, y, z in raw:
                    p = QVector3D(x, y, z)
                    uvs.append((QVector3D.dotProduct(p, xr) / tw,
                                QVector3D.dotProduct(p, yr) / th))
        if uvs is not None:
            uvw = fit_uv_affine(outer, uvs)
            if uvw is not None:
                return {**entry, "texture": {**entry["texture"], "uvw": uvw}}
        return entry

    front_src = attrs
    uv_proj = (getattr(face, "uv_projected_back", False) if flipped
               else getattr(face, "uv_projected", False))
    attrs = _bake_uvs(attrs, uv_mat, uv_proj)
    # A face painted DIFFERENTLY on each side (SketchUp: front green wall,
    # back roof tiles — possibly via instance inheritance on the unpainted
    # side): carry the back side's material as attrs["back"]; the renderer
    # shows it only from behind. Flipped faces already front their painted
    # side, and same-material sides stay plain double-sided.
    if not flipped:
        back_src = attr_map.get(getattr(face, "back_material_id", None))
        if back_src is None and inherited is not None:
            back_src = attr_map.get(inherited)
        if back_src is not None and back_src is not front_src:
            back = _bake_uvs(back_src,
                             getattr(face, "uv_transform_back", None),
                             getattr(face, "uv_projected_back", False))
            base = dict(attrs) if attrs else {}
            base["back"] = back
            attrs = base
    return (outer, holes, attrs)


def _image_has_cutout(path, cache={}) -> bool:
    """Whether the image carries REAL transparency (some pixels see-through)
    — the signature of a photo sprite (a person/animal/tree cutout PNG),
    versus an opaque photo panel (a sign or mural). Mirrors the DAE import's
    heuristic for face-me sprites."""
    cached = cache.get(path)
    if cached is not None:
        return cached
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    img = QImage(path)
    ok = False
    if not img.isNull() and img.hasAlphaChannel():
        small = img.scaled(32, 32, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        for yy in range(small.height()):
            for xx in range(small.width()):
                if small.pixelColor(xx, yy).alpha() < 32:
                    ok = True
                    break
            if ok:
                break
    cache[path] = ok
    return ok


def _image_quad_faces(child, placed, attr_map, inherited):
    """Payload faces for an Image entity's quad, with whole-picture UVs.

    An image quad always shows the whole picture once, so per-vertex UV =
    the vertex's normalised position on the LOCAL quad, baked as an exact
    world→UV affine (the default planar projection would sample in world
    space, after the placement rotation/scale — wrong region entirely)."""
    from core.texture import fit_uv_affine

    raws = [(_ring_raw(child, f.loops[0]) if getattr(f, "loops", None)
             else None, f) for f in child.faces.values()]
    pts = [p for raw, _f in raws if raw for p in raw]
    if not pts:
        return []
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    wspan = (x1 - x0) or 1.0
    hspan = (y1 - y0) or 1.0
    faces = []
    for raw, face in raws:
        if not raw or len(raw) < 3:
            continue
        outer = [placed.map(QVector3D(x * _INCH, y * _INCH, z * _INCH))
                 for x, y, z in raw]
        attrs = _face_attrs(face, attr_map, inherited)
        if attrs and "texture" in attrs:
            uvs = [((x - x0) / wspan, (y - y0) / hspan) for x, y, _z in raw]
            uvw = fit_uv_affine(outer, uvs)
            if uvw is not None:
                attrs = {**attrs, "texture": {**attrs["texture"], "uvw": uvw}}
        faces.append((outer, [], attrs))
    return faces


def _soft_edge_segments(defn, xform, out) -> None:
    """Append the transformed endpoint pairs of ``defn``'s soft/smooth/
    hidden edges to ``out`` — the file's own edge-display flags, used by
    ``apply_payload`` instead of angle-based softening."""
    for e in getattr(defn, "edges", {}).values():
        if not (getattr(e, "soft", False) or getattr(e, "smooth", False)
                or getattr(e, "hidden", False)):
            continue
        va = defn.vertices.get(e.v1_id)
        vb = defn.vertices.get(e.v2_id)
        if va is None or vb is None:
            continue
        pa = xform.map(QVector3D(va.x * _INCH, va.y * _INCH, va.z * _INCH))
        pb = xform.map(QVector3D(vb.x * _INCH, vb.y * _INCH, vb.z * _INCH))
        out.append(((pa.x(), pa.y(), pa.z()), (pb.x(), pb.y(), pb.z())))


def _collect(defn, xform, by_id, attr_map, out, depth, stack,
             proto_ids=frozenset(), proto_uses=None, inherited=None,
             image_uses=None, edges_out=None) -> None:
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
        entry = _face_entry(defn, face, xform, attr_map, inherited)
        if entry is not None:
            out.append(entry)
    if edges_out is not None:
        _soft_edge_segments(defn, xform, edges_out)
    for ins in getattr(defn, "instances", []):
        rid = getattr(ins, "ref_idx", None)
        child = by_id.get(rid)
        if child is None:
            continue
        placed = xform * _matrix(ins.matrix)
        child_inherited = getattr(ins, "material_id", None) or inherited
        cid = getattr(child, "id", None)
        if getattr(child, "is_image", False):
            if image_uses is not None:
                # An Image entity (photo placed as an object): pulled out of
                # its parent so it can become its own group — cutout images
                # turn to face the camera (billboard), like the DAE import.
                image_uses.append((child, placed, child_inherited))
            else:
                # Inside a face-me component being flattened: the image
                # stays part of it, with its whole-picture UVs.
                out.extend(_image_quad_faces(child, placed, attr_map,
                                             child_inherited))
            continue
        if image_uses is not None and \
                getattr(child, "always_faces_camera", False):
            # SketchUp's "always face camera" component (2D people like
            # Susan): extracted as its own billboard group.
            image_uses.append((child, placed, child_inherited))
            continue
        if proto_uses is not None and cid in proto_ids:
            proto_uses.setdefault((cid, child_inherited), []).append(placed)
            continue
        _collect(child, placed, by_id, attr_map, out, depth + 1, stack,
                 proto_ids, proto_uses, child_inherited, image_uses,
                 edges_out)


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
    def _subtree_has_faceme(d, stack=frozenset()):
        if getattr(d, "is_image", False) or \
                getattr(d, "always_faces_camera", False):
            return True
        if id(d) in stack:
            return False
        for ins in getattr(d, "instances", []):
            c = by_id.get(getattr(ins, "ref_idx", None))
            if c is not None and _subtree_has_faceme(c, stack | {id(d)}):
                return True
        return False

    proto_ids = set()
    for did, cnt in uses.items():
        d = by_id.get(did)
        if d is None or cnt < 2:
            continue
        # Face-me-carrying subtrees (images, face-camera components) are
        # excluded from sharing: each copy's billboard needs its own world
        # spot.
        if _subtree_has_faceme(d):
            continue
        polys = _subtree_polys(d, by_id, memo, set())
        if polys >= _INST_MIN_POLYS and polys * (cnt - 1) >= _INST_MIN_SAVED:
            proto_ids.add(did)

    groups: list = []
    proto_uses: dict = {}
    image_uses: list = []
    for r in roots:
        # The root's own loose faces (no instance recursion).
        loose: list = []
        ident = QMatrix4x4()
        for face in r.faces.values():
            entry = _face_entry(r, face, ident, attr_map)
            if entry is not None:
                loose.append(entry)
        if loose:
            loose_edges: list = []
            _soft_edge_segments(r, ident, loose_edges)
            groups.append({"name": name, "faces": loose,
                           "soft_edges": loose_edges})
        # Each top-level instance → its own group (or a shared-proto use).
        for ins in getattr(r, "instances", []):
            child = by_id.get(getattr(ins, "ref_idx", None))
            if child is None:
                continue
            placed = _matrix(ins.matrix)
            inh = getattr(ins, "material_id", None)
            if getattr(child, "is_image", False) or \
                    getattr(child, "always_faces_camera", False):
                image_uses.append((child, placed, inh))
                continue
            if getattr(child, "id", None) in proto_ids:
                proto_uses.setdefault((child.id, inh), []).append(placed)
                continue
            sub: list = []
            sub_edges: list = []
            _collect(child, placed, by_id, attr_map, sub, 0, set(),
                     proto_ids, proto_uses, inh, image_uses, sub_edges)
            if sub:
                groups.append({"name": getattr(child, "name", None) or name,
                               "faces": sub, "soft_edges": sub_edges})

    # Image entities → their own groups; cutout images (real alpha) become
    # face-me billboards that turn toward the camera, opaque photos stay
    # static panels — same rule as the DAE face-me import. An image quad
    # always shows the WHOLE picture once (see _image_quad_faces). An
    # "always faces camera" component (Susan) flattens its whole subtree
    # into one billboard group — the flag decides, no heuristic needed.
    for child, placed, inh in image_uses:
        if getattr(child, "is_image", False):
            faces = _image_quad_faces(child, placed, attr_map, inh)
            tex = next((a["texture"]["path"] for _o, _h, a in faces
                        if a and "texture" in a), None)
            billboard = bool(tex and _image_has_cutout(tex))
        else:
            faces = []
            _collect(child, placed, by_id, attr_map, faces, 0, set(),
                     inherited=inh)
            billboard = True
        if not faces:
            continue
        groups.append({"name": getattr(child, "name", None) or name,
                       "faces": faces, "billboard": billboard})

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
        local_edges: list = []
        _collect(d, QMatrix4x4(), by_id, attr_map, local, 0, set(),
                 inherited=inh, edges_out=local_edges)
        if local:
            protos.append({"name": getattr(d, "name", None) or name,
                           "faces": local, "instances": xforms,
                           "soft_edges": local_edges})

    if not groups and not protos:
        return None
    payload = {"backend": "openskp", "groups": groups, "protos": protos}
    # The file's style back-face colour (our upstream PR openskp#10): adopt it
    # so unpainted faces seen from behind read like they did for the author
    # (instead of IngeTrazo's own blue-grey default).
    for st in getattr(model, "styles", []) or []:
        back = getattr(st, "back_color", None)
        if back:
            payload["back_color"] = tuple(c / 255.0 for c in back)
            break
    return payload


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
