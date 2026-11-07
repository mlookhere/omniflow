# Windows/MuMu hardware acceptance checklist

**Status:** Not validated on actual MuMu hardware. Automated CI checks cannot prove
that MediaCodec Error 22 never occurs or that 10 Windows emulator processes are
sustainable on a particular PC. Keep control issue #1 and PR #2 open.

### Real Windows benchmark, 2026-11-07 (one MuMu)

Uploaded evidence from a 120-second run on `127.0.0.1:16640`:

- Video delivered **2,469 frames / 20.57 FPS**, longest preview/video gap **0.39 s**, no fallback snapshots or observed MediaCodec Error 22. The video threshold passed, but Error 22 recovery remains **unverified**, not automatically green.
- ADB screenshots: **87 captures / 121.078 s = 0.719 FPS**, below required **0.8 FPS**. Mean capture **1,373.77 ms**, p95 **1,719 ms**. Real workflow recognition p95 **16 ms**; no capture/detector errors. Screenshot acquisition, not recognition, is the measured bottleneck. The live session dashboard now shows observed screenshot+normalization p95
and (for built-in MuMu devices) separate ADB exec and PNG decode p95 for
the last 120 successful frames, **without additional screenshot commands**.
The ADB stage includes Android PNG encoding, ADB startup and transfer; the
PNG stage is local decoding, and the difference from total includes
normalization. The built-in production PNG screenshot command now has a
**6-second subprocess timeout**, matching the unchanged six-second
frame-age limit; a timeout raises a recoverable ADB error and forces two
fresh matching captures before further input. Read-only PNG profiling now
uses the same six-second ADB subprocess timeout and records this provenance,
so a stalled PNG request cannot be accepted under a looser diagnostic policy.
Raw framebuffer profiling remains separate with a 15-second diagnostic
timeout; other ADB operations are unaffected. The independent assessor
rejects missing or >6s PNG timeout evidence. The ten-device PNG benchmark
uses the same built-in device screenshot method and six-second timeout as
production, records `adb_timeout_seconds` in JSON, and the read-only
verifier and one-device capacity estimate both reject absent or loosened
timeout provenance.
This change bounds a blocked screenshot, **not** measured screenshot FPS.
The dashboard additionally shows rolling successful-frame FPS, elapsed
observation window (up to 120s), and the largest valid-frame gap including
time since the last capture. The rate appears after at least 10 seconds of
observation, and stalled/paused workers can show growing gaps. These metrics
do not initiate screenshots or count stale thumbnail repaints as captures.
A frame that becomes older than six seconds during OCR is rejected before
it reaches live capture telemetry; another check after preview publication
still prevents slow callbacks from authorizing stale input. Such stale
observations reset recognition and capture-stage latency state.
Every Android tap, swipe, key, text entry and workflow launch now rechecks
screenshot age immediately before dispatch, including the first command.
Delayed coordinate preparation cannot authorize input from expired pixels.
This is simulated input safety validation, not a physical game-input test.
They are **not** strict hardware acceptance: a full unbroken 120-second
benchmark, explicit failure accounting, ten-device contention and real-game
input/recovery tests are still required.
- Whole-host usage (59 samples): CPU avg **27.51%**, RAM avg **28,368.43 MiB** (43.87%), NVIDIA GPU avg **8.05%** (VRAM avg **3,859.81 MiB**). No idle baseline, so these figures **cannot be extrapolated numerically** to ten emulators.
- Read-only verifier **failed** on capture FPS. No 10× capacity projection was generated (correctly fail-closed). Ten-device contention, real gestures and branch policy remain outstanding.

**Next measurement:** profile ADB `screencap` transport/decode latency; evaluate a bounded **shared decoded H.264 frame source** as a capture alternative for the selected instance, with freshness/rotation and automatic fallback. The selected preview's watchdog and retry fallback serialize their own
ADB screenshot requests and may reuse one frame for at most 2 seconds from
capture start. While the selected workflow is actively taking screenshots,
the video preview now uses its latest worker-captured thumbnail and **skips
separate ADB fallback requests** when the thumbnail is stale or absent;
paused or inactive sessions still permit standalone fallback screenshots.
This avoids two competing screenshot loops for one device, without adding
encoders or supplying a cached frame to automation decisions. The production
worker now also schedules its default 0.9-second screenshot interval from
the **start** of the previous capture/recognition step, rather than sleeping
another 0.9 seconds after it completes. Slow captures therefore begin the
next poll without redundant delay. No capture requests overlap on a worker,
and the normal pause, stop, and exponential error-backoff paths are unchanged.
These are software contention and scheduling fixes, **not** measurements of
improved real MuMu FPS. Rerun the full 120-second physical benchmark before
claiming the >=0.8 FPS gate. Do not enable ten encoders by default or weaken
the 0.8 FPS criterion.

### Compare uncompressed Android capture (opt-in diagnostic only)

Android's `screencap` can send raw pixel data instead of compressed PNG.
OmniFlow's new decoder validates the Android framebuffer header, exact byte
length, dimensions and pixel format (RGB565/888, RGBA/RGBX/BGRA 8888);
unsupported formats **fail closed**. AOSP raw captures may use 12-byte or
16-byte headers depending on Android version.

Run a separate 120-second read-only profile on the same game screen:

```powershell
.\.venv\Scripts\python.exe tools\profile_capture.py --serial 127.0.0.1:16640 --seconds 120 --poll 0.9 --min-fps 0.8 --capture-format raw --output hardware-evidence\capture-profile-raw.json
```

Compare with the normal PNG profile. Both include `timings_ms.decode_ms`,
`adb_exec_ms`, `normalize_ms`, total timings and `payload_bytes_mean`.
The PNG mode continues to report its legacy `png_decode_ms` and `png_bytes_mean`.
Both modes now record `adb_timeout_seconds` (PNG 6s, raw 15s); reports
with missing or loosened timeout provenance fail the independent assessor.
Raw mode reports `raw_decode_ms`. Large uncompressed transfers can be
**slower**, so never assume raw is faster or switch the production workflow
without real, repeated device measurements and image-equivalence checks.


### Assess PNG versus raw without automatically changing capture

After both 120-second profiles and the stationary PNG/raw fidelity check
have produced JSON reports, combine them in **one read-only decision**:

```powershell
.\.venv\Scripts\python.exe tools\assess_capture.py --png hardware-evidence\capture-profile.json --raw hardware-evidence\capture-profile-raw.json --fidelity hardware-evidence\capture-equivalence.json --output hardware-evidence\capture-assessment.json
```

This assessment rechecks **actual** frame counts/duration/FPS, stage timings,
source geometry across all three reports, a strict **6-second maximum gap between
valid screenshots**, source format, matching MuMu serials (including localhost aliases), at least
three stable fidelity triplets, zero transport errors, integer frame totals,
a recognized MuMu ADB serial and capture lasting at least 95% of its own
requested duration, with unchanged **120-second and 0.8-FPS criteria**. A PNG FPS failure can be valid baseline data; the raw
candidate must meet 0.8 FPS, improve measured FPS by at least 10%, and have
lower capture p95 latency. When raw is slower the assessment can still be
complete, but it explicitly returns `raw_candidate_for_further_validation: false`.
Missing, inconsistent, shortened or loosened evidence fails closed and
overwrites any old success artifact. A completed comparison **only means
the diagnostic evidence is internally consistent**, not that raw capture
works in production. Evidence files are not authenticated same-session PC
attestations. Never enable raw capture without further live game, orientation,
input and resource validation. No run approves ten emulator operation.


### Verify PNG/raw image fidelity on a stationary screen

Before considering raw capture for production, capture **PNG → raw → PNG**
triplets on a stationary game menu. The PNG controls must match each other
before the raw sample is judged; moving scenes fail closed rather than
producing a misleading format verdict.

```powershell
.\.venv\Scripts\python.exe tools\compare_capture.py --serial 127.0.0.1:16640 --triplets 3 --output hardware-evidence\capture-equivalence.json
```

No screenshots are saved; the read-only JSON records original dimensions,
payload sizes and changed-pixel fractions. Fidelity is checked at the native
screenshot resolution, **before any 540x960 normalization**, so fine pixel
differences cannot disappear during downsampling. The JSON records
`comparison_pixel_space: native_source`; the assessor rejects older reports
without this native-pixel provenance. Defaults allow an 8-level BGR
per-channel difference on at most 0.5% of original source pixels. Rotation, unavailable
capture, unstable controls and raw mismatches all fail. A passing result
proves only the checked stationary-screen image similarity, **not** live
gameplay equivalence, capture FPS, input correctness or hardware acceptance.


### Profile the slow ADB screenshot pipeline (M6)

The one-MuMu baseline measured only **0.719 screenshot FPS**. Before modifying the
capture backend, measure three stages separately on the same Windows machine:

```powershell
.\.venv\Scripts\python.exe tools\profile_capture.py --serial 127.0.0.1:16640 --seconds 120 --poll 0.9 --min-fps 0.8 --output hardware-evidence\capture-profile.json
```

Compare `timings_ms.adb_exec_ms`, `png_decode_ms` and `normalize_ms`
(mean and p95), plus `effective_fps` and `png_bytes_mean`. ADB exec time
includes Android PNG encoding, ADB process startup and transport; these cannot
be separated further by this tool. Profiling sends no game input, starts no
H.264 encoders, and uses the exact same strict PNG decoder as production
screenshots: non-PNG diagnostics, truncated data and OpenCV decode failures
cannot be counted as valid frames. The runtime runner also rejects invalid BGR
frames returned by alternative device adapters and converts screenshot
normalization errors to recoverable ADB errors, clearing pending actions until
two fresh matching screenshots arrive. When normalization removes a top strip
(e.g. 40px in the legacy 749x1369 source), tap, swipe and field-focus points
are now mapped into the **same source content viewport**, rather than the
removed chrome. Rotation still requires two new matches; these simulated
tests do not replace real-game input verification. Missing devices, errors, and FPS below the
unchanged 0.8 threshold remain failures. **This is diagnosis, not proof of a faster
capture backend or ten-instance acceptance.**

### One-command M6 PNG/raw comparison (stationary screen only)

Start the **same named MuMu instance** on a stationary game menu. This is a
separate diagnostic mode from the changing-screen video/FPS acceptance run:

```powershell
.\.venv\Scripts\python.exe tools\collect_hardware_evidence.py --capture-comparison --serial 127.0.0.1:16640
```

This runs two independent **120-second** profiles (PNG and raw), three
PNG → raw → PNG static-screen comparisons, then the strict
`assess_capture.py` check. It saves four JSON reports, four logs and a
manifest in one unique `hardware-evidence/run-*` folder. No screenshots
or game input are retained, no H.264 encoder is started, and production
capture stays PNG. A PNG profile below 0.8 FPS is retained as failure
evidence but does not prevent the assessor from evaluating a faster raw
candidate. A complete comparison and a zero exit code **only** mean
diagnostic evidence passed the consistency assessment; the manifest keeps
`passed_read_only_checks: false` and `hardware_acceptance_complete: false`.
The comparison is not a live gameplay performance or production approval.

For the separate changing-screen acceptance collector, `--serial` now
pins **both video and benchmark** to the same MuMu transport, including
equivalent localhost aliases. If that instance is absent, the benchmark
fails instead of silently selecting a different emulator.

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
The JSON includes per-device effective FPS, p95 capture/recognition latency and
the longest interval without a valid captured/recognized frame (including
startup and shutdown gaps). Every instance must have an explicitly measured
maximum gap no greater than **6 seconds**. A high average FPS cannot hide
a lengthy capture blackout, and missing gap data is not accepted by the
read-only hardware verifier.
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

### Experimental concurrent raw-capture benchmark (read-only)

After collecting same-device PNG/raw performance and stationary image
equivalence, optionally run raw capture with multiple MuMu instances to
measure real ADB transfer contention:

```powershell
.\\.venv\\Scripts\\python.exe tools\\benchmark_mumu.py --instances 10 --seconds 120 --poll 0.9 --min-fps 0.8 --capture-format raw --output hardware-evidence\\capture-ten-raw.json
```

The default remains `--capture-format png`. Raw mode invokes uncompressed
`adb exec-out screencap` and strictly decodes each framebuffer; unsupported
layouts and transport failures are counted as capture errors. Both PNG and raw
benchmarks reject source orientation or resolution changes during a run,
rather than treating resized portrait/landscape images as equivalent.
Per-device FPS, latency, continuity, and host-resource gates remain unchanged. A passing raw
benchmark is **diagnostic performance evidence only**, not a substitute for
live-game image equivalence or a production switch. The independent
`verify_hardware_evidence.py` requires PNG provenance and rejects a raw
benchmark (or one missing its capture-format field), even if it reports
`passed: true`.

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
