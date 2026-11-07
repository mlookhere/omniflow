# Windows/MuMu hardware acceptance checklist

**Status:** Not validated on actual MuMu hardware. Automated CI checks cannot prove
that MediaCodec Error 22 never occurs or that 10 Windows emulator processes are
sustainable on a particular PC. Keep control issue #1 and PR #2 open.

## 0. Collect a complete read-only evidence set (Windows)

For a **single-MuMu capacity estimate**, start one emulator on a changing
game screen, then run:

```powershell
.\.venv\Scripts\python.exe tools\collect_hardware_evidence.py --instances 1 --workflow "path\to\workflow"
```

One instance is now the default. The report contains measured one-device
capture/OCR/video health and an idealized **10× aggregate screenshot-work
estimate**. This is NOT measured ten-device throughput, RAM, CPU or GPU usage.
Whole-host resource samples include Windows and other programs. To estimate
ten-instance resource use, measure the machine at idle first and multiply
the **incremental** one-instance usage by ten. Concurrent load may scale worse.

For actual ten-device validation, start all ten emulators and explicitly run:

```powershell
.\.venv\Scripts\python.exe tools\collect_hardware_evidence.py --instances 10 --workflow "path\to\workflow"
```

The optional `--serial 127.0.0.1:16384` selects the video instance;
omitting `--workflow` still benchmarks capture but leaves real OCR/image
recognition unverified. The command sequentially runs the existing 120-second
video diagnostic, 120-second capture benchmark for the selected instance count,
and strict verifier.
It sends no game touches, never weakens the fixed FPS/recovery thresholds, and
creates a unique ignored `hardware-evidence/run-*` folder for each attempt.
The folder contains the three JSON reports, per-step logs, and a manifest
recording exact exit codes and incomplete/missing artifacts. A zero collector exit code means **only** the requested count's read-only
gates succeeded. A single-device pass cannot validate ten-device operation,
and neither mode establishes full hardware acceptance. Missing files and timed-out steps fail closed.

Review the reports and attach evidence to [control issue #1](https://github.com/mlookhere/omniflow/issues/1).
Manual touch/swipe/orientation, Error 22 reproduction where applicable,
per-emulator resource analysis and GitHub branch protection remain separate
acceptance requirements. The run manifest is observational, not authenticated
same-host attestation.

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
If discovery fails or fewer than ten devices are connected, `--output` still
records an atomic `passed: false` report with the discovered and actually
measured instance counts; it never preserves an older successful report.
If host telemetry cannot start or finish, the benchmark records an explicit
`passed: false` report rather than leaving an earlier successful JSON artifact.
Device captures already completed are retained in that failure report.
The benchmark **does not** measure H.264 preview throughput. It now also
records low-rate **whole-host** CPU and RAM via `psutil` and NVIDIA GPU/VRAM
via the optional `nvidia-smi` command. If NVIDIA telemetry is unavailable,
`host_resources` reports missing GPU samples and the reason; it never reports
unmeasured GPU usage as 0%. These metrics cover the entire Windows host, not
individual MuMu processes or GPU video-encode engine usage.

## 3. Observe resources and recovery

While all ten devices are active, review `host_resources` in `capture-ten.json`.
Check `host_resources.metrics` for average/peak CPU, RAM, GPU and VRAM, and
`host_resources.errors` for unavailable counters. The companion evidence verifier
checks sample coverage and metric consistency, but does not choose acceptable
CPU/GPU budgets for your particular PC. **Task Manager remains necessary** to
inspect per-emulator processes and GPU video encode. Monitor Windows Task Manager GPU video encode,
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
If the requested MuMu device cannot be discovered or ADB discovery raises,
`--output` still overwrites any old success artifact with a
`passed: false` JSON report and zero observed frames; it does not imply
video or screenshot capture was attempted.

## 5. Combine real video and ten-instance evidence without false passes

Run the video and ten-emulator diagnostics above **on the actual Windows/MuMu PC**,
keeping their JSON outputs. Then evaluate them together (read-only):

```powershell
.\.venv\Scripts\python.exe tools\verify_hardware_evidence.py `
    --video video-health.json --benchmark capture-ten.json `
    --instances 10 --seconds 120 --min-video-fps 20 `
    --max-preview-gap 3 --max-video-gap 6 --min-capture-fps 0.8 `
    --output hardware-evidence.json
```

The verifier checks each device's **measured** duration (not merely the requested
command duration), unique devices across localhost aliases, screenshot FPS/errors, longest H.264
stream stall (distinct from screenshot fallback blackouts),
and consistency with the video's observed H.264 frames. No report may pass just
because its embedded `passed` flag says so. Missing/invalid input reports write a
failure artifact and return a nonzero exit code. Thresholds are explicit and
should reflect the actual workload; a static menu should not be used to judge
20 FPS video delivery.

A passing `hardware-evidence.json` means **only** the read-only capture criteria
were demonstrated. The result explicitly preserves missing evidence for real
input/rotation, sustained host CPU/RAM/GPU utilization when not sufficiently
sampled, and Error 22 recovery if it was not observed. Even when resource
telemetry is available, GPU-encode load, per-process attribution and acceptable
operational limits require separate real-PC review. It deliberately never marks full hardware acceptance complete.
Attach the JSON reports to control issue #1 after live measurement; do not use
CI-generated/mock data as a substitute.

### Error 22 log messages versus recovery attempts

Multiple MediaCodec `Error 22` messages can describe **one** failed encoder
startup. New diagnostics distinguish `error22_events` (log messages) from
`error22_windows` (distinct recovery attempts). Every recovery window must be
accounted for by either `error22_recovered` (followed by a real H.264 frame) or
`error22_unresolved` (still pending). The evidence verifier rejects missing
window counts and inconsistent accounting, even if the original diagnostic
reported `passed: true`. Reports written before this field existed must be
recollected using the updated diagnostic; do not infer recovery from legacy
logs or from screenshot fallback alone.
