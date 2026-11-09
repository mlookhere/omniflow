"""Regression tests for MuMu auto-discovery without an emulator connection."""
import unittest
from unittest.mock import patch

from swordx.adb import ADB, ADBError


class DeviceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.adb = ADB()

    def test_saved_device_already_available_does_not_reconnect(self):
        with (patch.object(self.adb, 'devices', return_value=['127.0.0.1:7555']),
              patch.object(self.adb, '_endpoint_open') as probe,
              patch.object(self.adb, 'connect') as connect):
            self.assertEqual(self.adb.discover(preferred='127.0.0.1:7555'),
                             ['127.0.0.1:7555'])
            probe.assert_not_called()
            connect.assert_not_called()

    def test_fresh_adb_server_connects_mumu_legacy_port(self):
        with (patch.object(self.adb, 'devices', side_effect=[[], ['127.0.0.1:7555']]),
              patch.object(self.adb, '_endpoint_open', side_effect=lambda p: p.endswith(':7555')),
              patch.object(self.adb, 'connect') as connect):
            self.assertEqual(self.adb.discover(), ['127.0.0.1:7555'])
            connect.assert_called_once_with('127.0.0.1:7555')

    def test_fresh_adb_server_connects_mumu12_second_instance(self):
        with (patch.object(self.adb, 'devices', side_effect=[[], ['127.0.0.1:16416']]),
              patch.object(self.adb, '_endpoint_open', side_effect=lambda p: p.endswith(':16416')),
              patch.object(self.adb, 'connect') as connect):
            self.assertEqual(self.adb.discover(), ['127.0.0.1:16416'])
            connect.assert_called_once_with('127.0.0.1:16416')

    def test_fresh_adb_server_connects_mumu12_fallback_port(self):
        with (patch.object(self.adb, 'devices', side_effect=[[], ['127.0.0.1:16385']]),
              patch.object(self.adb, '_endpoint_open', side_effect=lambda p: p.endswith(':16385')),
              patch.object(self.adb, 'connect') as connect):
            self.assertEqual(self.adb.discover(), ['127.0.0.1:16385'])
            connect.assert_called_once_with('127.0.0.1:16385')

    def test_prefers_saved_endpoint_without_changing_profile(self):
        with (patch.object(self.adb, 'devices', side_effect=[[], ['127.0.0.1:17000']]),
              patch.object(self.adb, '_endpoint_open', side_effect=lambda p: p.endswith(':17000')),
              patch.object(self.adb, 'connect') as connect):
            self.assertEqual(self.adb.discover(preferred='127.0.0.1:17000'),
                             ['127.0.0.1:17000'])
            connect.assert_called_once_with('127.0.0.1:17000')

    def test_one_failed_instance_does_not_prevent_other_instance(self):
        def connect(p):
            if p.endswith(':7555'):
                raise ADBError('Cannot connect')
            return 'connected'
        with (patch.object(self.adb, 'devices', side_effect=[[], ['127.0.0.1:16384']]),
              patch.object(self.adb, '_endpoint_open', side_effect=lambda p: p.endswith((':7555', ':16384'))),
              patch.object(self.adb, 'connect', side_effect=connect) as fn):
            self.assertEqual(self.adb.discover(), ['127.0.0.1:16384'])
            self.assertEqual(fn.call_count, 2)

    def test_closed_ports_never_trigger_connect(self):
        with (patch.object(self.adb, 'devices', side_effect=[[], []]),
              patch.object(self.adb, '_endpoint_open', return_value=False),
              patch.object(self.adb, 'connect') as connect):
            self.assertEqual(self.adb.discover(), [])
            connect.assert_not_called()

    def test_usb_device_is_not_treated_as_tcp_endpoint(self):
        with (patch.object(self.adb, 'devices', side_effect=[['usb-serial'], ['usb-serial']]),
              patch.object(self.adb, '_endpoint_open', return_value=False) as probe,
              patch.object(self.adb, 'connect') as connect):
            self.assertEqual(self.adb.discover(preferred='usb-missing'), ['usb-serial'])
            self.assertNotIn('usb-missing', [args.args[0] for args in probe.call_args_list])
            connect.assert_not_called()

    def test_no_extra_network_calls_after_single_preferred_serial_is_ready(self):
        with (patch.object(self.adb, 'devices', return_value=['emulator-5554']),
              patch.object(self.adb, '_endpoint_open') as probe):
            self.assertEqual(self.adb.discover(preferred='emulator-5554'), ['emulator-5554'])
            probe.assert_not_called()

    def test_mumu_serial_classification_avoids_other_android_devices(self):
        for value in ('127.0.0.1:7555', '127.0.0.1:16384',
                      '127.0.0.1:16416', 'localhost:16385'):
            self.assertTrue(ADB.is_mumu_serial(value), value)
        for value in ('emulator-5554', 'R3CN123', '127.0.0.1:5555',
                      '192.168.1.1:16384', '127.0.0.1:10000'):
            self.assertFalse(ADB.is_mumu_serial(value), value)

    def test_closed_local_port_probe_returns_false(self):
        with patch('swordx.adb.socket.create_connection', side_effect=ConnectionRefusedError):
            self.assertFalse(ADB._endpoint_open('127.0.0.1:16384'))


if __name__ == '__main__':
    unittest.main()
