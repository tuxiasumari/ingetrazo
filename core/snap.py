"""Snap engine: pick the best snap target near the cursor.

Snap kinds (priority high → low):
1. ``"axis"``           — explicit axis lock from arrow keys.
2. ``"reference"``      — parallel/perpendicular lock to a reference edge.
3. ``"axis"`` via Shift — Shift held while an axis inference is active.
4. ``"close"``          — closing the current polygon chain.
5. ``"endpoint"``       — vertex of an existing edge.
6. ``"origin"``         — world origin.
7. ``"axis_inference"`` — soft auto-detected axis alignment (visual cue only).
8. ``"none"``           — no snap.

Distance checks for point snaps are done in **screen-space pixels** so the
snap radius stays constant under zoom. The caller supplies a
``world_to_pixel`` callback so this module does not depend on Qt's
viewport directly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtGui import QVector3D


# Colors used by the rubber band and the 2D snap indicator. RGB floats [0, 1].
COLOR_ENDPOINT = (0.16, 0.62, 0.36)
COLOR_ORIGIN = (0.95, 0.45, 0.16)
COLOR_CLOSE = (0.20, 0.40, 0.78)
COLOR_AXIS_X = (0.86, 0.22, 0.27)
COLOR_AXIS_Y = (0.16, 0.62, 0.36)
COLOR_AXIS_Z = (0.20, 0.40, 0.78)
COLOR_REFERENCE = (0.85, 0.30, 0.80)  # magenta — parallel / perpendicular
COLOR_NONE = (0.0, 0.0, 0.0)

AXIS_COLORS = {
    "x": COLOR_AXIS_X,
    "y": COLOR_AXIS_Y,
    "z": COLOR_AXIS_Z,
}

AXIS_NAMES = {"x": "X", "y": "Y", "z": "Z"}


@dataclass
class SnapResult:
    point: QVector3D
    kind: str
    color: tuple[float, float, float] = COLOR_NONE
    axis: Optional[str] = None  # "x" / "y" / "z" when kind is "axis" or "axis_inference"


# ---- Helpers ---------------------------------------------------------------

def _detect_axis_alignment(
    start: QVector3D, candidate: QVector3D, angle_deg: float
) -> Optional[str]:
    """If the start→candidate direction is within ``angle_deg`` of an axis,
    return that axis as ``"x"``/``"y"``/``"z"``. Otherwise ``None``."""
    delta = candidate - start
    length = delta.length()
    if length < 1e-6:
        return None
    cos_thresh = math.cos(math.radians(angle_deg))
    nx = abs(delta.x()) / length
    ny = abs(delta.y()) / length
    nz = abs(delta.z()) / length
    # Pick the strongest alignment if multiple pass.
    candidates = [(nx, "x"), (ny, "y"), (nz, "z")]
    candidates.sort(reverse=True)
    if candidates[0][0] >= cos_thresh:
        return candidates[0][1]
    return None


def _direction_from_edge(edge, mode: str) -> Optional[QVector3D]:
    """Return a unit-length direction in the work plane for ``mode``.

    ``parallel``      → the edge's own direction.
    ``perpendicular`` → the edge's direction rotated 90° in the XY plane.

    For perpendicular we ignore the edge's Z component, so the lock stays
    on the ground plane (Z=0), which matches the user expectation in
    architecture / civil flows.
    """
    direction = edge.b - edge.a
    if direction.length() < 1e-6:
        return None
    if mode == "perpendicular":
        # 90° rotation in XY plane.
        direction = QVector3D(-direction.y(), direction.x(), 0.0)
    return direction.normalized()


# ---- Entry point ------------------------------------------------------------

# Type alias for the projection callback the viewport provides. Given the
# chain start point and a direction in world space, it returns the closest
# point on that infinite line to the camera ray that currently passes
# through the cursor pixel. This is what makes Z-axis locks usable (and
# X/Y locks correct when the start point is off the ground plane).
ProjectOntoLine = Callable[[QVector3D, QVector3D], QVector3D]


# Unit vectors for each world axis, used by the axis lock paths.
_AXIS_VECTORS = {
    "x": QVector3D(1.0, 0.0, 0.0),
    "y": QVector3D(0.0, 1.0, 0.0),
    "z": QVector3D(0.0, 0.0, 1.0),
}


def compute_snap(
    candidate_world: QVector3D,
    candidate_pixel: tuple[float, float],
    scene,
    world_to_pixel: Callable[[QVector3D], Optional[tuple[float, float]]],
    threshold_px: float,
    project_onto_line: Optional[ProjectOntoLine] = None,
    chain_first_point: Optional[QVector3D] = None,
    start_point: Optional[QVector3D] = None,
    axis_lock: Optional[str] = None,
    shift_held: bool = False,
    reference_edge=None,
    reference_mode: Optional[str] = None,
    inference_angle_deg: float = 3.0,
) -> SnapResult:
    # 1. Explicit axis lock (arrow keys). Use the viewport's camera-aware
    #    projection so locks to Z (vertical) actually move along Z.
    if axis_lock and start_point is not None and project_onto_line is not None:
        locked = project_onto_line(start_point, _AXIS_VECTORS[axis_lock])
        return SnapResult(locked, "axis", AXIS_COLORS[axis_lock], axis=axis_lock)

    # 2. Reference edge lock (Down arrow + edge under cursor).
    if (
        reference_edge is not None
        and reference_mode
        and start_point is not None
        and project_onto_line is not None
    ):
        direction = _direction_from_edge(reference_edge, reference_mode)
        if direction is not None:
            locked = project_onto_line(start_point, direction)
            return SnapResult(locked, "reference", COLOR_REFERENCE)

    # 3. Shift held + auto axis inference → lock to that axis.
    if shift_held and start_point is not None and project_onto_line is not None:
        inferred = _detect_axis_alignment(
            start_point, candidate_world, inference_angle_deg
        )
        if inferred is not None:
            locked = project_onto_line(start_point, _AXIS_VECTORS[inferred])
            return SnapResult(locked, "axis", AXIS_COLORS[inferred], axis=inferred)

    # 4–6. Point snaps (close, endpoint, origin) — closest wins by pixel distance.
    cx, cy = candidate_pixel
    best: Optional[tuple[float, QVector3D, str, tuple[float, float, float]]] = None

    def _consider(world: QVector3D, kind: str, color: tuple[float, float, float]) -> None:
        nonlocal best
        px = world_to_pixel(world)
        if px is None:
            return
        d = math.hypot(px[0] - cx, px[1] - cy)
        if d > threshold_px:
            return
        if best is None or d < best[0]:
            best = (d, world, kind, color)

    if (
        chain_first_point is not None
        and start_point is not None
        and chain_first_point is not start_point
    ):
        _consider(chain_first_point, "close", COLOR_CLOSE)

    if best is None or best[2] != "close":
        for edge in scene.edges:
            _consider(edge.a, "endpoint", COLOR_ENDPOINT)
            _consider(edge.b, "endpoint", COLOR_ENDPOINT)

    _consider(QVector3D(0.0, 0.0, 0.0), "origin", COLOR_ORIGIN)

    if best is not None:
        return SnapResult(best[1], best[2], best[3])

    # 7. Soft axis inference (no lock; visual cue only).
    if start_point is not None:
        inferred = _detect_axis_alignment(
            start_point, candidate_world, inference_angle_deg
        )
        if inferred is not None:
            return SnapResult(
                candidate_world, "axis_inference", AXIS_COLORS[inferred], axis=inferred
            )

    return SnapResult(candidate_world, "none", COLOR_NONE)
