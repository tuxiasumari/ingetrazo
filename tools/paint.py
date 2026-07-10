# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Paint (bucket) tool: assign a material colour to a face.

Behavior (SketchUp's Paint Bucket, ``B``):
- Left click on a face: paint it with the tool's current colour. The colour
  lives in the face's ``attrs["color"]`` (the generic per-region attrs from
  A.3), so it survives push/pull and the plane rebuild.
- If the clicked face is part of the current face selection, the whole
  selection is painted in one undoable step (paint many at once).
- **Alt**+click samples the face's colour into the current colour (eyedropper).
- Works on loose geometry and group faces alike (``pick_face_any``).

The current colour is class-level (shared across activations) and is set from
the toolbar swatch (a ``QColorDialog``); the tool only applies it.
"""
from __future__ import annotations

from PySide6.QtCore import Qt

from core.mesh import Face
from core.history import (
    CompoundCommand,
    SetFaceColorCommand,
    SetFaceTextureCommand,
)
from tools.base import Tool, ToolContext

# The default cream the viewport paints unpainted faces with — sampling an
# unpainted face yields this, and it is what "no colour" reads as.
DEFAULT_FACE_COLOR = (0.96, 0.95, 0.925)


class PaintTool(Tool):
    name = "Paint"
    shortcut = "B"
    uses_snap = False  # picks a face to paint; no snap markers

    # Shared current paint colour (RGB, 0..1), set from the toolbar swatch.
    current_color: tuple[float, float, float] = (0.80, 0.45, 0.30)
    # Shared current texture ({"path","sw","sh"}) or None. When set, the click
    # applies a texture instead of a colour — chosen from the toolbar.
    current_texture: dict | None = None

    def on_activate(self, viewport) -> None:
        pass

    def on_deactivate(self, viewport) -> None:
        viewport.set_hover(None)

    def on_hover(self, ctx: ToolContext) -> None:
        face, _group = ctx.viewport.pick_face_any(ctx.screen.x(), ctx.screen.y())
        ctx.viewport.set_hover(face)

    def on_click(self, ctx: ToolContext) -> None:
        vp = ctx.viewport
        face, _group = vp.pick_face_any(ctx.screen.x(), ctx.screen.y())
        if face is None:
            return

        if ctx.modifiers & Qt.AltModifier:
            # Eyedropper: adopt the face's material (texture if it has one, else
            # colour) as the current paint material.
            tex = face.attrs.get("texture")
            if tex is not None:
                PaintTool.current_texture = dict(tex)
            else:
                PaintTool.current_texture = None
                sampled = face.attrs.get("color")
                PaintTool.current_color = (tuple(sampled) if sampled is not None
                                           else DEFAULT_FACE_COLOR)
            vp.update()
            return

        # Paint the clicked face — or, if it is part of the current face
        # selection, the whole selection. A face on a curved surface (cylinder
        # side) paints the whole surface, SketchUp-style.
        sel_faces = [e for e in vp.scene.selection if isinstance(e, Face)]
        faces = (sel_faces if face in sel_faces
                 else vp.scene.mesh.surface_of(face))
        if PaintTool.current_texture is not None:
            vp.history.execute(
                SetFaceTextureCommand(faces, PaintTool.current_texture))
        else:
            # Painting a solid colour clears any texture on those faces, in one
            # undoable step.
            vp.history.execute(CompoundCommand([
                SetFaceColorCommand(faces, PaintTool.current_color),
                SetFaceTextureCommand(faces, None),
            ]))
        vp.update()
