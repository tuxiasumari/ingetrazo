# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Quadtree level-of-detail tile selection (Track G) — Google-Earth-style.

Instead of one zoom for the whole view (which forces near-detail and far-coverage
to fight), this walks the slippy-map tile pyramid and, per tile, decides: is it
detailed enough for its size on screen? A tile that projects **larger** than the
target pixel size is subdivided into its four children (more detail); one that
projects small enough is kept. So **near tiles refine to high zoom, far tiles
stay coarse**, the selection is a gap-free partition of the visible ground, and
the count stays bounded — exactly what keeps a tilted, far-reaching view fluid.

Pure and rendering-agnostic: the caller supplies a ``footprint(x, y, z)`` that
projects a tile's ground corners to screen pixels (and reports how many fell
behind the camera). The viewport builds that from the datum + camera.
"""
from __future__ import annotations


def select_tiles(footprint, screen_w: float, screen_h: float, start_tiles,
                 *, max_zoom: int = 19, target_px: float = 384.0,
                 budget: int = 256) -> list[tuple[int, int, int]]:
    """Select LOD tiles by screen-space error, descending from ``start_tiles``.

    ``footprint(x, y, z)`` returns ``(corners_px, behind)`` — the on-screen
    pixel corners of tile ``(x, y, z)`` that are in front of the camera, and how
    many of its four corners fell behind. Returns ``(x, y, z)`` tiles at mixed
    zooms; capped at ``budget``.
    """
    selected: list[tuple[int, int, int]] = []

    def recurse(x: int, y: int, z: int) -> None:
        if len(selected) >= budget:
            return
        corners, behind = footprint(x, y, z)
        if not corners:
            return                                  # entirely behind the camera
        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]
        bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
        straddles = behind > 0                      # spans the camera plane
        # Frustum cull: fully off-screen and not straddling the camera.
        if not straddles and (bx1 < 0 or bx0 > screen_w
                              or by1 < 0 or by0 > screen_h):
            return
        size = max(bx1 - bx0, by1 - by0)
        # Keep the tile when it's detailed enough (small on screen) and fully in
        # front; otherwise refine. A straddling tile always refines (its near
        # side needs detail; far children get culled).
        if z >= max_zoom or (not straddles and size <= target_px):
            selected.append((x, y, z))
            return
        for dx in (0, 1):
            for dy in (0, 1):
                recurse(2 * x + dx, 2 * y + dy, z + 1)

    for (x, y, z) in start_tiles:
        recurse(x, y, z)
    return selected
