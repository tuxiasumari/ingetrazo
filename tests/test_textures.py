# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""SketchUp-compatible textures: planar UV projection, the SetFaceTexture
command, OBJ export with vt + map_Kd, and .igz round-trip."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage, QColor, QVector3D

from core.history import History, SetFaceColorCommand, SetFaceTextureCommand
from core.scene import Scene
from core.texture import Texture, planar_uv
from formats import igz
from formats import obj as obj_format
import tests.test_fuzz_engine as F


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


def _cube(scene, hist, height=3.0):
    F._draw_rect(scene, hist, [V(0, 0), V(4, 0), V(4, 4), V(0, 4)], [])
    f = scene.mesh.faces[0]
    F._push(scene, hist, f, height if f.normal().z() > 0 else -height)


def _checker(path, n=16):
    img = QImage(n, n, QImage.Format_RGB888)
    for y in range(n):
        for x in range(n):
            img.setPixelColor(x, y, QColor(200, 120, 60) if (x + y) % 2
                              else QColor(245, 235, 220))
    img.save(str(path))


# ---- Planar UV projection ------------------------------------------------------

def test_planar_uv_scales_by_tile_size():
    # Top face (normal +Z): UVs are the X/Y world coords divided by the tile.
    pts = [V(0, 0, 3), V(4, 0, 3), V(4, 4, 3), V(0, 4, 3)]
    uv1 = planar_uv(V(0, 0, 1), pts, 1.0, 1.0)
    uv2 = planar_uv(V(0, 0, 1), pts, 2.0, 2.0)
    # A 2 m tile halves the UV span (4 m → 2 repeats instead of 4).
    span1 = max(u for u, _ in uv1) - min(u for u, _ in uv1)
    span2 = max(u for u, _ in uv2) - min(u for u, _ in uv2)
    assert abs(span1 - 4.0) < 1e-6
    assert abs(span2 - 2.0) < 1e-6


def test_coplanar_faces_share_projection():
    # Two faces on the same plane project continuously (same basis → seamless).
    a = planar_uv(V(0, 0, 1), [V(0, 0, 0)], 1.0, 1.0)[0]
    b = planar_uv(V(0, 0, 1), [V(4, 0, 0)], 1.0, 1.0)[0]
    assert a == (0.0, 0.0)
    assert b == (4.0, 0.0)


def test_texture_dataclass_round_trip():
    t = Texture("/x/brick.png", 0.5, 0.25)
    assert Texture.from_dict(t.as_dict()) == t


# ---- Command -------------------------------------------------------------------

def test_set_face_texture_command_do_undo():
    scene = Scene()
    hist = History(scene)
    _cube(scene, hist)
    face = scene.mesh.faces[0]
    tex = {"path": "/x/brick.png", "sw": 1.0, "sh": 1.0}
    hist.execute(SetFaceTextureCommand([face], tex))
    assert face.attrs["texture"] == tex
    hist.undo()
    assert "texture" not in face.attrs
    hist.redo()
    assert face.attrs["texture"] == tex


# ---- OBJ export ----------------------------------------------------------------

def test_obj_export_writes_texture_material_and_uvs(tmp_path):
    scene = Scene()
    hist = History(scene)
    _cube(scene, hist)
    tex_src = tmp_path / "checker.png"
    _checker(tex_src)
    top = next(f for f in scene.mesh.faces
               if all(abs(v.z() - 3) < 1e-9 for v in f.vertices))
    hist.execute(SetFaceTextureCommand(
        [top], {"path": str(tex_src), "sw": 1.0, "sh": 1.0}))

    out = tmp_path / "out.obj"
    obj_format.save_obj(scene, out)

    obj_text = out.read_text()
    mtl_text = (out.with_suffix(".mtl")).read_text()
    assert "vt " in obj_text                       # texture coords written
    assert "map_Kd checker.png" in mtl_text        # texture material
    assert (tmp_path / "checker.png").exists()     # image copied next to .obj
    # The textured face references v/vt; the plain faces reference v only.
    assert any("/" in tok for line in obj_text.splitlines()
               if line.startswith("f ") for tok in line.split()[1:])


# ---- .igz round-trip -----------------------------------------------------------

def test_texture_survives_igz_round_trip(tmp_path):
    scene = Scene()
    hist = History(scene)
    _cube(scene, hist)
    face = scene.mesh.faces[0]
    tex = {"path": "/x/brick.png", "sw": 0.5, "sh": 0.25}
    hist.execute(SetFaceTextureCommand([face], tex))
    path = tmp_path / "tex.igz"
    igz.save_scene(scene, path)

    loaded = Scene()
    igz.load_into(loaded, path)
    painted = [f for f in loaded.mesh.faces if f.attrs.get("texture")]
    assert len(painted) == 1
    assert painted[0].attrs["texture"] == tex


def test_textured_obj_round_trips_the_texture(tmp_path):
    scene = Scene()
    hist = History(scene)
    _cube(scene, hist)
    tex_src = tmp_path / "checker.png"
    _checker(tex_src)
    top = next(f for f in scene.mesh.faces
               if all(abs(v.z() - 3) < 1e-9 for v in f.vertices))
    hist.execute(SetFaceTextureCommand(
        [top], {"path": str(tex_src), "sw": 1.0, "sh": 1.0}))
    out = tmp_path / "out.obj"
    obj_format.save_obj(scene, out)

    loaded = Scene()
    obj_format.load_obj(loaded, out)
    textured = [f for f in loaded.mesh.faces if f.attrs.get("texture")]
    assert len(textured) == 1
    assert Path(textured[0].attrs["texture"]["path"]).name == "checker.png"


def test_planar_uv_rotation_and_scale():
    # Bigger tile size = fewer repeats; rotation turns the UV frame in-plane
    # (SketchUp's edit-material W/H/Rot).
    from PySide6.QtGui import QVector3D

    from core.texture import planar_uv

    n = QVector3D(0, 0, 1)
    pts = [QVector3D(1, 0, 0), QVector3D(0, 1, 0)]
    # Doubling the tile halves the UV.
    (u1, _), _ = planar_uv(n, pts, 1.0, 1.0)
    (u2, _), _ = planar_uv(n, pts, 2.0, 2.0)
    assert abs(u2 - u1 / 2.0) < 1e-9
    # 90° rotation maps the +X point onto the (former) V axis.
    (u90, v90), (u90b, v90b) = planar_uv(n, pts, 1.0, 1.0, rot=90.0)
    (u0, v0), (u0b, v0b) = planar_uv(n, pts, 1.0, 1.0)
    assert abs(abs(v90) - abs(u0)) < 1e-9         # swapped axes
    assert abs(abs(u90b) - abs(v0b)) < 1e-6      # (0,1) lands on former U
    # rotation preserves scale (rigid in-plane turn)
    import math
    assert abs(math.hypot(u90, v90) - math.hypot(u0, v0)) < 1e-9


def test_texture_rotation_round_trips_igz(tmp_path):
    from PySide6.QtGui import QVector3D

    from core.scene import Scene
    from formats import igz

    scene = Scene()
    f = scene.mesh.add_face([QVector3D(0, 0, 0), QVector3D(2, 0, 0),
                             QVector3D(2, 2, 0), QVector3D(0, 2, 0)])
    f.attrs["texture"] = {"path": "x.png", "sw": 1.5, "sh": 0.75, "rot": 45.0}
    p = tmp_path / "rot.igz"
    igz.save_scene(scene, p)
    scene2 = Scene()
    igz.load_into(scene2, p)
    tex = scene2.mesh.faces[0].attrs["texture"]
    assert tex["rot"] == 45.0 and tex["sw"] == 1.5
