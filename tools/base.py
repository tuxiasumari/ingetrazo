"""Base class for IngeTrazo tools and the per-event ``ToolContext`` they receive.

A tool is anything the user activates from the toolbar to interact with the
viewport: draw, modify, select. Both built-in tools and third-party plugins
inherit from :class:`Tool` so they can be registered uniformly.

Spatial tools (line, rectangle, push/pull, select, ...) override
:meth:`on_click`, :meth:`on_hover` and :meth:`on_cancel`. The viewport
raycasts the mouse pixel against the working plane and produces a
:class:`ToolContext` that combines the snapped world point, the raw screen
position, keyboard modifiers and the snap metadata.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QVector3D

from core.snap import SnapResult


@dataclass
class ToolContext:
    """Bundle of data a tool needs to react to a viewport event."""

    viewport: object  # forward reference to views.viewport.Viewport
    world: QVector3D
    screen: QPointF
    modifiers: Qt.KeyboardModifiers
    snap: SnapResult


class Tool(ABC):
    name: str = "Unnamed"
    icon: str | None = None
    shortcut: str | None = None
    # Drawing tools snap to geometry and show the snap markers/tooltips
    # (Endpoint, On Edge, On Face, ...). Tools that only pick existing
    # geometry (Select, Push/Pull) set this False: no snap engine, no markers.
    uses_snap: bool = True
    # Tools that support a click-drag rubber-band box (Select). For these the
    # viewport defers the click to release: a tiny drag is a click (on_click),
    # a real drag is a box (on_box_select).
    box_select: bool = False

    @abstractmethod
    def on_activate(self, viewport) -> None:
        """Called when the user selects this tool."""

    @abstractmethod
    def on_deactivate(self, viewport) -> None:
        """Called when the user switches to another tool."""

    # ---- High-level spatial input (overridden by drawing tools) -------------
    def on_click(self, ctx: ToolContext) -> None:
        """Left click at ``ctx.world`` (already snapped)."""

    def on_hover(self, ctx: ToolContext) -> None:
        """Mouse moved to ``ctx.world`` without a button pressed."""

    def on_box_select(self, viewport, rect, crossing: bool, additive: bool) -> None:
        """Rubber-band box released. ``rect`` is ``(x0, y0, x1, y1)`` in screen
        pixels (normalized so x0<=x1, y0<=y1). ``crossing`` is True for a
        right-to-left drag (select anything the box touches) and False for a
        left-to-right drag (select only what's fully enclosed). Only tools with
        ``box_select = True`` receive this."""

    def on_cancel(self, viewport) -> None:
        """Esc pressed — abandon any in-progress operation."""

    # ---- Key dispatch -------------------------------------------------------
    def on_key(self, viewport, key: int, modifiers: Qt.KeyboardModifiers) -> bool:
        """Tool gets first shot at the key. Return True to consume it."""
        return False

    def on_value(self, viewport, value: float) -> bool:
        """User typed a numeric length and pressed Enter (VCB style).

        Tools that accept a numeric value (line length, rectangle dimensions,
        circle radius, ...) override this and return True on success. The
        viewport handles digit buffering and dispatch; tools only need to
        consume the value.
        """
        return False

    # ---- Visual feedback hooks ---------------------------------------------
    def rubber_band_lines(self):
        """Return ``[(a, b), ...]`` line segments to draw as the live preview.

        ``LineTool`` returns a single segment; ``RectangleTool`` returns four.
        The viewport renders whatever the active tool returns; tools that
        don't preview anything default to an empty list.
        """
        return []
