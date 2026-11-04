"""Keep one Qt application alive for the complete offscreen GUI test session.

Legacy SwordXAuto and OmniFlow GUI suites historically create QApplication
independently in individual unittest classes. On Windows/PySide6, releasing the
first class-owned application while other widgets still exist can trigger a
native Qt process-abort *after* pytest has printed an all-passed summary.

One process-scoped owner gives the suite stable Qt object lifetime. This only
runs in an explicitly offscreen test environment and does not affect the app.
"""
from __future__ import annotations

import os

_qt_app = None


def pytest_sessionstart(session):
    global _qt_app
    if os.environ.get('QT_QPA_PLATFORM') != 'offscreen':
        return
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    _qt_app = QApplication.instance() or QApplication([])


def pytest_sessionfinish(session, exitstatus):
    if _qt_app is not None:
        # Dispose any still-open top-level widgets while QApplication remains
        # alive, rather than letting PySide tear them down during interpreter
        # shutdown after pytest has finished reporting its result.
        _qt_app.closeAllWindows()
        _qt_app.processEvents()