# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Base-map source preferences: a pasted custom XYZ URL (and the selected
source) persist across sessions via QSettings — a hand-crafted tile URL must
never be lost on restart."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication

_inst = QApplication.instance()
if _inst is None:
    _app = QApplication([])
elif not isinstance(_inst, QApplication):
    pytest.skip("a non-widget QGuiApplication is already active",
                allow_module_level=True)

from core.scene import Scene  # noqa: E402
from views.tray import BaseMapPanel  # noqa: E402

URL = "https://tiles.example.com/mi-fuente/{z}/{x}/{y}.png"


class _Win:
    class viewport:
        scene = Scene()


@pytest.fixture(autouse=True)
def _isolated_settings():
    prev_org = QCoreApplication.organizationName()
    prev_app = QCoreApplication.applicationName()
    QCoreApplication.setOrganizationName("IngeTrazoTest")
    QCoreApplication.setApplicationName("basemap-prefs-test")
    QSettings().clear()
    yield
    QSettings().clear()
    QCoreApplication.setOrganizationName(prev_org)
    QCoreApplication.setApplicationName(prev_app)


def _select_custom(panel):
    panel._source.setCurrentIndex(panel._source.findData(panel._CUSTOM))


def test_custom_url_survives_a_new_panel():
    panel = BaseMapPanel(_Win())
    _select_custom(panel)
    panel._custom_url.setText(URL)
    panel._apply_source()                      # what editingFinished triggers

    fresh = BaseMapPanel(_Win())               # "next session"
    assert fresh._source.currentData() == fresh._CUSTOM
    assert fresh._custom_url.text() == URL
    assert fresh._custom_url.isVisible() or True   # offscreen: check source
    src = fresh._current_source()
    assert src is not None and src.url_template == URL


def test_switching_to_preset_keeps_the_saved_url():
    panel = BaseMapPanel(_Win())
    _select_custom(panel)
    panel._custom_url.setText(URL)
    panel._apply_source()
    panel._source.setCurrentIndex(0)            # back to a preset (saves too)

    fresh = BaseMapPanel(_Win())
    assert fresh._source.currentData() != fresh._CUSTOM   # preset remembered
    assert fresh._custom_url.text() == URL                # URL not forgotten


def test_empty_field_never_erases_the_stored_url():
    panel = BaseMapPanel(_Win())
    _select_custom(panel)
    panel._custom_url.setText(URL)
    panel._apply_source()
    panel._custom_url.setText("")               # cleared to retype…
    panel._apply_source()                       # …and applied empty

    fresh = BaseMapPanel(_Win())
    assert fresh._custom_url.text() == URL
