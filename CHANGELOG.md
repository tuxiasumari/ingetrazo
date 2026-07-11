# Changelog

All notable changes to IngeTrazo are documented here.
Format inspired by [Keep a Changelog](https://keepachangelog.com); versions
follow [SemVer](https://semver.org).

## [0.1.0] — 2026-07-11

The first release. A usable, free, Linux-first SketchUp-style 3D modeler for
civil engineering and architecture — draw → model → tag → take off → export.

### Modeling engine
- Shared-vertex non-manifold topology engine (SketchUp's model): sticky
  geometry, automatic welding, face detection, planar-arrangement rebuilds.
- Push/Pull with the full solid pipeline: recess, steps, through-holes,
  clamps, distance inference, Ctrl = copy, double-click repeats — and the
  **BIM-grade watertightness guard**: the engine never commits a broken
  solid (ambiguous operations are refused safely, and told to the user).
- Robust curve entities: circles, polygons, 4 arc types; curves select as
  whole contours, split at intersections, survive copy/paste/offset/groups.
- Deterministic intersections: circle×line, circle×circle, rect×rect split
  into proper regions — on flat drawings, next to solids, and on solid faces.
- Transactional command history: any internal failure rolls back to the
  exact previous state, tells the user, and logs to `ingetrazo-errors.log`.
- Fuzz-tested: 1000 seeded operation sequences with structural invariants
  (watertightness, orientation, undo fidelity) — 996 clean, 4 known-hard
  frozen as expected failures.

### Tools
- Draw: Line, Rectangle, Rotated Rectangle, Circle, Polygon, Arc (2-point,
  3-point, centre+angle), Offset, Follow Me (profile swept along a path,
  mitred corners, closed paths weld into lathes).
- Transform: Move, Rotate (protractor), Scale (anchor + factor, negative
  mirrors) — live previews, exact snapshots undo, autofold.
- Select: click (curves/surfaces as wholes), double-click (face + edges),
  triple-click (whole connected solid), window/crossing box, Select All.
- Annotate: Tape Measure with construction guides, Protractor (angled
  guides), Dimensions with styles, terrain profile for geo paths.
- Eraser (click + stroke), Paint with materials, escalating Esc.

### Materials, layers, groups
- Categorised texture library (22 procedural, seamlessly tileable,
  licence-clean textures across 9 civil categories) painted at real-world
  tile size; edit width/height/rotation of any texture, undoably.
- Layers/tags with visibility and locking — top view + parallel projection
  + layers = the plan drawing, no separate 2D module.
- Groups: isolated geometry, edit-inside context (double-click in),
  cross-context undo correctness, face-me billboards.

### BIM (the thesis)
- Tag any faces or group as an IFC object (15 curated classes) — metadata
  over freeform geometry, never rigid primitives.
- Live quantities per object: area always, volume only when watertight.
- Takeoff CSV export — the bridge to IngePresupuestos today.
- **IFC4 export**, hand-written STEP (zero dependencies): spatial skeleton,
  real IFC classes, faceted BRep geometry, BaseQuantities in the file.

### Georeferencing (Track G)
- Local datum + UTM conversion; satellite base maps (Esri/Sentinel-2/custom
  XYZ) with area-limited capture; 3D draped terrain from free global DEM;
  geo paths with longitudinal profiles (stations, slopes, CSV/PNG export);
  KML/GeoJSON import.

### Interchange
- Native `.igz` documents (JSON, versioned).
- Import: COLLADA `.dae` (SketchUp exports, components, Y-up/inches
  conversion), OBJ (+MTL colours), KML/GeoJSON.
- Export: IFC4, STL (3D printing), OBJ (+MTL, textures with UVs).

### Experience
- Bilingual UI (English source, full Spanish), SketchUp-style movable
  icon toolbars, QGIS-style panels (Properties | BIM | Georef tabs),
  sky/ground horizon, paper-white maquette shading with face culling,
  infinite dashed axes.
- Scale figure: the author himself (1.65 m) as a face-me billboard cutout,
  plus generic 2D/3D people, tree, bush, car components — and "insert your
  own transparent PNG at real height".
- Desktop launcher + icon installer (`scripts/install_desktop.sh`);
  the icon is the author's mark: his tri-blade wrapped around the cube.

[0.1.0]: https://github.com/tuxiasumari/ingetrazo/releases/tag/v0.1.0
