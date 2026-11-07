"""Small, subprocess-only Android Debug Bridge client for MuMu Player."""
from __future__ import annotations

import os
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


class ADBError(RuntimeError):
    pass


def bundled_adb() -> str:
    exe = Path(__file__).resolve().parents[1] / 'vendor' / 'adb.exe'
    return str(exe) if os.name == 'nt' and exe.exists() else 'adb'


MUMU_MAX_PORT_SLOTS = 16

def mumu_port_numbers(slots: int = MUMU_MAX_PORT_SLOTS) -> list[int]:
    """MuMu legacy port and v12+ main/fallback ports for 1–16 instances."""
    if not 1 <= slots <= MUMU_MAX_PORT_SLOTS:
        raise ValueError('MuMu port slots must be between 1 and 16')
    return [7555] + [16384 + 32 * i + offset for i in range(slots) for offset in (0, 1)]


@dataclass
class ADB:
    serial: str = ''
    adb_path: str = ''

    def _run(self, *args: str, timeout: float = 10.0, device: bool = True) -> bytes:
        prefix = [self.adb_path or bundled_adb()]
        if device and self.serial:
            prefix += ['-s', self.serial]
        try:
            result = subprocess.run(prefix + list(args), capture_output=True, timeout=timeout,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ADBError(str(exc)) from exc
        if result.returncode:
            detail = result.stderr.decode(errors='replace').strip() or result.stdout.decode(errors='replace').strip()
            raise ADBError(detail or f'ADB exited {result.returncode}')
        return result.stdout

    def devices(self) -> list[str]:
        output = self._run('devices', '-l', device=False).decode(errors='replace')
        return [row.split()[0] for row in output.splitlines()[1:]
                if len(row.split()) >= 2 and row.split()[1] == 'device']

    @staticmethod
    def is_mumu_serial(serial: str) -> bool:
        """Recognize MuMu local ADB ports without mistaking USB phones for MuMu."""
        match = re.fullmatch(r'(?:127\.0\.0\.1|localhost):(\d+)', serial, flags=re.IGNORECASE)
        return bool(match and int(match.group(1)) in mumu_port_numbers())

    @staticmethod
    def _endpoint_open(endpoint: str) -> bool:
        """Only ask ADB to connect when the emulator's TCP listener is active."""
        try:
            host, port = endpoint.rsplit(':', 1)
            with socket.create_connection((host.strip('[]'), int(port)), timeout=0.15):
                return True
        except (OSError, ValueError):
            return False

    def discover(self, preferred: str = '', endpoint: str = '') -> list[str]:
        """Find devices even after a fresh ADB server loses MuMu TCP connections.

        `adb devices` shows already-connected transports only; it does not discover
        running MuMu instances. Probe MuMu's known local ports, connect the open
        ones, then enumerate again. Existing preferred devices are left alone.
        """
        connected = self.devices()
        if preferred and preferred in connected:
            return connected

        ports = mumu_port_numbers()
        candidates = dict.fromkeys([endpoint, preferred] +
                                   [f'127.0.0.1:{port}' for port in ports])
        valid = [address for address in candidates if address and address not in connected
                 and re.fullmatch(r'(?:[\w.-]+|\[[0-9a-fA-F:]+\]):\d{1,5}', address)]
        # Probing the 16-instance port range sequentially would add seconds to
        # discovery whenever most emulator ports are closed.
        with ThreadPoolExecutor(max_workers=12) as pool:
            available = list(pool.map(self._endpoint_open, valid))
        for address, is_open in zip(valid, available):
            if is_open:
                try:
                    self.connect(address)
                except ADBError:
                    continue  # One unavailable instance must not hide another.
        return self.devices()

    def connect(self, endpoint: str) -> str:
        if not re.fullmatch(r'(?:[\w.-]+|\[[0-9a-fA-F:]+\]):\d{1,5}', endpoint.strip()):
            raise ADBError('Use an address such as 127.0.0.1:7555')
        return self._run('connect', endpoint.strip(), device=False).decode(errors='replace').strip()

    def screenshot(self, *, timeout: float = 15.0) -> np.ndarray:
        png = self._run('exec-out', 'screencap', '-p', timeout=timeout)
        frame = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ADBError('ADB screenshot was empty or unreadable')
        return frame

    def size(self) -> tuple[int, int]:
        text = self._run('shell', 'wm', 'size').decode(errors='replace')
        sizes = re.findall(r'(\d+)x(\d+)', text)
        if not sizes:
            raise ADBError('Could not read device display size')
        return tuple(map(int, sizes[-1]))

    def tap(self, x: int, y: int) -> None:
        self._run('shell', 'input', 'tap', str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        """Android input swipe holds down for duration_ms while moving."""
        self._run('shell', 'input', 'swipe', str(x1), str(y1),
                  str(x2), str(y2), str(duration_ms),
                  timeout=max(10.0, duration_ms / 1000 + 8))

    def running(self, package: str) -> bool | None:
        """Return True/False for a verified process check, None if unsupported.

        Some Android builds lack working ``pidof`` support. Do not interpret a
        missing utility or an unusable ``ps`` response as a stopped game.
        """
        if not re.fullmatch(r'[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)+', package):
            raise ADBError('Invalid Android package ID')
        try:
            pids = self._run('shell', 'pidof', package, timeout=7).decode(errors='replace')
            if any(pid.isdecimal() for pid in pids.split()):
                return True
        except ADBError as exc:
            # Exit 1 is pidof's standard "no matching PID" result; exit 127
            # means the command is absent. Device/transport failures propagate.
            message = str(exc).lower()
            if not (message in ('adb exited 1', 'adb exited 127') or
                    'not found' in message and 'pidof' in message):
                raise

        for args in (('ps', '-A'), ('ps',)):
            try:
                listing = self._run('shell', *args, timeout=8).decode(errors='replace')
            except ADBError as exc:
                message = str(exc).lower()
                if any(word in message for word in ('unknown option', 'bad option', 'usage: ps', 'adb exited 1')):
                    continue
                raise
            lines = listing.strip().splitlines()
            if not lines or not any(column in lines[0].upper().split()
                                    for column in ('NAME', 'CMD', 'COMMAND')):
                continue
            names = (line.split()[-1] for line in lines[1:] if line.split())
            return any(name == package or name.startswith(package + ':') for name in names)
        return None

    def force_stop(self, package: str) -> None:
        self._run('shell', 'am', 'force-stop', package, timeout=12)

    def foreground(self) -> str:
        """Find the foreground Android package across MuMu/Android releases.

        Window and Activity dumps vary by Android version. A missing field
        should not prevent checking the other source, and a transport failure
        should not be mistaken for a closed game.
        """
        sources = (
            (('window',), ('mCurrentFocus', 'mFocusedApp')),
            (('activity', 'activities'),
             ('topResumedActivity', 'mResumedActivity', 'ResumedActivity', 'mFocusedApp')),
        )
        failed = 0
        for args, labels in sources:
            try:
                text = self._run('shell', 'dumpsys', *args, timeout=12).decode(errors='replace')
            except ADBError:
                failed += 1
                continue
            for label in labels:
                # Common forms: Window{... u0 pkg/.Activity},
                # ActivityRecord{... u0 pkg/.Activity}, and pkg/.Activity.
                found = re.search(r'\b' + re.escape(label) +
                                  r'\s*[:=][^\n]*?(?:\bu\d+\s+)?'
                                  r'([A-Za-z_][\w]*(?:\.[\w]+)+)/', text)
                if found:
                    return found.group(1)
        if failed == len(sources):
            raise ADBError('Could not query foreground app from Android')
        return ''

    def installed(self, package: str) -> bool:
        """True when Android's package manager recognizes the exact package."""
        try:
            output = self._run('shell', 'pm', 'path', package, timeout=10)
        except ADBError as exc:
            # On some Android versions pm exits 1 for a missing package.
            detail = str(exc).lower()
            if detail == 'adb exited 1' or 'package not found' in detail or 'unknown package' in detail:
                return False
            raise
        return any(line.lstrip().startswith(b'package:') for line in output.splitlines())

    def launch(self, package: str) -> None:
        output = self._run('shell', 'monkey', '-p', package, '-c',
                           'android.intent.category.LAUNCHER', '1', timeout=15)
        # monkey may exit 0 despite launching no activity.
        status = output.decode(errors='replace').lower()
        if ('no activities found' in status or 'monkey aborted' in status or
                'events injected: 0' in status):
            raise ADBError(f'Cannot launch {package}: no launcher activity (check Android package ID)')