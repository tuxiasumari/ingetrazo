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
from core.history import SetFaceColorCommand
from tools.base import Tool, ToolContext

# The default cream the viewport paints unpainted faces with — sampling an
# unpainted face yields this, and it is what "no colour" reads as.
DEFAULT_FACE_COLOR = (0.92, 0.89, 0.81)


class PaintTool(Tool):
    name = "Paint"
    shortcut = "B"
    uses_snap = False  # picks a face to paint; no snap markers

    # Shared current paint colour (RGB, 0..1), set from the toolbar swatch.
    current_color: tuple[float, float, float] = (0.80, 0.45, 0.30)

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
            # Eyedropper: adopt the face's colour as the current paint colour.
            sampled = face.attrs.get("color")
            PaintTool.current_color = (tuple(sampled) if sampled is not None
                                       else DEFAULT_FACE_COLOR)
            vp.update()
            return

        # Paint the clicked face — or, if it is part of the current face
        # selection, the whole selection in one undoable step.
        sel_faces = [e for e in vp.scene.selection if isinstance(e, Face)]
        faces = sel_faces if face in sel_faces else [face]
        vp.history.execute(SetFaceColorCommand(faces, PaintTool.current_color))
        vp.update()
