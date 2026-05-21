"""Base class for Wasia tools.

A tool is anything the user activates from the toolbar to interact with the
viewport: draw, modify, select. Both built-in tools and third-party plugins
inherit from :class:`Tool` so they can be registered uniformly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Tool(ABC):
    """Abstract base class for every Wasia tool.

    Concrete subclasses set ``name``, ``icon`` and ``shortcut`` as class
    attributes, then implement at least ``on_activate`` and ``on_deactivate``.
    """

    name: str = "Unnamed"
    icon: str | None = None
    shortcut: str | None = None

    @abstractmethod
    def on_activate(self, viewport) -> None:
        """Called when the user selects this tool."""

    @abstractmethod
    def on_deactivate(self, viewport) -> None:
        """Called when the user switches to another tool."""

    def on_mouse_press(self, event, viewport) -> None:
        pass

    def on_mouse_move(self, event, viewport) -> None:
        pass

    def on_mouse_release(self, event, viewport) -> None:
        pass

    def on_key_press(self, event, viewport) -> None:
        pass
