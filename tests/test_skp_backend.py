# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""SKP import seam (``formats/skp.py``) + the OpenSKP adapter
(``formats/skp_openskp.py``): container detection, the parse→apply flow, the
NeedsConverter fallback, and OpenSKP model → payload adaptation (fake model, so
no ``openskp`` package or ``.skp`` file is needed)."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from core.scene import Scene
from formats import skp as skp_format
from formats import skp_openskp


def _sketchup_bytes() -> bytes:
    # Real .skp files start with a UTF-16LE "SketchUp Model" marker.
    return b"\xff\xfe" + "SketchUp Model".encode("utf-16-le")


def test_detect_format_recognises_a_sketchup_file(tmp_path):
    p = tmp_path / "m.skp"
    p.write_bytes(_sketchup_bytes() + b"\x00" * 40)
    assert skp_format.detect_format(p) == "skp"


def test_detect_format_unknown_for_non_skp(tmp_path):
    p = tmp_path / "x.skp"
    p.write_bytes(b"not a sketchup file at all")
    assert skp_format.detect_format(p) == "unknown"
    assert skp_format.detect_format(tmp_path / "missing.skp") == "unknown"


def test_unrecognised_file_needs_converter(tmp_path):
    p = tmp_path / "x.skp"
    p.write_bytes(b"garbage")
    assert skp_format.can_handle(p) is False
    with pytest.raises(skp_format.NeedsConverter) as exc:
        skp_format.parse_skp(p)
    assert exc.value.format == "unknown"


def test_backends_status_lists_openskp():
    status = dict(skp_format.backends_status())
    assert "openskp" in status  # availability depends on the optional package


def test_cascade_parses_with_available_backend_and_applies(tmp_path, monkeypatch):
    # A wired backend that recognises the file is used; its payload is applied
    # to the scene as a group.
    class FakeBackend:
        name = "fake"

        def available(self):
            return True

        def supports(self, fmt):
            return fmt == "skp"

        def parse(self, path, progress=None):
            if progress:
                progress(1.0, "done")
            return {"backend": "fake", "groups": [{"name": "g", "faces": [
                ([_V(0, 0), _V(1, 0), _V(1, 1)], [], {"color": [0.2, 0.4, 0.6]})]}]}

    monkeypatch.setattr(skp_format, "_BACKENDS", [FakeBackend()])
    p = tmp_path / "y.skp"
    p.write_bytes(_sketchup_bytes())
    assert skp_format.can_handle(p) is True

    scene = Scene()
    calls = []
    used = skp_format.load_skp(scene, p, progress=lambda f, t: calls.append(t))
    assert used == "fake"
    assert len(scene.groups) == 1
    assert scene.groups[0].name == "g"
    assert calls == ["done"]


def test_empty_parse_falls_back_to_converter(tmp_path, monkeypatch):
    # A backend that recognises the file but yields no geometry must NOT hijack
    # the import — it signals NeedsConverter so skp2dae runs.
    class EmptyBackend:
        name = "empty"

        def available(self):
            return True

        def supports(self, fmt):
            return fmt == "skp"

        def parse(self, path, progress=None):
            return None

    monkeypatch.setattr(skp_format, "_BACKENDS", [EmptyBackend()])
    p = tmp_path / "z.skp"
    p.write_bytes(_sketchup_bytes())
    with pytest.raises(skp_format.NeedsConverter):
        skp_format.parse_skp(p)


# ---- OpenSKP adapter (fake model) ---------------------------------------------

def _V(x, y, z=0.0):
    from PySide6.QtGui import QVector3D
    return QVector3D(float(x), float(y), float(z))


def _fake_definition(*, id, name, verts, edges, faces, instances=()):
    return NS(
        id=id, name=name,
        vertices={vid: NS(id=vid, x=x, y=y, z=z) for vid, (x, y, z) in verts.items()},
        edges={eid: NS(id=eid, v1_id=a, v2_id=b) for eid, (a, b) in edges.items()},
        faces={fid: NS(id=fid, loops=loops, normal=(0, 0, 1), material_id=None)
               for fid, loops in faces.items()},
        instances=list(instances),
    )


def test_openskp_adapter_resolves_a_face_ring_in_metres():
    # A triangle (inches) → world-space metres (SketchUp inch = 0.0254 m, Z-up).
    root = _fake_definition(
        id=0, name="ROOT_MODEL",
        verts={1: (0, 0, 0), 2: (100, 0, 0), 3: (100, 100, 0)},
        edges={10: (1, 2), 11: (2, 3), 12: (3, 1)},
        faces={20: [[(10, 1), (11, 1), (12, 1)]]},
    )
    model = NS(definitions={0: root})
    payload = skp_openskp._adapt(model, "tri")
    assert payload["backend"] == "openskp"
    faces = payload["groups"][0]["faces"]
    assert len(faces) == 1
    outer, holes, attrs = faces[0]
    xs = sorted(round(p.x(), 4) for p in outer)
    assert xs == [0.0, 2.54, 2.54]          # 100 in = 2.54 m
    assert holes == []


def test_openskp_adapter_places_instances_with_transform():
    # Child def placed by an instance translated +100 in on X appears shifted.
    child = _fake_definition(
        id=5, name="Child",
        verts={1: (0, 0, 0), 2: (10, 0, 0), 3: (10, 10, 0)},
        edges={10: (1, 2), 11: (2, 3), 12: (3, 1)},
        faces={20: [[(10, 1), (11, 1), (12, 1)]]},
    )
    inst = NS(ref_idx=5, matrix=[1, 0, 0, 0, 1, 0, 0, 0, 1, 100, 0, 0, 1])
    root = _fake_definition(
        id=0, name="ROOT_MODEL", verts={}, edges={}, faces={}, instances=[inst])
    model = NS(definitions={0: root, 5: child})
    payload = skp_openskp._adapt(model, "inst")
    outer = payload["groups"][0]["faces"][0][0]
    # child X spans 0..10 in, shifted +100 in → 100..110 in → 2.54..2.794 m
    xs = sorted(round(p.x(), 4) for p in outer)
    assert min(xs) == pytest.approx(2.54, abs=1e-4)
    assert max(xs) == pytest.approx(2.794, abs=1e-4)


def test_openskp_adapter_returns_none_without_geometry():
    root = _fake_definition(id=0, name="ROOT_MODEL", verts={}, edges={}, faces={})
    assert skp_openskp._adapt(NS(definitions={0: root}), "empty") is None


def test_openskp_adapter_resolves_face_colours_via_materials_by_id():
    # Face.material_id → SkpModel.materials_by_id (our upstream PR openskp#3)
    # → IngeTrazo attrs["color"] in 0..1. A model without the join (PyPI
    # 0.2.0) simply imports uncoloured.
    root = _fake_definition(
        id=0, name="ROOT_MODEL",
        verts={1: (0, 0, 0), 2: (10, 0, 0), 3: (10, 10, 0)},
        edges={10: (1, 2), 11: (2, 3), 12: (3, 1)},
        faces={20: [[(10, 1), (11, 1), (12, 1)]]},
    )
    root.faces[20].material_id = 29491
    mat = NS(name="Wood", color=(255, 0, 51), transparency=1.0, id=29491)

    with_join = NS(definitions={0: root}, materials_by_id={29491: mat})
    attrs = skp_openskp._adapt(with_join, "m")["groups"][0]["faces"][0][2]
    assert attrs == {"color": [1.0, 0.0, 0.2]}

    without_join = NS(definitions={0: root})   # PyPI 0.2.0: no materials_by_id
    attrs = skp_openskp._adapt(without_join, "m")["groups"][0]["faces"][0][2]
    assert attrs is None


def test_openskp_adapter_extracts_textures_next_to_the_skp(tmp_path):
    # Material.texture (our upstream PR openskp#4) → attrs["texture"] with the
    # image written to <stem>/ beside the .skp and the tile size in metres.
    root = _fake_definition(
        id=0, name="ROOT_MODEL",
        verts={1: (0, 0, 0), 2: (10, 0, 0), 3: (10, 10, 0)},
        edges={10: (1, 2), 11: (2, 3), 12: (3, 1)},
        faces={20: [[(10, 1), (11, 1), (12, 1)]]},
    )
    root.faces[20].material_id = 5
    tex = NS(filename="glass.jpg", width=24.0, height=12.0,
             data=b"\xff\xd8fakejpeg")
    mat = NS(name="Glass", color=(8, 201, 241), transparency=0.5,
             id=5, texture=tex)
    model = NS(definitions={0: root}, materials_by_id={5: mat})

    skp = tmp_path / "casa.skp"
    skp.write_bytes(b"")
    attrs = skp_openskp._adapt(model, "casa", skp_path=skp)
    attrs = attrs["groups"][0]["faces"][0][2]

    img = tmp_path / "casa" / "glass.jpg"
    assert img.read_bytes() == b"\xff\xd8fakejpeg"
    assert attrs["texture"]["path"] == str(img)
    assert attrs["texture"]["sw"] == pytest.approx(24 * 0.0254)   # 0.6096 m
    assert attrs["texture"]["sh"] == pytest.approx(12 * 0.0254)


def _tri_def(id, name, instances=()):
    return _fake_definition(
        id=id, name=name,
        verts={1: (0, 0, 0), 2: (10, 0, 0), 3: (10, 10, 0)},
        edges={10: (1, 2), 11: (2, 3), 12: (3, 1)},
        faces={20: [[(10, 1), (11, 1), (12, 1)]]},
        instances=instances,
    )


def test_openskp_adapter_groups_per_top_level_instance():
    # Root loose faces -> one group named after the file; each top-level
    # instance -> its own group carrying the DEFINITION's name (SketchUp).
    child = _tri_def(5, "Farola")
    ins = NS(ref_idx=5, matrix=[1, 0, 0, 0, 1, 0, 0, 0, 1, 100, 0, 0, 1])
    root = _tri_def(0, "ROOT_MODEL", instances=[ins])
    payload = skp_openskp._adapt(NS(definitions={0: root, 5: child}), "obra")

    names = sorted(g["name"] for g in payload["groups"])
    assert names == ["Farola", "obra"]
    assert payload["protos"] == []


def test_openskp_adapter_shares_repeated_components(monkeypatch):
    # A definition placed twice above the sharing thresholds becomes ONE
    # prototype with two placement matrices — not two flattened copies.
    import formats.dae as dae_mod
    monkeypatch.setattr(dae_mod, "_INST_MIN_POLYS", 1)
    monkeypatch.setattr(dae_mod, "_INST_MIN_SAVED", 1)

    child = _tri_def(5, "Arbol")
    i1 = NS(ref_idx=5, matrix=[1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1])
    i2 = NS(ref_idx=5, matrix=[1, 0, 0, 0, 1, 0, 0, 0, 1, 100, 0, 0, 1])
    root = _fake_definition(id=0, name="ROOT_MODEL", verts={}, edges={},
                            faces={}, instances=[i1, i2])
    payload = skp_openskp._adapt(NS(definitions={0: root, 5: child}), "obra")

    assert payload["groups"] == []
    assert len(payload["protos"]) == 1
    proto = payload["protos"][0]
    assert proto["name"] == "Arbol"
    assert len(proto["instances"]) == 2
    # Prototype geometry is LOCAL (translation lives in the matrices).
    xs = [round(p.x(), 4) for p in proto["faces"][0][0]]
    assert max(xs) == pytest.approx(10 * 0.0254)

    # apply_payload: two instance Groups SHARING one prototype mesh.
    scene = Scene()
    skp_format.apply_payload(scene, payload)
    inst = [g for g in scene.groups if g.xform is not None]
    assert len(inst) == 2
    assert inst[0].mesh is inst[1].mesh


def test_openskp_adapter_inherits_instance_material():
    # SketchUp "paint the component": faces with material None inherit the
    # enclosing instance's material_id (upstream PR openskp#5).
    child = _tri_def(5, "Banca")            # faces carry material_id None
    ins = NS(ref_idx=5, material_id=77,
             matrix=[1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1])
    root = _fake_definition(id=0, name="ROOT_MODEL", verts={}, edges={},
                            faces={}, instances=[ins])
    wood = NS(name="Wood", color=(255, 0, 0), transparency=1.0, id=77,
              texture=None)
    model = NS(definitions={0: root, 5: child}, materials_by_id={77: wood})
    payload = skp_openskp._adapt(model, "obra")

    attrs = payload["groups"][0]["faces"][0][2]
    assert attrs == {"color": [1.0, 0.0, 0.0]}


def test_openskp_adapter_face_material_beats_inherited():
    child = _tri_def(5, "Banca")
    child.faces[20].material_id = 88        # face's own material wins
    ins = NS(ref_idx=5, material_id=77,
             matrix=[1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1])
    root = _fake_definition(id=0, name="ROOT_MODEL", verts={}, edges={},
                            faces={}, instances=[ins])
    mats = {77: NS(name="W", color=(255, 0, 0), transparency=1, id=77,
                   texture=None),
            88: NS(name="B", color=(0, 0, 255), transparency=1, id=88,
                   texture=None)}
    model = NS(definitions={0: root, 5: child}, materials_by_id=mats)
    payload = skp_openskp._adapt(model, "obra")

    assert payload["groups"][0]["faces"][0][2] == {"color": [0.0, 0.0, 1.0]}


def test_openskp_adapter_splits_prototypes_by_inherited_material(monkeypatch):
    # The same component painted red and green as a whole must NOT share one
    # prototype — one proto per inherited material.
    import formats.dae as dae_mod
    monkeypatch.setattr(dae_mod, "_INST_MIN_POLYS", 1)
    monkeypatch.setattr(dae_mod, "_INST_MIN_SAVED", 1)

    child = _tri_def(5, "Poste")
    i_red = NS(ref_idx=5, material_id=1,
               matrix=[1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1])
    i_red2 = NS(ref_idx=5, material_id=1,
                matrix=[1, 0, 0, 0, 1, 0, 0, 0, 1, 50, 0, 0, 1])
    i_green = NS(ref_idx=5, material_id=2,
                 matrix=[1, 0, 0, 0, 1, 0, 0, 0, 1, 100, 0, 0, 1])
    root = _fake_definition(id=0, name="ROOT_MODEL", verts={}, edges={},
                            faces={}, instances=[i_red, i_red2, i_green])
    mats = {1: NS(name="R", color=(255, 0, 0), transparency=1, id=1,
                  texture=None),
            2: NS(name="G", color=(0, 255, 0), transparency=1, id=2,
                  texture=None)}
    model = NS(definitions={0: root, 5: child}, materials_by_id=mats)
    payload = skp_openskp._adapt(model, "obra")

    assert len(payload["protos"]) == 2      # one per inherited material
    counts = sorted(len(p["instances"]) for p in payload["protos"])
    assert counts == [1, 2]                 # 2 red copies share, 1 green alone
    colors = sorted(p["faces"][0][2]["color"] for p in payload["protos"])
    assert colors == [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0]]
