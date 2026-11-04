"""Durable, local-first schedule dispatcher with atomic claims and device leases.

The dispatcher executes only while OmniFlow or its optional schedule agent runs.
Every occurrence is claimed once; stale occurrences are skipped (not replayed).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

UTC = timezone.utc
MAX_INSTANCES = 10
LEASE_SECONDS = 75
DEFAULT_GRACE_SECONDS = 300


def _utc(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError) as exc:
        raise ValueError(f'Unknown IANA time zone: {name}') from exc


def next_occurrence(kind: str, spec: dict, tz_name: str, after: float) -> float | None:
    """Strictly after a UTC timestamp, respecting the configured local timezone."""
    tz = _zone(tz_name)
    if kind == 'once':
        value = datetime.fromisoformat(spec['at'])
        if value.tzinfo is None:
            value = value.replace(tzinfo=tz)
        when = value.astimezone(UTC).timestamp()
        return when if when > after else None
    if kind == 'interval':
        minutes = int(spec['minutes'])
        if not 1 <= minutes <= 10080:
            raise ValueError('Intervals must be 1 to 10080 minutes')
        anchor = float(spec.get('anchor', after))
        gap = minutes * 60
        return anchor if anchor > after else anchor + (int((after - anchor) // gap) + 1) * gap
    if kind not in ('daily', 'weekly'):
        raise ValueError(f'Unsupported schedule type: {kind}')
    hour, minute = map(int, spec['time'].split(':'))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError('Time must be HH:MM')
    days = set(map(int, spec.get('weekdays', []))) if kind == 'weekly' else set(range(7))
    if not days or not days.issubset(set(range(7))):
        raise ValueError('Specify weekdays between Monday (0) and Sunday (6)')
    local = _utc(after).astimezone(tz)
    for offset in range(0, 9):
        day = local.date() + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        # Ignore nonexistent wall times (DST forward) instead of firing at a
        # wrong hour. Ambiguous fall-back times choose the first occurrence.
        if candidate.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != candidate.replace(tzinfo=None):
            continue
        timestamp = candidate.timestamp()
        if timestamp > after:
            return timestamp
    raise ValueError('Unable to find a valid local occurrence within 8 days')


@dataclass(frozen=True)
class Job:
    id: str
    name: str
    serial: str
    package: str
    workflow: str
    kind: str
    spec: dict
    timezone: str
    next_at: float | None
    enabled: bool
    max_minutes: int
    max_runs: int
    grace_seconds: int
    last_status: str = ''


class ScheduleStore:
    """SQLite WAL permits safe use from the GUI and a separate headless agent."""
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL,
                  serial TEXT NOT NULL, package TEXT NOT NULL,
                  workflow TEXT NOT NULL, kind TEXT NOT NULL,
                  spec TEXT NOT NULL, timezone TEXT NOT NULL,
                  next_at REAL, enabled INTEGER NOT NULL DEFAULT 1,
                  max_minutes INTEGER NOT NULL DEFAULT 60,
                  max_runs INTEGER NOT NULL DEFAULT 0,
                  grace_seconds INTEGER NOT NULL DEFAULT 300,
                  last_status TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                  intended_at REAL NOT NULL, claimed_at REAL NOT NULL,
                  outcome TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leases (
                  serial TEXT PRIMARY KEY, owner TEXT NOT NULL,
                  expires_at REAL NOT NULL
                );
            ''')
            # v0.1 development DBs can be reopened without losing schedules.
            columns = {row[1] for row in db.execute('PRAGMA table_info(jobs)')}
            if 'max_runs' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN max_runs INTEGER NOT NULL DEFAULT 0')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA busy_timeout=10000')
            db.execute('PRAGMA journal_mode=WAL')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _job(row: tuple) -> Job:
        ident, name, serial, package, workflow, kind, spec, zone, next_at, enabled, max_minutes, max_runs, grace, status = row
        return Job(ident, name, serial, package, workflow, kind,
                   json.loads(spec), zone, next_at, bool(enabled), max_minutes, max_runs, grace, status)

    FIELDS = 'id,name,serial,package,workflow,kind,spec,timezone,next_at,enabled,max_minutes,max_runs,grace_seconds,last_status'

    def list_jobs(self) -> list[Job]:
        with self._connect() as db:
            return [self._job(row) for row in db.execute(f'SELECT {self.FIELDS} FROM jobs ORDER BY name COLLATE NOCASE')]

    def save(self, name: str, serial: str, package: str, workflow: str,
             kind: str, spec: dict, timezone_name: str, max_minutes: int = 60,
             max_runs: int = 0, grace_seconds: int = DEFAULT_GRACE_SECONDS, job_id: str | None = None,
             enabled: bool = True, now: float | None = None) -> str:
        if not name.strip() or not serial.strip() or not package.strip() or not workflow.strip():
            raise ValueError('Job name, device, package and workflow are required')
        if not 0 <= max_minutes <= 10080:
            raise ValueError('Maximum run duration must be 0 (unlimited) or 1–10080 minutes')
        if not 0 <= max_runs <= 100000:
            raise ValueError('Maximum completed runs must be 0 (unlimited) to 100000')
        if not 0 <= grace_seconds <= 86400:
            raise ValueError('Late start tolerance must be 0–86400 seconds')
        now = time.time() if now is None else now
        schedule_at = next_occurrence(kind, spec, timezone_name, now - 0.001)
        if kind == 'once' and schedule_at is None:
            raise ValueError('One-time schedule must be in the future')
        ident = job_id or uuid4().hex
        with self._connect() as db:
            db.execute('''INSERT INTO jobs(''' + self.FIELDS + ''') VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                          ON CONFLICT(id) DO UPDATE SET
                          name=excluded.name,serial=excluded.serial,package=excluded.package,
                          workflow=excluded.workflow,kind=excluded.kind,spec=excluded.spec,
                          timezone=excluded.timezone,next_at=excluded.next_at,
                          enabled=excluded.enabled,max_minutes=excluded.max_minutes,
                          max_runs=excluded.max_runs,
                          grace_seconds=excluded.grace_seconds,last_status='' ''',
                       (ident, name.strip(), serial.strip(), package.strip(), workflow.strip(),
                        kind, json.dumps(spec), timezone_name, schedule_at, int(enabled),
                        max_minutes, max_runs, grace_seconds, ''))
        return ident

    def set_enabled(self, job_id: str, enabled: bool) -> None:
        with self._connect() as db:
            if enabled:
                row=db.execute('SELECT next_at FROM jobs WHERE id=?',(job_id,)).fetchone()
                if row is None:
                    raise ValueError('Unknown schedule')
                if row[0] is None:
                    raise ValueError('Completed one-time schedule needs a new date before enabling')
            db.execute('UPDATE jobs SET enabled=? WHERE id=?', (int(enabled), job_id))

    def delete(self, job_id: str) -> None:
        with self._connect() as db:
            db.execute('DELETE FROM jobs WHERE id=?', (job_id,))

    def claim_due(self, now: float | None = None) -> list[tuple[Job, bool]]:
        """Atomically consume due occurrences, even with two scheduler processes.

        Boolean is False if late beyond grace; cannot catch up stale tasks.
        """
        now = time.time() if now is None else now
        claimed = []
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute(f'SELECT {self.FIELDS} FROM jobs WHERE enabled=1 AND next_at <= ? ORDER BY next_at', (now,)).fetchall()
            for row in rows:
                job = self._job(row)
                nxt = next_occurrence(job.kind, job.spec, job.timezone, now)
                is_on_time = now - job.next_at <= job.grace_seconds
                outcome = 'claimed' if is_on_time else 'skipped_late'
                db.execute('UPDATE jobs SET next_at=?, enabled=?, last_status=? WHERE id=? AND next_at=?',
                           (nxt, int(nxt is not None), outcome, job.id, job.next_at))
                db.execute('INSERT INTO runs(job_id,intended_at,claimed_at,outcome) VALUES(?,?,?,?)',
                           (job.id, job.next_at, now, outcome))
                claimed.append((job, is_on_time))
        return claimed

    def mark(self, job_id: str, intended_at: float, outcome: str) -> None:
        with self._connect() as db:
            db.execute('UPDATE runs SET outcome=? WHERE job_id=? AND intended_at=?', (outcome, job_id, intended_at))
            db.execute('UPDATE jobs SET last_status=? WHERE id=?', (outcome, job_id))

    def history(self, limit: int = 100) -> list[tuple]:
        with self._connect() as db:
            return db.execute('SELECT job_id,intended_at,claimed_at,outcome FROM runs ORDER BY id DESC LIMIT ?', (limit,)).fetchall()

    def acquire(self, serial: str, owner: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM leases WHERE expires_at <= ?', (now,))
            if db.execute('SELECT owner FROM leases WHERE serial=?', (serial,)).fetchone():
                return False
            # Limit active sessions across GUI/headless processes to ten.
            if db.execute('SELECT COUNT(*) FROM leases').fetchone()[0] >= MAX_INSTANCES:
                return False
            db.execute('INSERT INTO leases VALUES(?,?,?)', (serial, owner, now + LEASE_SECONDS))
            return True

    def heartbeat(self, serial: str, owner: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._connect() as db:
            cursor = db.execute('UPDATE leases SET expires_at=? WHERE serial=? AND owner=? AND expires_at>?',
                                (now + LEASE_SECONDS, serial, owner, now))
            return cursor.rowcount == 1

    def release(self, serial: str, owner: str) -> None:
        with self._connect() as db:
            db.execute('DELETE FROM leases WHERE serial=? AND owner=?', (serial, owner))
