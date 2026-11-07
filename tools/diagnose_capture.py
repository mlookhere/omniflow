r"""Read-only real-device MuMu H.264 preview recovery diagnostic.

Examples:
    .venv\Scripts\python.exe tools\diagnose_capture.py --seconds 120 --output capture.json
    .venv\Scripts\python.exe tools\diagnose_capture.py --seconds 120 --max-preview-gap 6

No touches, game launches, workflow execution, or configuration changes.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omniflow.capture_health import CaptureHealth, evaluate_capture
from tools.benchmark_mumu import write_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only OmniFlow video health/recovery report')
    parser.add_argument('--serial', default='', help='MuMu ADB serial (default: first discovered instance)')
    parser.add_argument('--seconds', type=int, default=120, help='Diagnostic duration (10–300 seconds)')
    parser.add_argument('--min-video-fps', type=float, default=0, help='Optional minimum displayed H.264 FPS')
    parser.add_argument('--max-preview-gap', type=float, default=0, help='Optional maximum tolerated blackout in seconds')
    parser.add_argument('--allow-snapshot-only', action='store_true',
                        help='Treat fallback-only preview as usable, not as a successful video stream')
    parser.add_argument('--output', type=Path, help='Write atomic JSON evidence report')
    args = parser.parse_args(argv)
    if not 10 <= args.seconds <= 300:
        parser.error('--seconds must be between 10 and 300')
    for name in ('min_video_fps', 'max_preview_gap'):
        val = getattr(args, name)
        if not math.isfinite(val) or val < 0:
            parser.error(f'--{name.replace("_", "-")} must be nonnegative and finite')

    from omniflow.devices import discover_mumu

    def fail_before_capture(message: str) -> int:
        # Do not retain stale successful output from an earlier diagnostic.
        now = time.monotonic()
        report = {'device': args.serial or None,
                  **CaptureHealth(now).summary(now),
                  'passed': False, 'failures': [message]}
        if args.output:
            write_json(args.output, report)
            print(f'JSON evidence: {args.output}')
        print(message, file=sys.stderr)
        return 2

    try:
        serials = [args.serial] if args.serial else discover_mumu()
    except Exception as exc:
        return fail_before_capture(f'MuMu discovery failed: {exc}')
    if not serials:
        return fail_before_capture('No MuMu emulator found. Start MuMu and check ADB first.')
    serial = serials[0]

    # Qt is only needed after a usable target has been discovered.
    from PySide6.QtCore import QCoreApplication
    from omniflow.ui import VideoThread
    app = QCoreApplication.instance() or QCoreApplication([])
    video = VideoThread(serial)
    started = time.monotonic()
    health = CaptureHealth(started)
    # Qt invokes this callback on the diagnostic main thread during event polling.
    video.error.connect(lambda message: health.record_error(time.monotonic(), message))
    print(f'Read-only capture diagnostic: {serial}, {args.seconds}s.', flush=True)
    deadline = started + args.seconds
    stopped = True
    interrupted = False
    worker_exited = False
    try:
        video.start()
        while time.monotonic() < deadline:
            app.processEvents()
            frame, kind = video.take()
            health.observe(time.monotonic(), kind if frame is not None else None)
            if time.monotonic() - started > 3 and not video.isRunning():
                worker_exited = True
                break  # An unexpected worker exit must not look like a healthy preview.
            time.sleep(0.03)
    except KeyboardInterrupt:
        interrupted = True
        print('Interrupted by user.', file=sys.stderr)
    finally:
        finished = time.monotonic()
        video.stop()
        stopped = video.wait(6000)
        app.processEvents()
    # Shutdown can require seconds; don't count orderly cleanup as a blackout.
    report = {'device': serial, **health.summary(finished)}
    failures = evaluate_capture(report, allow_snapshot_only=args.allow_snapshot_only,
                                min_video_fps=args.min_video_fps,
                                max_preview_gap=args.max_preview_gap)
    if not stopped:
        failures.append('Video worker did not stop within six seconds')
    if worker_exited:
        failures.append('Video worker exited unexpectedly before diagnostic deadline')
    if interrupted:
        failures.append('Capture diagnostic interrupted before deadline')
    report['passed'] = not failures
    report['failures'] = failures
    print(json.dumps(report, indent=2))
    if args.output:
        write_json(args.output, report)
        print(f'JSON evidence: {args.output}')
    if failures:
        print('Capture diagnostic FAILED: ' + '; '.join(failures), file=sys.stderr)
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
