# IngeTrazo — modelador 3D libre

**Autor:** Marco Sumari Tellez · **Licencia:** GPL-3.0-or-later · **Repo:** `/home/sumaritux/wasia/` (directorio en disco aún se llama `wasia/`; el rename de carpeta queda para que lo haga el usuario fuera de la sesión — afecta venv. GitHub destino tentativo: `github.com/tuxiasumari/ingetrazo`)

> **Nota histórica:** este proyecto se llamó **Wasia** (quechua *wasi* = "casa") entre 2026-05-21 y 2026-05-23. Renombrado a **IngeTrazo** el 2026-05-23 para entrar al ecosistema visual de IngePresupuestos. Ver sesión 17 abajo. Extensión nativa pasó de `.wasia` a `.igz`; módulo `formats/wasia.py` a `formats/igz.py`.

Modelador 3D estilo SketchUp orientado a arquitectura, ingeniería civil e impresión 3D. Multiplataforma (Linux / Windows / macOS) sobre PySide6. Hermano open-source de [IngePresupuestos](../ingepresupuestos-pyside6/) — la integración IFC entre los dos cierra el loop modelo → metrado → presupuesto.

---

## 🧭 Visión de producto (definida 2026-05-22)

**Qué construir:** modelador 3D libre estilo SketchUp + **BIM como capa semántica opcional**, Linux-first, en español, integrado con IngePresupuestos vía IFC. No es clon de Revit ni de AutoCAD — es la herramienta que el ingeniero civil/arquitecto latinoamericano necesita para **modelar → metrar → presupuestar** sin salir del ecosistema. Mercado mal atendido hoy (AutoCAD/SketchUp/Revit no tienen Linux nativo; FreeCAD es UX dolorosa).

**Principios arquitectónicos (no negociables):**

1. **Freeform al núcleo, BIM como tagging encima.** El modelador es libertad total estilo SketchUp (dibujás una plaza, una escultura, una fachada curva — lo que quieras). El BIM **no vive en primitivas rígidas** tipo `WallTool` de Revit; vive como metadatos aplicados opcionalmente a geometría seleccionada (`IfcWall`, `IfcSlab`, `IfcColumn`, propiedades, materiales). Lo taggeado se exporta a IFC y alimenta el metrado; lo no taggeado es solo dibujo visual. Referente vivo del patrón: **BlenderBIM**.
2. **2D = Top View + Parallel + Layers, no un módulo separado.** No hay "modo 2D". La experiencia 2D *emerge* del 3D bien afinado: vista superior + proyección paralela + plano de trabajo Z=0 + capas para organizar (estructura / muros / instalaciones / mobiliario). Mismo motor, dos lecturas. Output profesional de planos (LayOut-equivalente: márgenes, sellos, escalas) se difiere a v2.
3. **Scope disciplinado.** No competir con SketchUp/AutoCAD/Revit feature-por-feature — esa es la receta para nunca shippear. Cada feature pasa por el filtro: "¿le sirve al usuario que querés que modele un edificio chico y saque cantidades?".

**Posicionamiento estratégico (secuencial, no paralelo):**

- **IngePresupuestos** = generador de caja a corto plazo. La mayor parte del tiempo va ahí mientras tracciona comercialmente.
- **IngeTrazo** = moat de largo plazo. Crece en segundo plano con scope acotado (motor sólido + IFC export mínimo) durante 12-18 meses; después se integra fuerte cuando IngePresupuestos genere flujo.
- **IFC bridge primero** (procesos separados, intercambio de archivos); embebido directo solo cuando el motor sea sólido y la licencia lo permita. El error a evitar: abandonar el producto que ya genera revenue por perseguir el sueño grande del modelador.

**Pendientes estratégicos:**

- ✅ **Rename (resuelto 2026-05-23).** Wasia → **IngeTrazo**. Verbo de oficio civil ("trazar" como acción del usuario), ritmo limpio en tres sílabas, encaja en el patrón ecosistema `Inge[X]`. Tagline planeado: *"Trazá. Metrá. Presupuestá."* Tradeoff aceptado: se pierde la identidad cultural quechua de "Wasia" a cambio de cohesión de marca con IngePresupuestos.
- ⏳ **Licencia.** GPL-3.0 actual atrapa a IngePresupuestos (closed-source) si en el futuro se quisiera embeber IngeTrazo como librería. Como nunca se distribuyó a nadie bajo GPL (sigue local en la laptop), el cambio es libre. Candidato fuerte: **Apache 2.0** — permite embeber sin acrobacia legal ni CLAs, sigue siendo OSS legítimo, suma cláusula de patentes. Decidir antes del primer push público.
- ⏳ **Rename de directorio en disco.** `/home/sumaritux/wasia/` → `/home/sumaritux/ingetrazo/`. Pendiente porque rompe el venv (paths hardcodeados); el usuario lo hace fuera de la sesión cuando le quede cómodo recrear el venv. Pasos sugeridos: `mv ~/wasia ~/ingetrazo && cd ~/ingetrazo && rm -rf venv && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`.

---

## Estado / Roadmap

### ✅ Sesión inaugural 2026-05-21
1. **Repo + esqueleto + GPL-3.0** (`ac278d6`) — carpetas en inglés, README/CONTRIBUTING/CODE_OF_CONDUCT, `.wasia` format placeholder (luego renombrado a `.igz` en sesión 17), main.py runnable con ventana vacía.
2. **Viewport: cámara orbital + grid + ejes XYZ** (`860a6b6`) — `OrbitCamera` Z-up estilo SketchUp/Blender, navegación middle-drag (orbit) / Shift+MMB-drag (pan) / wheel (zoom) / P (perspectiva ↔ paralela). Shaders `basic.vert`/`basic.frag`. **Wayland nativo OK** (vía `paintGL` que limpia explícitamente — ver `[[feedback-wayland-paintgl-explicito]]` en memoria de Claude).
3. **Tools (Line + Select) con SketchUp-style inferencing** (`1c454c9`):
   - Snap engine: endpoint, origin, close-polygon, axis_inference (auto, dentro de 3°), reference parallel/perpendicular.
   - LineTool: chain drawing + auto-close + rubber band naranja.
   - SelectTool: edge picking screen-space + Shift+click aditivo + Delete.
   - Axis lock con arrow keys: →=X, ←=Y, ↑=Z (toggle off pulsando la misma flecha).
   - **Down (↓)** = cycle parallel/perpendicular a una arista de referencia (capturada por hover).
   - **Shift** = lock contextual (locks la inferencia auto activa).
   - **VCB** (Value Control Box): tipear número → Enter → longitud exacta.
   - Camera-aware line projection (line-line closest a la ray) — Z lock funciona.
4. **Save/Open `.wasia`** (`3f7248d`, extensión luego renombrada a `.igz` en sesión 17) — JSON versioned, File menu (New/Open/Save/Save As), título dinámico con `*` dirty marker, prompt antes de descartar cambios.
5. **Undo/Redo** (`6379191`) — `core/history.py` con `Command` ABC, `History` stack, `AddEdgeCommand`/`DeleteEdgesCommand`/`AddFaceCommand`/`CompoundCommand`. Tools usan `viewport.history.execute(cmd)` siempre. Edit menu con Undo (Ctrl+Z) / Redo (Ctrl+Y o Ctrl+Shift+Z). Cada rectángulo / extrusión cuenta como **un solo paso atómico** vía `CompoundCommand`.
6. **Rectangle tool (R)** (`db41965`) — 2 clics → 4 aristas + 1 cara. Tools exponen `rubber_band_lines() -> list[(a,b)]` que el viewport renderiza genéricamente.
7. **Zoom Extents (F2) + Standard Views** (`f29224e`) — `Camera.fit_to(min,max)` + `Camera.set_view("top"/"front"/"right"/"iso"/...)` + `Scene.bounds()`. Menú View → Standard Views.
8. **Faces + Push/Pull (U)** (`e352688`) — `Face` con Newell-normal + centroide. Auto-cara cuando un polígono cierra (≥3 vértices). PushPullTool: hover → click cara → drag o VCB → commit con CompoundCommand (top edges + verticales + top face + N side faces). Render con polygon offset para no z-fighting con aristas.
9. **Adaptive work plane + VCB 3D** (`0f78087`) — `_world_from_pixel` raycastea al plano `Z = start_point.z` cuando hay tool activa con start_point a altura ≠ 0. VCB acepta `5;3;2` como delta XYZ; `LineTool.on_value` recibe float (longitud) o tuple (delta 3D).

### ✅ Sesión 2026-05-22 — hidden line removal
10. **Fix hidden line removal** — bug raíz **doble**:
    - **(a) FBO sin depth attachment**: en PySide6 6.11 + Mesa + Wayland, `QOpenGLWidget` ignora `setFormat(depthBufferSize=24)`; el default FB termina sin depth (verificado: `defaultFramebufferObject()=0`, `glReadPixels(DEPTH)` tras `glClearDepthf(0.5)` devuelve 0.0). Workaround: render en `views/viewport.py` a un `QOpenGLFramebufferObject` propio con `CombinedDepthStencil`, luego `glBlitFramebuffer` del color al default FBO del widget.
    - **(b) QPainter del overlay deshabilita `GL_DEPTH_TEST`** y el estado se hereda en el siguiente `paintGL` — confirmado con `glIsEnabled(GL_DEPTH_TEST)` que devuelve `0` en el frame 2. Por eso, aunque pongamos `glEnable(GL_DEPTH_TEST)` en `initializeGL`, las caras dejan de ocluir aristas después del primer overlay. Fix: re-establecer **todo** el estado GL relevante (`glEnable(GL_DEPTH_TEST)`, `glDepthFunc(GL_LEQUAL)`, `glDepthMask(GL_TRUE)`, `glEnable(GL_BLEND)`, blend func, `glClearDepthf(1.0)`, `glClearColor`) al inicio de cada `paintGL`.
    - Adicional: `glDepthMask(GL_FALSE)` en el grid (no debe ocluir geometría), polygon offset (1,1) sobre caras como cinturón+tirantes para aristas coplanares, request de OpenGL 3.3 Core en `main.py` + en el widget.

### ✅ Sesión 2026-05-22 (continuación) — UX de dibujo y topología
11. **HiDPI / device pixel ratio** — el FBO se creaba con tamaño lógico (`self.width()`) mientras el framebuffer del widget está en píxeles físicos (lógico × DPR). Consecuencia: el render se blitteaba al 1/DPR² del widget y el cursor quedaba desplazado del trazado. Fix: usar `int(self.width() * self.devicePixelRatioF())` para viewport, FBO y blit en `paintGL`/`resizeGL`.
12. **Adaptive work plane según cámara** — `_world_from_pixel` ahora elige el plano de trabajo según la orientación de la cámara. Si `|forward.z| ≥ sin(15°)` (top/iso/arquitectónica), plano horizontal a través de `start_point`. Si la cámara está casi al horizonte, plano vertical (XZ o YZ, el más perpendicular a la vista). Resuelve el problema de "dibujo aparente OK pero la línea cae al suelo" cuando se rota la cámara cerca del horizonte. Threshold de 15° elegido tras probar — más estricto rompía rectángulos en iso, más laxo molestaba en arquitectura.
13. **Auto-detección de polígonos** (`core/topology.py`, nuevo) — `find_smallest_cycle_through(edges, a, b)` hace BFS en el grafo de aristas (dedup por posición con tolerancia ≈ 0.1 mm) para encontrar el ciclo más chico que pasaría por la nueva arista. `is_planar` + `face_exists` filtran lo válido. `LineTool._commit_edge` lo usa: si dibujás 2 líneas que cierran un triángulo usando una arista del cubo, la cara aparece automáticamente. Estilo SketchUp.
14. **UX del snap** — tres ajustes encadenados:
    - Threshold de point snaps bajó de 12 → 9 px (estaba mushy: en aristas cortas el endpoint disparaba a lo largo de toda la línea).
    - Indicador continuo de axis lock (círculo 4×4 siguiendo el cursor) eliminado. El estado de lock se comunica sólo con el color del rubber-band, como SketchUp.
    - Con axis lock activo, `compute_snap` ahora dispara `endpoint` (cuadrado verde) cuando el cursor cae cerca de un vértice que **está sobre la línea de lock** — así podés clavarte exactamente en vértices existentes sin perder el lock.
15. **Orden de render** — ejes XYZ ahora se dibujan ANTES de las aristas del usuario, así una línea sobre el eje Z queda visible por encima del color del eje (`GL_LEQUAL` deja ganar al segundo draw en depths iguales). Rubber-band sigue al final, sin depth-test, encima de todo.

### ✅ Sesión 2026-05-22 (continuación) — face-plane inference
16. **Dibujar polígonos sobre cualquier cara** (`views/viewport.py`, `tools/rectangle.py`, `tools/line.py`) — antes el work plane sólo conocía Z=0 hasta el primer clic, así que al intentar dibujar sobre el techo de un cubo el clic caía al piso. Ahora:
    - `_current_work_plane(cursor)` hace `pick_face` bajo el cursor cuando no hay `start_point`; si pega, devuelve el plano `(centroid, normal)` de esa cara.
    - `mousePressEvent` captura ese plano en `tool.work_plane` al primer clic, así los puntos siguientes del chain se quedan coplanares (incluso en caras inclinadas o verticales).
    - `RectangleTool._corners` antes asumía XY (`z = a.z()` en los 4 corners); ahora deriva dos ejes en el plano vía `_plane_axes(normal)` (proyectando world +X sobre la cara, cross con la normal). Resultado: rectángulo se acuesta sobre cualquier orientación de cara — paredes verticales y techos inclinados incluidos. Bug previo: el rectángulo en una pared vertical colapsaba a un segmento al pie de la pared.
    - Convención para futuros tools (circle, polygon, arc, …): declarar `self.work_plane = None` + limpiar en `_reset()`; usar `_plane_axes` para derivar ejes en el plano en vez de hardcodear XY.

### ✅ Sesión 2026-05-23 — rename a IngeTrazo
17. **Wasia → IngeTrazo** (`b326c7d`) — proyecto renombrado para entrar al ecosistema visual de IngePresupuestos. Cambios:
    - Marca del producto: `Wasia` → `IngeTrazo` en README, CONTRIBUTING, CODE_OF_CONDUCT, docs/, plugins/README, i18n/{en,es}.json, todas las docstrings de paquetes (`core/`, `views/`, `tools/`, `tests/`, `formats/`), `main.py` (`setApplicationName`, `setOrganizationName`, copyright header), `views/main_window.py` (window title, dialog titles, "Quit IngeTrazo?").
    - Extensión nativa: `.wasia` → `.igz` (3 letras, las iniciales de **I**nge**G**z donde la "g" puede leerse como "geometría" o como sufijo arbitrario; se descartó `.itz` por choques con formatos legacy de InterTrust DRM aunque ambas tienen overlaps menores). Cambios en `views/main_window.py` (filter, suffix check, default name `untitled.igz`) y `formats/__init__.py`.
    - Módulo: `formats/wasia.py` → `formats/igz.py` (git mv, history preservada). Import en `views/main_window.py`: `from formats import igz as igz_format`. Constante `WASIA_FILE_FILTER` → `IGZ_FILE_FILTER`.
    - Schema JSON: clave `"wasia_format": 1` → `"igz_format": 1` en `formats/igz.py`. Sin backwards-compat (proyecto pre-release, los únicos `.wasia` que existían eran `ejemplo.wasia` y `untitled.wasia`).
    - Archivos físicos: `ejemplo.wasia` → `ejemplo.igz` (git mv). `untitled.wasia` queda como está (es WIP no-trackeado del usuario, lo renombra él si quiere). `formats/__pycache__/wasia.cpython-*.pyc` borrado.
    - **No tocado:** directorio raíz `/home/sumaritux/wasia/` (rename rompe venv; usuario lo hace fuera de sesión). Licencia (sigue GPL-3.0; decisión Apache 2.0 sigue pendiente). Cambios sin commitear de antes de sesión (`tools/line.py`, `tools/rectangle.py`, `views/viewport.py` salvo línea 122 que sí tenía referencia "Wasia") — el usuario los commitea por separado.
    - Verificación: `python -c "from formats import igz; from views.main_window import MainWindow, IGZ_FILE_FILTER; import main"` retorna OK con filter `"IngeTrazo document (*.igz);;All files (*)"`.

### 🐛 Conocidos sin resolver
- **Fan triangulation rompe para polígonos cóncavos** — funciona para rectángulos y convexos. Una L o cualquier no-convexo se triangula mal. Solución: ear-clipping.
- **Sin face culling** — ambos lados de cada cara se renderizan con el mismo color crema. Front/back vs SketchUp: front cream, back azul-grisáceo. Pendiente.
- **Sin merge de geometría coincidente** — dos rectángulos que comparten arista crean aristas duplicadas. SketchUp auto-suelda.
- **Auto-polígono encuentra UN solo ciclo** — si una arista cierra múltiples polígonos (clásico: diagonal en cuadrado → 2 triángulos), sólo crea uno (el primero que BFS encuentra). SketchUp crea ambos.
- **Polígono nuevo dentro de cara existente no la divide** — al dibujar un cuadradito interno en la cara superior de un cubo, se crea la cara nueva pero la grande sigue intacta debajo (dos caras coplanares). Falta split de face: detectar que el ciclo nuevo está contenido en una cara existente y restarlo / triangular el "donut" resultante.

### 🚧 Próxima sesión — prioridades (decididas 2026-05-22, reconfirmadas 2026-05-22)
**Refinar lo que ya existe ANTES de tools nuevas.** El motor básico es ~70%, pero la calidad de la selección y el manejo de polígonos define la experiencia. **Confirmado en la conversación de visión:** afinar el motor es el foco hasta nuevo aviso; el rename, el cambio de licencia y la capa BIM/IFC entran *después* de que el modelado básico esté sólido.
1. **Pulir auto-detección de polígonos** — detectar TODOS los ciclos chicos que cierra una arista nueva (no sólo el primero), evitar caras duplicadas con orientación opuesta, y ear-clipping para soportar cóncavos.
2. **Split de cara existente** — cuando un ciclo nuevo (polígono dibujado encima) está contenido dentro de una cara, restar la cara grande / triangular el agujero. Ya se puede dibujar sobre cualquier cara (face-plane inference resuelto en `[[project-face-plane-inference-done]]`); el siguiente paso natural es que el polígono interno divida la cara madre como hace SketchUp.
3. **Selección sólida** — hoy sólo se seleccionan aristas. Falta: seleccionar caras (con click sobre la cara), seleccionar al hacer rubber-band con drag, double-click para seleccionar todo lo conectado, triple-click para todo el sólido, hover highlighting.
4. **Después de eso** sigue Move/Rotate/Erase + auto-merge + face culling (en ese orden).

### 🔮 Roadmap v0.1 (versión inicial usable real)
Orden sugerido alineado con la visión (freeform + BIM tagging + 2D que emerge del 3D):
- **Groups / Components** — encapsulación de geometría reutilizable.
- **Tape Measure + Guide Lines** — líneas de construcción que no son geometría real. Crítico para flujo arquitectónico.
- **Layers / Tags** — visibilidad / lock por capa. **Habilita el flujo 2D** (Top + Parallel + Layers = experiencia 2D sin módulo separado).
- **Dimensions tool** — cotas entre dos puntos que se actualizan con la geometría. Imprescindible para planos imprimibles.
- **Text labels** — anotaciones en plano ("DORMITORIO", "ESC. 1:50", etc.).
- **Materials** — color sólido + textura por cara.
- **BIM tagging layer** — panel de propiedades para marcar geometría seleccionada como `IfcWall` / `IfcSlab` / `IfcColumn` / etc. con propiedades. Es la *única* capa "BIM-aware"; el modelador sigue siendo freeform.
- **IFC export** (basado en tags, no en primitivas) — gancho clave con IngePresupuestos. Sólo lo taggeado va al IFC.
- **IFC import** — para abrir modelos externos.
- **DWG / DXF I/O** — convivir con clientes/colegas que usan AutoCAD; no para competir contra él.
- **STL / 3MF export** — para impresión 3D.
- **Geo-referenciación** — terreno DEM + ortofoto. Carpeta `georef/` ya esqueleteada.
- **Plugin system público** — el patrón `Tool` + auto-discovery en `plugins/` ya está armado, falta documentar y publicar API.
- **Sistema de licencia y release** — portear desde IngePresupuestos: `core/update_manager.py`, `release.sh`, GitHub Actions, distribución vía R2.

**Diferido a v2:** generación profesional de planos (LayOut-equivalente: márgenes, sello, escala, múltiples vistas por hoja, leyendas). Por ahora, exportar la vista actual como SVG/PDF cubre el 80% del uso casual.

---

## Stack

| Capa | Librería |
|------|----------|
| UI | **PySide6 6.11** (Qt 6) — la única dep "GUI" |
| Render 3D | **QOpenGLShaderProgram + QOpenGLBuffer + QOpenGLVertexArrayObject** bundleados en PySide6. `moderngl` planeado pero **NO instalado todavía** (glcontext requiere `libx11-dev` para compilar en Python 3.14 — pendiente apt install) |
| Math 3D | **QMatrix4x4 + QVector3D + QVector4D** de QtGui (NO numpy, NO pyrr) |
| Empaquetado de vértices | **`array` stdlib** (sin numpy) |
| Snap fuzzy / inference | propio en `core/snap.py` |

**Sin** numpy, ifcopenshell, trimesh, manifold3d, pyassimp aún — esos llegan cuando se necesiten (probablemente IFC el primero).

```bash
cd /home/sumaritux/wasia    # directorio en disco aún se llama wasia/; rename pendiente
source venv/bin/activate
python main.py
```

Python 3.14.4 · venv local en `/home/sumaritux/wasia/venv/` (gitignored).

---

## Portabilidad / Plataformas ARM

Stack diseñado portable, pero hay matices por plataforma — anotados acá para no re-investigar:

- **Linux ARM64** (Asahi Linux en Mac M-series, Pinebook Pro, Raspberry Pi 4/5, Snapdragon X Elite con Linux): ✅ funciona directo. Mesa OpenGL es maduro en aarch64. Los workarounds Wayland (FBO propio con depth, reset de estado GL después de QPainter) protegen igual — el bug raíz es del combo PySide6/Mesa, no específico de x86.
- **macOS Apple Silicon** (M1/M2/M3/M4): ✅ funciona hoy. PySide6 6.5+ tiene wheels nativos arm64. ⚠️ Apple deprecó OpenGL desde macOS 10.14; sigue funcionando vía Qt RHI (que traduce OpenGL → Metal internamente) o la implementación legacy de Apple. Sin signal inmediato de remoción pero hay riesgo a años vista. Mitigación futura: Qt tiene path automático a Metal cuando suceda, sin requerir cambios en código nuestro.
- **Windows ARM64** (Surface Pro X, Snapdragon X Elite): ⚠️ Qt 6.8+ tiene wheels oficiales arm64, pero ecosistema menos pulido. Probable fricción con deps C nativas. Baja prioridad para testing — público chico, no es ROI mientras seamos solo dev.

**Python 3.14 + ARM = la fricción real más probable.** Las wheels de deps nativas grandes (`ifcopenshell`, `manifold3d`, `pyassimp`) suelen lagear en versiones nuevas de Python. Cuando integremos IFC en serio, plan de contingencia: declarar **Python 3.13 como "versión de referencia para wheels ARM"** mientras 3.14 madura su ecosistema. `ifcopenshell` es históricamente el más lento en publicar wheels para combinaciones nuevas — vigilar ese específicamente.

El código propio está limpio de asunciones x86: shaders GLSL 3.30 Core, math vía QtGui (sin numpy ni intrinsics), sin syscalls específicos de plataforma. La portabilidad real depende de wheels de terceros, no del motor.

---

## Arquitectura

```
ingetrazo/                     ← nombre lógico del proyecto; carpeta en disco sigue siendo `wasia/`
├── main.py                    ← entry point Qt
├── CLAUDE.md                  ← este archivo
├── LICENSE                    ← GPL-3.0 verbatim
├── README.md / CONTRIBUTING.md / CODE_OF_CONDUCT.md
├── core/
│   ├── camera.py              ← OrbitCamera (Z-up, lookAt, perspective/ortho, fit_to, set_view)
│   ├── geometry.py            ← Edge (eq=False, identity-hashable) + Face (Newell normal + centroid)
│   ├── scene.py               ← Scene (edges, faces, selection, version, bounds)
│   ├── snap.py                ← compute_snap(...) — 7 tipos de snap con resolver callbacks
│   └── history.py             ← Command ABC + History (undo/redo) + Add/DeleteEdge/AddFace/Compound
├── views/
│   ├── main_window.py         ← QMainWindow + menús (File/Edit/View/Tools) + toolbar + status bar
│   └── viewport.py            ← QOpenGLWidget — render + paintGL + tools dispatch + VCB + overlays
├── tools/
│   ├── base.py                ← Tool ABC + ToolContext (viewport, world, screen, modifiers, snap)
│   ├── select.py              ← SelectTool (pick edge + Shift-add + Delete)
│   ├── line.py                ← LineTool (chain + auto-close + VCB float/tuple)
│   ├── rectangle.py           ← RectangleTool (4 edges + 1 face CompoundCommand)
│   └── pushpull.py            ← PushPullTool (face hover + drag → extrude)
├── formats/
│   └── igz.py                 ← save_scene / load_into (JSON `.igz`, schema versionado)
├── plugins/                   ← carpeta para complementos de terceros (vacía + README)
├── georef/                    ← stubs para tiles/DEM/projections (a llenar)
├── resources/
│   ├── shaders/basic.vert + basic.frag
│   ├── icons/ (vacío)
│   ├── fonts/ (vacío — usaremos Inter cuando importemos)
│   └── styles/main.qss (comentado)
├── i18n/
│   ├── en.json
│   └── es.json
├── docs/
│   ├── architecture.md
│   ├── plugins.md
│   └── development.md
├── tests/
└── .github/workflows/ (vacío)
```

---

## Convenciones (NO romper)

- **Idioma**: TODO el código, comentarios, docstrings, commit messages y nombres de carpeta en **inglés** (decidido en sesión inaugural para atraer contributors). UI bilingüe via `i18n/{en,es}.json`. Es **lo opuesto a IngePresupuestos** (que es 100% español por ser closed-source).
- **Z-up**: convención SketchUp/Blender/FreeCAD/CAD. X rojo (este), Y verde (norte), Z azul (vertical). NO mezclar con Y-up de juegos.
- **Identity-equal entities**: `@dataclass(eq=False)` en Edge y Face. Esto las hace hashables (set/dict OK) y dos instancias con mismos valores se tratan como distintas. La selection set se llena con referencias.
- **Toda mutación pasa por Command**: nunca llamar `scene.edges.append(...)` directo desde un tool. Usá `viewport.history.execute(AddEdgeCommand(...))`. Así undo/redo siempre funciona.
- **Tools heredan de `tools.base.Tool`**: implementan al menos `on_activate`/`on_deactivate`. Spatial tools sobrescriben `on_click`/`on_hover`/`on_cancel`/`on_value` recibiendo `ToolContext`. Para preview gráfico, override `rubber_band_lines()` devolviendo lista de segmentos. Para label flotante custom, override `value_label() -> (text, world_pos)`.
- **Cualquier `QOpenGLWidget` debe `glClear` en `paintGL`** — Wayland es estricto, no perdona buffers no inicializados. Ver memoria `[[feedback-wayland-paintgl-explicito]]`.
- **`QMatrix4x4 * QVector4D` no está bindeado** en PySide6 6.11 — usar `mvp.map(QVector4D(x,y,z,1))`. Ver memoria `[[feedback-pyside6-matrix-vector-mul]]`.

---

## Gotchas críticos descubiertos

- **Z lock pre-refactor**: proyectar candidate (que venía del raycast Z=0) sobre el eje Z daba el mismo `start_point`. Fix: `_project_to_lock_line` con closest-point line-to-ray usando el rayo de la cámara (`views/viewport.py`). Mismo fix vale para reference lock con dirección 3D.
- **Adaptive work plane** (Fix 1 de la sesión 9): sin esto, después de subir con Z lock no podías dibujar al nivel del techo — el cursor caía al suelo. Solución: `_current_work_plane_z()` que usa `start_point.z()` cuando hay tool activa.
- **Polygon offset** activado solo para faces (`GL_POLYGON_OFFSET_FILL` con factor 1, units 1) — empuja las caras "atrás" en depth para que aristas coincidentes se vean limpias encima. Combinado con `glDepthFunc(GL_LEQUAL)` cubre todos los casos de aristas coplanares con caras.
- **Rubber band depth-test off**: el rubber-band naranja se pinta SIEMPRE encima de cualquier cosa, sin importar profundidad. Lo logramos con `glDisable(GL_DEPTH_TEST)` antes del draw, `glEnable` después.
- **QOpenGLWidget sin depth buffer real**: en esta combinación PySide6/Mesa/Wayland, el default FB del widget llega sin depth attachment aunque `setFormat(depthBufferSize=24)` y `context().format()` mientan diciendo que sí lo tiene. Por eso `Viewport.paintGL` renderea a su propio `QOpenGLFramebufferObject` (creado en `_ensure_scene_fbo` con `CombinedDepthStencil`) y blittea el color al final. **No tocar este flujo sin verificar que el depth buffer sobreviva** — la regresión es silenciosa: la app sigue funcionando, sólo se rompe la oclusión.
- **QPainter contamina el estado GL** entre frames. Cada `paintGL` debe re-establecer `GL_DEPTH_TEST`, `glDepthFunc`, `glDepthMask`, `GL_BLEND`, blend func y clear color/depth. No alcanza con setearlos una vez en `initializeGL`. La regresión típica es: hidden-line removal funciona en el primer frame y se rompe en todos los siguientes.

---

## Tests + CI

- `tests/` existe pero está vacía. Pytest planeado, sin tests escritos aún.
- GitHub Actions en `.github/workflows/` vacío (pendiente de portar el setup desde IngePresupuestos cuando empecemos a empaquetar releases).

---

## Memorias de Claude relacionadas

**De este proyecto** (`~/.claude/projects/-home-sumaritux-wasia/memory/` — directorio aún con el nombre viejo "wasia"):

- `project_face_plane_inference_done.md` — convención para tools nuevos: leer `tool.work_plane` y usar `_plane_axes(normal)` en vez de hardcodear XY.

**Del proyecto hermano IngePresupuestos** (`~/.claude/projects/-home-sumaritux-ingepresupuestos-pyside6/memory/`):

- `project_wasia_iniciado.md` — decisiones estratégicas originales del proyecto cuando aún se llamaba Wasia (GPL-3.0, idioma inglés, monetización via integración con IngePresupuestos). Nombre del archivo histórico; el contenido sigue aplicando a IngeTrazo.
- `feedback_wayland_paintgl_explicito.md` — Wayland exige `glClear` en `paintGL`.
- `feedback_pyside6_matrix_vector_mul.md` — `QMatrix4x4 * QVector4D` no bindea; usar `.map()`.
