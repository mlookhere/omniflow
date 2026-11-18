"""Small GUI-process supervisor. Never restarts an intentional, clean exit."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import data_dir

PREFERENCES = 'desktop_recovery.json'
MAX_CRASHES = 3
CRASH_WINDOW_SECONDS = 120


def restart_enabled(root: Path | None = None) -> bool:
    path = (root or data_dir()) / PREFERENCES
    try:
        return json.loads(path.read_text(encoding='utf-8')).get('restart_on_crash', True) is True
    except (OSError, ValueError, TypeError, AttributeError):
        return True


def set_restart_enabled(enabled: bool, root: Path | None = None) -> None:
    folder = root or data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / PREFERENCES
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps({'restart_on_crash': bool(enabled)}, indent=2), encoding='utf-8')
    os.replace(temporary, target)


def run_watchdog(app_path: Path, *, root: Path | None = None,
                 launch=None, clock=None, sleep=None) -> int:
    """Restart only abnormal exits; cap crash loops. Dependencies injectable for tests."""
    folder = root or data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    launch = launch or subprocess.Popen
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    crashes: list[float] = []
    log_path = folder / 'desktop_crashes.log'
    while True:
        with log_path.open('ab') as log:
            try:
                kwargs = dict(cwd=str(app_path.parent), stdin=subprocess.DEVNULL,
                              stdout=log, stderr=subprocess.STDOUT)
                if sys.platform == 'win32':
                    kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
                process = launch([sys.executable, str(app_path)], **kwargs)
                exit_code = process.wait()
            except OSError as exc:
                log.write(f'Could not start SwordXAuto: {exc}\n'.encode('utf-8', errors='replace'))
                return 1
            if exit_code == 0:  # Closing the GUI is intentional.
                return 0
            log.write(f'GUI exited unexpectedly (code {exit_code}).\n'.encode())
            if not restart_enabled(folder):
                return int(exit_code or 1)
            now = clock()
            crashes = [previous for previous in crashes if now - previous < CRASH_WINDOW_SECONDS]
            crashes.append(now)
            if len(crashes) > MAX_CRASHES:
                log.write(b'Restart limit reached; review desktop_crashes.log before restarting.\n')
                return int(exit_code or 1)
            log.write(f'Restarting GUI (attempt {len(crashes)}/{MAX_CRASHES}).\n'.encode())
        sleep(2)
