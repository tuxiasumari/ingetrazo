"""Static dimensions: the entity's geometry, the Add/Delete commands, the tool's
3-click placement, and .igz round-trip."""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QVector3D

from core.dimension import Dimension
from core.history import (
    AddDimensionCommand,
    DeleteDimensionsCommand,
    History,
)
from core.scene import Scene
from formats import igz
from tools.base import ToolContext
from tools.dimension import DimensionTool


def V(x, y, z=0.0):
    return QVector3D(float(x), float(y), float(z))


# ---- Entity --------------------------------------------------------------------

def test_dimension_value_and_line_points():
    d = Dimension(V(0, 0, 0), V(4, 0, 0), V(0, 0, 2))
    assert d.value() == 4.0
    assert d.label() == "4.00 m"
    ap, bp = d.line_points()
    assert ap == V(0, 0, 2) and bp == V(4, 0, 2)
    assert d.midpoint() == V(2, 0, 2)


def test_offset_for_cursor_is_perpendicular():
    # Cursor above the +X segment at (2, 0, 3): offset should be the vertical
    # component only (the along-segment part is dropped).
    off = Dimension.offset_for_cursor(V(0, 0, 0), V(4, 0, 0), V(2, 0, 3))
    assert off == V(0, 0, 3)


# ---- Commands ------------------------------------------------------------------

def test_add_and_delete_dimension_commands():
    scene = Scene()
    hist = History(scene)
    d = Dimension(V(0, 0, 0), V(4, 0, 0), V(0, 0, 2))
    hist.execute(AddDimensionCommand(d))
    assert scene.dimensions == [d]
    hist.undo()
    assert scene.dimensions == []
    hist.redo()
    assert scene.dimensions == [d]

    hist.execute(DeleteDimensionsCommand([d]))
    assert scene.dimensions == []
    hist.undo()
    assert scene.dimensions == [d]            # restored at its index


# ---- Tool ----------------------------------------------------------------------

class _StubVP:
    def __init__(self, scene):
        self.scene = scene
        self.history = History(scene)

    def update(self):
        pass


def _ctx(vp, world):
    return ToolContext(viewport=vp, world=world, screen=QPointF(0, 0),
                       modifiers=Qt.NoModifier, snap=None)


def test_dimension_tool_three_clicks_place_a_dimension():
    scene = Scene()
    vp = _StubVP(scene)
    tool = DimensionTool()
    tool.on_click(_ctx(vp, V(0, 0, 0)))         # first endpoint
    assert not scene.dimensions
    tool.on_click(_ctx(vp, V(4, 0, 0)))         # second endpoint
    assert not scene.dimensions                 # still placing the offset
    tool.on_click(_ctx(vp, V(2, 0, 3)))         # offset placement
    assert len(scene.dimensions) == 1
    d = scene.dimensions[0]
    assert d.value() == 4.0
    assert d.offset == V(0, 0, 3)
    assert tool.a is None                       # reset for the next dimension


def test_dimension_tool_preview_lines():
    scene = Scene()
    vp = _StubVP(scene)
    tool = DimensionTool()
    tool.on_click(_ctx(vp, V(0, 0, 0)))
    tool.on_hover(_ctx(vp, V(4, 0, 0)))
    assert tool.rubber_band_lines() == [(V(0, 0, 0), V(4, 0, 0))]  # measuring
    tool.on_click(_ctx(vp, V(4, 0, 0)))
    tool.on_hover(_ctx(vp, V(2, 0, 3)))
    lines = tool.rubber_band_lines()
    assert len(lines) == 3                       # 2 extension + 1 dimension line


# ---- .igz round-trip -----------------------------------------------------------

def test_dimension_survives_igz_round_trip(tmp_path):
    scene = Scene()
    hist = History(scene)
    hist.execute(AddDimensionCommand(
        Dimension(V(0, 0, 0), V(4, 0, 0), V(0, 0, 2))))
    path = tmp_path / "dim.igz"
    igz.save_scene(scene, path)

    loaded = Scene()
    igz.load_into(loaded, path)
    assert len(loaded.dimensions) == 1
    d = loaded.dimensions[0]
    assert d.a == V(0, 0, 0) and d.b == V(4, 0, 0) and d.offset == V(0, 0, 2)
    assert d.value() == 4.0


# ---- Select + delete -----------------------------------------------------------

def test_select_tool_deletes_a_dimension():
    from tools.select import SelectTool

    scene = Scene()
    vp = _StubVP(scene)
    d = Dimension(V(0, 0, 0), V(4, 0, 0), V(0, 0, 2))
    vp.history.execute(AddDimensionCommand(d))
    scene.selection.add(d)

    tool = SelectTool()
    tool.on_key(vp, Qt.Key_Delete, Qt.NoModifier)
    assert d not in scene.dimensions
    assert d not in scene.selection          # selection cleared of the deleted dim

    vp.history.undo()
    assert d in scene.dimensions             # undo brings it back
