import tempfile
import unittest
from pathlib import Path

import numpy as np

from omniflow.engine import OmniRunner
from swordx.workflows import create_workflow,record_step

class Device:
    def __init__(self,frame):self.frame=frame;self.taps=[];self.launches=[]
    def screenshot(self):return self.frame.copy()
    def size(self):return 2160,3840
    def tap(self,x,y):self.taps.append((x,y))
    def launch(self,p):self.launches.append(p)
    def foreground(self):return ''
    def running(self,p):return False

class DryRunTests(unittest.TestCase):
    def test_actions_count_and_relaunch_are_suppressed(self):
        with tempfile.TemporaryDirectory() as t:
            folder=create_workflow('Dry',Path(t))
            f=np.random.default_rng(14).integers(0,255,(960,540,3),dtype=np.uint8)
            k=record_step(folder,'Done',f,[(60,60,200,160)],taps=[(100,200)],end=True)
            import json
            path=folder/'manifest.json';data=json.loads(path.read_text())
            data['actions'][k]['action_chain']=[{'kind':'tap','point':[100,200]}, {'kind':'count_run'}]
            path.write_text(json.dumps(data))
            device=Device(f);runs=[];logs=[]
            runner=OmniRunner(device,folder,'com.example.game',log=logs.append,
                              completed=runs.append,dry_run=True)
            for now in (100,101,102,103,104,105):runner.step(now)
            self.assertFalse(device.taps)
            self.assertFalse(device.launches)
            self.assertEqual(runs,[])
            self.assertTrue(any('DRY RUN' in line for line in logs))

if __name__=='__main__':unittest.main()
