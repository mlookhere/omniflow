"""Data safety tests: no overwrites, round trips, profile references, and bad archives."""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np

from swordx.config import ProfileStore, Settings
from swordx.storage import delete_workflow, export_backup, import_backup
from swordx.workflows import BUILTIN, create_workflow, list_workflows, record_step, update_step_delay


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'old'
        self.target = self.root / 'new'
        self.source.mkdir()
        self.target.mkdir()
        self.store = ProfileStore(self.source)
        self.other = ProfileStore(self.target)
        self.store.create('Main', Settings(package='com.sword.test'))
        self.folder = create_workflow('Collected Screens', self.source)
        self.store.save('Main', Settings(package='com.sword.test', workflow='Collected Screens'))
        for _ in range(6):
            self.store.increment('Main')
        self.frame = np.random.default_rng(123).integers(0, 256, (960, 540, 3), dtype=np.uint8)
        record_step(self.folder, 'Tap menu', self.frame, [(20, 20, 100, 100)], taps=[(200, 300)])

    def _backup(self, **kwargs):
        filename = self.root / 'saved.zip'
        export_backup(filename, self.store, base=self.source, **kwargs)
        return filename

    def test_roundtrip_preserves_counts_captures_and_references(self):
        backup = self._backup()
        outcome = import_backup(backup, self.other, base=self.target)
        self.assertEqual(outcome.profiles, ['Default (imported)', 'Main'])
        self.assertEqual(outcome.workflows, ['Collected Screens'])
        self.assertEqual(self.other.runs('Main'), 6)
        self.assertEqual(self.other.get('Main').package, 'com.sword.test')
        self.assertEqual(self.other.get('Main').workflow, 'Collected Screens')
        folder = list_workflows(self.target)['Collected Screens']
        meta = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        action = next(iter(meta['actions'].values()))
        self.assertTrue((folder / 'captures' / action['capture']).is_file())
        self.assertTrue((folder / 'anchors' / next(iter(meta['states'].values()))[0]['file']).is_file())
        # Existing data is undisturbed by the import.
        self.assertEqual(self.other.runs('Default'), 0)

    def test_saved_delay_survives_portable_export_import(self):
        manifest = json.loads((self.folder / 'manifest.json').read_text(encoding='utf-8'))
        key = next(iter(manifest['actions']))
        update_step_delay(self.folder, key, 420)
        backup = self._backup()
        import_backup(backup, self.other, base=self.target)
        folder = list_workflows(self.target)['Collected Screens']
        restored = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(restored['actions'][key]['pre_tap_delay_seconds'], 420)

    def test_reimport_renames_both_and_relinks_profile(self):
        backup = self._backup()
        import_backup(backup, self.other, base=self.target)
        original = (self.target / 'profiles.json').read_bytes()
        again = import_backup(backup, self.other, base=self.target)
        self.assertIn('Main (imported)', again.profiles)
        self.assertEqual(again.workflows, ['Collected Screens (imported)'])
        self.assertEqual(self.other.get('Main').workflow, 'Collected Screens')
        self.assertEqual(self.other.get('Main (imported)').workflow, 'Collected Screens (imported)')
        self.assertEqual(self.other.runs('Main (imported)'), 6)
        self.assertIn('Main', json.loads(original)['profiles'])
        self.assertEqual(len(list_workflows(self.target)), 3)

    def test_exports_never_replace_existing_archive(self):
        backup = self._backup()
        first = backup.read_bytes()
        with self.assertRaises(FileExistsError):
            export_backup(backup, self.store, base=self.source)
        self.assertEqual(backup.read_bytes(), first)

    def test_selected_profile_only(self):
        backup = self._backup(profiles=['Main'], workflows=[])
        outcome = import_backup(backup, self.other, base=self.target, items='profile')
        self.assertEqual(outcome.profiles, ['Main'])
        self.assertEqual(outcome.workflows, [])
        self.assertEqual(self.other.get('Main').workflow, 'Collected Screens')

    def test_selected_workflow_only(self):
        backup = self._backup(profiles=[], workflows=['Collected Screens'])
        outcome = import_backup(backup, self.other, base=self.target, items='workflow')
        self.assertEqual(outcome.profiles, [])
        self.assertEqual(outcome.workflows, ['Collected Screens'])
        self.assertEqual(self.other.names(), ['Default'])

    def test_delete_profile_does_not_delete_its_workflow(self):
        self.store.select('Main')
        self.assertEqual(self.store.delete('Main'), 'Default')
        self.assertIn('Collected Screens', list_workflows(self.source))
        self.assertNotIn('Main', self.store.names())
        with self.assertRaisesRegex(ValueError, 'last profile'):
            self.store.delete('Default')

    def test_delete_workflow_resets_selected_profiles_only(self):
        self.store.select('Main')
        delete_workflow('Collected Screens', self.store, base=self.source)
        self.assertNotIn('Collected Screens', list_workflows(self.source))
        self.assertEqual(self.store.get('Main').workflow, BUILTIN)
        self.assertEqual(self.store.runs('Main'), 6)
        with self.assertRaisesRegex(ValueError, 'Only recorded'):
            delete_workflow(BUILTIN, self.store, base=self.source)

    def test_invalid_archive_must_not_write_to_existing_data(self):
        corrupt = self.root / 'bad.zip'
        with zipfile.ZipFile(corrupt, 'w') as archive:
            archive.writestr('backup.json', json.dumps({'format': 'SwordXAuto-Backup',
                            'version': 1, 'profiles': {}, 'workflows': []}))
            archive.writestr('workflows/0/anchors/../../attack.png', b'bad')
        original = (self.target / 'profiles.json').read_bytes()
        with self.assertRaises(ValueError):
            import_backup(corrupt, self.other, base=self.target)
        self.assertEqual(original, (self.target / 'profiles.json').read_bytes())

    def test_missing_template_must_not_write_data(self):
        archive = self._backup()
        damaged = self.root / 'damaged.zip'
        with zipfile.ZipFile(archive) as src, zipfile.ZipFile(damaged, 'w') as dest:
            for entry in src.namelist():
                if not entry.endswith('.png'):
                    dest.writestr(entry, src.read(entry))
        with self.assertRaisesRegex(ValueError, 'missing or unexpected'):
            import_backup(damaged, self.other, base=self.target)
        self.assertEqual(self.other.names(), ['Default'])
        self.assertEqual(len(list_workflows(self.target)), 1)


if __name__ == '__main__':
    unittest.main()
