"""Materials: per-face colour stored in attrs, painted via the Paint tool,
serialized to .igz, and surviving push/pull through the plane rebuild (A.3)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QVector3D

from core.edits import build_add_edges
from core.history import AddFaceCommand, History, SetFaceColorCommand
from core.scene import Scene
from formats import igz
from tools.base import ToolContext
from tools.paint import PaintTool


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


def _square(scene, hist):
    loop = [V(0, 0), V(4, 0), V(4, 4), V(0, 4)]
    hist.execute(build_add_edges(
        scene, [(loop[i], loop[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(loop))]))
    return scene.mesh.faces[0]


# ---- SetFaceColorCommand -------------------------------------------------------

def test_set_face_color_command_do_undo():
    scene = Scene()
    hist = History(scene)
    face = _square(scene, hist)
    assert "color" not in face.attrs

    hist.execute(SetFaceColorCommand([face], (0.2, 0.4, 0.8)))
    assert face.attrs["color"] == [0.2, 0.4, 0.8]

    hist.undo()
    assert "color" not in face.attrs        # restored to unpainted

    hist.redo()
    assert face.attrs["color"] == [0.2, 0.4, 0.8]


def test_set_face_color_none_clears():
    scene = Scene()
    hist = History(scene)
    face = _square(scene, hist)
    face.attrs["color"] = [0.1, 0.1, 0.1]
    hist.execute(SetFaceColorCommand([face], None))
    assert "color" not in face.attrs
    hist.undo()
    assert face.attrs["color"] == [0.1, 0.1, 0.1]


# ---- Paint tool ----------------------------------------------------------------

class _PaintVP:
    def __init__(self, scene, face):
        self.scene = scene
        self.history = History(scene)
        self._face = face

    def pick_face_any(self, x, y):
        return self._face, None

    def set_hover(self, entity):
        pass

    def update(self):
        pass


def _ctx(vp, modifiers=Qt.NoModifier):
    return ToolContext(viewport=vp, world=QVector3D(),
                       screen=QPointF(0.0, 0.0), modifiers=modifiers, snap=None)


def test_paint_tool_colours_clicked_face():
    scene = Scene()
    hist = History(scene)
    face = _square(scene, hist)
    vp = _PaintVP(scene, face)
    tool = PaintTool()
    PaintTool.current_color = (0.7, 0.3, 0.1)
    tool.on_click(_ctx(vp))
    assert face.attrs["color"] == [0.7, 0.3, 0.1]
    vp.history.undo()
    assert "color" not in face.attrs


def test_paint_tool_alt_samples_colour():
    scene = Scene()
    hist = History(scene)
    face = _square(scene, hist)
    face.attrs["color"] = [0.11, 0.22, 0.33]
    vp = _PaintVP(scene, face)
    tool = PaintTool()
    PaintTool.current_color = (0.9, 0.9, 0.9)
    tool.on_click(_ctx(vp, Qt.AltModifier))           # eyedropper
    assert PaintTool.current_color == (0.11, 0.22, 0.33)
    # Sampling does not record an undo step (no painting happened).
    assert not vp.history.undo_stack


def test_paint_tool_paints_whole_face_selection():
    scene = Scene()
    hist = History(scene)
    # Two side-by-side squares.
    a = _square(scene, hist)
    loop2 = [V(4, 0), V(8, 0), V(8, 4), V(4, 4)]
    hist.execute(build_add_edges(
        scene, [(loop2[i], loop2[(i + 1) % 4]) for i in range(4)],
        detect_faces=False, extra=[AddFaceCommand(list(loop2))]))
    b = next(f for f in scene.mesh.faces if f is not a)
    scene.selection.update({a, b})
    vp = _PaintVP(scene, a)                            # click lands on a
    tool = PaintTool()
    PaintTool.current_color = (0.5, 0.5, 0.0)
    tool.on_click(_ctx(vp))
    assert a.attrs["color"] == [0.5, 0.5, 0.0]
    assert b.attrs["color"] == [0.5, 0.5, 0.0]         # selection painted too


# ---- .igz round-trip -----------------------------------------------------------

def test_color_survives_igz_round_trip(tmp_path):
    scene = Scene()
    hist = History(scene)
    face = _square(scene, hist)
    face.attrs["color"] = [0.3, 0.6, 0.9]
    path = tmp_path / "painted.igz"
    igz.save_scene(scene, path)

    loaded = Scene()
    igz.load_into(loaded, path)
    f = loaded.mesh.faces[0]
    assert f.attrs.get("color") == [0.3, 0.6, 0.9]


def test_color_survives_pushpull(tmp_path):
    # Paint the top of a cube, push it up: the moved cap continues the base, so
    # the colour rides through the extrude + plane rebuild (A.3).
    import tests.test_fuzz_engine as F

    scene = Scene()
    hist = History(scene)
    base = _square(scene, hist)
    F._push(scene, hist, base, 3.0 if base.normal().z() > 0 else -3.0)
    top = next(f for f in scene.mesh.faces
               if all(abs(v.z() - 3) < 1e-9 for v in f.vertices))
    hist.execute(SetFaceColorCommand([top], (0.9, 0.1, 0.1)))

    F._push(scene, hist, top, 2.0)                     # extrude the painted top
    new_top = next(f for f in scene.mesh.faces
                   if all(abs(v.z() - 5) < 1e-9 for v in f.vertices))
    assert new_top.attrs.get("color") == [0.9, 0.1, 0.1]


def test_unpainted_face_writes_no_color_key(tmp_path):
    scene = Scene()
    hist = History(scene)
    _square(scene, hist)
    path = tmp_path / "plain.igz"
    igz.save_scene(scene, path)
    import json
    data = json.loads(Path(path).read_text())
    assert "color" not in data["scene"]["faces"][0]
