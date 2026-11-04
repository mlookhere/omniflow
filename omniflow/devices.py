"""ADB device facade; every command is explicitly scoped to its MuMu serial."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from swordx.adb import ADB, ADBError, MUMU_MAX_PORT_SLOTS, mumu_port_numbers


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


def canonical_mumu_serial(serial: str) -> str:
    """Treat localhost and 127.0.0.1 ADB routes to one MuMu port as one device."""
    if ADB.is_mumu_serial(serial):
        return f'127.0.0.1:{int(serial.rsplit(":", 1)[1])}'
    return serial


def mumu_serial_aliases(serial: str) -> tuple[str, ...]:
    """Also recognize leases created before local-device alias normalization."""
    canonical = canonical_mumu_serial(serial)
    if canonical != serial or ADB.is_mumu_serial(canonical):
        return canonical, 'localhost:' + canonical.rsplit(':', 1)[1]
    return (serial,)


def discover_mumu(max_ports: int = MUMU_MAX_PORT_SLOTS) -> list[str]:
    """Discover only MuMu transports; never surface USB phones or aliases twice."""
    ports = mumu_port_numbers(max_ports)
    control = Device()
    connected = control.devices()
    known = {canonical_mumu_serial(serial) for serial in connected}
    candidates = [f'127.0.0.1:{port}' for port in ports]
    # Parallel socket probing avoids waiting 0.15s for each absent instance.
    with ThreadPoolExecutor(max_workers=12) as pool:
        active = [address for address, open_ in
                  zip(candidates, pool.map(Device._endpoint_open, candidates)) if open_]
    for address in active:
        if canonical_mumu_serial(address) not in known:
            try:
                control.connect(address)
            except ADBError:
                continue  # A failed instance must not hide other emulators.
    result, seen = [], set()
    for serial in control.devices():
        if not ADB.is_mumu_serial(serial):
            continue
        key = canonical_mumu_serial(serial)
        if key not in seen:
            seen.add(key)
            result.append(serial)  # Preserve ADB's actual -s serial spelling.
    return result
