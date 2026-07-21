# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Differential validation harness (``scripts/skp_diff.py``): the fingerprint of
a Scene and the compare() diff, exercised on synthetic scenes (no .skp / Wine)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from PySide6.QtGui import QVector3D

from core.history import History
from core.scene import Scene
import tests.test_fuzz_engine as F

_SPEC = importlib.util.spec_from_file_location(
    "skp_diff", Path(__file__).resolve().parents[1] / "scripts" / "skp_diff.py")
skp_diff = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(skp_diff)


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


def _cube(scene, hist, size=4.0, height=3.0):
    loop = [V(0, 0), V(size, 0), V(size, size), V(0, size)]
    F._draw_rect(scene, hist, [QVector3D(p) for p in loop], [])
    f = scene.mesh.faces[0]
    F._push(scene, hist, f, height if f.normal().z() > 0 else -height)


def test_fingerprint_of_a_cube():
    scene = Scene()
    hist = History(scene)
    _cube(scene, hist)
    fp = skp_diff.fingerprint(scene)
    assert fp["faces"] == 6
    assert fp["triangles"] == 12
    assert fp["vertices"] == 8
    assert fp["bbox"]["size"] == [4.0, 4.0, 3.0]


def test_identical_scenes_have_no_diff():
    a, b = Scene(), Scene()
    _cube(a, History(a))
    _cube(b, History(b))
    issues = skp_diff.compare(skp_diff.fingerprint(a), skp_diff.fingerprint(b))
    assert issues == []


def test_compare_flags_geometry_and_bbox_differences():
    a, b = Scene(), Scene()
    _cube(a, History(a), height=3.0)
    _cube(b, History(b), height=5.0)   # taller box
    issues = skp_diff.compare(skp_diff.fingerprint(a), skp_diff.fingerprint(b))
    joined = "\n".join(issues)
    assert "bbox" in joined                 # height differs
    assert issues, "expected discrepancies for different geometry"


def test_load_candidate_returns_none_without_backend(tmp_path):
    # No pure backend is wired, so a .skp yields no candidate (the harness then
    # validates skp2dae output only) — never crashes.
    p = tmp_path / "m.skp"
    p.write_bytes(b"\x01\x02legacy")
    assert skp_diff.load_candidate(p) is None
