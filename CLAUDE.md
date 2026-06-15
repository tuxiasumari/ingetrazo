# IngeTrazo — modelador 3D libre

**Autor:** Marco Sumari Tellez · **Licencia:** GPL-3.0-or-later · **Repo:** `/home/sumaritux/ingetrazo/` (rename de carpeta `wasia/` → `ingetrazo/` hecho, venv recreado y funcional. GitHub destino tentativo: `github.com/tuxiasumari/ingetrazo`)

> **Nota histórica:** este proyecto se llamó **Wasia** (quechua *wasi* = "casa") entre 2026-05-21 y 2026-05-23. Renombrado a **IngeTrazo** el 2026-05-23 para entrar al ecosistema visual de IngePresupuestos. Ver sesión 17 abajo. Extensión nativa pasó de `.wasia` a `.igz`; módulo `formats/wasia.py` a `formats/igz.py`.

Modelador 3D estilo SketchUp orientado a arquitectura, ingeniería civil e impresión 3D. Multiplataforma (Linux / Windows / macOS) sobre PySide6. Hermano open-source de [IngePresupuestos](../ingepresupuestos-pyside6/) — la integración IFC entre los dos cierra el loop modelo → metrado → presupuesto.

---

## 🧭 Visión de producto (definida 2026-05-22)

**Qué construir:** modelador 3D libre estilo SketchUp + **BIM como capa semántica opcional**, Linux-first, en español, integrado con IngePresupuestos vía IFC. No es clon de Revit ni de AutoCAD — es la herramienta que el ingeniero civil/arquitecto latinoamericano necesita para **modelar → metrar → presupuestar** sin salir del ecosistema. Mercado mal atendido hoy (AutoCAD/SketchUp/Revit no tienen Linux nativo; FreeCAD es UX dolorosa).

**Principios arquitectónicos (no negociables):**

1. **Freeform al núcleo, BIM como tagging encima.** El modelador es libertad total estilo SketchUp (dibujás una plaza, una escultura, una fachada curva — lo que quieras). El BIM **no vive en primitivas rígidas** tipo `WallTool` de Revit; vive como metadatos aplicados opcionalmente a geometría seleccionada (`IfcWall`, `IfcSlab`, `IfcColumn`, propiedades, materiales). Lo taggeado se exporta a IFC y alimenta el metrado; lo no taggeado es solo dibujo visual. Referente vivo del patrón: **BlenderBIM**.
2. **2D = Top View + Parallel + Layers, no un módulo separado.** No hay "modo 2D". La experiencia 2D *emerge* del 3D bien afinado: vista superior + proyección paralela + plano de trabajo Z=0 + capas para organizar (estructura / muros / instalaciones / mobiliario). Mismo motor, dos lecturas. Output profesional de planos (LayOut-equivalente: márgenes, sellos, escalas) se difiere a v2.
3. **Scope disciplinado.** No competir con SketchUp/AutoCAD/Revit feature-por-feature — esa es la receta para nunca shippear. Cada feature pasa por el filtro: "¿le sirve al usuario que querés que modele un edificio chico y saque cantidades?".

### 🎯 Filosofía central — el norte que NO se negocia (definida 2026-06-05)

**El nombre ES la tesis: _trazar como en la vida real_.** IngeTrazo se usa como quien dibuja a mano, no como un CAD. Si una decisión de UX o feature mete complejidad de CAD y se aleja de "esto se siente como trazar a mano", es la decisión equivocada. Este es el filtro maestro sobre todos los demás.

**El flujo unificado es el producto** (así trabaja un ingeniero de verdad, en *un solo* entorno, no con 2D-aparte que es la forma vieja):

> **fotogrametría del terreno (dron/Agisoft) → georeferenciar → trazar encima → aplicar BIM a lo trazado**

El 2D separado quedó en el pasado; por eso el referente es SketchUp y no AutoCAD. El valor de IngeTrazo no es "otro modelador" — es *ese flujo completo sin salir del programa*, cerrando después el loop con IngePresupuestos vía IFC.

**Implicación de arquitectura (tenerla en la cabeza desde la Fase 1):** la `Scene` sostiene objetos **heterogéneos**, no "todo es geometría editable": (1) malla de referencia (fotogrametría, display-only, NO entra al motor de topología), (2) contexto georeferenciado, (3) geometría freeform editable (el motor de topología), (4) tags BIM encima de lo trazado. No hay que construir las cuatro ya, pero el diseño del `Scene` no debe cerrarse esa puerta (mismo criterio que el índice espacial). Ver `[[project-filosofia-trazo-flujo-unificado]]`.

**Recordatorio operativo:** Claude debe re-anclar al usuario a esta filosofía cuando una idea empiece a desviarse (escenarios "grandes/futuros" tipo Vulkan, ray tracing, AI-PCs, mallas pesadas son válidos pero son *display/import de referencia* — fáciles —, NO el motor de topología editable que es lo difícil y lo que define si IngeTrazo existe). Los cimientos (Fase 0 → Fase 1) van antes que el techo brillante.

**Posicionamiento estratégico (secuencial, no paralelo):**

- **IngePresupuestos** = generador de caja a corto plazo. La mayor parte del tiempo va ahí mientras tracciona comercialmente.
- **IngeTrazo** = moat de largo plazo. Crece en segundo plano con scope acotado (motor sólido + IFC export mínimo) durante 12-18 meses; después se integra fuerte cuando IngePresupuestos genere flujo.
- **IFC bridge primero** (procesos separados, intercambio de archivos); embebido directo solo cuando el motor sea sólido y la licencia lo permita. El error a evitar: abandonar el producto que ya genera revenue por perseguir el sueño grande del modelador.

**Pendientes estratégicos:**

- ✅ **Rename (resuelto 2026-05-23).** Wasia → **IngeTrazo**. Verbo de oficio civil ("trazar" como acción del usuario), ritmo limpio en tres sílabas, encaja en el patrón ecosistema `Inge[X]`. Tagline planeado: *"Trazá. Metrá. Presupuestá."* Tradeoff aceptado: se pierde la identidad cultural quechua de "Wasia" a cambio de cohesión de marca con IngePresupuestos.
- ⚠️ **Licencia — DECISIÓN AHORA VENCIDA (anotado 2026-06-14).** El supuesto "nunca se distribuyó, decidir antes del primer push público" **quedó desfasado: el repo YA es público** — `origin/main` existe en `github.com/tuxiasumari/ingetrazo` con toda la historia pusheada (47 commits subidos el 2026-06-14). O sea ya se publicó **bajo GPL-3.0**. Implicancia: el cambio a Apache 2.0 ya **no es libre/silencioso** — pasar de GPL→Apache requiere consentimiento de todo contribuidor cuyo código esté en la historia (hoy solo el autor + commits co-firmados por Claude, así que sigue siendo factible, pero hay que hacerlo deliberadamente, no asumir que "nunca salió"). GPL-3.0 sigue atrapando el embebido futuro en IngePresupuestos (closed-source). **Resolver pronto** si se quiere el embebido: o se decide Apache 2.0 y se re-licencia explícitamente (relicense commit + actualizar headers/LICENSE), o se acepta GPL y el puente con IngePresupuestos queda solo vía intercambio de archivos (procesos separados), nunca como librería embebida. Candidato sigue **Apache 2.0** (embebido sin CLAs, cláusula de patentes).
- ✅ **Rename de directorio en disco (hecho).** `/home/sumaritux/wasia/` → `/home/sumaritux/ingetrazo/`, venv recreado y la suite corre.

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
> Los 4 bugs de topología que vivían acá (fan-triangulation cóncava, sin auto-merge, un-solo-ciclo, no divide la madre) quedaron resueltos en la Fase 1 — ver commits `e778a72`..`c48e012`. Lo que sigue abierto:
- **Sin face culling** — ambos lados de cada cara se renderizan con el mismo color crema. Front/back vs SketchUp: front cream, back azul-grisáceo. Pendiente.
- ✅ **Push/pull "pasante" (through)** — resuelto (sesión 2026-06-08).
- ✅ **Solapamiento colineal / T-junctions sin soldar** — resuelto (sesión 2026-06-09) vía `prune_collinear_orphan_edges` + `resolve_tjunctions` en el pase de heal. Ver "Robustez de topología de plantas" arriba.

### 🚧 Programación secuenciada hacia v0.1 (definida 2026-06-05)

**Objetivo:** un "SketchUp-mínimo para Linux" usable por un ingeniero civil — NO un clon feature-por-feature (eso son años; esto son meses, ya vamos ~60-70% del MVP de modelado). El motor freeform standalone se mantiene (NO migrar a Blender: su UX no da el "feel" SketchUp y su GPL mata la integración embebida futura con IngePresupuestos). "Abrir modelos de SketchUp" se resuelve con import `.dae`/`.obj`, sin Blender.

**Regla de oro (no negociable):** una fase NO está terminada hasta cumplir las 3: (1) su Definición de Hecho (DoD) pasa, (2) está commiteada y la app arranca sin regresiones, (3) cero "lo dejo para después" dentro de la fase. No se abre la fase siguiente hasta esas tres. Nada de avanzar algo a medias y empezar otra cosa.

**FASE 0 — Limpieza de arranque** *(~0.5 día)*
Commitear los cambios sueltos (`tools/line.py`, `tools/rectangle.py`, `views/viewport.py`) + actualizar CLAUDE.md (rename de carpeta ya hecho, venv reparado el 2026-06-05).
- **DoD:** `git status` limpio, CLAUDE.md refleja la realidad.

**✅ FASE 1 — Motor de topología robusto** *(cerrada 2026-06-06; era la incógnita go/no-go)*
Los 5 sub-pasos hechos con tests (`tests/` pasó de vacía a ~99 tests): (1) auto-merge de vértices/aristas coincidentes, (2) split de aristas al cruzarse, (3) split de cara cuando un ciclo nuevo cae dentro, (4) detectar TODOS los ciclos + chord-split, (5) ear-clipping para cóncavos. Triangulación con huecos robusta vía port de **earcut** (`core/triangulate.py`). Motor de comandos de edición en `core/edits.py`; helpers de topología (intersección, contención, chord/chain split, clasificación de aristas) en `core/topology.py`.
- **DoD (pasa):** diagonal en cuadrado → 2 triángulos · cuadradito dentro de cara → divide la madre · 2 rects que comparten arista → 1 arista · "L" cóncava se rellena · push/pull sobre todas. Reemplaza los 4 bugs de topología de "Conocidos sin resolver".
- **Bonus de la misma sesión (más allá del DoD):** push/pull **sustractivo y solid-aware** — empujar hacia adentro talla un rebaje (recess) o **corta una grada** rebajando las paredes laterales adyacentes; limpia aristas colgantes y parte las verticales al nivel del corte. Ver `tools/pushpull.py` + commits `40be03a`, `67598c4`, `c551a5a`, `c48e012`.

**FASE 2 — Selección sólida** *(el "feel" SketchUp)*
Seleccionar caras (click), hover highlight, rubber-band con drag (ventana vs crossing), doble-click=conectado, triple-click=sólido.
- 📌 Checkpoint perf: dejar el pick detrás de una abstracción para poder insertar un índice espacial luego sin reescribir (NO construir el índice todavía).
- **DoD:** aristas y caras seleccionables con los 4 gestos + hover, integrado con undo.

**FASE 3 — Move + Eraser** *(de tablero a modelador; el salto de sensación más grande)*
Move (M) con snap/inferencia/VCB + copia con Ctrl; Eraser (E) por click y arrastre.
- **DoD:** muevo y borro con medida exacta y snap; la topología de Fase 1 aguanta los movimientos sin romper caras.

**FASE 4 — Kit de dibujo completo** *(casi completo)*
Circle (C), Arc (A), Offset (F), Tape Measure + guías (T). Usar `tool.work_plane` + `plane_axes` (ver `[[project-face-plane-inference-done]]`).
- ✅ **Offset (F)** hecho (`tools/offset.py` + `offset_loop`; push del anillo levanta muros con espesor).
- ✅ **Circle (C) + Polygon (G) + Rotated Rect (K) + Arc (A) + 3-Point Arc (J) HECHO (2026-06-14).** `tools/circle.py` (`_RadialTool` base: centro+radio → N-gon con un vértice hacia el cursor, VCB radio; **nº de lados = número tipeado ANTES del centro**, estilo SketchUp; `CircleTool` 24, `PolygonTool` 6), `tools/rotated_rectangle.py` (3 clics: esquina→arista base→ancho, VCB ancho), `tools/arc.py` (`ArcTool` 2 puntos+bulge, `ThreePointArcTool` 3 puntos por los que pasa el arco; circumcentro 2D, polilínea 16 seg, auto-face si cierra). Todos sobre `work_plane` vía `plane_axes`, rubber-band + value_label.
  - **Aristas soft (`Edge.soft`, slot nuevo en capture/restore + `.igz`):** el render del viewport **omite** las aristas soft. **La tool NO marca soft** — un círculo/arco dibujado **muestra su contorno** (24 segmentos leen redondo). Lo único que se esconde son las **facetas de una curva barrida**, y lo hace el push/pull.
  - **Cilindro liso (sin flag de curva, por diedro):** `pushpull._mutate` → tras la mutación, `_soften_curve_facets` marca soft toda arista **nueva** entre dos caras con **diedro chico** (`cos > 0.85` ≈ 31.8°, no coplanar) — las costuras verticales del costado de una curva. Así **círculo extruido = cilindro liso pero con el contorno (círculo arriba/abajo) VISIBLE**, polígono (lados a ≥45°) = prisma facetado con aristas visibles. Auto-gateado por el diedro (caja a 90° intacta); topología sin tocar (solo flag de render). Dentro del `SnapshotMutation` → undo/redo conservan el soft.
  - **UX de lados estilo SketchUp:** el campo VCB (abajo-derecha) muestra **"Lados"** antes del centro y **"Radio"** después (`_RadialTool.vcb_caption()`; `main_window._refresh_vcb` prefiere el método dinámico sobre `vcb_label`). Tipeás el nº de lados ahí antes de poner el centro.
  - 13 tests en `tests/test_drawing_tools.py`. Verificado visualmente (disco limpio, cilindro liso vs prisma octagonal facetado). **Falta:** Tape Measure + guías.
- **DoD:** círculos, arcos, paralelas y guías sobre cualquier cara. *(círculos/arcos/paralelas ✅; falta Tape Measure + guías.)*

**✅ FASE 5 — Groups v1** *(hecho 2026-06-08; era la deuda estructural contra la geometría pegajosa)*
`core/group.py`: cada Group tiene su propio `Mesh` aislado del weld del mesh principal. Make Group (Ctrl+G), Explode (Ctrl+Shift+G), Move como unidad (no arrastra el resto), pick/select/delete como unidad, render + serialización `.igz`.
- **DoD (pasa):** agrupo, al mover el grupo no se pega al resto.
- **Pendiente (v2):** **editar dentro del grupo** (doble-click para entrar al contexto), Outliner, y Components (instancias con transform reutilizable — hoy el grupo guarda geometría en coords de mundo).

**FASE 6 — Capas/Tags UI** *(habilita el "2D que emerge")*
UI sobre `core/layers.py` (ya existe el core): visibilidad/lock por capa.
- **DoD:** Top View + paralela + capas = experiencia 2D usable sin módulo separado.

**FASE 7 — Utilidad real**
Materiales (color/textura por cara), Dimensions, import `.dae`/`.obj` (abrir modelos exportados de SketchUp), export `.dae`/`.obj`/`.stl`.
- ✅ **Materiales — color sólido por cara HECHO (2026-06-14).** `Face.attrs["color"]` (RGB 0..1, base A.3 → rueda por push/pull + rebuild), `SetFaceColorCommand` (undoable, swap de attrs sin snapshot), **tool Paint (B)** en `tools/paint.py` (click pinta la cara / toda la selección de caras; **Alt**=eyedropper; corre sobre malla suelta y grupos vía `pick_face_any`), swatch de color en la toolbar (`QColorDialog`), render por color (VBO de caras agrupado por `attrs["color"]`, default crema, un draw por color en `viewport._face_runs`), y serialización `.igz` (`"color"` por cara). 8 tests en `tests/test_materials.py`.
- ✅ **Export STL + OBJ HECHO (2026-06-14).** `formats/stl.py::save_stl` (binario, todas las caras de malla+grupos trianguladas con normal geométrica outward — para impresión 3D / slicers) y `formats/obj.py::save_obj` (vértices dedup por posición, triángulos agrupados por color → `.mtl` con `Kd` por material; abre en Blender/MeshLab con los colores). File ▸ Export STL… / Export OBJ…
- ✅ **Import OBJ HECHO (2026-06-14).** `formats/obj.py::load_obj` — parsea `v`/`f`/`usemtl`/`mtllib` (índices +/−, n-gons), agrega las caras al scene, **funde coplanares** (`run_stitch coplanar_merge=True`) para reconstruir polígonos de un archivo triangulado (cubo de SketchUp/nuestro export → 6 quads editables) y corre `orient_outward` (el merge es winding-tolerant); `Kd` → `attrs["color"]` (salvo crema = sin pintar). File ▸ Import OBJ… (undo vía `SnapshotMutation`). 12 tests en `tests/test_export.py` (conteo de triángulos, normales outward, grupos, dedup, colores→materiales, **round-trip sólido limpio**, color round-trip). Falta de Fase 7: **textura** por cara, **Dimensions** (cotas), import `.dae` (COLLADA).
- ✅ **Bandeja lateral estilo SketchUp HECHO (2026-06-14).** `views/tray.py` — `QDockWidget` acoplable/plegable a la derecha (toggle en View ▸ Bandeja) con 3 secciones colapsables: **Materiales** (preview activo + grilla de swatches "En el modelo" computada de la escena + "Biblioteca" = colores preset + `resources/textures/*.png`; click = material activo y cambia a Paint; **+ Color…**/**+ Textura…** con tamaño de tile), **Estilo de cota** (decimales/unidad m·cm·mm/fuente/color → `scene.dimension_style`, re-render vivo; `viewport._format_dim_value`; serializado en `.igz`), **Info de entidad** (área de cara/largo de arista/valor de cota/material de lo seleccionado, refresca con `sceneVersionChanged`). Reemplazó los botones Color/Texture de la toolbar. 5 texturas bundle en `resources/textures/`. 9 tests de cota (incl. estilo + round-trip). Verificado visualmente.
- ✅ **Texturas SketchUp-compatible HECHO (2026-06-14).** Modelo SketchUp: textura = imagen + **tamaño real del tile** (sw×sh m), mapeada por **proyección planar** (`core/texture.py::planar_uv`: UV = posición mundo proyectada sobre el plano de la cara / tile size; base derivada de la normal → caras coplanares tilean **continuo**, como SketchUp). `face.attrs["texture"]={"path","sw","sh"}`. **Render GL** (shader `basic.vert/frag` extendido con `a_uv` + `sampler2D u_tex` + `u_use_texture`; VBO pos+uv interleaved `viewport._tex_faces_vao`, un draw por imagen con su `QOpenGLTexture` cacheada por path, Repeat+mipmap). **`SetFaceTextureCommand`** (undo). **PaintTool** extendido: `current_texture`; click aplica textura o color; Alt=eyedropper de cualquiera. Toolbar **Texture…** (file dialog + tamaño de tile vía `QInputDialog`). **Export OBJ** con `vt` (mismas UV planares) + `map_Kd` (imagen copiada junto al `.obj`) → abre con texturas en SketchUp/Blender; **import** lee `map_Kd` (tamaño default 1 m, las UV no se reimportan). Serialización `.igz`. 7 tests en `tests/test_textures.py`; verificado visualmente (cubo texturizado + render mixto textura/color/crema). ⚠️ warning inofensivo al cerrar (`QOpenGLTexture destroy sin contexto`). **Fase 7 DoD esencialmente completo.**
- ✅ **Dimensions — cotas estáticas HECHO (2026-06-14).** `core/dimension.py::Dimension` (entidad en `Scene.dimensions`, no geometría: dos puntos `a`/`b` fijos + `offset`; mide `|b−a|`, estática = no se re-mide al mover la geometría — la variante anclada es para después). **DimensionTool (D)** (3 clics: inicio→fin→colocar offset, con snap; preview vía `rubber_band_lines`+`value_label`). Render persistente en el overlay (`viewport._draw_dimensions`: líneas de extensión + línea de cota + ticks + valor) **con hidden-line removal** — cada segmento 3D se muestrea y solo se dibujan los tramos visibles (`_draw_occluded_segment` vía `_is_occluded`), y el texto/ticks se ocultan si su punto está detrás del sólido; así la cota se lee como parte del modelo y no flotando encima. `AddDimensionCommand`/`DeleteDimensionsCommand` (undo). Serialización `.igz` (`dimensions: [{a,b,offset}]`). **Cotas seleccionables y borrables** (2026-06-14): `viewport.pick_dimension` (distancia a las líneas de extensión/cota), integrado en `SelectTool._pick` (prioridad grupo>arista>cota>cara), box-select, y Delete vía `DeleteDimensionsCommand`; render naranja al seleccionar/hover. 7 tests en `tests/test_dimensions.py`. **Pendiente:** la variante anclada (auto-update).
- **DoD:** importo un modelo de SketchUp (OBJ ✅ / .dae pendiente), lo **acoto ✅**, lo **pinto ✅** y **exporto a STL ✅**. *(Solo falta import `.dae` para el DoD literal; con OBJ está cubierto el espíritu.)*

🏁 **Al cerrar Fase 7 = v0.1 usable real.** Recién después: BIM tagging, IFC export (gancho IngePresupuestos), DXF, geo-ref, etc. (ver Roadmap v0.1 largo abajo).

**Decisiones en paralelo (no bloquean código):** licencia GPL→Apache antes del primer push público (~Fase 7); índice espacial recién cuando en Fase 2/3 el mouse se arrastre con modelos grandes (ni antes = over-engineering, ni después = duele).

**Estimación honesta:** Fase 1 = 1-2 semanas (la incógnita); Fases 2-7 con foco = orden de 2-4 meses al v0.1. Lejos de "años" porque NO es clonar SketchUp.

> El registro de *lo hecho* vive en los commits de git (bitácora automática); este archivo guarda el *rumbo* (fases + DoD). No duplicar en el .md lo que git ya registra.

### 🏠 Cronograma "Casita" — capa operativa sobre las fases (definida 2026-06-06)

**Cómo trabajar el motor:** una casita mínima como **banco de pruebas vivo**. En vez de elegir features desde la lista abstracta de fases, **se dibuja la casita y los gaps aparecen solos** — así fue toda la sesión que cerró la Fase 1 (dibujar → encontrar el bug → arreglar). Es el "filtro maestro" del proyecto vuelto concreto, y encaja con la filosofía "trazar como en la vida real". Esto **no reemplaza** las fases ni sus DoD: las **reordena** y agrega un DoD de *integración*. Ver memoria de Claude `[[feedback-casita-dogfooding-driven]]`.

**Arrancar cada sesión preguntando: "¿qué parte de la casita todavía no se puede dibujar?"** y atacar eso.

| Hito de la casita | Qué fuerza | Estado |
|---|---|---|
| 0. Huella + caja (rectángulo, extruir) | Rectangle + Push/Pull | ✅ |
| 1. Muros con espesor (vaciar / anillo) | **Offset (F)** (Fase 4) | ✅ (sesión 2026-06-09) |
| 2. Vanos: puerta + ventana (push **atravesando**) | **Push/pull pasante (through-hole)** | ✅ (sesión 2026-06-08) |
| 3. Techo a dos aguas (subir el caballete) | **Move (M)** + topología que aguante mover | ✅ (frontón se rellena, gable) |
| 4. Tabiques + escalera | Subdivisión + grada solid-aware | ✅ (sesión 2026-06-06) |
| 5. Acabados (color por cara, cotas) | Materials + Dimensions (Fase 7) | ✅ color por cara + cotas estáticas (2026-06-14) |

**Reorden que revela la casita:** los bloqueos reales para *producir* una casita son **push/pull pasante** (puerta/ventana) y **Move** (techo) — que el roadmap abstracto tenía más abajo que "Fase 2 Selección". Selección *habilita el flujo* (agarrar caras cómodo) pero pasante + Move *producen la casita*.

**Estado casita (2026-06-09):** huella, **muros con espesor (Offset)**, vanos pasantes, techo a dos aguas, tabiques/escalera, y **Groups** (mover bloques sin pegarse) **hechos**. La casita es dibujable y editable reconocible end-to-end. Lo único que falta para "presentable": **acabados** (materiales por cara + cotas, Fase 7).

### 🩹 Robustez de topología de plantas dibujadas a mano (sesión 2026-06-09)

Dibujar una planta a mano (muros, vanos, paredes perpendiculares) destapaba bugs de topología que rompían el push/pull. Toda esta clase de problemas se cierra con un **pase de "heal"** (`core/topology.py::heal_overlapping_faces`) que corre **automático tras cada dibujar/borrar** (vía `SnapshotCompound`/`EraseSelectionCommand`) y también a mano en **Edit ▸ Heal Overlapping Faces**. Qué arregla, todo validado contra archivos reales `planta`–`planta5`:

- **Caras invertidas** → las voltea (una cara con winding al revés empujaba *hacia adentro*).
- **Huecos anidados redundantes** → deja solo el exterior (subdividir un anillo acumulaba hueco-dentro-de-hueco).
- **Madre redundante** → quita la cara grande dejada encima de sus subdivisiones (hole-aware: no borra un anillo legítimo).
- **Doble cara de puerta** (solapamiento parcial) → **perfora el hueco al muro** en vez de borrar la cara, así la cara de la puerta queda **presente y seleccionable** (para borrarla y abrir el vano). Gateado a modelos **planos** (`_mesh_is_flat`): en 3D no corre (los sólidos apilados anidan caras coplanares legítimamente).
- **Líneas dobladas colineales** (orphan, border-0) → poda el duplicado (`prune_collinear_orphan_edges`).
- **T-junctions sin soldar** → `resolve_tjunctions` parte cada arista en los vértices interiores donde otra cara la cruza, así dos paredes **comparten** la frontera (border-2) y borrar la línea divisoria las **fusiona** en vez de borrar una. Cierra el bug viejo "solapamiento colineal no se suelda/parte".

**Undo del dibujo:** los comandos delta del trazo (split/weld/hueco) no componen un inverso limpio → se envuelven en `SnapshotCompound` (snapshot antes/después, restore en undo/redo). Reversión exacta, sin líneas/planos basura.

✅ **Planar arrangement rebuild (hecho).** `core/arrangement.py` recomputa las caras mínimas de un plano desde el grafo de aristas (DCEL: parte cada cruce/solape, poda spurs colgantes, ordena half-edges por ángulo y traza caras tomando siempre la *next-edge clockwise* → caras CCW + outer CW; anida cada hueco en la cara que lo contiene). Expuesto como **Edit ▸ Rebuild Faces (Planar)** (`RebuildPlanarFacesCommand`, undo por snapshot). Funciona en cualquier plano (no solo Z=0) vía `plane_basis`/`coplanar_plane`; gateado a mallas de un solo plano (en 3D no corre). 10 tests en `tests/test_arrangement.py` (cuadrado, diagonal, cruces, cuadrado-en-cuadrado con hueco anidado, spur podado, línea doble, loop abierto, comando + undo + guard 3D). Es el fix de raíz determinista que reemplaza los heurísticos del heal para plantas a mano.

✅ **Grieta + costuras del push anidado 3D (resuelto, hoy vía fix de raíz).** El bug de `pris2.igz` (push repetido sobre una pared-de-bump de un prisma irregular) tenía dos síntomas: (1) **cara faltante** → malla no-hermética; (2) **diagonales visibles** = caras coplanares sin fusionar. Primero se cerró con el heal `cap_boundary_loops`; ese heal fue **reemplazado y eliminado** por el rebuild determinista por plano (ver sección siguiente). El arrangement plano sigue como **Edit ▸ Rebuild Faces (Planar)** para plantas a mano.

### ✅ Fix de raíz del motor de push/pull — COMPLETO (sesión 2026-06-09, tarde)

La deuda "parches/heals, no fix de raíz" quedó saldada. Los tres pasos, en orden:

1. ✅ **Orientación consistente en la malla no-manifold — paso 1 + integración (hecho 2026-06-09).** `core/orient.py`: `orient_outward(mesh)` voltea las caras (in-place, preservando identidad de objeto) para que toda normal apunte hacia afuera del sólido. Como la malla es no-manifold (winding no se propaga por half-edge: una arista la comparten 2 muros + un piso), el "afuera" se decide **por cara, independiente**, vía **ray-casting de paridad** (un punto justo afuera de una cara está fuera del sólido si un rayo cruza el resto de la malla un nº par de veces; jitter multi-rayo con voto para esquivar grazes de aristas compartidas). Sin seed, robusto a no-manifold. **Gateado a mallas cerradas** (`is_closed`) → no-op en hojas planas/abiertas (la base free-standing se conserva). `signed_volume(mesh)` para confirmar consistencia. **Integrado en `pushpull._mutate`**: tras extrude + stitch + `cap_boundary_loops`, una pasada `orient_outward` deja todo sólido commiteado con winding consistente → **mata la clase "no puedo empujar esta cara / empuja hacia adentro"** (un strip fresco wound al revés, una tapa volteada, o la base del primer extrude flat→solid). 31 tests nuevos (`tests/test_orient.py` 11 + `tests/test_pushpull_orient.py` 20: las 60 combinaciones de push laterales hechas permanentes — cada secuencia commitea sólido cerrado, sin costuras, volumen+ y ya outward-consistente). 229 verdes.

2. ✅ **Integrar el planar arrangement al push/pull 3D — núcleo + crack-heal reemplazado (hecho 2026-06-09).** El bloqueante "¿de qué lado está el sólido?" **resuelto** con la orientación del punto 1: `core/cap_rebuild.py::rebuild_plane(mesh, origin, normal)` recompute un plano desde sus aristas vía `planar_arrangement` + clasificación por winding-number contra las aristas de muro orientadas outward. 30 tests nuevos (`tests/test_cap_rebuild.py` + round-trip). De paso, fix de un crash **pre-existente** de redo-tras-push (`SnapshotMutation` re-ejecutaba el closure con `base_face=None`; ahora restaura el resultado capturado como `SnapshotCompound`).

3. ✅ **El rewrite del extrude (hecho 2026-06-09, cierra la deuda).** `_extrude_commands` quedó **naive y sin árbol de casos**: base consumida/conservada + tapa + un quad de muro por arista — aun cuando un quad cae sobre el plano de una cara existente. Las ramas `extend_wall_edge` / `subtract_loop_from_face` (notch) / strip y el heal `cap_boundary_loops` se **eliminaron** del código. Piezas que lo hicieron posible:
   - **Winding determinista del build naive:** con la base outward (invariante), `[a, b, b2, a2]` es outward para **ambos** sentidos del push (invertir el push invierte a la vez la normal geométrica del quad y de qué lado está el material) y la tapa conserva el winding de la base. Eso es lo que el intento anterior ("voltear la base") no vio.
   - **Invariante a la entrada Y a la salida:** `_mutate` corre `orient_outward` **antes** de extruir (mallas a mano o cargadas de `.igz` llegan con winding mixto; si la base se voltea, el signo del drag se reconcilia) y al final del commit.
   - **Clasificación volumétrica a dos lados en `rebuild_plane`:** una región lleva cara ⟺ hay material de **exactamente un lado** del plano (ninguno = fantasma; ambos = interior del sólido — p.ej. la boca donde un panel empujado hacia afuera toca su muro: una cara ahí sería partición interna y mataría el hueco de la ventana). La cara reconstruida se devuelve **ya outward** (hacia el lado vacío). "¿Hay material del lado s?" se responde con **paridad de ray-casting** (`core.orient.ray_parity_outside`, el mismo primitivo de `orient_outward`) desde un punto a 1 mm del plano, rayos alejándose del plano (nunca miran a través de la grieta que se está tapando). Leer el *volumen* y no las aristas del plano hace la respuesta **independiente del orden** en que se reconstruyen los planos: las caras coplanares duplicadas que el build naive deja a medio limpiar se cancelan de a pares en la paridad. (La 1ª versión leía aristas-de-muro del estado vivo → el resultado dependía del orden de iteración del set `new_faces` = del hash seed de Python → fallos intermitentes en la suite. Validado: las 24 permutaciones de orden del escenario overhang dan limpio.)
   - **Rebuild a punto fijo:** los planos se reconstruyen en rondas (clave de plano ordenada = determinista); las caras que un rebuild agrega cuentan como frescas y pueden re-flaggear planos; `apply_rebuild` devuelve `False` cuando el plano ya está en su forma reconstruida (fingerprint canónico de loops) → el loop termina. Tope duro de 4 rondas; converge en 1-2.
   - **Pipeline sólido** (`attached and was_solid`): `run_stitch(coplanar_merge=False)` (solo conectividad) → `apply_rebuild` sobre **planos de costura** (`seam_planes`: cara fresca coplanar-adyacente a otra — el strip sobre un muro, el quad fantasma sobre el muro a notchear, la tapa a ras de un techo viejo) → `apply_rebuild` sobre **planos de grieta** (`crack_planes`) → re-stitch liviano → `orient_outward`. **El merge `abs()` ya no corre en sólidos**; queda solo para hojas planas (`was_solid=False`), donde no existe "outward" y la tolerancia de winding es semánticamente honesta.
   - **Fase 0 del stitch — weld de vértices coincidentes** (`mesh.weld_coincident`): funde vértices en la misma posición (re-apunta aristas y loops, borra aristas de largo cero, caras degeneradas y duplicados), el follow-up que `move_vertex` tenía pendiente. Con eso el camino **prism-cap** (traslación) también quedó de raíz: empujar un bump de vuelta **a ras** disuelve panel + anillo + muros laterales en el muro anfitrión → cubo prístino de 6 caras / 12 aristas / 8 vértices, estilo SketchUp (antes quedaban 4 caras de área cero + panel sin fusionar + 4 costuras — gap pre-existente, validado contra HEAD).
   - Validación: storyline sano completo (cubo → bump → flush-back → grada de esquina → recess ciego, hermético/outward/sin costuras tras cada commit) + `pris2.igz` sin regresión (arranca roto del bug viejo; los pushes ahora dejan *menos* aristas abiertas que HEAD, peor caso 9 vs 20). **249 tests verdes** (4 nuevos: weld unit + flush-back end-to-end).

   ⚠️ **Limitación conocida (heredada, no nueva):** `apply_rebuild` unioniza todas las regiones sólidas del plano, así que una **diagonal dibujada por el usuario** sobre un plano que el push toca (costura/grieta) se disuelve. El merge viejo tenía la misma semántica para componentes seeded. Si molesta en la práctica: re-splitear las caras reconstruidas por las aristas de usuario sobrevivientes.

Red de regresión: 270 tests (incl. las 60 combinaciones de push del triángulo irregular `[(-0.2,-2.8),(2.7,-7.2),(4.1,-2.7)]`). Banco: `capturas/pris2.igz` (roto a propósito, sirve de bench de robustez sobre input dañado).

### 🧰 Paridad SketchUp del Push/Pull (auditada 2026-06-09)

Tras el fix de raíz se auditó el push/pull contra SketchUp (el usuario es ex-usuario intensivo). **Hecho (2026-06-09/10), todo en `tests/test_pushpull_ux.py`:**

- ① **Ctrl = push/pull a copy** (la cara de arranque queda como división de losa; las paredes se apilan como strips separados por el cinturón de aristas — así se apilan pisos; Ctrl togglea en vivo durante el drag y anula el camino prism-translate).
- ② **Doble-click repite la última distancia** sobre la cara bajo el cursor (`Tool.on_double_click` + dispatch en viewport, default = on_click para no romper el ritmo click-click de los tools de dibujo).
- ③ **VCB acepta negativos y unidades** (`-2` invierte la dirección del drag; `30cm`/`1500mm`/`2m` por campo, metro = unidad base; `ShortcutOverride` evita que `M`/`C` disparen el shortcut de herramienta a mitad de número).
- ④ **Clamp al encoger** ("Offset limited to"): `_compute_inward_limit` al lockear la cara — bloqueador = cara paralela del lado del material (−normal, sólidos outward) que solapa lateralmente el loop base pero **no** lo contiene estricto (esas son targets de through y no acotan). Empujar **exacto al límite** está permitido y la maquinaria de flush resuelve: tapa al fondo = caja colapsa a **una sola cara** (dedup de caras idénticas en `weld_coincident`); grada de esquina al piso = el notch **abre el piso** y queda sólido-L hermético. El clamp corre en drag, VCB y double-click.
- ⑤ **Inferencia de distancia** (`_infer_reference_distance`): durante el drag, apuntar a un vértice del modelo (incl. groups) clava la extrusión a "nivel con ese punto" (proyección del vértice sobre el eje del push). Escanea la malla **limpia** (el hover revierte el preview antes de leer geometría — los vértices móviles del sólido en formación no retroalimentan) y excluye los vértices del propio loop base (no se clava en 0 al arrancar). Threshold = `snap_threshold_px`.
- ⑥ **Autofold** (`core/topology.py::fold_nonplanar_faces`, integrado en `MoveVerticesCommand`): una cara que un Move dejó no-plana se **pliega** en piezas planas (triangula sobre su plano de Newell y re-fusiona los triángulos coplanares adyacentes → solo quedan los pliegues reales; un quad con una esquina levantada = exactamente 2 triángulos + 1 arista de pliegue). La fusión se confina a las piezas de la misma cara madre (un pliegue nunca se disuelve en un vecino coplanar). Undo: move plano = traslación inversa barata; move que plegó = restore de snapshot.
- ⑦ **Push/pull directo sobre caras de grupos** — *mejor que SketchUp*: sin paso de "entrar al grupo". `viewport.pick_face_any` elige la cara front-most entre la malla suelta y todos los grupos; el tool fija el grupo objetivo al lockear y **todo el pipeline corre sin cambios sobre el mesh aislado del grupo** vía la fachada `_GroupScene` (mismo contrato `mesh/faces/edges/selection/version` que `Scene`; coords ya en mundo). `SnapshotMutation(mesh=...)` snapshotea el mesh del grupo → undo/preview exactos; clamp, recess, colapso y orient operan con la geometría del grupo; la malla suelta no se toca.

- ⑧ **Regla de crease + dedup de caras (2026-06-10).** Auditando "¿qué falta para roca sólida?" se encontró y arregló un **crash real del flujo de planta** (levantar la 2ª de dos habitaciones que comparten muro → `IndexError`: el push reconstruía el muro compartido como duplicado idéntico y el merge de dos ciclos idénticos no tiene frontera que trazar). Fix triple: `dissolve_coplanar_region` dedupea ciclos idénticos en vez de crashear; **regla de crease** (una arista que carga una cara no-coplanar es estructural — nunca se fusiona a través de ella) en el merge fase 3 **y** en la unión del rebuild (`keep_keys`) → dos techos sobre un muro divisorio quedan **dos caras con ridge visible**, como SketchUp, no una losa flotando sobre el muro; `mesh.dedupe_faces()` como paso propio de la fase 0 del stitch. Test del flujo completo en `test_two_room_plan_raises_cleanly`.

### 🎯 PRÓXIMA SESIÓN — PENDIENTE (actualizado 2026-06-14)

> **Lo que queda, en orden sugerido para arrancar:**
>
> 1. **4 seeds `KNOWN_BAD` restantes (todos draw-side) — son la punta de un iceberg de SOLAPES coplanares (ver investigación abajo).** `cube 121`, `plan 152`, `plan 210` (orphan edges al dibujar un rect), `plan 242` (seam). El orphan/seam al dibujar es el *síntoma*; la causa es que el motor deja **caras coplanares que se solapan (doble cobertura)** en **~326/1000 secuencias** — invisibles al bench actual (su seam-check solo mira aristas de exactamente 2 caras coplanares; un solape sin arista compartida limpia se le escapa). Lo crean **tanto push como draw**. Disolverlos limpio es adyacente al techo del rebuild (ver investigación 2026-06-14 abajo). **Decisión:** no atacarlo como "4 bugs"; es un proyecto de calidad mayor (pre-IFC/STL). Dejar los 4 como xfail.
> 2. **Bloque C** (features de producto): Fase 2 selección (doble/triple-click), Fase 3 Eraser, face culling, Groups v2, Fase 4 (Circle/Arc/Tape), Fase 6 (UI capas), **Fase 7** — ✅ Materials color-por-cara (2026-06-14, tool Paint B); falta textura + Dimensions + import/export. Lista completa en el bloque **C** abajo.
>
> **El bloque A (motor "roca sólida") está COMPLETO** — A.1 fuzz bench (943/1000 limpias), A.2 caras inclinadas, A.3 `Face.attrs` por región, A.4 subdivisiones de usuario. Cerrado 2026-06-11.
>
> **El bloque B (UX restante del push/pull) está COMPLETO (2026-06-14)** — B.5 inferencia de distancia sobre caras/planos (fallback de `_infer_reference_distance` al `pick_face_any` bajo el cursor: proyecta el rayo∩plano sobre el eje del push; el vértice cercano sigue ganando primero), B.6 marcador verde estilo endpoint del punto inferido (`PushPullTool.inference_marker()` → `viewport._draw_inference_marker`), B.7 mensaje "Offset limited to X m" en la status bar cuando el clamp recorta (`viewport.flash_status` desde `_clamp_extrusion`). 3 tests nuevos en `tests/test_pushpull_ux.py` (face-infer + marker, clamp-flash). **Cerrada la paridad SketchUp del push/pull.**

---

*(Plan original definido 2026-06-10; estado de partida entonces: 273 tests verdes en `b949733`.)*

**A. Motor "roca sólida" — ✅ COMPLETO (2026-06-11):**

1. ✅ **Fuzz/property bench — el certificador. OPERANDO** *(2026-06-10/11 — ver sección "🧪 Fuzz bench" abajo; **943/1000 secuencias limpias**, 57 congeladas como `xfail` por seed en `KNOWN_BAD`)* `tests/test_fuzz_engine.py`: secuencias **aleatorias con semilla fija** (reproducibles) de operaciones reales — dibujar rect sobre cara/plano aleatorio, push de cara aleatoria con distancia aleatoria (incl. negativa → clamp/through/colapso), undo/redo intercalado — sobre escenarios variados (cubo, prisma irregular, planta multi-room, sólido con grupo). Invariantes tras **cada** commit: si la malla era cerrada sigue cerrada (`is_closed`), `signed_volume > 0`, sin costuras coplanares que este commit acuñó, sin aristas huérfanas, sin caras de área ~0, sin vértices duplicados sin soldar; undo→redo reproduce el estado (fingerprint canónico). **DoD (≥1000 limpias):** NO alcanzado aún — 943/1000; los 57 restantes son la tarea pendiente #2 de arriba. Cada falla: minimizar → test de regresión → fix de raíz (no parches).
2. ✅ **Banco de caras inclinadas. HECHO (2026-06-11, `64d921a`).** `tests/test_pushpull_slanted.py` (18 casos, todos verdes **sin cambios al motor** — los 19 fixes del fuzz ya cubrían lo inclinado): casa a dos aguas (caballete por chord+Move, frontones auto-rellenados, vol exacto 56), engrosar plano de techo (+ flush-back restaura las 7 caras exactas), extender el frontón pentágono (extensión prisma limpia, vol 70), recess sobre techo inclinado (+ crossover A.3: los attrs del techo sobreviven la subdivisión), cuña con tapa inclinada (las 6 permutaciones de orden × 3 pushes + cada cara in/out), y round-trip undo/redo. **DoD cumplido:** hermético/outward/sin costuras en todos.
3. ✅ **Identidad/atributos a través del rebuild — pre-requisito de Fase 7 (Materials). HECHO (2026-06-11, `2dfa79a`).** `Face.attrs: dict` genérico + herencia en todos los puntos de churn: `apply_rebuild` (por región: la cara nueva hereda de la vieja cuyo interior contiene su punto interior), `dissolve_coplanar_region/pair` y `dissolve_edge` (dominante = mayor área con attrs), `dedupe_faces` (sobreviviente adopta si no tiene propios), `fold_nonplanar_faces` (piezas continúan a la madre), heals (flip de winding, dedupe de huecos anidados, punch parcial), remainder de subdivisión de `AddFaceCommand`, tapa del extrude (continúa a la base consumida), y `capture_state/restore_state` (undo/redo round-trip). **DoD cumplido** en `tests/test_face_attrs.py` (8 tests): attrs sobreviven a extend, re-push extrude, notch (piso L), flush-dissolve, fold, dedupe, merge dominante y snapshot. Suite 1241 + 53 xfail, cero regresiones del fuzz. Es también la base para retomar los 53 seeds con identidad exacta por región.
4. ✅ **Diagonal de usuario sobre plano reconstruido. HECHO (2026-06-11, `e9e7a5c`).** Implementado vía keep-segs (más simple que el re-split planeado): los **rims del propio push** se capturan *por posición* al entrar al fixpoint (sobreviven a que rondas posteriores consuman los objetos); toda otra arista del plano con caras es estructura del usuario y entra a la unión como boundary (igual que un crease). El test es geométrico (yace-sobre — los splits no rompen la protección). De paso: fix del anidado de huecos en `_union_outline` (un hueco idéntico al contorno de una región conservada se anidaba en ELLA → anillo de área cero, fuzz cube 0) y el invariante de costuras del fuzz maduró con la semántica (una costura es bug solo si su arista la acuñó este commit — las viejas persisten estilo SketchUp). **DoD cumplido** en `tests/test_user_subdivisions.py` (5 tests): el chord sobrevive a un bump en su pared y a un Ctrl-stack arriba; el seam del strip apilado sí se disuelve; attrs por lado (A.3) viajan. Suite 1260 + 57 xfail (A.4 expone 4 seeds netos profundos, absorbidos en KNOWN_BAD).

**B. UX restante del push/pull — ✅ COMPLETO (2026-06-14):**

5. ✅ **Inferencia de distancia sobre caras/planos.** `_infer_reference_distance` cae al `pick_face_any` bajo el cursor cuando no hay vértice dentro del threshold: proyecta el hit `rayo∩plano` sobre el eje del push (`dot(hit − anchor, normal)`). El vértice cercano gana primero (esquina precisa); la cara base y sus coplanares se filtran (guard `|dist| ≥ _MIN_EXTRUDE`). Escanea sobre el preview revertido (mesh limpia).
6. ✅ **Marcador visual del punto inferido.** `PushPullTool.inference_marker() → (world, kind)`; `viewport._draw_inference_marker` pinta un cuadrado verde estilo endpoint en el overlay cuando engancha.
7. ✅ **Mensaje "Offset limited to X m" en status bar.** `viewport.flash_status(text)` (envuelve `window().statusBar().showMessage`); `_clamp_extrusion(viewport)` lo dispara cuando recorta. Llamado desde drag, VCB y double-click.

**C. Para sesiones siguientes (NO de esta sesión, no empezar hasta cerrar A+B):**

- **Fase 2 restante (selección):** doble-click = geometría conectada, triple-click = sólido completo (SelectTool hoy solo tiene click/Shift/box-select/hover). Nota: `Tool.on_double_click` ya existe (lo agregó la sesión de paridad).
- **Fase 3 restante:** **Eraser (E)** — borrar por click y por arrastre (no existe `tools/eraser.py`; hoy solo Delete sobre la selección).
- **Sin face culling** (render): ambos lados de cada cara con el mismo crema; SketchUp pinta front crema / back azul-gris. Con la orientación outward ya garantizada por el motor, es solo trabajo de shader/render.
- **Groups v2:** editar dentro del grupo (doble-click entra al contexto, dibujar/borrar adentro), Outliner, Components (instancias con transform).
- **Fase 4 restante:** Circle (C), Arc (A), Tape Measure + guías (T).
- **Fase 6:** UI de capas sobre `core/layers.py`.
- **Fase 7:** Materials (usa el punto A.3) + Dimensions + import/export `.dae`/`.obj`/`.stl` → v0.1.
- **M4:** serialización `.igz` indexada por vértices (base para OBJ/glTF/IFC).
- **Índice espacial** cuando el pick/motor duela con modelos grandes (parity/orient/arrangement son O(F²)-ish por commit — bien a escala casita, molasses en edificios reales).
- **Origen local para georef:** `QVector3D` es float32 → en coordenadas UTM (~500 km) la precisión cae a cm; la Scene necesitará offset de origen local. Decidir al diseñar georef.
- **Estrategia:** licencia GPL→Apache 2.0 antes del primer push público; rename de carpeta `~/wasia` → `~/ingetrazo` (usuario, fuera de sesión); CI GitHub Actions (portar de IngePresupuestos).

**Brechas vs SketchUp que quedan SIN plan (aceptadas por ahora):** edición general dentro de grupos (cubierta por Groups v2 arriba).

### 🧪 Fuzz bench del motor + fixes de raíz que destapó (sesión 2026-06-10, tarde)

**A.1 construido y operando** (`tests/test_fuzz_engine.py`): 1000 secuencias seeded reproducibles (4 escenarios × 250 seeds; sweep rápido = 200 en cada corrida, resto `@pytest.mark.slow` — deseleccionar con `-m "not slow"`) de dibujar-rect / push (±, −50, Ctrl) / undo-redo intercalado, con invariantes tras cada commit: cerrada→cerrada-o-plana, volumen+ y orientación consistente (`orient_outward(m)==[]`), costuras coplanares solo sobre trazos del usuario o divisiones Ctrl, sin huérfanas / área-0 / vértices sin soldar, undo→redo reproduce el fingerprint canónico. **Estado: 943/1000 limpias; los 57 que el motor aún no sobrevive están congelados como `xfail` por seed en `KNOWN_BAD`** (regenerar la lista: `python -m tests.test_fuzz_engine`; un seed arreglado empieza a pasar y se poda de la lista). Suite completa: **1260 verdes + 57 xfail (~3-4 min)**. Repro de cualquier seed: `tests.test_fuzz_engine.run_sequence(escenario, seed)`. *(Nota: subió de 947→943 al cerrar A.4 — preservar las subdivisiones del usuario hizo visibles 4 debilidades en secuencias largas; trade aceptado, todo en KNOWN_BAD.)*

**Fixes de raíz que el bench destapó** (regresiones nombradas en `tests/test_fuzz_regressions.py` y `tests/test_orient.py`):

1. **`Face.interior` — particiones interiores como concepto del motor** (`core/mesh.py`, `core/orient.py`). El ray-casting de paridad contaba las particiones (losa de Ctrl-push, muro que comparten dos cuartos) como cruces de frontera → corrompía orientación y clasificación en cualquier malla con divisiones, y `orient_outward` flipeaba la losa en *cada* llamada (no idempotente). Ahora las detecta por **peeling iterativo** (clasifica contra el set frontera, excluye las que leen interior, reclasifica a punto fijo), las marca, y todo test volumétrico las excluye (`ray_parity_outside` del rebuild, `signed_volume`). Marca también en mallas **abiertas** (planta mixta sólido+hoja); el flip solo corre en cerradas; gate barato `_all_coplanar` para no pagar paridad en dibujos planos.
2. **`rebuild_plane` con reglas de cobertura** (`core/cap_rebuild.py`). Región con material igual a ambos lados → decide la cobertura: **cara fresca** del push (su winding determinista declara qué lado se vació — resuelve la dependencia circular entre planos cuando un quad fresco coincide con la parte muerta de una partición), **cara existente** (estructura del usuario: la partición o la hoja plana se re-emite — antes el rebuild se comía el muro divisorio y los pisos planos de una planta), o **nada** (fantasma → se descarta). Devuelve `(outer, holes, is_partition)`.
3. **`apply_rebuild` acepta rebuild vacío**: `[]` es respuesta real (todas las regiones fantasma/interior — p.ej. la aleta de espesor cero que deja un colapso flush) y **borra** las caras del plano; antes `if not rebuilt` lo trataba como no-op y la aleta sobrevivía abierta.
4. **Push de cara con huecos**: `_classify_base` y `_cap_positions` ignoraban los loops de hueco → el prism-translate trasladaba el muro dejando el rim del hueco (y el panel de adentro) clavados en el plano viejo: cara-con-hueco no-plana, corrupta. Ahora clasifican y mueven outer + rims (`PushPullTool._cap_loop_positions`).
5. **Colapso lateral flush**: el camino prism-translate corre el **mismo fixpoint de rebuild** que el extrude (extraído a `_rebuild_planes_fixpoint`, compartido) — empujar el flanco de un bump a ras del flanco opuesto disuelve el bump completo (cubo prístino 6 caras / 12 aristas / 8 vértices). Antes la limpieza colgaba de `face in mesh.faces`, falso justo cuando el weld consumía la cara empujada.
6. **Clamp lateral del push hacia adentro** (`_compute_inward_limit`): además del blocker paralelo, (a) **salida inmediata** — pared vecina que "se abre" respecto del push (`dot(−n, normal vecina) > 0`) → límite 0: la pared larga del prisma triangular no puede entrar sin subtracción booleana, el push queda no-op; (b) **salida distante** — rayos desde muestras apenas interiores del loop, primer cruce con cara frontera no-paralela, menos 1 mm (el contacto lateral es de filo, no de cara — aterrizar exacto degenera). Mata la clase "sólido invertido con volumen negativo" (era el peor síntoma: corrupción silenciosa).
7. **Push mínimo = resolución del weld** (`_MIN_EXTRUDE = 2e-4` en `tools/pushpull.py`): una extrusión bajo la tolerancia de soldado (1e-4) crasheaba `add_edge` ("degenerate edge"); ahora es no-op.
8. **Heal del draw gateado a planas** (`core/topology.py::heal_overlapping_faces`): `orient_coplanar_faces` solo corre en mallas planas — en 3D un mismo plano lleva legítimamente outwards opuestos (dos sólidos espalda con espalda) y el pase volteaba muros hacia adentro. En 3D, el winding del sub-face recién dibujado lo decide `orient_outward` al final del heal.

**Segunda pasada (misma sesión, más tarde): 185 → 93 xfail (907/1000 limpias).** Seis fixes de raíz más, los gordos del motor:

9. **Ctrl-push corre el pipeline sólido completo** (`13a625d`): el Ctrl-inward depositaba strips coincidentes con los planos de frontera sin cleanup (pares con winding opuesto que corrompían toda paridad posterior). Tres piezas: (a) **trazado angular** en `_union_outline` (regla DCEL next-edge-clockwise, como `arrangement._trace_faces`) — el pop arbitrario podía atravesar un cruce de crease y pellizcar dos caras en un contorno auto-tocante; (b) **exclusiones de paridad en keep-mode**: Ctrl no remueve material → un quad fresco sombreado por *cualquier* cara vieja de su plano duplicaría el conteo de una frontera que persiste (se excluye); los push con base consumida conservan la doctrina contraria ("cancel in pairs": su barrido sí vacía la región); en keep-mode las caras frescas tampoco declaran frontera; (c) la tapa del Ctrl-inward se marca `Face.interior` al construirse. Resultado: Ctrl-inward de una pared entera = la estructura SketchUp exacta (frontera partida en el cinturón con crease, tapa como división interior, hermético).
10. **Dedupe diferido en sólidos** (`cbfabab`): un colapso flush deja el quad de barrido *idéntico* a la cara con la que debe aniquilarse; dedupearlos antes del rebuild restauraba la lectura de material y el colapso −50 nunca clasificaba como vaciado. `run_stitch(dedupe=False)` en el primer stitch del camino sólido — solo el rebuild volumétrico sabe distinguir "conservar uno" (muro compartido) de "borrar ambos" (región vaciada).
11. **Jitter de paridad 0.08 → 3e-4** (`cbfabab`): el cono de jitter es relativo a la dirección → su desvío lateral crece con la distancia; un strip de 0.3 m a 4 m desviaba ±0.32 m y la mayoría de los rayos lo perdían → "sin material" por suerte de seed. El cono apretado sigue esquivando grazes y se mantiene en ~2 cm a 60 m.
12. **Weld cross-cell** (`1d3a03b`): el registry suelda por clave redondeada (4 decimales) — dos puntos a 2e-6 pueden caer en celdas distintas (esquina inclinada calculada por dos caminos float32) → vértices casi-coincidentes con aristas paralelas duplicadas y cáscara abierta. `Mesh._lookup` sondea las 26 celdas vecinas con chequeo de distancia real; `weld_coincident` ganó una pasada cross-cell determinista.
13. **Crease geométrico en la unión** (`f007900`): la protección de crease matcheaba por par exacto de endpoints, pero el arrangement parte las aristas en los cruces (un rect dibujado cruzando el cinturón de un Ctrl-stack) y los tramos perdían el crease. Ahora son *segmentos 2D* con test yace-sobre (tolerancia atada al `_TOL` del arrangement — float32 en planos inclinados desvía más de 1e-6).
14. **Colapso a plano sobre base subdividida** (`1299a16`): la tapa aterrizada y la base con hueco no son ciclos idénticos → quedaba sándwich de volumen 0; si el resultado del prism-translate es plano, corre `heal_overlapping_faces` (la regla "madre redundante" lo resuelve).

**Tercera pasada (93 → 76):** dos fixes más — 15. **búsqueda de madre hole-aware** (`f2ef796`): `find_containing_face` elegía madre por "menos vértices" ignorando huecos: un rect anidado dentro de otro rect punchaba a la abuela (hueco-dentro-de-hueco que el heal dedupeaba) y quedaba flotando; `loop_inside_face(outside_holes=True)`. 16. **declaraciones solo en pushes que remueven** (`c970ebb`): el winding fresco solo testifica vaciado en carves hacia adentro; en un push hacia afuera el quad sobre una pared existente es partición legítima (habitación levantada tocando a la vecina), no frontera.

**Cuarta pasada (76 → 67):** 17. **dedupe por ciclo exterior, sobrevive el subdividido** (`4bbfab0`): dos caras sobre la misma región son siempre basura en un modelo de superficie, pero la firma de ciclo idéntico incluía los huecos — una tapa lisa colapsada flush sobre una base *subdividida* (mismo exterior, huecos distintos) nunca dedupeaba y quedaba apilada; ahora la firma es solo el loop exterior y en colisión sobrevive la cara con más huecos (carga la subdivisión del usuario, cuyos huecos llenan sus propias caras).

**Quinta pasada (67 → 53):** 18. **quads keep-mode a través del interior = particiones nuevas** (`e83da68`): un Ctrl-stack crecido *hacia adentro del cuerpo* tiende divisiones donde no existía cara (región material-ambos-lados sin cobertura vieja) — se descartaban como bocas fantasma y la cáscara se abría en sus rims (prism 96). En keep-mode, la cobertura por quad fresco propio marca partición. 19. **declaración direccional completa** (`2fd7204`): carve hacia adentro declara en regiones *ambos-lados* (vacío aún amurallado lee material); push hacia afuera declara en regiones *ningún-lado* — su barrido **envuelve** frontera vieja que sigue contando en paridad y el material nuevo lee como vacío (plan 216: la tapa del stack tragada por un push más largo). Ctrl nunca declara.

**Los 53 que faltan — y el techo del rule-set (hallazgo de la 6ª pasada).** Se intentaron dos fixes estructurales y ambos terminaron **revertidos por netos-negativos** pese a arreglar sus seeds objetivo: (a) *clipping de solape parcial* (recortar los triángulos de un quad fresco al resto no cubierto por caras viejas coplanares — el caso prism 147: quad mitad-sobre-pared, mitad-en-aire); (b) *separación op/fresh* (que las caras emitidas por rebuilds no hereden derechos de declaración — round 2 descartaba la pared que round 1 reconstruyó). Cada variante arreglaba 1-3 seeds y desestabilizaba 3-22 otros: **el rule-set de declaraciones/cobertura llegó a su techo de complejidad** — está en un óptimo local donde los point-tests (¿cubre? ¿declara?) ya no pueden distinguir los casos. La salida de fondo es **A.3 (identidad/attrs por región a través del rebuild)**: con herencia de identidad exacta por región, "qué cara es continuación de qué" deja de adivinarse con point-in-polygon. Recomendación: hacer A.3 ANTES de seguir con estos 53. Clases restantes: solape parcial (prism 147), −50 degenerados (Ctrl-flush a través = hueco→túnel, cube 190 — semántica a decidir), planta/grupo largos.

**Triage de los 57 (2026-06-14, sesión que pausó el bloque para ir a features).** Corridos los 57: **53 son "closed mesh left open (crack survived)"** tras un push, 3 orphan edges, 1 seam; ninguno ya-pasa. Todos fallan en step 2-7 con historia previa no trivial (sin repros cortos, confirmado). Subclases del crack: **26 = Ctrl-stack previo (partición interior) → push regular posterior crackea** (la más grande), 13 = push limpio sobre bumps de pushes anteriores, 8 = push de cara **con hueco**, 5 = el push que falla es Ctrl. **Repro dirigido limpio capturado** (subclase grande, no requiere fuzz): cubo → dibujar rect en el piso → Ctrl-push ese rect hacia arriba (columna interior) → push del piso-remanente (16 con hueco) hacia abajo → **4 aristas con 3 caras** (duplicado coplanar) en el nivel movido. El nudo es semántico: empujar una cara cuyo **hueco está "tapado" por otra cara/columna interior** es grado-CSG (el aro del hueco se desprende de la columna anclada). **Decisión del usuario:** pausar los 57 (xfail ya protege contra regresiones) y avanzar el bloque C; la robustez vuelve cuando duela en uso real o se encare el rewrite por identidad de región.

### ✅ Guard de hermeticidad grado-BIM — cierra la clase entera de push-cracks (2026-06-14)

**57 → 4 xfail (996/1000 limpias), cero regresiones.** Al reconsiderar los 57 con BIM/IFC como tesis: el diferencial de IngeTrazo frente a SketchUp es producir **sólidos válidos** (el metrado/IFC exige hermeticidad; SketchUp no la garantiza y corrompe alegremente). Y la mayoría de los 57 resultaron **operaciones semánticamente mal definidas** (empujar a través de/alrededor de particiones interiores o huecos tapados — sin resultado hermético canónico). Conclusión correcta para un motor grado-BIM: **no "producir el resultado correcto de ops ambiguas" (a menudo indefinido), sino GARANTIZAR que el motor nunca cometa un sólido roto.**

**El fix (en `PushPullTool._mutate`, no toca la clasificación parity/declaration):** wrapper alrededor de `_mutate_inner`. Si la malla **era** un sólido cerrado (`is_closed and not _mesh_is_flat`) y el resultado **no** queda cerrado-ni-plano, se **rechaza el push: restore del snapshot pre-op** (no-op) en vez de commitear un crack. Una hoja plana (recess en superficie, primer extrude flat→solid) nunca se guarda (legítimamente abre). UX: `_commit` avisa en la status bar ("Push refused: would break the solid"). Tests dirigidos en `tests/test_pushpull_ux.py` (contrato del guard vía monkeypatch + push válido no se rechaza).

**Honestidad sobre qué es y qué NO es:** esto **no** hace que los 53 ops "funcionen bien" — los hace **fallar seguro** (no-op). Los ~18 "clean bien definidos" que *deberían* producir geometría quedan como **brecha de capacidad** (rechazados, fail-safe), a habilitar incrementalmente después mejorando el rebuild — pero el motor **ya nunca emite un sólido roto**, que es la garantía que el thesis BIM necesita. Los 4 `KNOWN_BAD` restantes son **draw-side** (orphan/seam al dibujar un rect), otro subsistema. El rewrite por identidad-de-región (A.3) sigue disponible si en el futuro se quiere convertir los rechazos en resultados correctos.

### 🔬 Iceberg de solapes coplanares — investigación 2026-06-14 (NO atacar como bug suelto)

Al intentar arreglar los **4 draw-side**, se descubrió que son el síntoma de un problema transversal grande: el motor deja **caras coplanares que se solapan (doble cobertura del mismo lado, no-interior)** en **~326/1000 secuencias** del fuzz. Son invisibles al bench actual (el seam-check solo detecta aristas con exactamente 2 caras coplanares; un solape full-wall + sub-rect que NO comparte una arista limpia se escapa) y al guard BIM (la malla sigue `is_closed`). Defecto real: rompen STL/IFC (caras duplicadas) y, cuando un draw aterriza sobre la región doble-cubierta, orphan-ean sus aristas (los 3 casos orphan) o dejan un seam (plan 242).

**Lo investigado y por qué NO se mergeó:**
- Ejemplo canónico (plan 152): pared x=3 → Face1 (pared completa 0..2.7, área 10.8, sin huecos) + Face2 (mitad inferior 0..1.958, área 7.83) doble-cubren. `apply_rebuild` con **params limpios** (`op=None, fresh=set(), removing=False`) lo disuelve perfecto (13→12 caras, sigue cerrado). Viable en aislado.
- Pero en el fixpoint del push `apply_rebuild` usa `op=op_rims`/keep_segs que **protegen la arista del solape como "estructura de usuario"** (A.4) → no lo disuelve. Un pase final con params limpios SÍ disuelve los del lado-push **sin regresar A.4** (55 dirigidas verdes) — PERO **el draw también crea solapes** (plan 152 falla en step 0 = dibujar el rect ya crea el solape), y ese pase solo corre en `_mutate` (push). Medio fix, costo O(F²), no arregla ninguno de los 4. **Revertido.**
- Detector hole-aware (`_interior_point` de uno dentro del otro, excluyendo huecos): no marca subdivisiones legítimas (mother-con-hueco + child) — esas usan huecos, no doble-cubren. Marca solo duplicados reales.

**Cuándo y cómo atacarlo (proyecto propio, pre-IFC/STL):** (1) agregar el invariante de solape al bench (`_coplanar_overlap`, hole-aware, mismo-lado, no-interior) para *medir*; (2) raíz en AMBOS paths — el draw/heal (`edits.py`/`heal_overlapping_faces`) y el push (fixpoint) — disolviendo el duplicado sin tocar subdivisiones de usuario (la tensión solape-vs-A.4 es la misma del techo del rule-set, así que probablemente necesite la identidad-por-región de A.3). La medición exacta: con el invariante activo el regenerate da ~324 fallos (= los 326 solapes ahora detectados).

### 🔧 Migración del motor a conectividad de vértices compartidos (swap en `main`, 2026-06-08)

Decisión arquitectónica de fondo: migrar el motor de topología del modelo viejo (`Edge`/`Face` guardan **copias** de puntos; la conectividad se **redescubre por posición** con tolerancia) a **vértices compartidos, no-manifold** — el modelo de SketchUp. Motivo: el modelo viejo es frágil (bug float32), push/pull crecía como árbol de casos, move era O(n). El modelo nuevo (`core/mesh.py`) elimina el matching por posición, hace `move` O(1) por vértice y es el cimiento de Groups. **No es half-edge de manual** (ese asume 2-manifold y rompería con la geometría de arquitectura, p.ej. una arista que comparten 2 muros y un piso); es **shared-vertex + incidencia radial**.

**Estado: M0–M2 (swap) + robustez de push/pull MERGEADOS a `main` (`785dc8c`).** La app corre sobre el mesh. El push/pull ahora hace un **stitch watertight** tras cada edición (resolver T-junctions, colapsar vértices colineales redundantes, fusionar regiones coplanares — sin grietas, particiones internas ni costuras fantasma al encadenar pushes), con **undo exacto por snapshot** y **preview en vivo = resultado real**. 153 tests verdes.

Pendiente (no bloquea features): **M3** — turbio/bajo valor, `edits.py` todavía necesita objetos-valor por posición para *simular* el batch antes de ejecutar comandos, así que `geometry.py` no se borra limpio (el beneficio del swap ya está logrado). **M4** — serialización `.igz` indexada por vértices (más valioso; base OBJ/glTF/IFC).

> Plan completo, alcance, riesgos y log de progreso en **`docs/halfedge-migration-plan.md`**. No duplicar acá.

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
cd /home/sumaritux/ingetrazo
source venv/bin/activate
python main.py
```

Python 3.14.4 · venv local en `/home/sumaritux/ingetrazo/venv/` (gitignored).

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
│   ├── dimension.py           ← Dimension (cota estática: a/b/offset; entidad de anotación)
│   ├── texture.py             ← Texture + planar_uv (proyección planar SketchUp-style)
│   ├── mesh.py                ← MOTOR NUEVO: Vertex compartido + Edge (incidencia radial) + Face + Mesh (no-manifold). Reemplazando a geometry.py y parte de topology.py — ver docs/halfedge-migration-plan.md
│   ├── geometry.py            ← motor VIEJO (Edge/Face por copia). En vías de retiro (M3); hoy solo para objetos throwaway de simulación en edits/topology
│   ├── scene.py               ← Scene (envuelve un Mesh; edges/faces son vistas de solo-lectura; + groups[])
│   ├── group.py               ← Group (Mesh propio aislado del weld; base de Components)
│   ├── snap.py                ← compute_snap(...) — snaps + inferencias (extensión, perpendicular con predicción de conexión)
│   ├── topology.py            ← grafo/geometría: ciclos, intersección, contención, chord/chain split + heal (orient/holes/overlaps/orphans/T-junctions)
│   ├── triangulate.py         ← port de earcut (huecos) + fan convexo; plane_axes, is_convex
│   ├── edits.py               ← build_add_edge(s): planifica split/weld/auto-face/subdivisión (envuelto en SnapshotCompound)
│   └── history.py             ← Command ABC + History (undo/redo) + Add/Delete + Compound + Snapshot* + Group/Heal commands
├── views/
│   ├── main_window.py         ← QMainWindow + menús (File[+Import/Export]/Edit/View/Tools) + toolbar + status bar + Tray
│   ├── tray.py                ← Bandeja lateral (QDockWidget): Materiales (swatches) + Estilo de cota + Info de entidad
│   └── viewport.py            ← QOpenGLWidget — render (+grupos+texturas) + paintGL + tools dispatch + VCB + clipboard + zoom-al-cursor
├── tools/
│   ├── base.py                ← Tool ABC + ToolContext (viewport, world, screen, modifiers, snap)
│   ├── select.py              ← SelectTool (pick edge/face/grupo + Shift-add + Delete + box-select)
│   ├── line.py                ← LineTool (chain + auto-close + VCB float/tuple)
│   ├── rectangle.py           ← RectangleTool (4 edges + 1 face CompoundCommand)
│   ├── rotated_rectangle.py   ← RotatedRectangleTool (K) — rect en ángulo (3 clics)
│   ├── circle.py              ← CircleTool (C) + PolygonTool (G) — N-gon centro+radio
│   ├── arc.py                 ← ArcTool (A, 2pt+bulge) + ThreePointArcTool (J) — polilínea soft
│   ├── move.py                ← MoveTool (mueve posiciones o un grupo entero; snap/VCB/axis magnético)
│   ├── offset.py              ← OffsetTool (F) — offset de cara → anillo + cara interna (muros con espesor)
│   ├── paste.py               ← PasteTool — pega el clipboard siguiendo el cursor
│   ├── paint.py               ← PaintTool (B) — color por cara (attrs["color"]); Alt=eyedropper
│   ├── dimension.py           ← DimensionTool (D) — cota estática de 3 clics
│   └── pushpull.py            ← PushPullTool (extrude / recess / step / pasante; stitch watertight)
├── formats/
│   ├── igz.py                 ← save_scene / load_into (JSON `.igz`, schema versionado; color por cara)
│   ├── stl.py                 ← save_stl (binario, triángulos world-space + normal outward) — impresión 3D
│   └── obj.py                 ← save_obj / load_obj (vértices indexados + .mtl con color; import funde coplanares + orient)
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

- `tests/` con **~99 tests** pytest (estrenados en la Fase 1, antes vacía). Cubren topología (automerge, edge/face split, chord/chain, multi-ciclo, contención), triangulación con huecos (earcut), subdivisión por borde, y push/pull sustractivo/solid-aware (recess, grada, poda de huérfanos). Correr: `source venv/bin/activate && python -m pytest tests/ -q`. `pytest>=8.0` en `requirements.txt`.
- GitHub Actions en `.github/workflows/` vacío (pendiente de portar el setup desde IngePresupuestos cuando empecemos a empaquetar releases).

---

## Memorias de Claude relacionadas

**De este proyecto** (`~/.claude/projects/-home-sumaritux-ingetrazo/memory/`):

- `project_face_plane_inference_done.md` — convención para tools nuevos: leer `tool.work_plane` y usar `_plane_axes(normal)` en vez de hardcodear XY.

**Del proyecto hermano IngePresupuestos** (`~/.claude/projects/-home-sumaritux-ingepresupuestos-pyside6/memory/`):

- `project_wasia_iniciado.md` — decisiones estratégicas originales del proyecto cuando aún se llamaba Wasia (GPL-3.0, idioma inglés, monetización via integración con IngePresupuestos). Nombre del archivo histórico; el contenido sigue aplicando a IngeTrazo.
- `feedback_wayland_paintgl_explicito.md` — Wayland exige `glClear` en `paintGL`.
- `feedback_pyside6_matrix_vector_mul.md` — `QMatrix4x4 * QVector4D` no bindea; usar `.map()`.
