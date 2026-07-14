# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""COLLADA (.dae) import — SketchUp-style documents."""
from __future__ import annotations

from core.orient import is_closed, signed_volume
from core.scene import Scene
from formats.dae import load_dae

_NSDECL = 'xmlns="http://www.collada.org/2005/11/COLLADASchema"'


def _cube_dae(tmp_path, up="Z_UP", meter="1.0"):
    """A unit cube as triangles, one red material, one instanced node."""
    # 8 corners of the unit cube (Z_UP coordinates).
    pos = ("0 0 0  1 0 0  1 1 0  0 1 0  "
           "0 0 1  1 0 1  1 1 1  0 1 1")
    tris = ("0 3 2 0 2 1  4 5 6 4 6 7  0 1 5 0 5 4  "
            "1 2 6 1 6 5  2 3 7 2 7 6  3 0 4 3 4 7")
    text = f"""<?xml version="1.0"?>
<COLLADA {_NSDECL} version="1.4.1">
  <asset><unit meter="{meter}"/><up_axis>{up}</up_axis></asset>
  <library_effects>
    <effect id="fx-red"><profile_COMMON><technique sid="t">
      <lambert><diffuse><color>1 0 0 1</color></diffuse></lambert>
    </technique></profile_COMMON></effect>
  </library_effects>
  <library_materials>
    <material id="mat-red"><instance_effect url="#fx-red"/></material>
  </library_materials>
  <library_geometries>
    <geometry id="cube"><mesh>
      <source id="cube-pos">
        <float_array id="cube-pos-arr" count="24">{pos}</float_array>
        <technique_common>
          <accessor source="#cube-pos-arr" count="8" stride="3"/>
        </technique_common>
      </source>
      <vertices id="cube-v">
        <input semantic="POSITION" source="#cube-pos"/>
      </vertices>
      <triangles count="12" material="RED">
        <input semantic="VERTEX" source="#cube-v" offset="0"/>
        <p>{tris}</p>
      </triangles>
    </mesh></geometry>
  </library_geometries>
  <library_visual_scenes>
    <visual_scene id="scene">
      <node id="n1">
        <instance_geometry url="#cube">
          <bind_material><technique_common>
            <instance_material symbol="RED" target="#mat-red"/>
          </technique_common></bind_material>
        </instance_geometry>
      </node>
    </visual_scene>
  </library_visual_scenes>
</COLLADA>
"""
    p = tmp_path / "cube.dae"
    p.write_text(text)
    return p


def test_triangulated_cube_comes_back_as_six_quads(tmp_path):
    scene = Scene()
    load_dae(scene, _cube_dae(tmp_path))
    m = scene.mesh
    assert len(m.faces) == 6                       # coplanar-merged
    assert all(len(f.loop) == 4 for f in m.faces)
    assert is_closed(m)
    assert abs(signed_volume(m) - 1.0) < 1e-6
    # the bound material's diffuse colour landed on the faces
    assert all(f.attrs.get("color") == [1.0, 0.0, 0.0] for f in m.faces)


def test_y_up_and_inches_convert_to_zup_metres(tmp_path):
    scene = Scene()
    load_dae(scene, _cube_dae(tmp_path, up="Y_UP", meter="0.0254"))
    m = scene.mesh
    assert is_closed(m)
    assert abs(signed_volume(m) - 0.0254 ** 3) < 1e-9
    zs = sorted({round(v.position.z(), 6) for v in m.vertices})
    assert zs == [0.0, 0.0254]                     # COLLADA Y became world Z


def test_instanced_component_with_transform(tmp_path):
    # SketchUp components: geometry lives in library_nodes, the scene
    # instances it with a transform.
    body = f"""<?xml version="1.0"?>
<COLLADA {_NSDECL} version="1.4.1">
  <asset><up_axis>Z_UP</up_axis></asset>
  <library_geometries>
    <geometry id="tri"><mesh>
      <source id="p"><float_array id="pa" count="9">0 0 0  1 0 0  0 1 0</float_array>
        <technique_common><accessor source="#pa" count="3" stride="3"/></technique_common>
      </source>
      <vertices id="v"><input semantic="POSITION" source="#p"/></vertices>
      <polylist count="1">
        <input semantic="VERTEX" source="#v" offset="0"/>
        <vcount>3</vcount><p>0 1 2</p>
      </polylist>
    </mesh></geometry>
  </library_geometries>
  <library_nodes>
    <node id="comp"><instance_geometry url="#tri"/></node>
  </library_nodes>
  <library_visual_scenes>
    <visual_scene id="scene">
      <node id="a"><instance_node url="#comp"/></node>
      <node id="b"><translate>10 0 0</translate><instance_node url="#comp"/></node>
    </visual_scene>
  </library_visual_scenes>
</COLLADA>
"""
    p = tmp_path / "comp.dae"
    p.write_text(body)
    scene = Scene()
    load_dae(scene, p)
    xs = sorted(round(v.position.x(), 3) for v in scene.mesh.vertices)
    assert len(scene.mesh.faces) == 2              # both instances imported
    assert 10.0 in xs and 11.0 in xs               # the translate applied


def test_big_import_mirrors_sketchup_groups(tmp_path, monkeypatch):
    """A reference-size DAE splits into one Group per SketchUp assembly
    (group / component instance / the root's own loose geometry) instead of
    one monolithic blob — so a farola is selectable/movable on its own and
    the loose editing mesh never swallows the model."""
    body = f"""<?xml version="1.0"?>
<COLLADA {_NSDECL} version="1.4.1">
  <asset><up_axis>Z_UP</up_axis></asset>
  <library_geometries>
    <geometry id="tri"><mesh>
      <source id="p"><float_array id="pa" count="9">0 0 0  1 0 0  0 1 0</float_array>
        <technique_common><accessor source="#pa" count="3" stride="3"/></technique_common>
      </source>
      <vertices id="v"><input semantic="POSITION" source="#p"/></vertices>
      <polylist count="1">
        <input semantic="VERTEX" source="#v" offset="0"/>
        <vcount>3</vcount><p>0 1 2</p>
      </polylist>
    </mesh></geometry>
    <geometry id="quad"><mesh>
      <source id="qp"><float_array id="qpa" count="12">0 0 0  1 0 0  1 1 0  0 1 0</float_array>
        <technique_common><accessor source="#qpa" count="4" stride="3"/></technique_common>
      </source>
      <vertices id="qv"><input semantic="POSITION" source="#qp"/></vertices>
      <polylist count="2">
        <input semantic="VERTEX" source="#qv" offset="0"/>
        <vcount>3 3</vcount><p>0 1 2 0 2 3</p>
      </polylist>
    </mesh></geometry>
  </library_geometries>
  <library_nodes>
    <node id="comp"><instance_geometry url="#tri"/></node>
  </library_nodes>
  <library_visual_scenes>
    <visual_scene id="scene">
      <node id="root" name="SketchUp">
        <instance_geometry url="#tri"/>
        <node id="f1" name="farola"><translate>10 0 0</translate>
          <instance_node url="#comp"/></node>
        <node id="g1" name="pergola"><instance_geometry url="#quad"/></node>
      </node>
    </visual_scene>
  </library_visual_scenes>
</COLLADA>
"""
    p = tmp_path / "proyecto.dae"
    p.write_text(body)
    import formats.dae as dae_mod
    monkeypatch.setattr(dae_mod, "_MAX_FUSE_LOOPS", 2)
    monkeypatch.setattr(dae_mod, "_SPLIT_MIN", 1)
    scene = Scene()
    load_dae(scene, p)
    assert not scene.mesh.faces                     # nothing lands loose
    names = sorted(g.name for g in scene.groups)
    assert names == ["SketchUp", "farola", "pergola"]
    by_name = {g.name: g for g in scene.groups}
    assert len(by_name["pergola"].mesh.faces) == 1  # quad fused back
    # the farola instance carries its baked transform
    xs = [v.position.x() for v in by_name["farola"].mesh.vertices]
    assert min(xs) >= 10.0


def test_empty_document_raises(tmp_path):
    p = tmp_path / "empty.dae"
    p.write_text(f'<?xml version="1.0"?><COLLADA {_NSDECL} version="1.4.1"/>')
    scene = Scene()
    try:
        load_dae(scene, p)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def _textured_quad_dae(tmp_path, image_name="brick.png"):
    """A quad textured with a real image file, split into two triangles with
    explicit TEXCOORDs (offsets 0=vertex, 1=uv) — the SketchUp export shape."""
    import shutil
    img = tmp_path / image_name
    shutil.copy("/home/sumaritux/ingetrazo/resources/textures/brick.png", img)
    body = f"""<?xml version="1.0"?>
<COLLADA {_NSDECL} version="1.4.1">
  <asset><up_axis>Z_UP</up_axis></asset>
  <library_images>
    <image id="img1"><init_from>{image_name}</init_from></image>
  </library_images>
  <library_effects>
    <effect id="fx1"><profile_COMMON>
      <newparam sid="surf"><surface type="2D"><init_from>img1</init_from></surface></newparam>
      <newparam sid="samp"><sampler2D><source>surf</source></sampler2D></newparam>
      <technique sid="t"><lambert><diffuse>
        <texture texture="samp" texcoord="UV0"/>
      </diffuse></lambert></technique>
    </profile_COMMON></effect>
  </library_effects>
  <library_materials>
    <material id="mat1"><instance_effect url="#fx1"/></material>
  </library_materials>
  <library_geometries>
    <geometry id="quad"><mesh>
      <source id="qp"><float_array id="qpa" count="12">0 0 0  2 0 0  2 3 0  0 3 0</float_array>
        <technique_common><accessor source="#qpa" count="4" stride="3"/></technique_common>
      </source>
      <source id="quv"><float_array id="quva" count="8">0 0  1 0  1 1  0 1</float_array>
        <technique_common><accessor source="#quva" count="4" stride="2"/></technique_common>
      </source>
      <vertices id="qv"><input semantic="POSITION" source="#qp"/></vertices>
      <triangles count="2" material="M">
        <input semantic="VERTEX" source="#qv" offset="0"/>
        <input semantic="TEXCOORD" source="#quv" offset="1" set="0"/>
        <p>0 0 1 1 2 2  0 0 2 2 3 3</p>
      </triangles>
    </mesh></geometry>
  </library_geometries>
  <library_visual_scenes>
    <visual_scene id="scene">
      <node id="n1">
        <instance_geometry url="#quad">
          <bind_material><technique_common>
            <instance_material symbol="M" target="#mat1"/>
          </technique_common></bind_material>
        </instance_geometry>
      </node>
    </visual_scene>
  </library_visual_scenes>
</COLLADA>
"""
    p = tmp_path / "textured.dae"
    p.write_text(body)
    return p, str(img)


def test_textured_import_carries_real_uvs(tmp_path):
    """A DAE face whose material has an on-disk image imports TEXTURED: the
    file's own UVs are kept as a world→UV affine map, the two export
    triangles fuse back into one quad, and evaluating the map at the corners
    reproduces the original texture coordinates."""
    from PySide6.QtGui import QVector3D
    from core.texture import affine_uv

    def V(x, y, z=0.0):
        return QVector3D(float(x), float(y), float(z))

    p, img = _textured_quad_dae(tmp_path)
    scene = Scene()
    load_dae(scene, p)
    faces = scene.mesh.faces
    assert len(faces) == 1                          # triangles fused
    tex = faces[0].attrs.get("texture")
    assert tex is not None and tex["path"] == img
    uvs = affine_uv(tex["uvw"], [V(0, 0), V(2, 0), V(2, 3), V(0, 3)])
    expect = [(0, 0), (1, 0), (1, 1), (0, 1)]
    for (u, v), (eu, ev) in zip(uvs, expect):
        assert abs(u - eu) < 1e-5 and abs(v - ev) < 1e-5
    # derived tile size: one repeat spans the quad (2 m × 3 m)
    assert abs(tex["sw"] - 2.0) < 1e-4 and abs(tex["sh"] - 3.0) < 1e-4


def test_textured_import_missing_image_falls_back_to_color(tmp_path):
    p, img = _textured_quad_dae(tmp_path)
    import os
    os.remove(img)                                  # user copied only the .dae
    scene = Scene()
    load_dae(scene, p)
    assert len(scene.mesh.faces) == 1
    tex = scene.mesh.faces[0].attrs.get("texture")
    assert tex is None                              # no file -> tint fallback
    assert scene.mesh.faces[0].attrs.get("color") is not None


def test_faceme_sprite_imports_as_mesh_billboard(tmp_path):
    """A component that is a single vertical plane textured with an
    alpha-cutout image (SketchUp face-me people/animals/trees) imports as a
    billboard group whose geometry turns toward the camera — COLLADA drops
    the 'always face camera' flag, so the shape+alpha heuristic recovers it."""
    p, img = _textured_quad_dae(tmp_path, image_name="sprite.png")
    import shutil
    shutil.copy("/home/sumaritux/ingetrazo/resources/components/"
                "person_billboard.png", img)      # a real cutout PNG
    # make the quad VERTICAL (xz plane) so it reads as a sprite
    text = (tmp_path / "textured.dae").read_text()
    text = text.replace("0 0 0  2 0 0  2 3 0  0 3 0",
                        "0 0 0  2 0 0  2 0 3  0 0 3")
    (tmp_path / "textured.dae").write_text(text)
    scene = Scene()
    load_dae(scene, tmp_path / "textured.dae")
    assert not scene.mesh.faces                    # pulled out of the loose mesh
    assert len(scene.groups) == 1
    g = scene.groups[0]
    assert getattr(g, "billboard", False) == "mesh"
    assert any(f.attrs.get("texture") for f in g.mesh.faces)


def test_opaque_photo_panel_stays_static(tmp_path):
    """A rectangular panel with an OPAQUE image (a sign, a mural) must NOT
    become a spinning billboard."""
    p, img = _textured_quad_dae(tmp_path, image_name="mural.png")
    text = (tmp_path / "textured.dae").read_text()
    text = text.replace("0 0 0  2 0 0  2 3 0  0 3 0",
                        "0 0 0  2 0 0  2 0 3  0 0 3")   # vertical too
    (tmp_path / "textured.dae").write_text(text)
    scene = Scene()
    load_dae(scene, tmp_path / "textured.dae")
    assert not scene.groups                        # brick.png has no cutout
    assert len(scene.mesh.faces) == 1


def test_repeated_components_share_one_prototype(tmp_path, monkeypatch):
    """A component instanced several times imports as SHARED-prototype
    instance groups: one mesh, N transforms — the memory lever for big
    SketchUp files. Moving one instance must not touch its siblings."""
    quad = """
    <geometry id="quad"><mesh>
      <source id="qp"><float_array id="qpa" count="12">0 0 0  1 0 0  1 1 0  0 1 0</float_array>
        <technique_common><accessor source="#qpa" count="4" stride="3"/></technique_common>
      </source>
      <vertices id="qv"><input semantic="POSITION" source="#qp"/></vertices>
      <polylist count="2">
        <input semantic="VERTEX" source="#qv" offset="0"/>
        <vcount>3 3</vcount><p>0 1 2 0 2 3</p>
      </polylist>
    </mesh></geometry>"""
    body = f"""<?xml version="1.0"?>
<COLLADA {_NSDECL} version="1.4.1">
  <asset><up_axis>Z_UP</up_axis></asset>
  <library_geometries>{quad}</library_geometries>
  <library_nodes>
    <node id="comp" name="banca"><instance_geometry url="#quad"/></node>
  </library_nodes>
  <library_visual_scenes>
    <visual_scene id="scene">
      <node id="root" name="SketchUp">
        <node id="i1"><instance_node url="#comp"/></node>
        <node id="i2"><translate>10 0 0</translate><instance_node url="#comp"/></node>
        <node id="i3"><translate>20 0 0</translate><instance_node url="#comp"/></node>
      </node>
    </visual_scene>
  </library_visual_scenes>
</COLLADA>
"""
    p = tmp_path / "bancas.dae"
    p.write_text(body)
    import formats.dae as dae_mod
    monkeypatch.setattr(dae_mod, "_MAX_FUSE_LOOPS", 2)
    monkeypatch.setattr(dae_mod, "_SPLIT_MIN", 1)
    scene = Scene()
    load_dae(scene, p)
    inst = [g for g in scene.groups if g.xform is not None]
    assert len(inst) == 3
    assert len({id(g.mesh) for g in inst}) == 1        # ONE shared prototype
    assert len(inst[0].mesh.faces) == 1                # fused quad, local coords
    # world positions come from the transforms
    from PySide6.QtGui import QVector3D
    origins = sorted(round(g.xform.map(QVector3D(0, 0, 0)).x(), 3)
                     for g in inst)
    assert origins == [0.0, 10.0, 20.0]

    # moving ONE instance leaves the siblings (and the prototype) untouched
    from core.history import History, MoveGroupCommand
    hist = History(scene)
    hist.execute(MoveGroupCommand(inst[0], QVector3D(0, 5, 0)))
    assert round(inst[0].xform.map(QVector3D(0, 0, 0)).y(), 3) == 5.0
    assert round(inst[1].xform.map(QVector3D(0, 0, 0)).y(), 3) == 0.0
    assert inst[0].mesh is inst[1].mesh                # still shared
    hist.undo()
    assert round(inst[0].xform.map(QVector3D(0, 0, 0)).y(), 3) == 0.0

    # igz round-trip keeps the sharing
    from formats import igz
    out = tmp_path / "bancas.igz"
    igz.save_scene(scene, out)
    scene2 = Scene()
    igz.load_into(scene2, out)
    inst2 = [g for g in scene2.groups if g.xform is not None]
    assert len(inst2) == 3
    assert len({id(g.mesh) for g in inst2}) == 1
    origins2 = sorted(round(g.xform.map(QVector3D(0, 0, 0)).x(), 3)
                      for g in inst2)
    assert origins2 == [0.0, 10.0, 20.0]

    # materialize (make unique) detaches only that copy, in world coords
    g = inst2[1]
    g.materialize()
    assert g.xform is None
    assert g.mesh is not inst2[0].mesh
    xs = sorted({round(v.position.x(), 3) for v in g.mesh.vertices})
    assert xs[0] in (0.0, 10.0, 20.0) and xs[-1] - xs[0] == 1.0


def test_instance_edit_flows_make_unique():
    """Entering an instance materializes it (make unique); exploding an
    instance lands its geometry at the WORLD position."""
    from PySide6.QtGui import QMatrix4x4, QVector3D
    from core.group import Group
    from core.mesh import Mesh
    from core.history import History, ExplodeGroupCommand

    scene = Scene()
    proto = Mesh()
    proto.add_face([QVector3D(0, 0, 0), QVector3D(1, 0, 0),
                    QVector3D(1, 1, 0), QVector3D(0, 1, 0)])
    g1, g2 = Group(proto, name="a"), Group(proto, name="b")
    m = QMatrix4x4(); m.translate(10, 0, 0)
    g1.xform = QMatrix4x4()
    g2.xform = m
    scene.groups.extend([g1, g2])

    # enter-to-edit materializes only that copy
    scene.begin_group_edit(g2)
    assert g2.xform is None and g2.mesh is not proto
    assert scene.mesh is g2.mesh
    xs = {round(v.position.x(), 3) for v in g2.mesh.vertices}
    assert xs == {10.0, 11.0}
    scene.end_group_edit()
    assert g1.mesh is proto and g1.xform is not None   # sibling untouched

    # exploding an instance drops world-space geometry into the loose mesh
    hist = History(scene)
    hist.execute(ExplodeGroupCommand(g1))
    assert g1 not in scene.groups
    xs = {round(v.position.x(), 3) for v in scene.mesh.vertices}
    assert xs == {0.0, 1.0}
    hist.undo()
    assert g1 in scene.groups and not scene.mesh.faces
