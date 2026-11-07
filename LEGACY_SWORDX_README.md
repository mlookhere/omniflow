# Sword x Staff — Visual Automator v1.5.3

Windows / MuMu Player automation with screen-state detection, crash recovery, profiles, run counting, and an **editable visual workflow recorder**. Runs are driven by whichever screen is visible, **not by a fixed list of steps**.

## v1.5.3 — ADB game status diagnostics and detection robustness

- **Check game status (ADB)** below the Android package field reports the
  selected MuMu instance, package install status, foreground package, and
  whether the game process is running. No OCR is needed for this check.
- More Android-compatible `dumpsys window` / `dumpsys activity` parsing avoids
  missed foreground detection across MuMu versions.
- ADB's `monkey` command sometimes exits successfully even when it says
  "No activities found"; this now reports a launcher error rather than
  falsely claiming that game recovery worked.
- **If the game is running but the UI says STOPPED**, verify the **Android
  package** matches the *actual* package on that emulator. An incorrect
  package cannot be repaired by OCR and must be corrected in the profile.
- App recovery still requires automation to be running, and launches the
  configured Android package only after confirmed process absence. ADB
  UNKNOWN never by itself triggers a relaunch.
- Existing workflows, profiles, delays, matching, metrics, and preview
  behavior are unchanged.

## v1.5.2 — Cleaner recorder, visible spinner arrows, Android game recovery

- Removed the two decorative **PAIR 1 / PAIR 2** status pills from the
  Detection & gestures tab. The actual Region 1 / Tap 1 / Drag 1 and optional
  Region 2 / Tap 2 / Drag 2 editing tools are unchanged.
- Every numeric stepper now displays explicit gold **up/down arrow icons**, with
  grey variants when the widget is disabled. Icons are shipped in `assets/icons`
  and work regardless of the Windows theme or launch directory.
- Under **Advanced / Recovery**, use **Automatically relaunch the game if it closes**
  (enabled by default). This is **Android game recovery**, separate from the
  **Restart desktop app after an unexpected exit** watchdog:
  - While automation is running and MuMu remains connected, the bot checks the
    configured Android game package during unrecognized screens.
  - It requires repeat evidence that the process has exited before launching
    Sword x Staff through the selected emulator's ADB connection.
  - A currently foreground game or recently recognized game screen prevents
    false crash alerts; a running game left in the background is brought forward.
  - It **does not restart the MuMu emulator** or the desktop GUI, and it is
    not active when automation is stopped.
  - Failed Android relaunch commands are logged and retried rather than
    terminating the automation thread.
  - Disabling this option also disables game-stall restarts; desktop crash
    recovery remains a separate setting.
- Existing profiles and workflows load unchanged; legacy profiles default to
  Android game recovery enabled. No state rerecording or migration is required.

### Upgrade

Extract this release into its own folder, run `setup.bat` if needed, and launch
with `start.vbs` (silent) or `start.bat` (console diagnostics). Your personal data
continues to live in `%LOCALAPPDATA%\SwordXAuto` and is not bundled or replaced.

### Validation

176 offline tests passed, 18 Windows Qt tests skipped in the build environment.
Live emulator recovery and actual Qt rendering should be checked on Windows.

---

## v1.5.1 — Refined editor and optional desktop crash restart

This update changes the **interface and desktop launcher only**. MuMu discovery,
OCR templates, tap/drag execution, matching/retry logic, saved profiles, run
statistics and workflow formats are unchanged.

### Cleaner main window

- Removed the `MUMU / VISUAL AUTOMATION` heading above the app name and the
  extra `Wait states suppress unsafe taps` caption below the controls.
- During automation, controls that would change the emulator, Android package,
  profile or workflow are **disabled and visibly grey** (including Detect,
  Refresh, Connect, package input, profile management, recorder and advanced
  recovery controls). Stop automation to edit them.
- Grey disabled styles override gold/green/red colors, including hover states.

### State-focused workflow recorder

- The selected state is clearly labeled **ACTION** (green), **WAIT** (red),
  or **NEW/NOT SELECTED** (gold/neutral).
- The capture overlay and two region/action status indicators distinguish
  complete pairs, missing gestures, and Wait states. Regions are green for
  actions or red for Wait; taps and swipes are gold.
- Buttons that cannot currently be used are disabled: no saved state to
  delete, no device to capture, no region for a gesture, inactive drag duration,
  no selected workflow to save, or no pending changes to save.
- **Save State** remains the only save button. All existing recordings remain
  editable, including screenshots previously recorded on another device.

### Restart the GUI after a desktop crash

- Launch using **`start.vbs`** to show only the GUI (no command prompt), or
  **`start.bat`** for troubleshooting. Both launch `watchdog.py`, a small
  supervisor that runs the existing `app.py` unchanged.
- Under **Advanced / Recovery**, toggle **Restart desktop app after an
  unexpected exit**. It defaults to on and is saved in
  `%LOCALAPPDATA%\SwordXAuto\desktop_recovery.json`.
- A normal window close exits cleanly and **does not restart**. A nonzero
  process exit can restart the app. The supervisor stops after three rapid
  relaunch attempts within two minutes to avoid infinite loops.
- Troubleshooting output is recorded at
  `%LOCALAPPDATA%\SwordXAuto\desktop_crashes.log`.
- This restarts the **desktop GUI**, not Windows, MuMu or the game. It does
  **not automatically press Start automation after recovery**. The feature
  works only when launched through `start.vbs` or `start.bat`, not when running
  `app.py` directly. It does not detect a frozen but still running GUI.

### Safe upgrade

Extract v1.5.1 alongside your current installation, run `setup.bat` if you
have not installed its dependencies, then open `start.vbs` or `start.bat`.
Your profiles and workflows stay in `%LOCALAPPDATA%\SwordXAuto`. Export a
backup via **Manage data** before updating as a precaution.

---

## v1.5.0 — Session dashboard, bounded missed-click retry and UI redesign

This update builds on v1.4.13 without changing your saved workflow names,
region templates, clicks, drags, existing delays, or Match/Cancel safeguards.
The Windows UI uses a **charcoal + warm-gold** theme, with **green** for healthy
recognition / safe Start and **red** for unrecognized states / Stop / Delete.

### Session dashboard

- **SESSION RUNS** is the primary large counter; the old persistent total is
  no longer displayed or incremented by the automation runner.
- **SESSION TIME** measures wall-clock time since pressing Start, including
  pauses and deliberate pre-action waits. It freezes after stopping.
- **AVG / RUN**, **FASTEST**, and **LAST** are based on completed runs in the
  current session only. Incomplete runs are never included in the average.
- **RUNS / HOUR** is completed runs divided by active session time, scaled to
  one hour. Paused time is excluded from active time and run durations.
- The first run's duration starts when automation starts; later durations
  begin at the previous confirmed completion. As before, an end marker is
  counted only when the runner **exits** that recorded state.
- Historic `runs` entries in `%LOCALAPPDATA%\SwordXAuto\profiles.json` are **not
  deleted or rewritten**. Keeping them supports old backup imports without
  affecting the new session-only interface. New sessions begin at zero.

### Workflow recorder: optional missed-click retries

Open **Edit / Record**, select a saved state, then choose the **Timing & retry**
tab. Enable **Retry if action was missed**, choose **After interval (same
screen)**, enter the retry delay, and set **Maximum extra taps**. For example,
`15 sec` and `2` allows one initial tap plus **at most two more taps**, provided
that action's screen remains recognized. The cap re-arms after the detected
state changes. **One Save State** saves all of these options together.

- A new state starts with retry **off** and a proposed cap of **2**.
- For an existing state, a missing cap means **Unlimited · legacy** (zero),
  preserving its previous retry behavior unless you explicitly edit it.
- **Wait / do nothing** states always suppress taps. For Match/Cancel toggles,
  continue using the **Cancel negative state** and safe **Only after leaving
  and returning** retry mode; timed retries are not safe for toggles.
- A cap affects only timed retries. Return-only mode still requires a verified
  departure and return and respects its minimum interval.
- A longer **Wait before action** delay still applies **only before the first
  tap in a recognized visit**, and is separate from retry timing.

### Layout and navigation

The main window groups profile/workflow selection at the top, emulator and game
controls on the left, a collapsible **Advanced / Recovery** section, session
metrics and detection status on the right, then live Android preview and logs.
The editor has separate **Detection & gestures** and **Timing & retry** tabs,
while keeping the saved-state list and its single **Save State** button visible.

### Upgrade safely

1. Stop the running bot. Optionally use **Manage data → Export everything**.
2. Extract this release into a **new** folder such as
   `F:\Projects\swordx_auto\v1.5.0` (do not delete the previous version).
3. Run `setup.bat` if dependencies are not installed; then launch `start.bat`.
4. Choose your existing profile/workflow. Nothing must be re-recorded.

The direct H.264 MuMu preview, device discovery, negative states, gestures,
pre-action delays, crash recovery, and order-independent screen recognition
remain in place. All backend regression tests pass; Windows Qt interactions
and live MuMu frame rate need validation on the target machine.

---

## v1.4.13 — Duplicate recorded workflows without overwriting

Open **Record workflow**, select an existing recorded workflow, and click
**Duplicate** next to **+ New**. Enter a new name (for example,
`Alqhard (copy)`); the editor opens the newly created independent workflow.
All states, region templates, saved screenshots, taps, swipes, waiting/negative
states, end-of-run markers, retry settings, delays, and ordering are preserved.

The original workflow is never edited by copying it. Its images and manifest
are separate from the copy, so editing, deleting, exporting, or renaming states
in the copy does not affect the source. Duplicate names, including names that
differ only by capitalization, are refused instead of being overwritten.
If duplication fails halfway through, no partial workflow appears in the list.

The **built-in dungeon** is a special hard-coded workflow and is not editable
or duplicable from the recorder. The Duplicate button applies to user-recorded
workflows. The duplicate uses the same screen-first detection engine; dragging
state rows to reorder them is still visual organization only. Stop and restart
automation to switch a running profile to the copied workflow.

Existing workflows remain in `%LOCALAPPDATA%\SwordXAuto\workflows` and no
profile references are changed automatically by duplication. Live Windows GUI
validation is pending; backend copy and isolation are covered by tests.

---

## v1.4.12 — Recognize Cancel and wait (no tap)

Match and Cancel share a position. Trying to infer whether a queue ended from
countdowns, finished runs, or arbitrary elapsed time is less reliable than
**recognizing what the button currently says**. The recorder now has a
**Wait / do nothing when detected (negative state)** checkbox for any workflow.

### Configure once for Match/Cancel

1. Stop automation, open **Record workflow**, select the existing workflow.
2. Leave the existing **Start match** state intact. Keep **Retry action → Only
   after leaving and returning**, with your preferred minimum interval (for
   example 40 seconds). You do not need to rerecord Start match.
3. While the game is actively matchmaking and the button reads **Cancel**,
   choose **+ New state**, capture that MuMu screen, and give it a name such as
   **Matchmaking — Cancel**.
4. Enable **Wait / do nothing when detected (negative state)**. Draw **Region 1**
   tightly around distinctive **Cancel** text/button artwork. Do **not** add a
   tap or drag. Click the existing **Save State** button.
5. Restart automation so the engine reloads the workflow.

When **Cancel** is visible: the Wait state takes precedence over potentially
similar tap states, so the runner simply monitors the screen. When the game
returns to an actual **Match** state: it can press Match again, independently
of whether a dungeon completed or an invitation expired. The configured retry
interval is only a *minimum spacing between taps*, not a timer that forces a
tap on an unchanged queue screen. Repeats can continue without recording an
explicit sequence or relying on an end-of-run marker.

Negative states are generic: use them anywhere automation must observe and
wait (loading overlays, cooldowns, blocked controls). They require only one
or two regions and no action. **Detection is independent of saved-state order**.
A passive state must have a distinctive visual match; broad static artwork
can falsely block an action. If Cancel and Match are visually indistinguishable
in the chosen crop, refine the region before running unattended.

Existing states, gestures, delays, profiles and screenshots remain unchanged.
You can also turn an existing action into a Wait state using the same Save State
button without recapturing; its original template and gesture remain stored
for restoration. A newly recorded Wait state needs a tap/drag drawn before it
can be converted into an action state. Native Windows UI and live MuMu behavior
still require user-side validation.

---

## v1.4.11 — Resume Match after expired team acceptance

**Fix:** If someone fails to accept the second countdown, the group can dissolve
without ever showing another **recorded** state. The safe `Only after leaving
and returning` retry gate could therefore stay locked indefinitely on the
original Match screen.

The runner now also recognizes **two consecutive, substantially different
unrecognized frames** as evidence that a matchmaking overlay/countdown took
place. This is **not a timer-based retry**. Before releasing the guard on
return, it checks that the actual **tap target looks like the original Match
control**, rather than the active queue's Cancel control. Other matching states
on the same page remain blocked until the button has returned. The saved
minimum retry interval still applies. If a distinct screen or Match button
cannot be verified, the bot deliberately waits to avoid cancelling the queue.

**No changes required to your existing workflow.** Keep `Start match` with
`Retry action` enabled and `Only after leaving and returning` selected; no
recapture or re-import is necessary. The fix changes only runtime recognition,
its tests, this documentation, and the application version label. It does not
change saved profiles, workflow files, the live emulator preview, recording,
gesture timing, the end-of-run counter, or the per-state watchdog.

To test: start a queue, let the group invitation appear, allow one person to
miss the acceptance deadline, and confirm the bot taps Match once again after
the original button returns (and any minimum interval elapses). It must not
press the button again while it displays Cancel. Live MuMu validation remains
necessary.

---

## v1.4.10 — Re-arm matchmaking after a completed dungeon run

**Fix:** The **Only after leaving and returning** retry guard could stay locked
through the next loop if the intermediate screens were not sufficiently
different in the watchdog's low-resolution fingerprint. A completed run is now
an independent, reliable signal that the previous matchmaking attempt ended.

The runner re-arms return-only actions **when the end-of-run state has been
recognized and exited** (the same transition used by the completed-run
counter). The existing minimum retry interval still applies. It **does not**
re-arm from elapsed time, an unrecognized/loading screen, an unchanged queue,
or a lookalike difficulty screen; Match remains protected against Cancel taps.

Once the next Match state is detected, the bot can tap again and continue
another cycle. The event is logged as `Run finished; return-only actions
re-armed: start match` if a gate was still locked.

**No workflow migration or re-recording.** Keep your existing profile, saved
retry mode, screenshots, pre-tap delays, and gestures. Only the runtime guard,
version label, regression tests, and these notes were updated. The app continues
to use `%LOCALAPPDATA%\SwordXAuto` for saved data. Tests verify the original
v1.4.9 guard stalls in the reproducer and the new guard completes repeated
cycles. Actual MuMu gameplay still needs live validation.

---

## v1.4.9 — One-button workflow editor cleanup

The recorder is organized around **Workflow → Saved states → Edit state**, with
**one Save State button** fixed at the bottom of the panel. That button writes
all applicable options together: name, end-of-run marker, detection regions,
taps/drags, press durations, pre-action delay, and retry settings.

1. Open **Record workflow** and choose a workflow; its existing states appear
   immediately at the top. Click a state to edit it, or choose **+ New state**.
2. For a new state, **Capture / replace MuMu screen**, draw Region 1, and add
   Tap 1 or Drag 1. Region 2 plus its own action is optional; overlapping
   regions and right-click removal still work.
3. Configure **Wait before action**, **Retry action** (interval or safe return
   mode), and **End of run**, then click **Save State** exactly once. Drag state
   entries to reorganize them visually; detection is **not** step-order-based.

**Editing an existing state does not require recapturing anything.** If only
its name, delay, retry options or end marker changed, Save State edits the
manifest atomically and preserves the original captures, anchors and gestures
byte-for-byte. This also works with **legacy recordings that have no stored
full-screen capture**. If you redraw a detection region, change a tap/drag,
or replace the screenshot, the recorder saves those visual changes as before.
Legacy two-anchor **AND** matching is retained unless you intentionally
re-record its regions/actions.

There are no longer separate *Save delay only*, *Save retry settings only*,
or *Save new/changed state* buttons. The editor warns before discarding
unsaved changes when switching states, workflows, creating another state or
closing the dialog. Deleting a state requires confirmation.

**No migration or recording recreation is needed.** Existing workflows and
profiles remain in `%LOCALAPPDATA%\SwordXAuto`. Installing this release does
not reset saved data. Restart automation after editing a workflow to load the
updated manifest. This is an editor/persistence cleanup only: matchmaking
retry behavior, 5-minute delays, detection, profile data, H.264 preview,
MuMu selection, and recovery are unchanged. Actual GUI behavior on Windows
still requires live verification.

---

## v1.4.8 — Workflow-level retry guard for matchmaking

**This fixes repeated Match taps (Cancel) caused by the recorder's Retry checkbox.**
A recorded retry previously occurred every 25 seconds on any screen that continued
matching that state's template. That is unsafe for Match/Cancel toggles.

### Configure an existing workflow — no recapture

1. Stop automation. Open **Record workflow** and select the existing workflow.
2. Click **Start match** (or whichever recorded state presses the Match button).
3. Keep **Enable retries for this state** checked.
4. Select **Only after leaving and returning**.
5. Set the minimum interval (e.g., **40 sec**) and click **Save retry settings only
   (no recapture)**. Then restart automation.

This mode taps Match once and **never repeats it merely because 40 seconds
elapsed**. It re-arms only after the recorder sees a **different, recognized
full game page**, followed by the original Match screen returning. Brief UNKNOWN
frames, animated counters, or a different region on the **same** dungeon menu
cannot re-arm it. Detection continues throughout matchmaking. No-click waiting
on a recognized or unrecognized queue screen does not trigger idle-screen
restart timers; actual confirmed game-process crashes still recover.

**If a failed queue returns to a visually identical menu without ever showing
another recognizable page**, the safe mode continues waiting rather than risk
tapping Cancel. To automate that edge case, record distinct queue/idle visual
indicators first; time alone cannot prove the button is Match rather than Cancel.

For other states, **After interval (same screen)** retains legacy retry behavior,
with a customizable 1–86400-second repeat interval (25 seconds by default).
This is a retry *after the first tap*, not the independent **Wait before tap**
delay and not the per-profile **Screen-state timeouts…** crash watchdog.

### Your uploaded Alqhard workflow

A separately supplied **Alqhard - safe matchmaking import.zip** is an optional
portable backup with only the **Start match** action changed to return-only retry
and a 40-second minimum interval. In the app choose **Manage data → Import**;
importing with a duplicate workflow name creates an **(imported)** copy. All
other recorded state images, taps, drags and the five-minute handoff delay are
preserved. Alternatively use **Save retry settings only** to edit the current
workflow in place without importing a duplicate.

Install this release in a separate folder. Existing profiles and workflows in
`%LOCALAPPDATA%\SwordXAuto` are left untouched by extraction. Android H.264
preview, MuMu device detection and OCR detection are unchanged.

---

## v1.4.7 — Independent state timeouts and immediate live preview

### Change how long matchmaking can wait

1. **Stop** automation (your saved data stays intact).
2. Select the **Profile** and **Workflow** you want to configure.
3. Click **Screen-state timeouts…** under **RECOVERY**.
4. Set a duration for the recorded matchmaking state (or **Shrine Details** for the
   built-in dungeon workflow). Use **10–15 min**, or **Never** if matchmaking has
   no predictable upper limit.
5. Click **Save**, then **Start automation**.

Timeouts are independent for each state and are saved per profile and per workflow
in `%LOCALAPPDATA%\SwordXAuto\profiles.json`. Only recognized, unchanged screens
use this timeout. **The default is 4 minutes**, exactly as before. **Never** (0 min)
disables this *specific recognized-state watchdog*; it does not disable checks
for a crashed game or recovery from unknown/frozen screens. The existing
**Wait before tap/drag** is separate and is NOT modified: it still delays the
first input after recognizing a state. Recorded regions, gestures, run counters,
profiles, and saved workflow manifests are unchanged.

**Example:** With the matchmaking state set to 15 min, the bot clicks Match once,
waits without hitting Cancel, and restarts only if the same recognized state
still hasn't transitioned after 15 minutes. If the game reaches the invitation
screen first, recognition immediately continues the normal workflow.

### Live game picture from the start

The live video is still an independent, device-specific **Android H.264 stream**:
no Windows GDI/MSS or desktop capture. Previously the H.264 decoder could wait
for the next keyframe before presenting an image, so the preview might remain
blank until matchmaking animations started. Now, from the very first OCR poll,
the panel shows a temporary frame **reused from the same emulator screenshot
that the state detector already captured** while the video decoder initializes.
It keeps that still picture refreshed sparingly if the H.264 encoder stops
sending frames, and switches back to the direct 30-FPS-target video immediately
when H.264 frames arrive. No additional screenshot subprocess/loop is started;
there is no slow continuous ADB preview mode.

The stream is started when you click **Start automation**, regardless of which
game state is recognized. Preview capture remains independent of recorded steps,
delays, and taps; each app instance uses its own selected ADB device. Video can
be toggled by clicking the picture without stopping the workflow. Actual stream
FPS depends on MuMu's H.264 encoder; the bootstrap still frames are not 30 FPS.

**Install:** Extract v1.4.7 into a new directory. Reuse the v1.4.6 `.venv` or
run `setup.bat` once to install dependencies; use `start.bat` or your existing
GUI-only VBS launcher. Settings, profiles, and workflows remain in AppData.
Regression tests run with `test.bat`. Live MuMu/Windows behavior requires validation.

---

## v1.4.6 — Per-device video directly from MuMu (no desktop capture)

The live preview now receives a **compressed H.264 stream from the selected Android
emulator** (`adb -s DEVICE exec-out screenrecord --output-format=h264 ... -`).
It no longer captures a Windows window using GDI or MSS and **does not switch back
to the slow ADB screenshot preview**. Covering, moving, or minimizing the MuMu
*window* will not mix other desktop windows into the stream; emulator throttling
while minimized may still reduce video FPS.

- **Correct multi-instance previews:** Video capture is hard-bound to the `MuMu device`
  selected for this running app/profile. Run two SwordXAuto windows with different
  selected serials for two different previews, e.g. `127.0.0.1:16384` and
  `127.0.0.1:16416`. Each starts and stops its *own* video subprocess.
- **Optimized encoding:** 432×768 (fallback 288×512), 1.5 Mbps. Render is capped
  at 30 FPS with a latest-frame-only queue. Measured FPS is shown, not guaranteed.
- **Toggle video:** Clicking the live game panel stops video encoding/decoding;
  clicking again starts it. This does not pause automation.
- **Auto-reconnect:** Video restarts if the stream ends (including Android's
  approximately 180-second `screenrecord` limit) or the encoder disconnects.
  The preview may briefly pause during restarts, but the game workflow continues.
- **Error reporting:** If video cannot start, the panel shows the device and
  exact stream error. No unrelated MuMu window is substituted. OCR continues.

**Upgrade:** Run `setup.bat` once in v1.4.6 to install `av` (PyAV H.264 decoder)
into the project's `.venv`. Start with `start.bat` or your existing `start.vbs`.
No extra Windows application, screen recorder, MSS/GDI dependency or modifications
to a saved workflow are required.

**Performance expectations:** Stream encoding still uses resources inside each
emulator. The lower resolution limits the cost, but 30 FPS depends on the
emulator's encoder and your PC. Android `screenrecord` can be unsupported on
some MuMu builds; test live on your machine. This version's GUI/video stream
could not be run against MuMu in the Linux validation environment.

The automation engine, profile storage, delay logic, game crash recovery, and
recording format are unchanged from v1.4.5. User data remains under
`%LOCALAPPDATA%\SwordXAuto` and is neither migrated nor overwritten.

---

### Earlier release notes (historical)

The v1.4.4 and v1.4.5 desktop capture descriptions below document *previous*
releases. Their GDI, MSS, window-recognition, and ADB-preview fallback features
**are no longer used in v1.4.6**.

## v1.4.5 — High-FPS preview fallback fix

v1.4.4 sometimes displayed **ADB fallback** permanently because `mss` was missing from
an existing `.venv`, or because MuMu changed its top-level window title to the game
name. This update uses the emulator's **Windows process name** as well as its title,
recognizes the **Sword x Staff** title, and excludes the automator's own window.

The preview now has two independent desktop capture implementations:

- **MSS** when available and working.
- **Windows GDI** (built into Windows), if MSS is missing or repeatedly fails.

Both target **30 FPS** in a separate preview thread and leave ADB-based game recognition
completely unchanged. The FPS indicator shows the achieved *display* rate and the
selected backend. Capture keeps trying to reconnect when MuMu's window returns;
errors and failure-to-find-window cases appear in the activity log rather than being
silent. ADB fallback remains available only when a usable native capture is not
currently possible.

**No new installation step or recapture is needed:** start this version using your
current `.venv` and saved workflows. The emulator window must be **restored, visible
and not obscured** for accurate desktop-based capture. Neither native backend can
stream a minimized emulator or reliably see pixels hidden behind other windows.
Whether the PC achieves 30 FPS must be verified on the Windows/MuMu device itself.

All changes are restricted to live preview source selection, preview UI reporting,
tests and these instructions. Profiles, recorder, state detection, delays, run totals,
crash recovery, and automation control flow are unchanged.

## v1.4.4 — Click-to-toggle 30-FPS-target preview

The **Live game** panel is now a toggle: click anywhere on the picture (or its "Preview off" placeholder) to hide/show the preview. Disabling it stops preview capture and processing; it **does not** pause the game or automation. The existing **Pause** control still pauses the automation itself.

With MuMu visible on the Windows desktop, the preview independently captures the MuMu window at a **30 FPS target** using `mss`. The automation continues to use its original, lower-frequency ADB screenshots for all OCR/state detection, gestures, delays, game recovery, and completed-run counting. The panel shows the **measured display FPS**, not a promised fixed rate. Actual FPS depends on your Windows hardware, GPU, emulator visibility, and window size. This is *not* a guaranteed 30 FPS on every machine.

To enable the new capture mode, run **`setup.bat` once after upgrading** to install the new `mss` requirement; then run `start.bat`. Keep the MuMu window restored and visible: desktop capture may include overlapping windows. If MuMu is minimized, unavailable, or the capture dependency fails, the app automatically falls back to the previous low-FPS ADB preview. High-FPS video can remain on while automation is paused. The preview is display-only; clicking it does **not** click the game, alter a saved gesture, or take over your phone controls.

The change is intentionally isolated to the preview/UI plus the new `swordx/preview.py` and `mss` dependency. **No existing profile, workflow, saved delay, run count, or engine behavior is modified.** No user data is stored in the extracted project directory; existing `%LOCALAPPDATA%\SwordXAuto` files remain intact.

## Install

1. Extract `SwordXAuto-v1.4.3.zip` into a new folder. Keep your existing working folder and **do not manually copy new files over an older installation** until you have validated this version.
2. Run **`setup.bat`** once (Python 3.11+ required), then **`start.bat`**.
3. Start MuMu in portrait at **2160 × 3840 / 960 DPI** and open Sword x Staff.
4. Select the emulator device, detect the Android game package, choose a workflow, and press **Start automation**.
5. Optionally set the MuMu launcher path for emulator recovery; in-game relaunch uses the detected Android package.

Existing v1.1 profiles, settings, run totals and workflow folders are retained under `%LOCALAPPDATA%\SwordXAuto\`. All new recordings contain a locally saved copy of the screenshot for later editing.

## Where saved profiles and workflows live (v1.4)

**The installation directory is not the profile storage directory.** Your project may be installed in `F:\Projects\swordx_auto`, but saved data from v1.1–v1.3.1 lives here by default:

- `%LOCALAPPDATA%\SwordXAuto\profiles.json` — every named profile, its selected workflow and **completed-run counts**. There are no separate profile files; all profiles are entries in this JSON file.
- `%LOCALAPPDATA%\SwordXAuto\workflows\` — each custom workflow in its own UUID-named folder. Its `manifest.json` stores recorded states, tap/drag actions and settings. `anchors\` contains matching images; `captures\` contains the recorded screenshots.
- `%LOCALAPPDATA%\SwordXAuto\settings.json` — legacy default settings, retained for compatibility.
- The built-in dungeon workflow lives in `<installation>\assets\` and is always available; it cannot be deleted.

**v1.4 reads the existing storage location unchanged**; merely extracting or running the new version will not replace your existing profiles or workflow folders.

### Manage data in the app

Click **Manage data…** beside **+ New profile**. Stop automation first (the manager intentionally blocks changes while the runner is active).

1. **Open data folder** opens the live Windows storage folder. **Open recorded workflows folder** opens its child folder, and **Open selected workflow files** opens the exact UUID folder used by the selected workflow.
2. **Export profile** backs up the selected profile and its saved run total to a `.zip` file. **Export workflow** backs up the selected custom workflow, including full screenshots and matching regions, to a `.zip` file. These separate exports include only the selected item.
3. **Export everything** creates a portable `.zip` containing **all profiles and custom workflows**, with their counters, metadata and referenced images. The initial save suggestion is `<installation>\backups\`, but you can choose `F:\Projects\swordx_auto` or any folder. **An existing backup filename is never overwritten**; choose a different name if it already exists.
4. **Import profile / workflow / backup** restores the selected type from exported ZIP files. Existing profiles and workflows are **never replaced**: conflicts become `Name (imported)`, `Name (imported 2)`, etc. Full-backup imports also update imported profile references to renamed workflows. Importing a profile alone does not automatically import its workflow; use **Import backup** when moving both together.
5. **Delete profile** permanently deletes its saved settings and completed-run count, after confirmation. It does **not** delete the profile's associated workflow. Deleting the very last profile is blocked.
6. **Delete workflow** permanently deletes its custom workflow folder and screenshots, after confirmation. Profiles using it automatically switch to **Built-in dungeon**; their run totals are preserved. The built-in workflow cannot be deleted.

For the safest handoff between installations, **Export everything** before deleting or moving anything, and use **Import backup** in the new installation. All imports are validated and staged before changing saved data. A live MuMu test is still needed for the new dialog interactions.

## Fix in v1.4.3 — automatic MuMu device detection

On launch and when you click **Refresh**, the app first checks already-connected Android devices. If your saved device is missing, it checks the standard MuMu local ADB ports (7555 for older MuMu and 16384/16416/etc. for MuMu 12), connects the active instances, then selects the saved device or a single available device automatically. This restores detection after the ADB server forgets its connection, without requiring manual **Connect address**.

If more than one MuMu instance is attached and none matches your saved profile, select the intended instance once. Nonstandard ports can still be connected using **ADB address**. This change touches only MuMu discovery, dropdown selection, and corresponding tests; workflows, gesture timing, delays, completed-run counts, profiles, and recovery logic are unchanged.

## Fix in v1.4.2 — delayed custom actions and preview

Fixed `Stopped: KeyError: 'tap'` on custom recordings saved with the v1.3+ gesture format. The **live-preview crosshair** was trying to read the old `tap` field even when the recorded action correctly stored its location under `gestures`. This stopped the automation thread before it could finish a configured wait.

- The preview now reads the position of the tap, or **the starting point** of a drag/long press, directly from the recorded gesture. Older saved `tap` and `taps` actions are still supported.
- Preview and actual gesture execution share the same coordinate conversion to avoid mismatched tap locations.
- A five-minute delay starts when that state is first detected, remains active while it is continuously recognized, and **fires the tap/drag only after the 300 seconds have elapsed**. It does not block state detection or the UI.
- Existing profiles, custom workflows, screenshots, timers and completed-run counts are **not modified or migrated** by this code-only patch; no workflow re-recording is needed.

**To update:** Stop v1.4.1, extract v1.4.2 to a **separate directory** and run `setup.bat` then `start.bat`. Use your existing profile and workflow; the updated app reads the same `%LOCALAPPDATA%\SwordXAuto` files. Run `test.bat` for offline checks. If the game state changes during the waiting period, its delay will reset (see the behavior notes below). Actual Windows preview and MuMu interactions require live validation.

## Optional per-state delay (new in v1.4.1)

You can delay **the first tap, swipe or long press** after the bot recognizes a specific recorded state. The default is **0 minutes, 0 seconds**, so current saved workflows behave exactly as before.

1. **Stop automation** and select **Record workflow**. Choose your existing custom workflow and click the saved state you want to delay.
2. Under **WAIT BEFORE TAP / DRAG (OPTIONAL)**, choose minutes and seconds (0–24 hours total).
3. Click **Save delay only (no recapture)**. This updates only the delay for that state, without modifying its screenshots, detection regions, gestures, or other settings. It also works with older states that no longer have their full original screenshot. Alternatively, **Save changes to selected state** saves the delay together with other edits.
4. Restart automation to load the revised workflow. The delay is displayed in the recorded-state list, and **Next action in M:SS** appears in the main window while a wait is active.

**Behavior:** The countdown begins when a recorded state is recognized. The game continues to be checked during the wait, without blocking the UI. If that state disappears (including an unrecognized screen), the countdown is cancelled and restarts only when a state is recognized again. A new recognized state triggers its own saved action and independent delay. Same-named recordings are alternatives sharing a visit timer, and the currently best-matching alternative determines the gesture. If the app is paused, paused time does not consume the wait. Intentional waits longer than four minutes do not activate the recognized-screen stall watchdog; after the action fires, normal stall protection resumes.

**Manual takeover:** Set a delay on the state at which you want to temporarily take control on your phone. The bot waits *while that particular state stays visible*. This is not a global "hands-off" mode: navigating to another recorded state can trigger that state's action. For guaranteed control while freely navigating the game, use the app's **Pause** button instead.

Existing profiles, saved workflows, and per-profile completed-run counters stay in `%LOCALAPPDATA%\SwordXAuto`. Extract the new app to a separate directory; it does not migrate or overwrite those files on launch. The built-in dungeon workflow remains unchanged; the delay editor applies to custom recorded workflows.

## Recorder — tap, drag and long press (v1.3)

1. Stop automation and open **Record workflow**. Select an existing custom workflow or click **+ New**.
2. Click **Capture MuMu** to use the current Android game screen.
3. Select **Region 1** and draw a distinctive detection area. Choose **Tap 1** and click its target, **or** choose **Drag 1** and drag from a start point to an end point. **One region + one gesture is sufficient.**
4. *Optional*: add **Region 2** and its own **Tap 2** or **Drag 2** as an alternative. Either detected region independently fires its corresponding gesture. Regions can overlap.
5. For each drag, set **duration** (default 700 ms, range 100–10,000 ms). Longer durations drag more slowly. To **press and hold without moving**, choose Drag and click one point without moving the mouse, then set a longer duration (e.g., 1500 ms). Name the state, optionally configure **Safe to retry** and **End of loop**, then save.
6. Click a saved entry to edit its detection regions, gesture type, drag endpoints, duration, name and other settings. Select **Tap** to replace a drag with a tap, or **Drag** to replace a tap. Click **Save changes**. **Capture MuMu** replaces the screenshot while retaining region/gesture coordinates.
7. **Right-click** a tap, drag endpoint, or drag line to remove its gesture; right-click a region to remove the region and its paired gesture. **Gesture takes priority over region** on overlaps. Removing Region 1 promotes Region 2 (if present).
8. Drag states up/down in the saved-state list to organize them. This controls **only how they appear in the editor**. It never determines what the bot clicks next. Click **+ New state** to add more states, even ones with the same name.

### How recognition and actions work

- **One pair**: matching Region 1 triggers its Tap 1 or Drag 1.
- **Two pairs**: either matching region can recognize the state. The stronger match chooses its corresponding tap/drag. Ties choose Region 1. Only **one** action fires per recognized visit; it does not perform both gestures.
- **Same-named recordings**: separate screen captures with the same name are interchangeable ways to recognize one logical state. The strongest matching saved variant is used. Switching between variants of that name does not repeat the gesture until a different recognized state is visited or recovery resets the loop.
- **State ordering**: purely cosmetic. The detector looks at every saved state every cycle. Equal-score ties between different saved states use a stable identifier, not the list position.
- **Detection quality**: a region must have enough visual detail; avoid timers, animation, or unchanging artwork present on many different screens. Indistinguishable regions cannot reliably select between different tap targets. Recording the same image multiple times will not teach the detector to distinguish identical screens.

**Old recordings are backward-compatible.** v1.1 custom states continue to use both of their original detection regions with the original single tap. v1.1 did not save complete screenshots, so selecting one in the new editor shows its surviving region crops in place; use **Capture MuMu** on that same screen to update it. An edited two-region state must receive **Tap 2 or Drag 2** before it can be saved in the new paired format. The built-in dungeon workflow is unchanged.

## Crash detection fix (v1.3.1)

- **Fixed false “Game process is gone” messages while the game was running.** The Android process probe now calls `pidof` directly, verifies results using `ps` when necessary, and treats unsupported checks as **unknown**, not a crash.
- Visible Sword x Staff activity and recognized workflow screens override missing process results. Combat and loading screens are allowed to be unrecognized without immediately triggering recovery.
- A real closure is reported **only after two confirmed missing-process checks** separated by at least 10 seconds and outside a short grace period after a recognized game screen. Relaunch attempts share a 90-second cooldown with other recovery actions.
- A live game temporarily in the background is logged separately as a foregrounding action, not a crash.
- This version changes the engine and ADB process-check code only; existing profiles, saved workflows, screenshots and run totals remain compatible.

## Reliability and run counter

- If a closed Android game process is confirmed, it is relaunched; if the emulator disconnects, the program retries the connection and can open the configured MuMu launcher.
- Frozen, unrecognized screens are restarted after 75 seconds; recognized screens stuck without a transition are restarted after four minutes. Unknown but active screens receive a longer watchdog window to avoid interrupting combat. Restarts are throttled.
- Match/Ready and recorded taps/drags are performed once per recognized visit unless explicitly marked safe to retry.
- A run is credited when the bot sees a terminal **End of loop** state and then **a different recognized state**. The built-in dungeon uses the results screen. A crash before the confirming transition does not increment the counter.
- Runs persist **per named profile**, alongside its emulator connection and selected workflow. Pause does not count as a freeze.

## Verification

Run **`test.bat`** to execute the offline regression suite. The tests cover per-state delays, interrupted waits, post-delay stall recovery, delay-only edits, and original dungeon screens, cross-checked Android process detection, false-crash prevention, crash/watchdog behavior, repeated loops, editable state persistence, region/gesture matching, timed drags and holds, coordinate scaling, deletion, order independence, and old workflow compatibility. Tests that exercise Qt canvas hit-testing require PySide6 and are skipped when it is unavailable.

**Current limitations:** The build environment does not provide PySide6 or a live MuMu instance, so the Windows editor, right-click behavior, drag gestures, and drag/drop still require live desktop validation. The recorder uses fast bounded-region OpenCV matching, not full-screen text OCR. Drag gestures are drawn manually on a captured screenshot; the recorder does not passively record touch activity while playing. When two active regions look identical, matching cannot determine which tap the user intended.

### Source layout

- `app.py`: desktop app, profiles, live preview
- `swordx/adb.py`: MuMu ADB screenshots, taps, timed swipes, and app lifecycle
- `swordx/detection.py`: normalized screenshots and state recognition
- `swordx/engine.py`: click dispatch, retry/recovery and loop counting
- `swordx/workflows.py`: create/edit/delete/reorder, screenshot and anchor storage
- `swordx/storage.py`: no-overwrite ZIP export/import, backups, profile-safe workflow deletion
- `swordx/manager.py`: profile/workflow management and file-location UI
- `swordx/recorder.py`: interactive canvas and state list editor
- `tests/`: offline regression tests

`vendor/adb.exe` and DLLs come from the platform tooling included in your original bot package. The automator operates locally; it requires no cloud service.
