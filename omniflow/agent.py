"""Optional unattended scheduler, usable at Windows logon without the GUI."""
from __future__ import annotations

import signal
import time
from pathlib import Path
from threading import Event

from .manager import InstanceManager
from .schedule import ScheduleStore
from .storage import root_dir, migrate_legacy, local_workflows


def run(stop: Event | None = None, *, log=None, interval: float = 1) -> None:
    stop = stop or Event()
    root = root_dir()
    migrate_legacy(root)
    database = ScheduleStore(root/'schedules.sqlite3')
    sessions = InstanceManager(database)
    agent_log = root/'agent.log'
    def report(message):
        if log:
            log(message)
        else:
            with agent_log.open('a',encoding='utf-8') as f:
                f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {message}\n')
    report('Schedule agent started')
    try:
        while not stop.is_set():
            try:
                for item in sessions.tick_schedules(local_workflows(root)):
                    report(item)
                for item in sessions.drain_events():
                    report(item)
            except Exception as exc:
                report(f'Agent error: {type(exc).__name__}: {exc}')
            stop.wait(interval)
    finally:
        sessions.stop_all()
        for session in sessions.snapshot():
            if session.worker:session.worker.join(timeout=3)
        report('Schedule agent exited')


def main():
    stop=Event()
    def shutdown(*_):stop.set()
    signal.signal(signal.SIGINT,shutdown)
    signal.signal(signal.SIGTERM,shutdown)
    run(stop)


if __name__=='__main__':main()
