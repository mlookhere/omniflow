# Windows/MuMu hardware acceptance checklist

**Status:** Not validated on actual MuMu hardware. Automated CI checks cannot prove
that MediaCodec Error 22 never occurs or that 10 Windows emulator processes are
sustainable on a particular PC. Keep control issue #1 and PR #2 open.

## 1. Diagnose selected emulator video

Run in the installed OmniFlow virtual environment, with the intended MuMu
instance started:

```powershell
.\.venv\Scripts\python.exe tools\diagnose_capture.py --seconds 120 --serial 127.0.0.1:16384
```

Record delivered preview FPS, the number of fallback screenshots, reconnect
messages, errors containing `22`, and how quickly video resumes. Repeat on
both changing game screens and static menus, portrait and landscape. The video
stream may be unavailable, but screen-fallback must remain usable. Do not
claim that the full video pipeline is smooth solely because low-rate snapshots
continue.

## 2. Ten independent emulator capture/recognition workers

Start ten MuMu instances, then test the same cadence as the default instance
manager (roughly one screenshot per 0.9 seconds per device):

```powershell
.\.venv\Scripts\python.exe tools\benchmark_mumu.py --instances 10 --seconds 120 --poll 0.9 --output capture-ten.json
```

Optionally measure a *real* workflow's recognition cost by adding
`--workflow <your-workflow-folder>`; this measures image, color and any
configured OCR conditions without sending taps. Use `--min-fps <threshold>`
only after choosing a realistic performance goal for that PC.

This benchmark fails (exit code 1) on fewer than the requested devices, no
frames, any capture/recognition errors, or an explicit FPS threshold miss.
The JSON includes per-device effective FPS and p95 capture/recognition latency.
The benchmark **does not** measure H.264 preview throughput or GPU load.

## 3. Observe resources and recovery

While all ten devices are active, monitor Windows Task Manager GPU video encode,
GPU 3D, CPU, RAM, and per-emulator performance. Observe multiple minutes of
actual workflows, interaction accuracy, state transition retries and failed
process relaunches. Confirm only the selected emulator starts an H.264 video
stream and that another process cannot lease the same device.

## Decision criteria

Record actual hardware evidence in issue #1. No missing observations count as
passes. Merge only when required CI is green **and** live preview/recovery and
multi-instance operation have been verified. Never infer flawless encoder
operation from mocked tests or the absence of a reported error.

## 4. Record failure/recovery evidence, not just average FPS

The read-only video diagnostic now reports the **actual displayed** H.264
frames separately from fallback screenshots. It also measures the longest
period with no preview frame, the longest period with no video frame,
Error 22 occurrences, and time until video returns after Error 22.

```powershell
.\.venv\Scripts\python.exe tools\diagnose_capture.py --seconds 120 `
    --serial 127.0.0.1:16384 --output video-health.json
```

For a changing game screen, use a measured acceptance threshold:

```powershell
.\.venv\Scripts\python.exe tools\diagnose_capture.py --seconds 120 `
    --min-video-fps 20 --max-preview-gap 3 --output video-health.json
```

A static game menu may legitimately render fewer H.264 frames; in that case,
omit the video-FPS threshold but still inspect frame gaps and recovery. To
**explicitly** accept the slower screenshot fallback without claiming the
video stream works, use `--allow-snapshot-only`. The default fails when no
H.264 frames arrive. Unresolved Error 22 events, missing preview frames,
failed thresholds, unexpected thread termination, and unfinished worker
shutdown all fail the diagnostic. JSON is written atomically even when
acceptance fails so the result can be attached to issue #1.
