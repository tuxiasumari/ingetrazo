# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""SKP import via the external skp2dae converter (separate process — the
proprietary SketchUp DLL never touches IngeTrazo). A fake converter script
stands in for the real one: detection honours SKP2DAE_EXE, the conversion
lands next to the .skp, and the resulting DAE flows into the scene."""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

if sys.platform == "win32":
    pytest.skip("fake converter is a POSIX shell script", allow_module_level=True)

_inst = QApplication.instance()
if _inst is None:
    _app = QApplication([])
elif not isinstance(_inst, QApplication):
    pytest.skip("a non-widget QGuiApplication is already active",
                allow_module_level=True)

from views.main_window import MainWindow  # noqa: E402

_MINI_DAE = """<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <asset><unit name="inch" meter="0.0254"/><up_axis>Z_UP</up_axis></asset>
  <library_geometries>
    <geometry id="g0"><mesh>
      <source id="g0_pos"><float_array id="g0_pos_a" count="9">0 0 0 10 0 0 10 10 0</float_array>
        <technique_common><accessor source="#g0_pos_a" count="3" stride="3">
        <param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>
        </accessor></technique_common></source>
      <vertices id="g0_v"><input semantic="POSITION" source="#g0_pos"/></vertices>
      <triangles count="1"><input semantic="VERTEX" source="#g0_v" offset="0"/>
      <p>0 1 2</p></triangles>
    </mesh></geometry>
  </library_geometries>
  <library_visual_scenes><visual_scene id="scene">
    <node id="root"><instance_geometry url="#g0"/></node>
  </visual_scene></library_visual_scenes>
  <scene><instance_visual_scene url="#scene"/></scene>
</COLLADA>
"""


@pytest.fixture()
def fake_converter(tmp_path, monkeypatch):
    fixture = tmp_path / "fixture.dae"
    fixture.write_text(_MINI_DAE)
    script = tmp_path / "skp2dae"
    script.write_text(f"#!/bin/bash\ncp '{fixture}' \"$2\"\n")
    script.chmod(0o755)
    monkeypatch.setenv("SKP2DAE_EXE", str(script))
    return script


def test_find_converter_honours_env(fake_converter):
    cmd = MainWindow._find_skp_converter()
    assert cmd == [str(fake_converter)]


def test_find_converter_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SKP2DAE_EXE", str(tmp_path / "nope"))
    monkeypatch.setenv("PATH", str(tmp_path))          # sin skp2dae en PATH
    monkeypatch.setenv("HOME", str(tmp_path))          # sin instalacion local
    assert MainWindow._find_skp_converter() is None


def test_skp_import_end_to_end(fake_converter, tmp_path):
    window = MainWindow()
    window.viewport.scene.clear()
    skp = tmp_path / "modelo.skp"
    skp.write_bytes(b"not really a skp; the fake converter ignores it")
    assert window.import_skp_path(skp)
    # el .dae quedo JUNTO al .skp (texturas relativas siguen validas)
    assert (tmp_path / "modelo.dae").exists()
    scene = window.viewport.scene
    total = len(scene.mesh.faces) + sum(
        len(g.mesh.faces) for g in scene.groups)
    assert total == 1                                  # el triangulo llego


def test_extract_skp_dlls_from_addon_zip(tmp_path):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("sketchup_importer/SketchUpAPI.dll", b"fake-api")
        zf.writestr("sketchup_importer/SketchUpCommonPreferences.dll", b"fake-prefs")
        zf.writestr("sketchup_importer/otro.pyd", b"x")
    got = MainWindow._extract_skp_dlls(buf.getvalue(), tmp_path)
    assert sorted(got) == ["SketchUpAPI.dll", "SketchUpCommonPreferences.dll"]
    assert (tmp_path / "SketchUpAPI.dll").read_bytes() == b"fake-api"
    assert not (tmp_path / "otro.pyd").exists()
