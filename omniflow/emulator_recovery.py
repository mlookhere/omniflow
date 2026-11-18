"""Conservative host-side startup for a specifically owned MuMu instance."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from swordx.adb import ADBError


def instance_index(serial: str) -> int | None:
    """MuMu 12 main/alternate ADB ports identify the same VM index."""
    if not serial.startswith(('127.0.0.1:', 'localhost:')):
        return None
    try:
        port = int(serial.rsplit(':', 1)[1])
    except ValueError:
        return None
    offset = port - 16384
    if offset < 0 or offset % 32 not in (0, 1) or offset // 32 >= 16:
        return None  # Legacy 7555 cannot safely identify an instance.
    return offset // 32


def instance_offline(device) -> bool:
    """ADB absence plus a closed local TCP listener indicates a down VM."""
    if instance_index(device.serial) is None:
        return False
    try:
        if device.serial in device.devices():
            return False
        return not device._endpoint_open(device.serial)
    except (ADBError, OSError):
        return False  # ADB failure is not proof that the host VM exited.


def find_mumu_cli() -> Path | None:
    configured = os.environ.get('OMNIFLOW_MUMU_CLI')
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    roots = [Path(os.environ[key]) for key in ('PROGRAMFILES', 'PROGRAMFILES(X86)')
             if os.environ.get(key)]
    candidates = [root / product / subdir / executable
                  for root in roots
                  for product in ('Netease/MuMuPlayer-12.0', 'Netease/MuMuPlayerGlobal-12.0')
                  for subdir in ('shell', 'nx_main')
                  for executable in ('mumu-cli.exe', 'MuMuManager.exe')]
    return next((path for path in candidates if path.is_file()), None)


def launch_instance(serial: str, *, cli: Path | None = None) -> bool:
    """Request startup; never shutdown/restart or act on unknown instance IDs."""
    index = instance_index(serial)
    cli = cli or find_mumu_cli()
    if index is None or cli is None or not cli.is_file():
        return False
    if cli.name.lower() == 'mumumanager.exe':
        command = [str(cli), 'api', '-v', str(index), 'launch_player']
    else:
        command = [str(cli), 'control', '-v', str(index), 'launch']
    try:
        result = subprocess.run(command, capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ADBError(f'MuMu instance {index} launch failed: {exc}') from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).decode(errors='replace').strip()
        raise ADBError(f'MuMu instance {index} launch failed: {detail or result.returncode}')
    return True
