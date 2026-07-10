# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Quadtree LOD tile selection (Track G): screen-space refinement, frustum
culling, partition property, and budget cap. Pure — synthetic footprints."""
from __future__ import annotations

from georef.quadtree import select_tiles

W = H = 1000.0


def _is_ancestor(a, b) -> bool:
    """True if tile ``a`` is an ancestor of tile ``b`` in the quadtree."""
    ax, ay, az = a
    bx, by, bz = b
    if az >= bz:
        return False
    shift = bz - az
    return (bx >> shift, by >> shift) == (ax, ay)


def test_no_subdivision_when_tiles_are_small():
    # Every tile projects to a tiny 10 px square in view → keep the start tiles.
    def footprint(x, y, z):
        return [(500, 500), (510, 500), (510, 510), (500, 510)], 0
    tiles = select_tiles(footprint, W, H, [(0, 0, 5)], target_px=384)
    assert tiles == [(0, 0, 5)]


def test_refines_large_tiles_to_max_zoom():
    # A tile always projects huge (whole screen) → subdivide until max_zoom.
    def footprint(x, y, z):
        return [(0, 0), (W, 0), (W, H), (0, H)], 0
    tiles = select_tiles(footprint, W, H, [(0, 0, 10)], max_zoom=13,
                         target_px=384, budget=999)
    assert tiles                        # produced something
    assert all(z == 13 for _, _, z in tiles)   # refined to the cap
    assert len(tiles) == 4 ** 3          # 10→13 = 3 levels → 64 leaves


def test_frustum_culls_offscreen_tiles():
    # Left child projects off-screen (negative x); right child on-screen.
    def footprint(x, y, z):
        if z == 6:
            return [(0, 0), (W, 0), (W, H), (0, H)], 0    # start: subdivide
        # children at z7: x even = off-screen left, x odd = on-screen
        if x % 2 == 0:
            return [(-200, 0), (-100, 0), (-100, H), (-200, H)], 0
        return [(400, 400), (410, 400), (410, 410), (400, 410)], 0
    tiles = select_tiles(footprint, W, H, [(0, 0, 6)], target_px=384)
    assert tiles                                 # some kept
    assert all(x % 2 == 1 for x, _, _ in tiles)  # only on-screen children


def test_result_is_a_partition_no_parent_and_child():
    # Near quadrant huge, others small → mixed zoom; no tile is an ancestor of
    # another (a proper gap-free partition).
    def footprint(x, y, z):
        if (x, y) == (0, 0) and z < 9:
            return [(0, 0), (W, 0), (W, H), (0, H)], 0    # refine this branch
        return [(500, 500), (505, 500), (505, 505), (500, 505)], 0
    tiles = select_tiles(footprint, W, H, [(0, 0, 5)], target_px=384, budget=999)
    zooms = {z for _, _, z in tiles}
    assert len(zooms) > 1                        # genuinely mixed LOD
    for a in tiles:
        for b in tiles:
            assert not _is_ancestor(a, b)


def test_budget_caps_output():
    def footprint(x, y, z):
        return [(0, 0), (W, 0), (W, H), (0, H)], 0        # always subdivide
    tiles = select_tiles(footprint, W, H, [(0, 0, 0)], max_zoom=19, budget=50)
    assert len(tiles) <= 50


def test_straddling_tile_refines_not_kept_coarse():
    # A start tile with corners behind the camera must refine (not be kept huge).
    calls = {"leaf": 0}

    def footprint(x, y, z):
        if z < 4:
            return [(200, 200), (800, 200)], 2            # 2 corners behind
        calls["leaf"] += 1
        return [(500, 500), (505, 505)], 0
    tiles = select_tiles(footprint, W, H, [(0, 0, 2)], target_px=384)
    assert all(z >= 4 for _, _, z in tiles)               # descended past straddle
    assert calls["leaf"] > 0
