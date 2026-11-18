import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from omniflow.storage import migrate_legacy,local_workflows
from swordx.workflows import create_workflow,record_step


class MigrationTests(unittest.TestCase):
    def test_copy_not_move_and_only_once(self):
        with tempfile.TemporaryDirectory() as path:
            base=Path(path)
            old=base/'SwordXAuto';old.mkdir()
            folder=create_workflow('Favorite',old)
            frame=np.random.default_rng(5).integers(0,255,(960,540,3),dtype=np.uint8)
            record_step(folder,'Main screen',frame,[(20,20,200,150)],taps=[(100,300)])
            before={p.relative_to(folder).as_posix():p.read_bytes() for p in folder.rglob('*') if p.is_file()}
            new=base/'OmniFlow';new.mkdir()
            self.assertEqual(migrate_legacy(new,old),['Favorite'])
            self.assertEqual(migrate_legacy(new,old),[])
            copied=local_workflows(new)['Favorite']
            self.assertNotEqual(folder,copied)
            self.assertEqual(before,{p.relative_to(copied).as_posix():p.read_bytes() for p in copied.rglob('*') if p.is_file()})
            self.assertEqual(before,{p.relative_to(folder).as_posix():p.read_bytes() for p in folder.rglob('*') if p.is_file()})
            self.assertTrue((new/'.legacy-imported').exists())

    def test_missing_legacy_data_is_noop(self):
        with tempfile.TemporaryDirectory() as path:
            self.assertEqual(migrate_legacy(Path(path)/'OmniFlow',Path(path)/'missing'),[])

if __name__=='__main__':unittest.main()
