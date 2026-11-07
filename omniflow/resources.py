"""Read-only host resource sampling during a MuMu hardware benchmark.

CPU/RAM are whole-host measurements (psutil), not per-emulator attribution.
NVIDIA GPU/VRAM use nvidia-smi if present; unavailable metrics stay absent.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import threading
import time
from statistics import mean
from typing import Callable


def parse_nvidia_gpu(output: str) -> dict[str, float]:
    """Summarize all reported GPUs without inventing per-emulator usage."""
    rows = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(',')]
        if len(parts) != 2:
            raise ValueError('Unexpected nvidia-smi output')
        try:
            usage, memory = (float(value) for value in parts)
        except ValueError as exc:
            raise ValueError('Unavailable NVIDIA GPU utilization or VRAM') from exc
        if not (math.isfinite(usage) and 0 <= usage <= 100 and
                math.isfinite(memory) and memory >= 0):
            raise ValueError('Invalid NVIDIA GPU utilization or VRAM')
        rows.append((usage, memory))
    if not rows:
        raise ValueError('No GPUs returned by nvidia-smi')
    return {
        'gpu_utilization_percent': max(row[0] for row in rows),
        'gpu_memory_used_mib': sum(row[1] for row in rows),
        'gpu_count': len(rows),
    }


def sample_host_resources(*, gpu_command: str | None = None) -> tuple[dict, list[str]]:
    """Collect one sample; time-limit the external NVIDIA query."""
    sample: dict[str, float | int] = {}
    errors: list[str] = []
    try:
        import psutil
        memory = psutil.virtual_memory()
        sample.update(cpu_percent=float(psutil.cpu_percent(interval=None)),
                      ram_percent=float(memory.percent),
                      ram_used_mib=float(memory.used) / (1024 * 1024))
    except (ImportError, OSError, ValueError) as exc:
        errors.append(f'CPU/RAM unavailable: {type(exc).__name__}: {exc}')
    command = gpu_command if gpu_command is not None else shutil.which('nvidia-smi')
    if not command:
        errors.append('GPU unavailable: nvidia-smi not found')
    else:
        try:
            result = subprocess.run([command, '--query-gpu=utilization.gpu,memory.used',
                                     '--format=csv,noheader,nounits'],
                                    capture_output=True, text=True, timeout=2, check=True)
            sample.update(parse_nvidia_gpu(result.stdout))
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            errors.append(f'GPU unavailable: {type(exc).__name__}: {exc}')
    return sample, errors


class HostResourceMonitor:
    """One optional sleeping sampler; never blocks the screenshot workers."""

    FIELDS = ('cpu_percent', 'ram_percent', 'ram_used_mib',
              'gpu_utilization_percent', 'gpu_memory_used_mib')

    def __init__(self, *, interval_seconds: float = 2.0,
                 probe: Callable[[], tuple[dict, list[str]]] = sample_host_resources):
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError('Resource sampling interval must be positive and finite')
        self.interval_seconds = interval_seconds
        self.probe = probe
        self.stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0
        self._samples: list[dict] = []
        self._errors: set[str] = set()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError('Resource monitor already started')
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._run, name='OmniFlow-host-telemetry',
                                        daemon=True)
        self._thread.start()

    def _run(self) -> None:
        # Prime psutil's interval-less CPU counter before collecting samples.
        try:
            import psutil
            psutil.cpu_percent(interval=None)
        except (ImportError, OSError, ValueError):
            pass
        while not self.stopped.wait(self.interval_seconds):
            try:
                sample, errors = self.probe()
                self._samples.append(sample)
                self._errors.update(errors)
            except Exception as exc:
                self._errors.add(f'Host telemetry failed: {type(exc).__name__}: {exc}')

    def stop(self) -> dict:
        self.stopped.set()
        if self._thread is not None:
            # An in-flight nvidia-smi call is independently bounded to 2s.
            self._thread.join(timeout=3)
        if self._thread is not None and self._thread.is_alive():
            self._errors.add('Host telemetry thread did not finish after Stop')
        elapsed = max(0.0, time.monotonic() - self._started) if self._started else 0.0
        values = {}
        for field in self.FIELDS:
            observed = [float(s[field]) for s in self._samples
                        if field in s and isinstance(s[field], (int, float))
                        and not isinstance(s[field], bool) and math.isfinite(s[field])]
            values[field] = {
                'samples': len(observed),
                'average': round(mean(observed), 2) if observed else None,
                'peak': round(max(observed), 2) if observed else None,
            }
        return {
            'duration_seconds': round(elapsed, 3),
            'interval_seconds': self.interval_seconds,
            'sample_count': len(self._samples),
            'gpu_count': max((s['gpu_count'] for s in self._samples if 'gpu_count' in s),
                             default=None),
            'metrics': values,
            'errors': sorted(self._errors),
        }
