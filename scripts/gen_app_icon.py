# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Regenerate the application icon sizes from the master mark.

The icon is the author's own IngeTrazo mark — his SUMARI tri-blade wrapped
around the isometric modelling cube, with the mini-cube at the centre —
processed from his original artwork (background removed outside the hexagon,
tight-cropped, square-padded). The canonical master lives at
``resources/icons/ingetrazo_master.png``; this script only rescales it:

    python scripts/gen_app_icon.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "resources" / "icons"
SIZES = (512, 256, 128, 64, 48, 32, 16)


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    master = QImage(str(ICONS / "ingetrazo_master.png"))
    if master.isNull():
        raise SystemExit("missing resources/icons/ingetrazo_master.png")
    for size in SIZES:
        master.scaled(size, size, Qt.KeepAspectRatio,
                      Qt.SmoothTransformation).save(
            str(ICONS / f"ingetrazo_{size}.png"))
        print("wrote", f"ingetrazo_{size}.png")


if __name__ == "__main__":
    main()
