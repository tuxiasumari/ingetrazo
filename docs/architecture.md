# Wasia architecture

This is an early-stage document. Expect it to grow as the codebase does.

## High-level layout

```
wasia/
├── main.py            ← Qt application entry point
├── core/              ← scene graph, camera, geometry primitives, layers
├── views/             ← Qt widgets: main window, viewport, panels
├── tools/             ← built-in modeling tools (line, rectangle, push/pull, ...)
├── plugins/           ← third-party tools, discovered at startup
├── georef/            ← real-world location: tiles, DEM, projections
├── styles/            ← visual style presets (shader modes)
├── materials/         ← material library and editor
├── analysis/          ← 3D-printing checks (manifold, thickness, overhangs)
├── formats/           ← import / export for OBJ, COLLADA, glTF, STL, 3MF, IFC
├── i18n/              ← UI translations (en, es, ...)
├── resources/         ← shaders, icons, fonts, stylesheets
└── tests/             ← automated tests
```

## Rendering pipeline

The 3D viewport uses **QOpenGLWidget** (PySide6) as the Qt-managed surface and **ModernGL** as the Python-friendly wrapper around OpenGL 3.3+. Shaders live in `resources/shaders/` and are loaded by the `styles/` modules.

## Scene graph

To be documented.

## Plugin system

See [plugins.md](plugins.md).
