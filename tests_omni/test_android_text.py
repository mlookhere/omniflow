"""Android input text must be constrained before sending any ADB command."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from omniflow.devices import Device, android_text_argument
from swordx.adb import ADBError


class AndroidTextTests(unittest.TestCase):
    def test_supported_text_is_escaped_for_android_input(self):
        self.assertEqual(android_text_argument('Ready Player_1@example.com'),
                         'Ready%sPlayer_1@example.com')
        device = Device(serial='127.0.0.1:16384')
        with patch.object(device, '_run') as run:
            device.input_text('Ready Player_1@example.com')
        run.assert_called_once_with('shell', 'input', 'text',
                                    'Ready%sPlayer_1@example.com')

    def test_unsupported_characters_rejected_before_adb(self):
        device = Device(serial='127.0.0.1:16384')
        with patch.object(device, '_run') as run:
            for text in ('', '  ', '你好', 'hello;rm', 'A&B', 'a%sb',
                         'hello\\nworld', 'quote"', 'x'*251):
                with self.subTest(text=text), self.assertRaises(ADBError):
                    device.input_text(text)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
