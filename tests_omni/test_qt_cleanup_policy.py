"""Verify that offscreen Qt shutdown actually drains delayed deletion events."""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import Mock, patch

import conftest


class QtLifecyclePolicyTests(unittest.TestCase):
    def test_session_cleanup_disposes_windows_and_drains_deferred_events(self):
        app = Mock()
        widgets = [Mock(), Mock()]
        app.topLevelWidgets.return_value = widgets
        delivered = []
        core = types.ModuleType('PySide6.QtCore')
        core.QEvent = types.SimpleNamespace(Type=types.SimpleNamespace(DeferredDelete=52))
        core.QCoreApplication = types.SimpleNamespace(
            sendPostedEvents=lambda receiver, event: delivered.append((receiver, event)))
        qt = types.ModuleType('PySide6')
        with patch.object(conftest, '_qt_app', app), patch.dict(
            sys.modules, {'PySide6': qt, 'PySide6.QtCore': core}
        ):
            conftest.pytest_sessionfinish(None, 0)
        app.closeAllWindows.assert_called_once_with()
        for widget in widgets:
            widget.close.assert_called_once_with()
            widget.deleteLater.assert_called_once_with()
        self.assertEqual(delivered, [(None, 52), (None, 52)])
        self.assertEqual(app.processEvents.call_count, 2)
        app.quit.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
