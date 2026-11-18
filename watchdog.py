"""Launch SwordXAuto with optional recovery after abnormal desktop exits."""
from pathlib import Path
from swordx.watchdog import run_watchdog

if __name__ == '__main__':
    raise SystemExit(run_watchdog(Path(__file__).with_name('app.py')))
