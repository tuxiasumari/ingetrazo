"""Select tool: pick edges by screen-space proximity and delete them.

Behavior:
- Left click on / near an edge: select it. Shift-click adds to the
  current selection; plain click replaces it.
- Left click on empty space: clear the selection.
- Delete / Backspace: remove the selected edges from the scene.
"""
from __future__ import annotations

from PySide6.QtCore import Qt

from tools.base import Tool, ToolContext


class SelectTool(Tool):
    name = "Select"
    shortcut = "S"

    def on_activate(self, viewport) -> None:
        pass

    def on_deactivate(self, viewport) -> None:
        pass

    def on_click(self, ctx: ToolContext) -> None:
        viewport = ctx.viewport
        edge = viewport.pick_edge(ctx.screen.x(), ctx.screen.y())
        additive = bool(ctx.modifiers & Qt.ShiftModifier)
        if edge is None:
            if not additive:
                viewport.scene.clear_selection()
        else:
            viewport.scene.select([edge], additive=additive)
        viewport.update()

    def on_key(self, viewport, key: int, modifiers: Qt.KeyboardModifiers) -> bool:
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            if viewport.scene.selection:
                viewport.scene.delete_selection()
                viewport.update()
            return True
        return False
