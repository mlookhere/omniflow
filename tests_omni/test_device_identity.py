"""MuMu 10+ instance discovery and device lease alias safety."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omniflow.devices import (Device, canonical_mumu_serial, discover_mumu,
                              mumu_serial_aliases)
from omniflow.schedule import ScheduleStore
from swordx.adb import ADB, mumu_port_numbers


class MuMuDeviceIdentityTests(unittest.TestCase):
    def test_recognizes_all_sixteen_slots_including_tenth(self):
        ports = mumu_port_numbers()
        self.assertEqual(len(ports), 33)
        self.assertIn(16384 + 32 * 9, ports)  # 10th emulator main port
        self.assertIn(16385 + 32 * 9, ports)  # 10th emulator fallback port
        self.assertTrue(ADB.is_mumu_serial('127.0.0.1:16672'))
        self.assertTrue(ADB.is_mumu_serial('localhost:16673'))
        self.assertTrue(ADB.is_mumu_serial('127.0.0.1:16864'))
        self.assertFalse(ADB.is_mumu_serial('127.0.0.1:16896'))
        self.assertFalse(ADB.is_mumu_serial('192.168.1.4:16672'))

    def test_rejects_invalid_port_slot_counts(self):
        for count in (0, -1, 17):
            with self.subTest(count=count), self.assertRaises(ValueError):
                mumu_port_numbers(count)

    def test_canonicalization_changes_only_mumu_localhost_alias(self):
        self.assertEqual(canonical_mumu_serial('localhost:16672'), '127.0.0.1:16672')
        self.assertEqual(canonical_mumu_serial('127.0.0.1:16672'), '127.0.0.1:16672')
        self.assertEqual(canonical_mumu_serial('localhost:5555'), 'localhost:5555')
        self.assertEqual(canonical_mumu_serial('emulator-5554'), 'emulator-5554')
        self.assertEqual(mumu_serial_aliases('localhost:16672'),
                         ('127.0.0.1:16672', 'localhost:16672'))

    def test_discovery_probes_tenth_slot_and_ignores_non_mumu_devices(self):
        discovered = [
            ['USB1234', '127.0.0.1:5555'],
            ['USB1234', '127.0.0.1:5555', '127.0.0.1:16672'],
        ]
        with patch.object(Device, 'devices', side_effect=discovered), \
             patch.object(Device, '_endpoint_open', side_effect=lambda p: p.endswith(':16672')), \
             patch.object(Device, 'connect', return_value='connected') as connect:
            self.assertEqual(discover_mumu(max_ports=10), ['127.0.0.1:16672'])
            connect.assert_called_once_with('127.0.0.1:16672')

    def test_discovery_deduplicates_two_hostnames_without_reconnecting(self):
        connected = ['localhost:16384', '127.0.0.1:16384', 'usb-serial']
        with patch.object(Device, 'devices', return_value=connected), \
             patch.object(Device, '_endpoint_open', side_effect=lambda p: p.endswith(':16384')), \
             patch.object(Device, 'connect') as connect:
            self.assertEqual(discover_mumu(max_ports=1), ['localhost:16384'])
            connect.assert_not_called()

    def test_unrelated_connected_usb_does_not_appear_as_mumu(self):
        with patch.object(Device, 'devices', return_value=['emulator-5554', 'USB1234']), \
             patch.object(Device, '_endpoint_open', return_value=False), \
             patch.object(Device, 'connect') as connect:
            self.assertEqual(discover_mumu(max_ports=10), [])
            connect.assert_not_called()


class DeviceLeaseAliasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ScheduleStore(Path(self.tmp.name) / 'schedules.sqlite3')

    def test_two_localhost_names_cannot_both_acquire_emulator(self):
        self.assertTrue(self.store.acquire('localhost:16672', 'gui', now=1000))
        self.assertFalse(self.store.acquire('127.0.0.1:16672', 'agent', now=1000))
        self.assertTrue(self.store.heartbeat('localhost:16672', 'gui', now=1020))
        self.store.release('127.0.0.1:16672', 'gui')
        self.assertTrue(self.store.acquire('127.0.0.1:16672', 'agent', now=1020))
        self.assertFalse(self.store.heartbeat('localhost:16672', 'gui', now=1030))

    def test_upgrade_respects_existing_legacy_alias_lease(self):
        with self.store._connect() as conn:
            conn.execute('INSERT INTO leases VALUES (?,?,?)', ('localhost:16384', 'legacy', 1150))
        self.assertFalse(self.store.acquire('127.0.0.1:16384', 'agent', now=1000))
        self.assertTrue(self.store.heartbeat('127.0.0.1:16384', 'legacy', now=1010))
        self.store.release('127.0.0.1:16384', 'legacy')
        self.assertTrue(self.store.acquire('localhost:16384', 'gui', now=1011))

    def test_different_mumu_ports_remain_independent(self):
        self.assertTrue(self.store.acquire('localhost:16672', 'a', now=1000))
        self.assertTrue(self.store.acquire('localhost:16673', 'b', now=1000))
        self.assertFalse(self.store.acquire('127.0.0.1:16673', 'c', now=1000))

    def test_non_mumu_device_ids_are_not_changed(self):
        self.assertTrue(self.store.acquire('emulator-5554', 'a', now=1000))
        self.assertTrue(self.store.acquire('localhost:5555', 'b', now=1000))
        self.assertFalse(self.store.acquire('emulator-5554', 'c', now=1000))


if __name__ == '__main__':
    unittest.main()
