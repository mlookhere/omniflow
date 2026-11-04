"""ADB device facade; every command is explicitly scoped to its MuMu serial."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from swordx.adb import ADB, ADBError


@dataclass
class Device(ADB):
    def ensure_connected(self) -> None:
        # Scheduled workers may be launched before the GUI has discovered any
        # MuMu TCP transports (e.g., after Windows login).
        try:
            if self.serial in self.devices():
                return
            if self.serial.startswith(('127.0.0.1:', 'localhost:')):
                self.connect(self.serial)
        except ADBError:
            # Connection failures are retried by the worker, not mistaken
            # for a stopped Android game.
            raise

    def keyevent(self, code: int) -> None:
        self._run('shell', 'input', 'keyevent', str(code))

    def input_text(self, value: str) -> None:
        # Android input text needs escaped spaces; avoid injecting shell syntax.
        if any(ch in value for ch in '\n\r\\&|;<>$`'):
            raise ADBError('Text contains characters unsupported by Android input text')
        self._run('shell', 'input', 'text', value.replace(' ', '%s'))

    def apps(self) -> list[str]:
        lines = self._run('shell', 'pm', 'list', 'packages', '-3', timeout=15).decode(errors='replace').splitlines()
        return sorted(x.removeprefix('package:') for x in lines if x.startswith('package:'))


def discover_mumu(max_ports: int = 16) -> list[str]:
    """Parallel socket probe avoids sequential 0.15s * 33 port stalls."""
    control = Device()
    connected = control.devices()
    candidates = [f'127.0.0.1:{port}' for port in
                  [7555] + [16384 + 32 * n + offset for n in range(max_ports) for offset in (0, 1)]]
    with ThreadPoolExecutor(max_workers=12) as pool:
        active = [addr for addr, open_ in zip(candidates, pool.map(Device._endpoint_open, candidates)) if open_]
    for addr in active:
        if addr not in connected:
            try:
                control.connect(addr)
            except ADBError:
                continue
    return control.devices()
