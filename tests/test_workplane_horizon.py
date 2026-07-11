# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Near-horizon escape hatch for the captured work plane.

A first click on a horizontal face (the ground, a slab) captures its plane and
pins the drawing chain flat — which made drawing a line UPWARD impossible: the
SketchUp gesture is to orbit down to the horizon, where the horizontal plane
is unreadable anyway, and draw up. At near-horizon views a HORIZONTAL captured
plane now yields to the vertical plane through the start point; vertical
captured planes (walls) are untouched.
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def viewport():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    elif not isinstance(app, QApplication):
        pytest.skip("another Qt application flavour is already running")
    from views.viewport import Viewport
    return Viewport(None)


class _Tool:
    def __init__(self):
        self.work_plane = (QVector3D(0, 0, 0), QVector3D(0, 0, 1))
        self.start_point = QVector3D(2, 3, 0)


def test_iso_view_keeps_captured_ground_plane(viewport):
    viewport.active_tool = _Tool()
    viewport.camera.set_view("iso")
    _, n = viewport._current_work_plane()
    assert abs(n.z()) > 0.99                        # still the ground plane


def test_horizon_view_unlocks_vertical_drawing(viewport):
    tool = _Tool()
    viewport.active_tool = tool
    viewport.camera.set_view("front")               # camera at the horizon
    pt, n = viewport._current_work_plane()
    assert abs(n.z()) < 1e-6                        # vertical plane
    assert (pt - tool.start_point).length() < 1e-9  # through the start point


def test_captured_wall_plane_is_untouched_at_horizon(viewport):
    tool = _Tool()
    tool.work_plane = (QVector3D(0, 0, 0), QVector3D(1, 0, 0))  # a wall
    viewport.active_tool = tool
    viewport.camera.set_view("front")
    _, n = viewport._current_work_plane()
    assert abs(n.x()) > 0.99                        # the wall keeps its plane
