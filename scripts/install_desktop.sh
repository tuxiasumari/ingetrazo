#!/usr/bin/env bash
# Install the IngeTrazo launcher + icons for the current user (Linux).
# On Wayland/GNOME the dock and app-grid icon come from the .desktop entry
# and the hicolor icon theme — setWindowIcon alone is not enough.
#
# It also registers branded *document* icons for the file types IngeTrazo
# works with (.igz / .dae / .skp) via a freedesktop MIME package. This is
# purely cosmetic: it makes the file manager show the icons, it does NOT
# change which program opens those files.
#
#   scripts/install_desktop.sh              install
#   scripts/install_desktop.sh --uninstall  remove everything it installed
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

APPS="$HOME/.local/share/applications"
ICONS_HICOLOR="$HOME/.local/share/icons/hicolor"
MIME_DIR="$HOME/.local/share/mime"
MIME_PKG="$MIME_DIR/packages/ingetrazo.xml"
APP_SIZES=(16 32 48 64 128 256 512)
# freedesktop MIME icon names for each document type (see resources/mime).
MIME_ICONS=(application-x-ingetrazo model-vnd.collada+xml application-vnd.sketchup.skp)

refresh_caches() {
  command -v update-desktop-database >/dev/null && \
    update-desktop-database "$APPS" 2>/dev/null || true
  command -v update-mime-database >/dev/null && \
    update-mime-database "$MIME_DIR" 2>/dev/null || true
  command -v gtk-update-icon-cache >/dev/null && \
    gtk-update-icon-cache -f "$ICONS_HICOLOR" 2>/dev/null || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -f "$APPS/ingetrazo.desktop" "$MIME_PKG"
  for size in "${APP_SIZES[@]}"; do
    rm -f "$ICONS_HICOLOR/${size}x${size}/apps/ingetrazo.png"
  done
  for name in "${MIME_ICONS[@]}"; do
    find "$ICONS_HICOLOR" -name "${name}.png" -path '*/mimetypes/*' -delete 2>/dev/null || true
  done
  refresh_caches
  echo "IngeTrazo desinstalado del lanzador."
  exit 0
fi

# ── Launcher ────────────────────────────────────────────────────────────────
mkdir -p "$APPS"
# Point Exec at this checkout's venv + main.py.
sed "s|^Exec=.*|Exec=$ROOT/venv/bin/python $ROOT/main.py %f|" \
    "$ROOT/packaging/ingetrazo.desktop" > "$APPS/ingetrazo.desktop"

# ── Application icon (dock / app grid) ──────────────────────────────────────
for size in "${APP_SIZES[@]}"; do
  dir="$ICONS_HICOLOR/${size}x${size}/apps"
  mkdir -p "$dir"
  cp "$ROOT/resources/icons/ingetrazo_${size}.png" "$dir/ingetrazo.png"
done

# ── Document icons for .igz / .dae / .skp ───────────────────────────────────
# Copy the hicolor mimetype PNGs and register the MIME package so the file
# manager paints the branded icon on those files. Does not steal the opener.
HICOLOR_SRC="$ROOT/resources/icons/hicolor"
if [ -d "$HICOLOR_SRC" ] && [ -f "$ROOT/resources/mime/ingetrazo.xml" ]; then
  cp -r "$HICOLOR_SRC/." "$ICONS_HICOLOR/"
  mkdir -p "$(dirname "$MIME_PKG")"
  cp "$ROOT/resources/mime/ingetrazo.xml" "$MIME_PKG"
fi

refresh_caches

echo "IngeTrazo instalado en el lanzador. Buscalo como 'IngeTrazo'."
echo "Los archivos .igz / .dae / .skp ahora muestran su icono en el explorador."
