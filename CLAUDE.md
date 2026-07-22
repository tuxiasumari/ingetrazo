# IngeTrazo — modelador 3D libre

**Autor:** Marco Sumari Tellez · **Licencia:** GPL-3.0-or-later · **Repo:** `github.com/tuxiasumari/ingetrazo` (público) · **Web:** https://ingetrazo.com (deploy: `cd ~/ingetrazo-web && npx wrangler deploy && git push`)

Modelador 3D estilo SketchUp para arquitectura/ingeniería civil e impresión 3D. Linux-first, multiplataforma, PySide6. Hermano open-source de [IngePresupuestos](../ingepresupuestos-pyside6/) — la integración IFC cierra el loop modelo → metrado → presupuesto. *(Se llamó **Wasia** 2026-05-21..23; extensión nativa `.igz`.)*

> **Bitácora:** el registro de *lo hecho* vive en los commits de git y en la historia de este archivo (`git log -p CLAUDE.md` para el detalle de sesiones pasadas). Este archivo guarda el **rumbo**: visión, invariantes, estado, pendientes y gotchas vigentes.

---

## 🧭 Visión y principios (NO negociables)

**El nombre ES la tesis: _trazar como en la vida real_.** Si una decisión de UX mete complejidad de CAD y se aleja de "esto se siente como trazar a mano", es la equivocada. Filtro maestro sobre todos los demás.

**El flujo unificado es el producto** (un solo entorno, no 2D-aparte):
> **terreno (fotogrametría/GPS, georef) → trazar encima → aplicar BIM a lo trazado → .ifc → IngePresupuestos (presupuesto → cronograma → control de obra)**

1. **Freeform al núcleo, BIM como tagging encima.** Sin primitivas rígidas tipo Revit; el BIM son metadatos opcionales sobre geometría seleccionada. Lo taggeado va al IFC/metrado; lo demás es dibujo. Referente: BlenderBIM.
2. **2D = Top View + Parallel + Layers**, no un módulo separado. Output profesional de planos (LayOut-equivalente) diferido a v2.
3. **Scope disciplinado.** No competir feature-por-feature con SketchUp/AutoCAD/Revit. Filtro: "¿le sirve al que modela un edificio chico y saca cantidades?".
4. **`Scene` heterogénea:** malla de referencia (display-only, NUNCA entra al motor de topología), contexto georef, geometría freeform editable (el motor), tags BIM. El terreno/DEM/imports pesados jamás pasan por el weld/heal.
5. **AI-native (invariante, sin construir IA aún):** toda edición ejecutable sin mouse vía capa de acciones explícita (`Tool` + `Command` ya lo cumplen ~70%). La IA será orquestación OPCIONAL sobre el motor determinista — genera *recetas* de acciones, nunca mallas directas; el guard de hermeticidad es el validador del loop agéntico. El moat: API de acciones + semántica de dominio (BIM/IFC/normativa latam/metrado) + flujo unificado + libre/Linux/offline/español — no la generación cruda.
6. **Posicionamiento:** IngePresupuestos = caja a corto plazo; IngeTrazo = moat a largo (motor sólido + IFC, integración fuerte después). IFC bridge por archivos primero; embebido solo con motor maduro y licencia compatible.
7. **Licencia:** GPL-3.0 público desde 2026-07-05. Marco es único titular de copyright → puede re-licenciar (p.ej. Apache 2.0 para el embebido futuro) cuando quiera. Si el norte es "libre para siempre", GPL ya es correcto — decisión abierta, sin urgencia.

**Regla de oro de fases:** una fase no está terminada hasta (1) DoD pasa, (2) commiteada y la app arranca sin regresiones, (3) cero "lo dejo para después". **Dogfooding:** priorizar dibujando un escenario real (la "casita", los archivos del usuario) — los gaps aparecen solos; pedir siempre `.igz`/`.skp` de repro.

---

## 📦 Estado actual (2026-07-21)

**v0.2.2 released** (tag + binarios Windows por CI + instalado en la PC del usuario vía `scripts/install_desktop.sh`). El usuario lo usa como programa normal; las sesiones suelen arrancar con reportes de uso real.

**El modelador está MUY completo:** dibujo (línea, rect, rect rotado, círculo, polígono, arcos ×4, offset, sígueme, texto 3D), push/pull robusto con **guard de hermeticidad grado-BIM** (nunca commitea un sólido roto; ops ambiguas se rechazan fail-safe), move/rotar/escala, grupos (v2: entrar con doble clic) + **componentes/instancias compartidas** (proto + xforms, O(1) transformar), materiales + texturas SketchUp-compatible (proyección planar + UVs afines por cara), pintar (B) con eyedropper, **Invertir caras**, cotas + texto guía, capas, bandeja lateral, face culling (dorso azul-gris, color de estilo del archivo), aristas soft/superficies curvas/profiles, **transparencias** (cutout con dither Bayer + materiales translúcidos con pase blend), zoom/zoom ventana, UI bilingüe (`tr()` + `es.json`).

**I/O:** `.igz` (JSON versionado, protos compartidos) · import **`.skp` directo** (ver abajo) · import/export `.dae` COLLADA (export con **geolocalización** para asoleamiento) · import/export OBJ · export STL, **glTF/GLB** (PBR + geolocalización), imagen hi-res del viewport · IFC4 export a mano (STEP sin deps).

**BIM→IFC validado end-to-end:** `.ifc` pasa `ifcopenshell.validate` limpio; cantidades honestas por clase (`Qto_*BaseQuantities`: muro NetSideArea, losa, columna/viga, puerta/ventana, por-metro); puente real con el importador de IngePresupuestos verificado (metrados exactos); "Taggear al dibujar" + push/pull propaga tags. Hallazgos pendientes del lado IngePresupuestos: su `IFC_MAP` pierde `IFCRAILING`/`IFCCOVERING` en silencio; prefiere `max()` de áreas en vez de `Net*` sobre `Gross*`.

**Georref (Track G):** MVP completo — datum local (`SceneDatum`, UTM↔local exacto), teselas XYZ (presets + fuentes custom con nombre persistentes), terreno 3D drapeado (DEM AWS terrarium + mosaico), GeoPath (subsistema propio, NUNCA `Scene.mesh`) con perfil longitudinal vivo + export CSV/PNG, puntos topográficos CSV (P,N,E,Z estación total) con snap bit-exacto. Falta expansión: G5 curvas de nivel, G6 malla fotogramétrica georef (WebODM OBJ) + KML/GeoJSON/DXF.

**Perf:** índice de pick NumPy vectorizado, caras en un draw call (vcolor), chunks por grupo/instancia, hover coalescing — plaza de 394k tris orbita a 60 fps. Pendiente de fondo: import DAE grande ~27 s (ítem "grupos de referencia como arrays NumPy puros").

**Tests: ~871 rápidos + ~800 slow (fuzz 996/1000 limpias, 4 xfail draw-side)** — `python -m pytest tests/ -q -m "not slow"`. CI Windows en tags `v*`.

---

## 🔌 Import SketchUp (.skp) — estrategia OpenSKP

**Decisión 2026-07-21: apoyar de lleno a OpenSKP upstream** (`github.com/iamahsanmehmood/openskp`, MIT, parser clean-room puro-Python) con fork propio como seguro (`tuxiasumari/openskp`, rama `ingetrazo` = main + todos nuestros PRs, instalada editable en el venv). **11 PRs upstream abiertos** (#3–#13): material id, extracción de texturas, material de instancia, UV por cara (matriz 3×3 posicionada/photo-fit), nombres UTF-8, image entities, face-camera behavior, colores de estilo, back material, useTrans, texturas compartidas de materiales colorizados.

- **`formats/skp.py`** = costura: cascada backend openskp → fallback **skp2dae** (satélite Wine + SketchUpAPI.dll del add-on de Blender, proceso separado — la DLL de Trimble JAMÁS entra al árbol GPL; instalador de un clic; `skp2dae.exe` debe re-adjuntarse a CADA release). **Legacy MFC (≤2020): SOPORTADO nativo desde 2026-07-22** — `openskp/legacy.py` en el fork (rama `ingetrazo`, commit local SIN pushear ni PR upstream aún): walker completo del CArchive MFC (store map global, bootstrap de base por el tag del material 2, oráculos parent-de-loop), validado con paridad EXACTA (caras/aristas/área/bbox + fingerprint `skp_diff` idéntico incl. materiales y texturas) en 5 modelos reales v16/v17/v18 vs sus re-guardados VFF de SketchUp Web. Deltas vs la spec pública 2017 (crate Rust `openskp` de hew3d, GPL — usada solo como spec, no su código): vértices ANTES del puntero de curva en CEdge, CLoop +2 bytes de flags, CEdgeUse con preámbulo, back-material antes de los edge-refs redundantes, opacidad gateada por u8 (análogo useTrans), v16 sin pid-mask (CEntity schema 3), flag face-camera en gap[-9] pre-thumbnail. Gaps conocidos: <2 materiales no bootstrapea (cae a skp2dae), colorizados legacy sin re-tintar, CImage/thumbnail doc omitidos, UVs posicionadas sin verificación visual.
- **`formats/skp_openskp.py`** = adapter a payload IngeTrazo: precedencia de materiales SketchUp (cara frontal propia → trasera propia (+flip) → heredado de instancia → estilo), UVs posicionados (receta inversa de la matriz texture→plane), mapeo default en frame LOCAL, colorizados re-tintados (shift/tint HLS, alpha preservado, archivo propio `<mid>_<nombre>`), opacidad, billboards face-me e imágenes, protos por (def, material heredado).
- **Conocimiento del formato TLV** (decodificado acá): cara `AC0D` → `D107` material frontal, `AF0D` trasero, `D007→…→1527` matriz UV 3×3 f64; instancia `6419` (`D107` = pintar componente); `581B→5D1B` = always-face-camera; `8315==2` = image entity; XML: `useTrans` gatea `trans`, `type="2"` = colorizado (imagen compartida cross-carpeta), estilos `4000/4001` = colores frontal/trasero. Detalle en `docs/skp-backend.md` y `docs/openskp-collaboration.md`.
- **`scripts/skp_diff.py`** = harness de paridad (fingerprint por áreas, fusion-invariant) con skp2dae como oráculo caja-negra (límite clean-room: jamás descompilar la DLL).
- **Paridad lograda en archivos reales del usuario** (plaza Yanque, Toril): bbox exacto, área 0.00%, materiales/texturas/grupos/billboards/translucidez al nivel de SketchUp Web.
- Pendientes track: pulir gaps del parser legacy (ver arriba) y decidir cuándo/cómo aportarlo upstream (el usuario pidió NO reportar hasta estar seguros), issue upstream por instance-tree misplacement, respuestas del maintainer.

---

## Stack

PySide6 6.11 (única dep GUI) · OpenGL 3.3 core vía Qt (QOpenGLShaderProgram/Buffer/VAO) · math QtGui (QMatrix4x4/QVector3D) · NumPy 2.5 (DEM, picks, texturas) · openskp editable desde `~/openskp` · ifcopenshell solo como herramienta de dev (NO en requirements). Python 3.14, venv local.

```bash
cd /home/sumaritux/ingetrazo && source venv/bin/activate && python main.py
```

**Portabilidad:** wheels ARM de deps nativas son la fricción real (vigilar ifcopenshell); código propio limpio de asunciones x86. macOS: OpenGL deprecado pero Qt tiene path a Metal.

---

## Arquitectura (mapa)

```
core/     mesh.py (motor: vértices compartidos no-manifold) · scene.py · camera.py ·
          snap.py · topology.py (ciclos/heal) · triangulate.py (earcut) · edits.py ·
          history.py (Command/undo) · orient.py (outward por paridad) ·
          cap_rebuild.py + arrangement.py (rebuild determinista por plano) ·
          group.py (+instancias xform) · bim.py (15 clases IFC + cantidades) ·
          dimension.py · texture.py · text3d.py · textlabel.py · sweep.py · layers.py · i18n.py
views/    main_window.py · viewport.py (render+FBO+picks+VCB+tools dispatch) ·
          tray.py (materiales/cotas/info/capas/BIM/Terreno) · profile_panel.py · icons.py
tools/    base.py (Tool ABC) + select/line/rectangle/circle/arc/move/offset/pushpull/
          paint/dimension/text/geopath/...
formats/  igz.py · skp.py + skp_openskp.py · dae.py · obj.py · stl.py · gltf.py · ifc.py · fuse.py
georef/   datum.py · tiles.py + tile_fetcher.py · dem.py · terrain.py · geopath.py ·
          profile.py · points.py
scripts/  install_desktop.sh · gen_textures/components/doc_icons/app_icon.py · skp_diff.py
docs/     skp-backend.md · openskp-collaboration.md · halfedge-migration-plan.md
```

---

## Convenciones (NO romper)

- **Código/comentarios/commits en inglés**; UI bilingüe vía `core/i18n.py::tr("English source")` + `i18n/es.json` (mapa plano; el inglés es la clave y el fallback). Strings visibles SIEMPRE `tr()`; atributos de tool se traducen en el punto de display, no en la clase. Sin toolchain `.ts`/`.qm`.
- **Z-up** (SketchUp/Blender). X rojo este, Y verde norte, Z azul vertical.
- **Toda mutación pasa por `Command`** (`viewport.history.execute(...)`) — nunca mutar `scene` directo desde un tool.
- **Tools heredan de `tools.base.Tool`**; preview vía `rubber_band_lines()` / `value_label()`; tools de dibujo leen `tool.work_plane` + `plane_axes(normal)` (no hardcodear XY).
- **Identity-equal entities**: `@dataclass(eq=False)` en Edge/Face; la selección guarda referencias.
- Archivos `.igz`/`.skp` de repro del usuario quedan sin trackear en la raíz (scratch).
- Releases: AppId GUID del `.iss` NUNCA cambia; `skp2dae.exe` se re-adjunta a cada release; versión única en `core/version.py`.

---

## Gotchas críticos vigentes (Qt/GL/PySide6)

- **PySide6 bindings:** `QMatrix4x4 * QVector4D` no está bindeado → `mvp.map(...)`. `setUniformValue(loc, 1.0)` rutea el float de Python a la sobrecarga **int** → para uniforms float escalares usar **`setUniformValue1f`** (causó el bug "todo se ve líneas").
- **QOpenGLWidget sin depth real** (PySide6/Mesa/Wayland): el viewport renderea a un FBO propio `CombinedDepthStencil` y blittea. No tocar ese flujo — la regresión es silenciosa (se rompe solo la oclusión). **MSAA va en el FBO de escena**, no en el widget.
- **QPainter contamina el estado GL**: cada `paintGL` re-establece depth test/func/mask, blend, clear color/depth. Y todo `paintGL` debe `glClear` (Wayland estricto).
- **Wayland nativo intercala frames viejos** bajo ráfagas de update (bug del compositor; XWayland perfecto). Decisión del usuario: quedarse en Wayland (multi-monitor DPI mixto). Escape: `QT_QPA_PLATFORM=xcb`.
- **HiDPI:** FBO/viewport/blit en píxeles físicos (`width() * devicePixelRatioF()`).
- **Cutouts + mipmaps:** el discard duro en alpha 0.5 borra texturas caladas al minificar (el promedio cae bajo 0.5) → el shader usa dither Bayer bajo el umbral. Los pases translúcidos (u_opacity<1) van con blend, depth-mask off, después de los opacos.
- **Verificación visual:** `QWidget.grab()` NO captura el overlay QPainter — usar `import -window` (ImageMagick) sobre XWayland, o `viewport.render_image()`. Íconos SIEMPRE validarlos a 24 px reales y en modo oscuro.
- **QSettings en scripts sueltos:** fijar `setOrganizationName/setApplicationName` como main.py o escriben a `Unknown Organization`.
- **Wine re-encodea argv** al codepage ANSI → rutas con acentos a skp2dae pasan por temp ASCII.
- **`capture_state` NO sirve para copiar mallas** (preserva identidad y aliasa); copiar = add_face/add_edge profundo.
- **`orient_outward` y glifos:** el probe de centroide falla en caras cóncavas sin huecos — los windings del texto 3D se fijan analíticamente; no tocar el probe.

---

## 🎯 Pendientes (por prioridad tentativa)

1. **PRÓXIMA SESIÓN (definida 2026-07-21):** el usuario declaró **paridad visual con SketchUp lograda** en sus archivos reales; quedan (a) **detalles del render de transparencias** (afinar el look del pase translúcido/cutouts) y (b) **otras optimizaciones** de IngeTrazo.
2. **Track .skp upstream:** issue instance-tree misplacement (hallado, sin reportar — lo único no reportado), legacy MFC **HECHO local 2026-07-22** (pulir gaps + decidir aporte upstream cuando el usuario dé el OK), seguimiento de PRs #3–#13 + issue #2.
3. **Lado IngePresupuestos** (sesión en aquel repo): `IFC_MAP` +RAILING/COVERING, preferir `Net*` sobre `Gross*`, mapear tags→partidas con el RAG "Sugerir partidas".
4. **Flathub** (definido, sin empezar): IngeTrazo + IngeCAD; capturas PNG (videos opcionales WebM <10 MiB sin audio); `appstreamcli validate` fatal; el punto duro es PySide6+Qt6+GL en Flatpak. App-ID: `com.ingetrazo.IngeTrazo`.
5. **Renders:** (2) glTF PBR + "Enviar a Blender" con plantilla → (3) sombras de sol en viewport → (4) AI render opcional. NUNCA motor fotorrealista propio.
6. **Kit restante:** Tape Measure + guías (T) · Eraser (E) por arrastre · Outliner · Texture Position.
7. **Motor (diferido, atacar cuando duela):** iceberg de solapes coplanares (~326/1000 secuencias, invisible al bench; pre-STL/IFC en serio) + los 4 xfail draw-side + rechazos del guard → resultados correctos. La salida de fondo es **A.3: identidad/attrs por REGIÓN a través del rebuild** (el rule-set de declaraciones llegó a su techo). Limitación conocida: `apply_rebuild` disuelve diagonales de usuario en planos tocados por push.
8. **Perf de fondo:** grupos de referencia como arrays NumPy puros (import DAE 27 s → objetivo archivos 80 MB) · edición de mallas 17k+ tris.
9. **Georref expansión:** G5 contornos · G6 fotogrametría + KML/GeoJSON/DXF · CSV import ya hecho.
10. **v2:** planos profesionales (LayOut-equivalente), DWG/DXF (IngeCAD es el hermano 2D), IFC import, plugins públicos.

---

## Memorias de Claude relacionadas

En `~/.claude/projects/-home-sumaritux-ingetrazo/memory/`: filosofía/flujo unificado · casita dogfooding · AI-native · estrategia OpenSKP (`project-skp-import-strategy-openskp`) · skp2dae · sitio web · migración SketchUp · IngeCAD. Del hermano IngePresupuestos: `project_integracion_ingetrazo_flujo` · gotchas Wayland/PySide6 originales.
