r"""Read-only, selected-device live video diagnostic for Windows/MuMu.

Run after setup.bat, with MuMu started:
    .venv\Scripts\python.exe tools\diagnose_capture.py --seconds 30
    .venv\Scripts\python.exe tools\diagnose_capture.py --serial 127.0.0.1:16384

No touches, game launches, workflow execution, or configuration changes.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Measure OmniFlow H.264 capture and recovery')
    parser.add_argument('--serial', default='', help='MuMu ADB serial (default: first discovered instance)')
    parser.add_argument('--seconds', type=int, default=30, help='Sample duration, 10–300 seconds')
    args = parser.parse_args(argv)
    if not 10 <= args.seconds <= 300:
        parser.error('--seconds must be between 10 and 300')

    from PySide6.QtCore import QCoreApplication
    from omniflow.devices import discover_mumu
    from omniflow.ui import VideoThread

    serials = [args.serial] if args.serial else discover_mumu()
    if not serials:
        print('No MuMu emulator found. Start MuMu and check ADB first.')
        return 2
    serial = serials[0]
    app = QCoreApplication.instance() or QCoreApplication([])
    video = VideoThread(serial)
    errors: list[str] = []
    counts: Counter = Counter()
    video.error.connect(errors.append)
    print(f'Capture diagnostic for {serial} ({args.seconds} seconds). No touch input sent.', flush=True)
    started = time.monotonic()
    deadline = started + args.seconds
    try:
        video.start()
        while time.monotonic() < deadline:
            app.processEvents()
            frame, kind = video.take()
            if frame is not None:
                counts[kind] += 1
            time.sleep(0.03)
    except KeyboardInterrupt:
        print('Interrupted by user.')
    finally:
        video.stop()
        video.wait(6000)
        app.processEvents()
    elapsed = max(0.001, time.monotonic() - started)
    result = {
        'device': serial,
        'elapsed_seconds': round(elapsed, 1),
        'displayed_video_frames': counts['video'],
        'displayed_video_fps': round(counts['video'] / elapsed, 1),
        'fallback_snapshots': counts['snapshot'],
        'reported_stream_errors': len(errors),
        'recent_errors': errors[-10:],
    }
    print(json.dumps(result, indent=2))
    return 0 if counts['video'] > 0 or counts['snapshot'] > 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
