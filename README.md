# Wasia

**Free and open 3D modeler for architects, civil engineers, and 3D-printing enthusiasts.**

Wasia (Quechua *wasi* = "house" + *IA* = artificial intelligence) is a SketchUp-inspired 3D modeler built natively for Linux, Windows and macOS. It aims to combine an approachable push/pull workflow with georeferenced modeling and 3D-printing-ready outputs.

## Status

Early development. Not yet usable end-to-end.

## Goals

- **SketchUp-like UX** — push/pull, inferencing, orbit camera, groups, layers, perspective + parallel views.
- **Native cross-platform** — Linux, Windows, macOS.
- **Free Software** — GPL-3.0-or-later.
- **Geo-referenced modeling** — real-world terrain (DEM) and satellite tiles.
- **3D-printing ready** — STL / 3MF export with mesh validation (manifold checks, wall thickness, overhangs).
- **Plugin system from day one** — write your own tools in plain Python.
- **IFC import / export** — integration with [IngePresupuestos](https://ingepresupuestos.com) for quantity takeoff (BIM 5D).
- **Bilingual UI** — Spanish and English from the start; more languages welcome.

## Stack

| Layer | Library |
|-------|---------|
| UI | PySide6 (Qt 6) |
| 3D rendering | ModernGL (OpenGL 3.3+) |
| Math | NumPy + pyrr |
| Mesh ops | trimesh |
| Boolean ops | manifold3d |
| Format import | pyassimp |
| BIM | ifcopenshell |
| Geo | pyproj, mercantile, rasterio |

## Quick start (developers)

```bash
git clone https://github.com/<your-user>/wasia.git
cd wasia
python3 -m venv venv
source venv/bin/activate          # Linux / macOS
# .\venv\Scripts\activate         # Windows
pip install -r requirements.txt
python main.py
```

Requires Python 3.11+.

## Contributing

We welcome contributors from anywhere. See [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

Issues tagged `good first issue` are a great starting point.

## License

[GPL-3.0-or-later](LICENSE) — same family as Blender, FreeCAD and PrusaSlicer. You are free to use, study, modify and redistribute Wasia, provided derivative works remain under the same license.

## Author

Marco Sumari Tellez — Civil Engineer, Lima, Peru.

---

## En español

Wasia es un modelador 3D libre estilo SketchUp para arquitectura, ingeniería civil e impresión 3D. Multiplataforma, hecho en Perú, comunidad abierta. Más información en [docs/](docs/).
