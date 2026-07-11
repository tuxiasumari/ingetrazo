#!/usr/bin/env bash
# Install the IngeTrazo launcher + icons for the current user (Linux).
# On Wayland/GNOME the dock and app-grid icon come from the .desktop entry
# and the hicolor icon theme — setWindowIcon alone is not enough.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
# Point Exec at this checkout's venv + main.py.
sed "s|^Exec=.*|Exec=$ROOT/venv/bin/python $ROOT/main.py %f|" \
    "$ROOT/packaging/ingetrazo.desktop" > "$APPS/ingetrazo.desktop"

for size in 16 32 48 64 128 256 512; do
  dir="$HOME/.local/share/icons/hicolor/${size}x${size}/apps"
  mkdir -p "$dir"
  cp "$ROOT/resources/icons/ingetrazo_${size}.png" "$dir/ingetrazo.png"
done

command -v update-desktop-database >/dev/null && \
  update-desktop-database "$APPS" || true
command -v gtk-update-icon-cache >/dev/null && \
  gtk-update-icon-cache -f "$HOME/.local/share/icons/hicolor" || true

echo "IngeTrazo instalado en el lanzador. Buscalo como 'IngeTrazo'."
