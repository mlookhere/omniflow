# OmniFlow 0.2.1

**Universal MuMu Player visual automation — Windows x64, Python + PySide6.**

OmniFlow is a substantial, source-based overhaul of the uploaded SwordXAuto project. It is game-agnostic: workflows react to recognized screens rather than a predetermined step number or game-specific dungeon logic.

The core has been exercised with automated tests, including **ten simulated simultaneous emulator sessions**. This release has **not yet been tested on your actual Windows/MuMu installation**, and ten-instance hardware performance or 30 FPS video is not guaranteed. Keep your earlier app until you validate the new workflow on one emulator.

## Install and launch

1. Extract the `OmniFlow` folder somewhere permanent, e.g. `F:\Projects\OmniFlow`.
2. Install **Python 3.12 or 3.13, 64-bit**, with the Windows `py` launcher. Run **`setup.bat`** once. It creates `.venv` and installs PySide6, headless OpenCV, NumPy, PyAV, ONNX Runtime, RapidOCR, host telemetry, and timezone data. Setup fails if the OCR inference smoke test fails.
3. Open MuMu Player and launch a game. Double-click **`start.vbs`** for OmniFlow without a console, or `start.bat` for diagnostics.
4. Open **Dashboard → Refresh**, choose an emulator, click **Detect** for the foreground Android package (or **Apps** to select an installed package), then choose a workflow and press **Start**.

Personal data is stored in **`%LOCALAPPDATA%\OmniFlow`**, not inside the source directory. On first launch, recorded workflows and named profiles from `%LOCALAPPDATA%\SwordXAuto` are **copied**, not moved or overwritten. Existing legacy application files remain untouched. There is also an Import backup option for older exported ZIP workflows.

## v0.2.1 — Reliable MuMu video preview and Error 22 recovery

Android's built-in `screenrecord --output-format=h264` is a live, raw H.264
Annex-B stream. **Error 22 / EINVAL** can occur when an emulator rejects an
encoder configuration or when a decoder probes an incomplete pipe. This
release consumes raw H.264 through PyAV's codec parser instead of demuxing a
container. Each encoder restart creates a fresh decoder, and interrupted
packets are not flushed as if they were complete video frames.

- The selected emulator's video stream is monitored in a separate watchdog
  thread, so a blocking ADB read is interrupted even if the UI is busy.
- Portrait and landscape games get appropriately oriented encoder and
  preview dimensions, rather than forcing every game into a portrait stream.
- The video worker **does not give up after a fixed number of failures**. It
  retries indefinitely while enabled, with exponential backoff capped at 8s,
  using a lower-resolution/lower-bitrate profile after failed starts.
- It keeps only the newest decoded frame for display, with a 30 FPS render
  target. Occasional slower OCR screenshots no longer overwrite healthy video.
- Static game screens are checked using a low-frequency ADB screenshot before
  restarting the encoder. An unchanged screen does not trigger a restart loop.
- When streaming fails, a selected-device screenshot is displayed while H.264
  reconnects. The preview distinguishes these snapshots from live FPS.
- Switching devices stops the old encoder **without a long GUI-thread wait**;
  a new encoder starts only after the previous worker has finished.
- Android/decoder errors are recorded in OmniFlow's activity log. Missing PyAV
  disables video mode cleanly while leaving screen-based automation available.
- Only the selected emulator uses an H.264 encoder; up to ten workflow workers
  remain independent and do not each decode a video stream.

**Windows setup:** keep MuMu's ADB port available. If the project is installed
from GitHub rather than the download package, install Android platform-tools
and make `adb.exe` available on PATH; OmniFlow otherwise uses the bundled
`vendor/adb.exe` when present. See the logs when an emulator refuses H.264.

**Scope:** hardware encoder/driver failures cannot be guaranteed away; this
release recovers automatically and maintains a lower-rate screenshot preview
when video is unavailable. Actual continuous 30 FPS and ten-emulator load
must be measured on the target Windows/MuMu installation.

### Diagnose an emulator's video capture

From a Windows terminal inside your OmniFlow installation:

```powershell
.\.venv\Scripts\python.exe tools\diagnose_capture.py --seconds 30
```

Supply `--serial 127.0.0.1:16384` to test a specific MuMu instance. The tool
measures video frames delivered to the preview, recovery snapshots, and recent
H.264 errors. **It does not send any taps or run automation.** If the FPS is
consistently low, inspect the error list and MuMu's encoding settings before
increasing the number of emulators.

### Verification

The CI suite checks source completeness, legacy workflows, scheduling,
concurrency, the raw H.264 decoder with a known-good test stream, and offscreen
Qt switching. New Windows CI coverage is added to catch platform-specific
preview regressions. Live MuMu capture is still a separate test gate.

---

## v0.2.0 — Capture-first workflow authoring

The Studio now treats the screenshot as the source of truth. You **never need
manual X/Y input** to record a tap, double tap, press-and-hold, swipe, OCR
region, color zone or individual pixel. Actual coordinates are stored internally
for reliable scaling to each emulator's resolution.

**Create a state using WHEN → THEN → Save State:**

1. Load or capture the current game screen. A saved capture works offline.
2. Open **WHEN · Recognition**, choose **+ Condition**, then draw a tight zone
   over the text, icon, color, or indicator you want to recognize. Text can be
   matched case-insensitively or with a regex; the optional OCR model can
   prefill the expected text directly from the selected image zone.
3. To match colors or pixels, select the area or point directly. OmniFlow
   samples the original screenshot automatically and saves the BGR value and
   editable tolerance. Use **This must NOT be visible** for negative conditions.
4. Enable **Use conditions only** when no image template is needed. OCR-only
   and color-only states do **not** require a dummy Region 1. One or more
   valid conditions are still mandatory.
5. Open **THEN · Actions**, choose **+ Action**, and pick the tap/hold point
   or swipe path directly on the screenshot. Only meaningful options such as
   duration, text or Android key code are entered as numbers/words. Actions
   can be reordered into a sequence; Wait states execute no input.
6. The main canvas overlays OCR zones (cyan), negative zones (red), other
   conditions (gold), and existing gestures. The list summarizes each rule
   and action in plain language rather than displaying JSON coordinates.
7. **Save State once.** Other workflow files and older SwordXAuto recordings
   retain their format. Run the built-in read-only recognition test or Dry Run
   before enabling actual touches.

**Safety:** A reconstructed screenshot from an older workflow cannot be used as
if it were a real screen when recording new touches. Capture the actual screen
first. A condition-only state requires at least one condition; an actionable
state requires an action. OCR is optional; the editor reports when the OCR
model is not installed instead of silently passing detection.

**Status:** Verified offline with the existing legacy and new engine test
suites. Windows Qt interactions, real emulator recognition and ten-emulator
performance remain to be validated on the target machine. GitHub PR #2 now contains the complete Python source and required
test fixtures, but it remains a draft until Windows/MuMu validation.

## What's implemented

| Area | Behavior |
|---|---|
| Multi-instance | Up to **10** independent MuMu ADB sessions; no duplicate input to one emulator, even across OmniFlow processes |
| Recognition | Saved image templates with bounded-region matching; optional OCR, pixel and average-color conditions; negated conditions |
| Decision engine | Recognize from any recorded state, confirm across two polls, prioritize explicit Wait/no-tap states |
| Actions | Tap, double tap, swipe, drag, press & hold, Android key, text, launch game, wait and mark completed run |
| Retry | Once per visit by default; optional timed retry with limits or re-arm after a state changes |
| Recording | Screenshot canvas, 1–2 visual regions and gesture anchors, extra OCR/color rules and ordered action sequences, single **Save State** |
| Scheduling | Once, daily, selected weekdays, intervals; time zone, late-start grace, time limit, completed-run limit, enable/disable, execution history |
| Unattended mode | Optional Windows-logon schedule agent running independently of the GUI, using the same SQLite job claims and device locks |
| Preview | Reuse the latest OCR screenshot; optionally enable **direct H.264 up to 30 FPS** for the *selected* instance only |
| Stability | Android package health checks, per-worker error handling, ADB reconnect, desktop GUI crash watchdog |
| Analytics | Session runs, active/elapsed duration, average, fastest and runs/hour (selected session) |
| Library | New, duplicate, edit, export, import, guarded delete, and configuration presets |
| Testing | Built-in dry-run mode; live recognition test in the Studio; read-only performance benchmark |

### Creating a visual workflow

1. Open **Workflow Library & Studio** and choose **+ New** (or duplicate a migrated workflow).
2. Choose **Open Studio**, then **+ New state → Capture** while the desired game screen is visible.
3. Draw a tight Region 1 for image-template detection, **or** select Detection Rules → Use conditions only and draw an OCR/color zone. No dummy region needed.
4. In **Detection Rules**, optionally add an OCR text, regex, color, pixel or negative condition. For dynamic screens you can turn on **Use conditions only** (requires at least one rule).
5. In **Action Sequence**, add and reorder actions. Existing taps and drags can be retained as the start of a sequence. The **Timing & retry** tab controls delay and retry policy.
6. **Save State**. Repeat for each screen the automation should recognize. A workflow can start from any recognized state.
7. Add a specific **Wait state** covering any screen where a tap is unsafe, e.g. *Cancel* during active matchmaking. The Wait state is given precedence over overlapping active states.
8. Test with the **Test saved workflow** button (no touches). Enable **Dry run** under Performance & Recovery for a complete read-only automation trial before allowing real input.

**OCR is included in the normal installation.** Run `setup.bat` to install
RapidOCR 3.x and the ONNX Runtime CPU inference backend, then execute an OCR
inference self-check. Studio's **Read text from selected zone** button fills
the expected text from the captured screenshot. OCR-based workflow rules reuse
the same lazy-loaded engine; no separate download or install step is needed.

To verify dependencies again without touching an emulator:

```powershell
.\.venv\Scripts\python.exe tools\check_requirements.py
```

Text-entry actions are separate from OCR: **Type text** sends characters to
the selected Android input field using ADB, and the game must have focus. The
ADB text command does not yet support arbitrary Unicode/IME text reliably;
a verified Unicode input adapter and field-focus confirmation are roadmap
work, not a silently completed capability.

### How the engine decides

A normalized frame is captured once and evaluated against recorded states. High-confidence explicit **Wait** states block actions. Action states require a stable match across two polling cycles. Once a state is acted on it will not be tapped again unless the user explicitly enables retries, or the engine recognizes departure and return. Timed retry is *not* recommended for buttons that become Cancel/Stop when pressed.

If a screen isn't recognized, OmniFlow does **nothing** until a valid state appears. The app-process recovery logic uses **ADB**, not OCR, and relaunches a game only after confirming its process is absent. It does not automatically restart a working emulator just because a screen looks unfamiliar.

## Scheduling — first-class feature

1. Open the **Scheduling** tab and select **+ New schedule**.
2. Set a name, emulator serial, Android package and workflow.
3. Choose **Once**, **Every day**, **Selected weekdays** or **Every N minutes**, with an **IANA time zone** (e.g. `America/Los_Angeles`).
4. Set **Max session duration** and **Stop after completed runs** (`Unlimited` is valid for both), plus how long a missed start may be tolerated.
5. Save. Scheduled occurrences are persisted immediately in `%LOCALAPPDATA%\OmniFlow\schedules.sqlite3` and have an audit trail in the Scheduling tab.

**If the GUI remains open**, scheduling works automatically. To run schedules while the GUI is closed, enable the independent **schedule agent** once using Windows PowerShell, from the extracted OmniFlow folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-scheduler.ps1
```

The Windows task launches the agent at **user logon**. You can also start it immediately with `start-scheduler.vbs`. This is local software, not a cloud service; Windows, your user session and the relevant MuMu emulators must be running. Only run one standalone agent manually; Windows Task Scheduler is configured to ignore additional task instances.

To remove the logon task without deleting schedules:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall-scheduler.ps1
```

Scheduling safety: claims happen atomically in SQLite, so a GUI and an agent cannot both execute the same occurrence. Live device leases prevent conflicting clicks. Busy devices are skipped, not double-controlled. Missed schedules beyond the configured grace period are skipped, not replayed en masse. A crashed agent can be restarted by Windows Task Scheduler. Check `%LOCALAPPDATA%\OmniFlow\agent.log` and the Scheduling history for outcomes.

**Important:** Automatic desktop recovery and scheduled starts are separate: the desktop watchdog will reopen the UI after a crash, but it does not silently resume a manually started automation. The independent schedule agent handles future scheduled occurrences.

## Performance: up to ten emulators

**Design target**, not an asserted real-hardware benchmark. OmniFlow uses one worker thread and one ADB connection identity per device, small fixed-size template regions, bounded preview images, OpenCV limited to one internal CPU thread, and no unnecessary ten-stream video decoding.

The default image polling interval is about **0.85 s per emulator**, adjustable down to **0.35 s** under Performance & Recovery. Start with ~1 s per instance, then benchmark your own machine before reducing it. Low-latency 30 FPS video can be enabled **only for the selected session**; screen recognition is still driven by ADB captures. CPU-based image matching is usually cheaper and more predictable than moving tiny regions to a GPU. The optional OCR model is shared across sessions.

Run this **read-only** benchmark after starting your intended MuMu instances:

```powershell
.\.venv\Scripts\python.exe tools\benchmark_mumu.py --instances 10 --seconds 20
```

It measures screenshot acquisition and normalization latency; it does not send touches and does not measure full OCR workflow throughput. Increase or decrease your polling interval based on real results.

## Architecture

- `omniflow/engine.py`: general visual state detector, OCR/color conditions, action chains, safe retries and Android health monitoring.
- `omniflow/devices.py`: ADB discovery and input, including ten-plus MuMu TCP ports.
- `omniflow/manager.py`: isolated sessions, device leases, pause/stop, scheduling dispatch and live session metrics.
- `omniflow/schedule.py`: SQLite WAL schedule persistence, timezone calculations, atomic claims and device leases.
- `omniflow/agent.py`: optional background scheduler loop.
- `omniflow/ui.py`: dashboard, schedules, workflow library and performance settings.
- `omniflow/studio.py`: visual recorder extension, OCR/negative rule editor and action-sequence authoring.
- `omniflow/storage.py`: non-destructive migration and OmniFlow data folder.
- `swordx/`: isolated legacy compatibility layer for saved images, recorders, backup ZIPs, ADB/preview and existing tests. It is not the main execution engine.

The release is **editable Python source**, not a compiled Windows EXE. `start.vbs` launches the desktop without a console using the installed Python environment. `watchdog.py` from the old project is preserved for compatibility; OmniFlow uses `omniflow_watchdog.py`.

## Verification and limitations

Run both regression suites:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m unittest discover -s tests_omni -q
```

This build passed its local non-GUI tests, including scheduling, ten mocked sessions, old-workflow storage, action chains and engine safety. **Desktop Qt tests were not run here because PySide6 was not available in the build environment.** Actual MuMu connectivity, codec compatibility, OCR model installation, timezone behavior on Windows, and ten-emulator load must be verified on the target PC. Back up your previous workflow data before switching.


## Live-preview fallback during MediaCodec failures

Only the **selected** emulator uses H.264 video encoding. If Android's encoder
rejects the stream (including Error 22), OmniFlow retains a usable preview
through low-rate ADB screenshots approximately every 1.5 seconds during
bounded decoder/encoder retry backoff (up to eight seconds). Screenshot
fallback does not create a second encoder or touch the game.

If PyAV is missing, the preview stays in ADB screenshot mode instead of
going blank, and reports the missing dependency. Repeated identical
fallback errors are rate-limited in the activity log.

For a read-only hardware report run `tools/diagnose_capture.py` as
documented above. Encoder reliability and ten-instance throughput must
still be validated on actual Windows/MuMu hardware.
