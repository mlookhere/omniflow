"""Restart the OmniFlow desktop after abnormal exits, not normal window closes."""
from pathlib import Path
from swordx.watchdog import run_watchdog
from omniflow.storage import root_dir

if __name__=='__main__':
    raise SystemExit(run_watchdog(Path(__file__).with_name('omniflow.py'),root=root_dir()))
