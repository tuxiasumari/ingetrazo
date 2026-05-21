"""Wasia entry point.

Free 3D modeler for architecture, civil engineering, and 3D printing.

Copyright (C) 2026 Marco Sumari Tellez and Wasia contributors.
Licensed under GPL-3.0-or-later. See LICENSE.
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from views.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Wasia")
    app.setOrganizationName("Wasia")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
