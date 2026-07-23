# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for IngeTrazo.

Produces (one-dir mode):
- Windows: dist\\ingetrazo\\  (ingetrazo.exe + DLLs + _internal resources)
- Linux:   dist/ingetrazo/   (binary + libs)

Build:
    pyinstaller ingetrazo.spec --noconfirm

The same spec works on every platform (mirrors ingepresupuestos.spec in the
sibling repo). Runtime resource lookups are repo-root-relative
(``Path(__file__).parents[1] / "resources"``), which in the frozen one-dir
layout resolves inside ``_internal/`` — so every data destination below
mirrors the repo layout exactly.

Rendering note (Windows): Qt 6 picks desktop OpenGL and falls back to the
bundled software rasterizer (``opengl32sw.dll``, shipped by the PySide6
wheel) when the driver can't give a 3.3 core context — both paths satisfy
the viewport's requirements, no forcing needed.
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve()

# ── Bundled assets — destinations MIRROR the repo layout ─────────────────────
datas = [
    ('resources/shaders/*.vert',   'resources/shaders'),
    ('resources/shaders/*.frag',   'resources/shaders'),
    ('resources/icons/*.png',      'resources/icons'),
    ('resources/icons/*.ico',      'resources/icons'),
    ('resources/icons/mimetypes/*.ico', 'resources/icons/mimetypes'),
    ('resources/mime/*.xml',       'resources/mime'),
    ('resources/textures/*.png',   'resources/textures'),
    ('resources/textures/library.json', 'resources/textures'),
    ('resources/components/*.obj', 'resources/components'),
    ('resources/components/*.mtl', 'resources/components'),
    ('resources/components/*.png', 'resources/components'),
    ('resources/components/thumbs/*.png', 'resources/components/thumbs'),
    ('i18n/*.json',                'i18n'),
]

# Optional trees (present today, tolerated if pruned later).
for opt_src, opt_dst in [
    ('resources/styles', 'resources/styles'),
    ('resources/fonts', 'resources/fonts'),
]:
    if (ROOT / opt_src).is_dir():
        datas.append((opt_src, opt_dst))

# ── Hidden imports ───────────────────────────────────────────────────────────
hiddenimports = [
    # Qt submodules sometimes missed by static analysis.
    'PySide6.QtOpenGL',
    'PySide6.QtOpenGLWidgets',
    'PySide6.QtNetwork',        # tile/DEM fetch (georef)
    # Lazily imported project modules (inside functions) — listed for safety.
    'core.text3d',
    'core.textlabel',
    'tools.place_group',
    'tools.paste',
    'georef.points',
    'georef.terrain',
    'georef.dem',
    'georef.profile',
    # Pure-Python .skp backend (our openskp fork with the classic-MFC
    # reader) — imported lazily by formats/skp.py.
    'openskp',
    'openskp.model',
    'openskp._core',
    'openskp.legacy',
    'openskp.vff',
    'openskp.parser',
    'openskp.geometry',
    'openskp.transforms',
    'openskp.materials',
    'openskp.metadata',
    'openskp.triangulator',
]

excludes = [
    'tkinter',
    'matplotlib',
    'pandas',
    'IPython',
    'jupyter',
    'numpy.tests',
    # Dev-only IFC validator: heavy, never imported by the app itself.
    'ifcopenshell',
    'pytest',
]

a = Analysis(
    ['main.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

icon = None
if sys.platform == 'win32':
    win_ico = ROOT / 'resources' / 'icons' / 'ingetrazo.ico'
    if win_ico.exists():
        icon = str(win_ico)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ingetrazo',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # UPX sometimes breaks PySide6 — never enable
    console=False,              # GUI app, no terminal window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ingetrazo',
)
