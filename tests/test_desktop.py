"""Packaging lifecycle tests using synthetic state and a simulated Cocoa quit."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import desktop
from server import create_server


class DesktopTests(unittest.TestCase):
    def test_default_requires_drive_onboarding_and_developer_override_is_explicit(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(desktop.default_data_dir())
        with patch.dict(os.environ, {'RIVER_DATA_DIR': '~/river-custom-data'}):
            self.assertEqual(desktop.default_data_dir(), Path.home() / 'river-custom-data')

    def test_cocoa_quit_closes_server_once_and_preserves_progress(self):
        callbacks = set()
        before_quit = SimpleNamespace()

        class Event:
            def __call__(self, callback):
                callbacks.add(callback)
                return callback

            def unregister(self, callback):
                callbacks.remove(callback)

        class FakeApp:
            def __init__(self, *args, **kwargs):
                pass

            def run(self):
                for callback in tuple(callbacks):
                    callback()

        fake_rumps = SimpleNamespace(App=FakeApp, events=SimpleNamespace(before_quit=Event()),
                                     clicked=lambda *_: lambda fn: fn)
        with tempfile.TemporaryDirectory() as folder:
            data_dir = Path(folder) / 'data'
            def make_server(*args, **kwargs):
                kwargs['ai_review'] = False
                kwargs['canvas_vault'] = Path(folder) / 'canvas.json'
                result = create_server(*args, **kwargs)
                result.store.state['packagingTest'] = 'synthetic saved progress'
                result.store.save()
                return result
            mask = os.umask(0o077)
            try:
                with patch.dict(sys.modules, {'rumps': fake_rumps}),\
                     patch.object(desktop, 'APP_SUPPORT', Path(folder)),\
                     patch.object(desktop, 'is_ready', return_value=False),\
                     patch.object(desktop.webbrowser, 'open'),\
                     patch.object(desktop.logging, 'basicConfig'),\
                     patch('server.create_server', side_effect=make_server):
                    self.assertEqual(desktop.run_desktop(data_dir, 0), 0)
            finally:
                os.umask(mask)
            self.assertEqual(callbacks, set())
            reopened = create_server(data_dir, port=0, canvas_vault=Path(folder) / 'canvas.json')
            try:
                self.assertEqual(reopened.store.state['packagingTest'], 'synthetic saved progress')
            finally:
                reopened.server_close()
                reopened.store.db.close()
