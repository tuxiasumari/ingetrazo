"""Concave faces fill correctly — Phase 1, sub-step 5.

A non-convex face fan-triangulated produces triangles that spill outside the
polygon. Routing concave (no-hole) faces through ear-clipping fixes the fill
while convex faces keep the cheap fan path. Covers the convexity test, the
``Face.triangulate`` routing (area conservation for concave loops on any
plane), and that convex faces are unaffected.

Headless: ``QVector3D`` value types only.
"""
from __future__ import annotations

import math

from PySide6.QtGui import QVector3D

from core.geometry import Face
from core.triangulate import is_convex


def V(x: float, y: float, z: float = 0.0) -> QVector3D:
    return QVector3D(x, y, z)


def _tri_area(a, b, c) -> float:
    return QVector3D.crossProduct(b - a, c - a).length() * 0.5


def _area(face) -> float:
    return sum(_tri_area(*t) for t in face.triangulate())


# Classic concave loop: an L (area 3) and a non-convex arrow / chevron.
L_SHAPE = [V(0, 0), V(2, 0), V(2, 1), V(1, 1), V(1, 2), V(0, 2)]
SQUARE = [V(0, 0), V(2, 0), V(2, 2), V(0, 2)]


# ---- is_convex --------------------------------------------------------------

def test_is_convex_square():
    assert is_convex([(0, 0), (2, 0), (2, 2), (0, 2)]) is True


def test_is_convex_triangle():
    assert is_convex([(0, 0), (1, 0), (0, 1)]) is True


def test_is_concave_l_shape():
    assert is_convex([(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]) is False


def test_is_convex_ignores_collinear_midpoint():
    # A square with an extra vertex on the bottom edge is still convex.
    assert is_convex([(0, 0), (1, 0), (2, 0), (2, 2), (0, 2)]) is True


# ---- Face.triangulate routing ----------------------------------------------

def test_concave_face_fills_correctly():
    # The whole point: a fan would overshoot; ear-clipping gives exactly 3.
    assert abs(_area(Face(list(L_SHAPE))) - 3.0) < 1e-6


def test_concave_face_triangle_count():
    # An n-gon triangulates into n-2 triangles regardless of convexity.
    tris = Face(list(L_SHAPE)).triangulate()
    assert len(tris) == len(L_SHAPE) - 2


def test_convex_face_unaffected():
    face = Face(list(SQUARE))
    assert len(face.triangulate()) == 2
    assert abs(_area(face) - 4.0) < 1e-6


def test_concave_face_on_vertical_plane():
    el = [V(0, 0, 0), V(2, 0, 0), V(2, 0, 1), V(1, 0, 1), V(1, 0, 2), V(0, 0, 2)]
    assert abs(_area(Face(el)) - 3.0) < 1e-6


def test_concave_face_rotated():
    ang = math.radians(40)
    cos, sin = math.cos(ang), math.sin(ang)

    def rot(p):
        return V(p.x() * cos - p.y() * sin, p.x() * sin + p.y() * cos, 0)

    assert abs(_area(Face([rot(p) for p in L_SHAPE])) - 3.0) < 1e-6


def test_concave_face_with_hole_still_works():
    # Concave outer + a hole: both mechanisms together.
    outer = [V(0, 0), V(4, 0), V(4, 1), V(1, 1), V(1, 4), V(0, 4)]  # big L, area 7
    hole = [V(0.25, 0.25), V(0.75, 0.25), V(0.75, 0.75), V(0.25, 0.75)]  # 0.25
    assert abs(_area(Face(outer, [hole])) - (7.0 - 0.25)) < 1e-6
