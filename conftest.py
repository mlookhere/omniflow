"""Own and explicitly drain the offscreen Qt application during test teardown.

PySide's implicit Python interpreter shutdown can run after Qt's native
libraries start unloading; that previously caused intermittent Windows aborts
*after* a passed suite. Never suppress pytest or native subprocess exit codes.
"""
from __future__ import annotations

import gc
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
    if _qt_app is None:
        return
    # Destroy widgets while their QApplication and native Qt plugins remain
    # alive. Closing alone leaves deleteLater()/DeferredDelete events queued,
    # which otherwise execute at unpredictable points during Python teardown.
    from PySide6.QtCore import QCoreApplication, QEvent

    _qt_app.closeAllWindows()
    for widget in _qt_app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _qt_app.processEvents()
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _qt_app.processEvents()
    _qt_app.quit()
