# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""SKP import seam — pluggable parser backends with a skp2dae fallback.

IngeTrazo aims to open **any** ``.skp`` (old → recent). This module is the
single seam between IngeTrazo and *how* an ``.skp`` is read, so the parser can
evolve independently of the app:

  1. **A pure-Python backend** — OpenSKP (https://github.com/iamahsanmehmood/
     openskp) or a maintained fork. Offline, Linux-native, no Wine, no
     proprietary DLL. Preferred; its version coverage grows over time.
  2. **The skp2dae converter** (Trimble's ``SketchUpAPI.dll`` via Wine). The
     full-coverage fallback for files the pure backend can't read. It is a
     SEPARATE program (the proprietary DLL never enters GPL IngeTrazo), so its
     dialog/subprocess flow lives in ``views.main_window``, not here.

**Parse then apply.** A backend *parses* a file into a plain **payload** (world-
space face loops, no ``Scene`` touched). The heavy parse runs outside the undo
history; :func:`apply_payload` then adds the geometry cheaply inside a command.
When no pure backend can produce geometry, :func:`parse_skp` raises
:class:`NeedsConverter` before any mutation, and the UI runs skp2dae — so a
failed pure parse never leaves a half-applied edit.

Nothing here imports a parser at module load — a missing OpenSKP is just an
unavailable backend.
"""
from __future__ import annotations

from pathlib import Path


class NeedsConverter(Exception):
    """No pure backend can read this ``.skp`` — the caller should fall back to
    the external skp2dae converter. Carries the path and detected format."""

    def __init__(self, path, fmt: str) -> None:
        super().__init__(f"No pure SKP backend for {path} (format={fmt})")
        self.path = Path(path)
        self.format = fmt


def detect_format(path) -> str:
    """Best-effort container detection from the file's first bytes, no parser
    involved:

    * ``"skp"``     — a SketchUp document (UTF-16 ``SketchUp Model`` marker, or
      a ``PK`` ZIP-wrapped container). Covers legacy MFC and 2021+ files alike —
      both begin with the same marker, so the *era* is not observable from the
      magic bytes (OpenSKP handles the range, so we don't need to tell them
      apart here).
    * ``"unknown"`` — not recognisably a SketchUp file / unreadable.
    """
    try:
        head = Path(path).read_bytes()[:64]
    except OSError:
        return "unknown"
    if head[:4] == b"PK\x03\x04":
        return "skp"
    if b"S\x00k\x00e\x00t\x00c\x00h" in head:   # UTF-16LE "Sketch"
        return "skp"
    return "unknown"


class _OpenSkpBackend:
    """Pure-Python OpenSKP backend. Parses via :mod:`formats.skp_openskp`, which
    imports ``openskp`` lazily. ``available()`` is True only when the package is
    importable; ``supports`` covers any recognised SketchUp file (OpenSKP reads
    a broad version range). A parse that yields no geometry returns ``None`` from
    :meth:`parse`, so the seam falls back to the converter."""

    name = "openskp"

    def available(self) -> bool:
        try:
            import openskp  # noqa: F401
        except Exception:  # noqa: BLE001 — optional dependency
            return False
        return True

    def supports(self, fmt: str) -> bool:
        return fmt == "skp"

    def parse(self, path, progress=None):
        from formats import skp_openskp
        return skp_openskp.parse(path, progress=progress)


#: Ordered list of pure-Python backends. Extend/replace as coverage grows.
_BACKENDS: list = [_OpenSkpBackend()]


def backends_status() -> list[tuple[str, bool]]:
    """``(name, available)`` for each pure backend — for diagnostics / an
    About-style report of what can be opened without the converter."""
    return [(b.name, b.available()) for b in _BACKENDS]


def can_handle(path) -> bool:
    """True when a pure backend is available and recognises ``path``. Note this
    does NOT guarantee a non-empty parse — the actual geometry check happens in
    :func:`parse_skp`, which raises :class:`NeedsConverter` on an empty result."""
    fmt = detect_format(path)
    return any(b.available() and b.supports(fmt) for b in _BACKENDS)


def parse_skp(path, progress=None) -> dict:
    """Parse ``path`` with the first pure backend that produces geometry, and
    return its payload. Raises :class:`NeedsConverter` when no pure backend can
    read the file (unrecognised format, parser error, or an empty parse) — the
    caller then runs the external skp2dae converter. Touches no ``Scene``."""
    path = Path(path)
    fmt = detect_format(path)
    for backend in _BACKENDS:
        if not (backend.available() and backend.supports(fmt)):
            continue
        try:
            payload = backend.parse(path, progress=progress)
        except Exception:  # noqa: BLE001 — a parser that chokes → try next / fall back
            payload = None
        if payload and payload.get("groups"):
            return payload
    raise NeedsConverter(path, fmt)


def apply_payload(scene, payload) -> str:
    """Add a parsed payload's geometry to ``scene`` as reference groups (an
    isolated ``Mesh`` per group, like the big-DAE import). Runs the same
    clean-up the DAE reference import does — coplanar fusion (merges the raw
    SketchUp polygons and drops double-sided duplicates) and smooth-edge
    softening — so a ``.skp`` opened through the pure backend looks identical
    to one that came through the converter. Cheap relative to the parse; the
    caller wraps it in a command for undo. Returns the backend name."""
    from core.group import Group
    from core.mesh import Mesh
    from formats.dae import _add_fused
    from formats.fuse import fuse_coplanar_loops, soften_smooth_edges

    def _build_mesh(faces, soft_edges=None):
        mesh = Mesh()
        if soft_edges is not None:
            # The backend carried SketchUp's ORIGINAL polygons plus the
            # file's own per-edge display flags — add everything as-is and
            # soften exactly the flagged edges. No coplanar fusion: SketchUp
            # keeps coplanar same-material faces separate with their edges
            # visible (glass mullions, beam/column lines), so fusing them
            # dissolved real user lines.
            for outer, holes, attrs in faces:
                try:
                    face = mesh.add_face(outer, holes or None)
                except Exception:  # noqa: BLE001 — skip a degenerate polygon
                    continue
                if attrs:
                    face.attrs.update(attrs)
            if soft_edges:
                def _k(p):
                    return (round(p[0], 5), round(p[1], 5), round(p[2], 5))
                wanted = {frozenset((_k(a), _k(b))) for a, b in soft_edges}
                for e in mesh.edges:
                    a = e.v0.position
                    b = e.v1.position
                    if frozenset((_k((a.x(), a.y(), a.z())),
                                  _k((b.x(), b.y(), b.z())))) in wanted:
                        e.soft = True
            return mesh
        # Legacy payloads without edge flags: the DAE-style clean-up.
        # Fusion works on plain loops; faces with holes (window rings) keep
        # their explicit topology and are added directly.
        raw = [(outer, attrs) for outer, holes, attrs in faces if not holes]
        for item in fuse_coplanar_loops(raw):
            _add_fused(mesh, [item])
        for outer, holes, attrs in faces:
            if not holes:
                continue
            try:
                face = mesh.add_face(outer, holes)
            except Exception:  # noqa: BLE001 — skip a degenerate polygon
                continue
            if attrs:
                face.attrs.update(attrs)
        soften_smooth_edges(mesh)
        return mesh

    for gp in payload.get("groups", []):
        mesh = _build_mesh(gp["faces"], gp.get("soft_edges"))
        if mesh.faces:
            g = Group(mesh, name=gp.get("name"))
            if gp.get("billboard"):
                # Image-entity cutout (photo person/animal/tree): the real
                # geometry turns toward the camera each frame, like the DAE
                # face-me import.
                g.billboard = "mesh"
            scene.groups.append(g)
    # Shared components: ONE prototype mesh (local coordinates), one Group per
    # placement with only a local->world matrix (Components v1 instances).
    for pr in payload.get("protos", []):
        mesh = _build_mesh(pr["faces"], pr.get("soft_edges"))
        if not mesh.faces:
            continue
        for xf in pr.get("instances", []):
            g = Group(mesh, name=pr.get("name"))
            g.xform = xf
            scene.groups.append(g)
    back = payload.get("back_color")
    if back and getattr(scene, "back_face_color", None) is None:
        # Adopt the file's style back-face colour so unpainted faces seen
        # from behind read like they did for the author.
        scene.back_face_color = tuple(back)
    scene.version += 1
    return payload.get("backend", "?")


def load_skp(scene, path, progress=None) -> str:
    """Convenience: :func:`parse_skp` then :func:`apply_payload` into ``scene``.
    Returns the backend name; raises :class:`NeedsConverter` if none applies.
    (The UI splits these two so the heavy parse runs outside the undo history.)"""
    return apply_payload(scene, parse_skp(path, progress=progress))
