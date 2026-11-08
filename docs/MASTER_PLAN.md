# OmniFlow master plan

**Source of truth:** [Control issue #1](https://github.com/mlookhere/omniflow/issues/1) and [draft PR #2](https://github.com/mlookhere/omniflow/pull/2). This plan tracks acceptance, not a release claim. Keep it synchronized with GitHub state after completed milestones.

## Repository workflow

- Development: `work/1-visual-first-studio` → PR #2 → `dev` (integration). `main` is reserved for production promotion.
- One independently deliverable future scope change receives its own issue, branch from `dev`, and PR to `dev`; never directly modify integration or production.
- All required CI jobs must run and succeed on the **exact PR head** before a merge. Missing, pending, failed or skipped-required jobs are not green.
- **Governance blocker:** GitHub reports `dev` unprotected and required-check enforcement disabled. A green workflow is not a substitute for enforced branch policy.

## Milestones

| Item | Goal | Current evidence / remaining work |
| --- | --- | --- |
| 1/4 | Import baseline, preserve SwordXAuto workflows and add Linux/Windows CI | Source, fixtures, compatibility tests and CI are on existing work branch; final integration not merged |
| 2/4 | Visual-first Studio: screenshot zones, OCR/image/color/pixel/negative matching and tap/swipe/hold authoring | Implemented under PR #2; real user capture and workflow validation still required |
| 3/4 | Universal engine: composable conditions, retries, Wait-state safety, scale-independent input | Implemented with regression coverage; live touch/swipe/rotation safety remains unverified |
| 4/4 | Concurrent MuMu operations, leases, H.264/Error 22 recovery and ten-emulator performance | Automated safety/decoder tests and read-only diagnostics available; actual Windows/MuMu evidence and resource stability not yet collected |

## Hardware acceptance (outstanding)

Use [hardware-validation.md](hardware-validation.md) on the actual Windows/MuMu PC, not mocked CI devices:

1. Capture genuine H.264 FPS, blackouts, fallback screenshots, Error 22 windows and recovery timings on moving screens and both orientations.
2. Start with the 120-second one-MuMu PNG benchmark and label 10× estimates as extrapolations. Only full-length, internally consistent, error-free PNG reports with <=6s frame gaps can produce a theoretical workload projection; this is not ten-emulator proof. PNG/raw comparison additionally rejects non-MuMu serials, fractional frame counts, measurement duration shorter than 95% of the requested run, and legacy fidelity reports without proof of native-source pixel comparison. Real ten-instance acceptance still requires ten distinct MuMu devices running concurrently for at least 120 seconds, including representative workflow OCR/image recognition and per-device FPS/latency/errors.
3. Measure CPU/RAM/GPU load and verify input correctness, retries, device lease exclusion, process restart and orientation on the real game.
4. Preserve diagnostic JSON and evidence with issue #1. The read-only evidence verifier cannot establish full hardware acceptance.
5. Enforce the required `dev` branch checks (Linux Python 3.12/3.13, Windows Qt and inherited regressions), verify their current-head results, and only then assess readiness to merge.

## Non-goals

Do not rewrite existing workflow data, relax CI gates, infer hardware performance from unit tests, or publish a production release before all acceptance evidence is reviewed.


## Platform capability roadmap — tracked by measurable gates

This expands the original four milestones; it **does not** claim any game is already
automatable. Games can restrict input, use inaccessible rendering, move UI
elements, change server state or enforce anti-automation policies. A genuinely
game-agnostic platform needs composable primitives, robust observability and
explicit per-game verification, not an unlimited-success claim.

| Milestone | Scope | Measurable completion gate | State |
| --- | --- | --- | --- |
| 5. Dependency-complete installation | One setup includes OCR inference, video, PySide6, telemetry and repeatable smoke test; useful errors for missing packages | Fresh Windows install and both Linux Python CI versions run OCR inference; Studio prefills a chosen OCR zone; missing backend fails clearly | **In development** |
| 6. Capture performance & frame arbitration | Profile ADB screenshots and reject silent capture stalls or changing source geometry; reuse selected-session preview frames only within an acquisition-time freshness window; reject stale input and compare PNG/raw fidelity at full native resolution | Production PNG and separately labeled diagnostic raw benchmarks measure >=0.8 screenshot-equivalent FPS per instance for >=120s and <=6s gaps; raw alone never satisfies acceptance; verify recovery and <=6s input freshness, no extra encoders | **Partial: PNG/raw geometry, native-pixel fidelity, shared fail-closed decoder, runner-side malformed-frame and OpenCV recognition/preview recovery plus selected-video watchdog OpenCV recovery, rejection of slow-OCR frames before publication and last-moment device-input/relaunch freshness checks, crop-consistent input geometry, one-flight selected-preview ADB fallback that yields to active workflow capture and benchmark-owned ADB polling during concurrent diagnostics, start-to-start worker polling without double-sleeping after slow PNG captures, per-session rolling live FPS/gap and ADB/PNG/total capture p95 visibility, freshness-aligned six-second production/profiler/ten-device PNG timeout with independent provenance checks, strict full-duration and shared-window per-frame H.264/ten-device capture evidence and verified one-device projection provenance; real 0.719 FPS still fails** |
| 7. Complete input & text primitives | Screenshot-selected field tap + ASCII text, later verified focus/readback, Unicode/IME path, copy/paste where permitted, multitouch and scroll | Safe ASCII validated before tap; ownership checked before tap and typing; real focus and readback, Unicode and gestures still outstanding | **Partial: visual field input implemented, unverified on hardware** |
| 8. Universal state graph | Reusable condition AND/OR/NOT groups, timers, priorities, branch/loop/subworkflow, state variables, timeout/recovery, dead-end handling | Unit/fuzz tests and dry-run replay for every transition; unknown/passive states never tap; bounded retries and lease cancellation verified | **Partial** |
| 9. Recording & inspection | Record full multi-step runs; visual condition editing; state auto-suggestions; live trace/timeline, screenshot diff and side-by-side debugger | Build/edit/debug a workflow with no manual X/Y; replay saved images from unknown starting state; exported traces contain exact actions and matched evidence | **Partial** |
| 10. Device & session resilience | Per-device health/capabilities, process relaunch, reconnect, orientation, startup, cleanup, concurrency leases, telemetry | Recovery tests for emulator/game crashes; no double input; sustained ten-device test separately from one-device projections | **Partial; hardware incomplete** |
| 11. Workflow portability | Versioned schema, non-destructive migration, import/export, screen resolution/aspect scaling, template/rule versioning | Import a legacy workflow plus new workflows; compare outputs across two resolutions and orientations with zero data loss | **Partial** |
| 12. Production readiness | Install/update packaging, searchable docs, permissions, audit logs, security/privacy constraints and release governance | New Windows PC installs and passes diagnostics; complete hardware acceptance checklist; exact-head CI required/enforced on protected `dev`; PR merged only after gates | **Blocked by hardware and branch protection** |

### Active development priority — core engine and visual Studio (November 7, 2026)

The user explicitly redirected implementation work away from multi-device scaling. Continue
the existing control issue, work branch and draft PR; prioritize the capabilities that
make a **single workflow reliable and easy to author**. Previously recorded ten-device
and physical-release acceptance requirements remain documented, **not waived**.

1. **P0 Studio feedback loop:** test the current unsaved WHEN conditions on a selected
   screenshot without saving, sending input or running a workflow; surface individual
   PASS/FAIL results. Validate OCR regular expressions at authoring time and report
   malformed imported patterns as workflow errors. Automated offscreen coverage must
   verify there are no manifest or emulator side effects.
2. **P0 recognition correctness:** strengthen condition/priority inspection, show what
   matched or failed and avoid silent false-positive taps. Support predictable state
   confirmation and read-only replay from saved screenshots.
3. **P1 authoring usability:** clear state/action labels, intuitive edit/reorder/copy,
   a single Save operation, visible errors, undo/reset safety and no typed XY fields.
4. **P1 workflow reliability:** bounded transitions/retries, passive/negative conditions,
   safe field text, fail-closed unsupported input, and actionable dry-run traces.
5. **P2 advanced composition:** reusable condition groups, branching, variables and
   subflows, introduced only after their behavior is testable from the Studio.

**Deprioritized:** multi-emulator scheduling, ten-device benchmarking, hardware
resource extrapolation and raw screenshot transport comparisons. Preserve their
existing tests and documented minimums. Do not claim hardware acceptance, change
required CI gates or merge into an unprotected `dev` branch on the strength of
Studio-only tests.

**Studio recognition-inspection implementation (current work branch):** The
saved-workflow read-only test now shares the exact production detector path and
shows, per saved state, image-threshold rejection, unevaluated conditions,
individual OCR/pixel/color/negative condition PASS/FAIL, the chosen state and
other matching states overridden by a passive WAIT or rank. The unsaved-rule
preview remains separate: it tests the current WHEN rules on a screenshot but
cannot claim that a whole workflow or edited unsaved template would be selected.
Neither test sends Android input or writes workflow data. Next work should
improve trace clarity and inline visual editing rather than multi-device scaling.

**Core Studio save integrity (November 7, 2026):** A settings-only save now
persists the state metadata, unsaved WHEN conditions, optional action sequence
and template/condition-only mode together in **one atomic manifest replacement**.
New and recaptured visual states already use the recorder's single atomic
`record_step` path. Legacy recorder-only settings edits preserve extra fields
unchanged. Canceling a New State transition does not reload the disk snapshot
over unsaved Studio rules/actions, and disabling condition-only mode is rejected
until a real image detection region has been recorded. Tests cover successful
single-write saves, retained screenshot/template bytes, failed-save guards and
cancelled navigation. CI and real-device acceptance remain separate gates.

**Condition editor correctness (November 7, 2026):** Switching recognition
types in the rule dialog never silently carries a color sampled from the wrong
kind of target. Pixel ↔ zone conversion clears incompatible geometry and requires
a new visual selection; OCR ↔ zone color retains the compatible zone and samples
its own expected BGR from the screenshot. Opening an existing saved rule without
changing its type retains its original expected BGR, including intentional
custom tolerances. GUI regressions cover conversion in both directions, visual
pixel selection, type restoration and correct per-kind status labels.

**Action editor ergonomics:** Tap, double-tap, hold and field-text actions
share a visually chosen point; swipe/drag uses a distinct visually chosen path.
Switching to Wait, Back or text-only actions temporarily hides these targets
instead of deleting them, and switching back restores the prior compatible
selection. Incompatible targets never authorize a different action kind. An
offscreen Qt test covers switching among those action types without entering XY.

**Offline screenshot replay (current core-Studio milestone):** A separate
`Replay recorded screenshots (no taps)` action evaluates the saved workflow
against its actual saved full-screen captures in presentation order. It uses
the production detector's `inspect` path and reports the recorded state,
actual selected state, and a reason when a different state wins (including
passive WAIT conflicts). Missing, corrupt and legacy screenshots are clearly
`unavailable`, never green. Capture references are restricted to actual
files under the workflow's `captures` directory. No ADB calls, emulator taps
or workflow writes are made. Replays are single-frame recognition evidence,
**not** action/timing/retry simulations or physical device acceptance. Unit
and offscreen Qt tests cover read-only results and missing/unsafe sources.

**Replay collision diagnostics (current iteration):** The read-only replay now
also records *other passing states* for each saved screenshot, even when the
expected state is selected. Competing passive WAIT and actionable candidates
are visibly distinguished in a scrollable five-column results table (recorded
state, result, selected state, other matches, reason). Image-template failures
show measured score and required minimum; failed OCR/color/pixel/NOT checks
retain concise rule descriptions. Multiple overlapping matches are a warning,
not automatically a replay failure, because the normal priority ordering still
chooses one state. The audit only inspects static saved captures; it does not
predict state transitions or change runtime tap behavior.

**Selected-state action preview (current iteration):** Saved screenshot replay
now shows the *intended* action sequence for whichever state actually wins,
rather than for the recorded state's label. The runner and audit share one
resolver for modern sequential actions, older single gestures and historical
two-anchor alternative taps. A winning passive WAIT explicitly previews *no
input*, and text content is hidden to avoid exposing field values. The visible
audit includes configured initial delay and step order; it does **not** execute
actions, type text, count a run or simulate retries. Physical execution still
requires two fresh matching observations, active device ownership and any
configured timing before an input command. Tests verify alternate-anchor
selection, action-chain precedence, WAIT override, step order and text redaction.

**Unsaved Studio editing history (current iteration):** WHEN conditions,
condition-only recognition mode and THEN action steps now support a bounded
30-edit **Undo / Redo** history. Adding, changing, deleting, reordering and
resetting rules or actions can be reversed without re-recording a screen or
writing workflow files. Rule and action selections remain visible after list
refreshes. History is scoped to the currently edited state, is cleared after
a successful Save, New State or state switch, and is preserved if an unsaved
transition is canceled. Undo/redo does not roll back screenshot geometry,
timing/retry fields or workflow-level changes; conservative unsaved-change
prompts remain enabled. Qt tests cover condition toggles, action reorder/reset,
selection restoration and save boundaries. This is draft-only undo, not a
persistent version history.

**Visual editing copy and order (current iteration):** Both WHEN condition rows
and THEN action steps now offer **Copy, move up/down and Remove** controls.
Copy creates an independent deep copy immediately below the selected row;
move retains the moved row's selection and is bounded at the first/last row.
Contextual controls disable themselves when there is no valid selected row or
movement is impossible, rather than appearing clickable but doing nothing.
These operations participate in the same 30-step draft Undo/Redo history,
preserve the user's editing context and only persist during an explicit Save.
Reordering conditions changes their review/display sequence, not the AND
recognition semantics; reordering actions changes their actual execution order.
Offscreen Qt tests cover independent copies, selection, disabled controls,
boundary no-ops, undo/redo and saved action-chain ordering.

**Dry-run execution validation and traces (November 7, 2026):** The
single-device runner now shares one input-free `validate_action` preflight
between real execution and dry-run simulation, covering supported action kinds,
normalized tap/focus/swipe targets, gesture duration, Android key codes, wait
duration and safe ADB text. Invalid or unsupported steps never increment
dry-run progress or claim a successful action. Each dry-run step logs its
ordinal, recognized state, action kind and explicit no-input marker; wait
steps are included, with their configured duration. Text payloads are not
included in trace logs, and dry-run `count_run` never modifies session run
totals. The Studio also uses this validator to reject malformed imported
or copied draft action chains *before saving*. Tests verify malformed inputs,
zero Android commands, ordering, privacy, and unchanged manifest on a
failed Save. Timing waits still use the existing nonblocking runner logic;
this is not physical device verification or an emulator-free full replay.

**Unsaved THEN action preview (November 7, 2026):** The Studio's THEN tab now
has a **Preview current draft actions (no taps)** control. It describes the
actual unsaved chain, or the currently recorded legacy gestures when no
override exists, using the same chain resolver and preflight validator as
runtime execution and saved screenshot replay. It checks bad targets and
unsupported commands *before Save*, displays waits and action order, and
never includes typed contents. Passive WAIT is explicitly no-input. Legacy
multi-anchor actions display a warning that only the first candidate is
shown; the winning anchor can select a different action at runtime. The
preview never captures the emulator, sends Android commands, writes workflow
files or simulates recognition/retries. Its result resets when edits change
the draft. Windows Qt and pure tests cover valid/invalid previews, redaction,
passive states, legacy actions and disk/device isolation.

**Authoring-time recognition validation (November 7, 2026):** The shared
`validate_condition` preflight checks OCR expressions/regex, detector kind,
integer screenshot region, pixel location, BGR components and finite color
tolerances without invoking OCR or examining pixels. It runs before a Studio
Save, before unsaved condition previews (validating the full rule set before
any OCR), and once at workflow load before real matching. This catches copied,
imported or externally edited malformed rules, including a dangerous invalid
negated color tolerance that could otherwise appear to pass. Errors identify
the affected condition/state, and refused Studio saves leave the manifest,
screenshots and anchors unchanged. Unit and offscreen Qt regressions cover bad
regex/regions/pixels/colors/tolerances and preserved valid saved workflows.

**Fail-closed matcher configuration (November 7, 2026):** Workflow startup
now validates state/action key alignment, explicit template versus
condition-only recognition mode, and finite matching metadata. Global and
per-state confidence thresholds must be finite numbers from 0 to 1;
priorities must be finite; modes are limited to `all` or `any`;
`passive` and `skip_template` must be actual booleans. A state with no
recorded image anchors may not silently fall back to condition matching
unless condition-only mode is explicitly enabled, and condition-only states
must contain rules. Invalid configurations fail *before* OCR, device capture
or input, with a state-specific diagnostic rather than an accidental match.
Normal image states and explicit OCR/color-only states remain supported.
Python unit tests cover NaN/infinite thresholds, invalid priority/modes,
ambiguous booleans, missing probes and mismatched state/action keys, plus
offscreen Qt validation of visible saved-inspection warnings. No runtime
confidence ordering or threshold policy is otherwise changed.

**Saved anchor integrity (November 7, 2026):** The Studio and runtime now
validate each image-anchor reference before loading legacy or mixed-mode
templates: filenames must be standalone names (no parent traversal, absolute
paths, Windows separators or drive/stream syntax), the resolved path must
remain inside the workflow's anchors directory (including symlinks), and
the recorded region must contain valid normalized integer bounds. After
loading, the actual grayscale anchor dimensions must match that region
exactly. A malformed template fails startup or saved-state inspection with
a state/template-specific explanation instead of being read from outside the
workflow or mismatching another image area. Python and offscreen Windows Qt
regressions cover path/geometry corruption, symlink escapes, image-size
mismatch and no device input or manifest writes. Normal legacy anchors and
explicit condition-only states are unchanged.

**Whole-chain action preflight (November 7, 2026):** Validating each step
only when it executes can still permit a valid first tap before an invalid
later step from an externally modified manifest is discovered. The runner
now validates *all* executable action steps before any screenshots or
Android input, including modern sequential chains and **every** legacy
multi-anchor alternative, using the existing `validate_action` checker.
Malformed action collections, unsafe text/targets and unsupported later
steps fail closed with a state and step index; per-step runtime checks
remain in place. Passive WAIT states do not execute retained legacy gesture
data and preserve compatibility. Offline saved-screenshot replay applies
the same startup preflight, warning without accepting a partial action
plan. Python and offscreen Qt regressions verify zero emulator input,
no manifest writes and preserved valid workflows.

**Saved retry and timing preflight (current iteration):** A workflow loaded
from external edits could previously contain string-valued `retry` or `end`
flags, negative delays, fractional retry intervals, oversized repeat limits
or an unsupported retry mode. Runtime coercion or clamping could silently
change how often a game receives input or whether a run is counted. The
detector now validates `retry` and `end` as real booleans, initial delay
as 0–86400 whole seconds, retry interval as 1–86400 whole seconds, extra
tap count as 0–20 and retry mode as `interval` or `on_return` before
OCR, screenshot capture or input. Valid boundary values and legacy absent
fields retain their existing meanings. Startup and offscreen Studio
inspection tests assert precise warnings, no ADB activity and unchanged
workflow files. This is metadata validation, not a change to retry policy.

**Retry serialization compatibility fix:** The existing workflow writer omits
`retry_mode` to represent `interval` and writes `on_return` explicitly,
but the runner had interpreted a missing mode as `on_return`. This prevented
persisted interval retries from firing. The runner and manifest validator now
default to `interval`, matching the writer; explicit `on_return` still never
retries an unchanged screen. A round-trip test writes both modes through
`update_step_retry` and verifies actual action timing with the input stub.
The existing per-visit retry cap and two-frame recognition checks remain.

**Boolean rule integrity and no-input inspection (current iteration):**
Imported OCR/color/pixel conditions previously used truthiness for the
`regex` and `negate` flags. Values like `"false"` therefore acted as
`True`, silently turning a literal OCR check into regex matching or
reversing a recognition condition. Shared condition preflight now permits
only actual booleans when these flags are supplied (omitted flags preserve
their old false default). The same preflight protects Studio draft saves,
offline previews, saved-workflow inspection, and runtime startup. Saved
Studio recognition inspection now validates the workflow **before** asking
a connected emulator for any screenshot. Pure and Windows Qt regressions
cover string, integer, and null flag rejection; genuine true/false matching;
the draft Save boundary; and no ADB or manifest writes on invalid rules.

**Studio saved-state image containment (current iteration):** The runtime
detector and offline replay already rejected unsafe image references, but
the visual editor's `load_step` still opened `capture` and fallback
`anchors` filenames directly from the manifest. An imported or externally
edited workflow could therefore direct the editor to inspect files outside
the workflow when selecting a state. Studio state loading now checks all
recorded image names and their resolved paths before any image read,
rejecting traversal, absolute paths, drive syntax and symlink escapes;
recorded region coordinates must also be valid normalized integers.
Missing historical captures still render their in-workflow template crops
for editing and require recapture as before. Python workflow-editor tests
cover unsafe capture/anchor references, malformed regions, symlink escapes
and legacy fallback; an offscreen Qt test verifies a readable warning
without image access or manifest changes. No Android input or device
permission is needed for this safety check.

**Safe saved-state image cleanup (November 7, 2026):** Saving a recaptured
state or deleting an old state still used untrusted manifest capture/anchor
filenames when unlinking obsolete image files, even though loading images
already enforced path containment. The editor now preflights *all* image
references against the saved workflow before any replacement screenshot is
written, any manifest is changed, or any old image is removed. Traversal,
absolute/Windows drive names and symlink escapes fail closed; legacy blank or
missing capture references remain valid. Python tests cover refusal of unsafe
anchor/capture cleanup, unchanged manifest/external files, and valid recapture
and deletion; offscreen Windows Qt covers the Studio Delete action warning
without file changes. This is a narrow editor integrity guard, not physical
workflow validation or a release/merge claim. Required CI and actual MuMu
acceptance remain independent gates.

**Shared saved-image preservation (November 7, 2026):** Legacy/imported
workflows can contain multiple states pointing to one screenshot or anchor.
After safe-path cleanup was introduced, recapturing/deleting one of those
states would otherwise unlink an image still required by the surviving state.
Reclamation now validates each recorded reference and removes only old assets
that are not referenced by any other state (including resolved in-workflow
aliases). A malformed sibling-state reference blocks destructive cleanup before
any screenshot or manifest write. Python regressions exercise shared-image
retention during recapture and deletion, and an offscreen Windows Qt regression
checks the real Studio Delete action. No user workflow data is migrated or
rewritten just for this guard; real-device and enforced CI acceptance remain
outstanding.

**Safe screenshot creation for imported state keys (November 7, 2026):**
Earlier integrity guards covered *reading and deleting* recorded images, but
recapture still interpolated the manifest's state key into its new JPG/PNG
filenames, and new states could write through externally symlinked image
folders. Newly recorded files now use generated, state-key-independent leaf
names; both destination roots and images pass the same in-workflow containment
validation **before the first image write**. Existing state identifiers remain
unchanged for legacy imports, and previously saved filenames still load.
Python tests verify traversal-shaped imported keys cannot redirect writes and
symlinked output folders are rejected before any manifest/image mutation;
offscreen Windows Qt tests verify a visible Save warning and no writes. No
real-device or required-CI-enforcement acceptance is inferred from these tests.

**Atomic Studio manifest staging (November 7, 2026):** The shared
manifest writer previously reused one fixed `manifest.tmp` staging path
across saves, allowing overlapping writes to interfere or leave a stale
staging file when replacement failed. Every manifest save now stages in its
own generated temporary file beside the destination and removes that file
in a `finally` block; the previous manifest remains intact if replacement
fails. This is an integrity and failure-cleanup improvement, **not**
concurrent editor conflict resolution: last successful whole-manifest
replacement remains authoritative. Python tests verify failed-replacement
cleanup and distinct staging paths across successive saves. Offscreen Qt
covers a Studio Save failure warning and preserved saved state; physical
MuMu acceptance and enforced branch gates remain outstanding.

**Replay state-decision evidence (November 7, 2026):** The saved-screenshot
replay now keeps the detector's per-state inspection outcomes, including every
candidate's selected/overridden/failed status, actual image score versus
threshold, and each evaluated OCR/color/pixel/NOT condition's PASS/FAIL.
Conditions skipped after a failed image threshold are labeled *not evaluated*,
not counted as passing. Selecting a replay row in Studio shows those details
in a separate read-only panel, without additional screenshots, Android input,
manifest changes, or exposing raw OCR output. Unavailable captures show their
specific reason rather than an empty or green evidence panel. Source-level
and offscreen Qt regressions cover WAIT collisions, image rejection, conditions,
selection and unavailable captures. This remains a **one-frame recognition
inspection**, not a sequence/timing simulation or physical-device acceptance.

**Offline replay directory containment (November 7, 2026):** The
saved-screenshot replay originally compared capture paths to the resolved
`captures` directory, but did not ensure that directory itself remained
inside the workflow. A symlink replacing the whole directory could therefore
redirect reads outside the saved workflow. Replay now uses the recorder's
shared safe-image resolver and reports affected screenshots as **unavailable**
before any screenshot read; invalid Windows-style capture filenames also fail
closed. The detector separately refuses a redirected `anchors` directory
*before* it reads any image template. Tests cover both directory redirects,
no external OpenCV reads, original manifest integrity and the offline Qt
replay status without emulator access. This extends the same previously
implemented read/write/delete boundary; it does not relax recognition or
represent physical MuMu validation.

**Committed edit consistency on cleanup errors (November 7, 2026):**
After a successful atomic manifest replacement, both recapturing and deleting
a state previously unlinked unused old images with errors still propagated to
Studio. If Windows held a screenshot file open, a completed update could be
reported as failed despite being permanently persisted, risking duplicate
operations and user confusion. Pre-commit validation and manifest writes remain
strict and fail closed. Only post-commit removal of **validated, no-longer-
referenced** images is now best effort: permission/IO failures are logged,
leaving a harmless orphan for later maintenance rather than falsifying the
Save/Delete result. Python tests cover actual recorded state persistence and
warning logs for both recapture and delete with simulated locked files, and
an offscreen Qt test confirms the real Studio Delete flow reflects the
persisted result. This is not a successful cleanup claim; the remaining old
files are explicitly verified to persist in the failure regression.

**Failed saved-state selection rollback (November 7, 2026):** An
externally edited or corrupted saved state can fail validation while being
selected in the Studio. Previously the list kept that failed row highlighted,
while the editor retained the earlier state's data and `editing_key`, making
a subsequent Save misleading. Both the original recorder and OmniFlow Studio
now restore the previous list selection on a failed load, or clear it if the
user was editing a new state. The existing unsaved-discard rollback shares
this same signal-safe helper. Source-level regressions verify unsafe
imported anchor/capture references fail before image reads or manifest writes.
Offscreen Qt regression verifies both selection contexts, an actionable
warning, unchanged editor fields/target and no false dirty status. This is UI state consistency,
not a claim that the invalid saved state was repaired.

**Saved screenshot geometry safety (November 7, 2026):** Studio's
canvas draws recorded screenshots and overlays on a fixed normalized 540×960
coordinate grid. A present but corrupt or differently sized imported JPEG
previously reached the editor (or could be tested during offline replay)
without verifying its native dimensions, risking misleading touch markers
and recognition results. Saved captures now must decode as 540×960 BGR color
frames before Studio loads them. Invalid geometry or unreadable saved files
produce a visible load failure and restore the previous selected state;
offline replay reports such captures as **unavailable** without calling the
recognition engine or creating an action preview. Missing full-screen captures
from older workflows still render from safe region templates and require
recapture before visual editing. Python tests verify normalized geometry,
corruption and legacy fallback; offscreen Qt checks both selected-state
rollback and read-only replay warnings. This is file-level geometry safety,
not proof of correct real-device touch scaling or MuMu acceptance.

**Normalized action target compatibility (November 7, 2026):** The
runtime's inclusive right/bottom edge values `x=540` and `y=960` are
intentional legacy coordinate inputs, safely clamped onto physical display
pixels even on rotated/cropped screens. Existing Linux geometry tests explicitly
verify this contract. A stricter exclusivity change was therefore **reverted
after failing existing tests**, rather than silently breaking imported
workflows or weakening the geometry gates. Python and offscreen Qt tests now
explicitly verify accepted inclusive edges and reject true overshoots
(`x=541` or `y=961`) before execution or Studio Save. New visual
authoring still selects only real pixels through `x=539`, `y=959`.
Physical-device geometry correctness remains an independent acceptance gate.

**Partial screenshot write rollback (November 7, 2026):** A failed OpenCV
image encoder can write incomplete PNG/JPEG bytes before returning false or
raising an exception. Previously the recorder only tracked image paths
**after** successful writes; incomplete output could linger in `anchors`
or `captures` even when the manifest transaction was rejected. Both
generated asset paths are now registered for cleanup *before* attempting
their individual image writes. Failure removes the partial target and all
other newly written images, leaves previous assets and manifest untouched,
and shows Studio's existing failed-save warning without a ghost state.
Source-level regressions cover new and recaptured states, both asset types
and both false-return/exception failures; offscreen Qt verifies the Studio
new-state path. This does not claim that filesystem cleanup can succeed if
Windows denies deletion; real hardware acceptance remains outstanding.

**Failed drag-to-reorder recovery (November 7, 2026):** The Studio
previously reloaded the entire workflow after a reorder manifest write failed,
risking a discard prompt or loss of unsaved WHEN/THEN edits and draft history.
The recorder now tracks the most recently persisted presentation order for
the selected workflow and, on a failed reorder, puts the existing list items
back in that order with list signals blocked. The highlighted row is restored
to the state actually being edited; no workflow reload, screenshot reset,
draft rewrite or undo/redo reset occurs. A successful drag updates the
in-memory baseline; a subsequent failed drag restores that latest committed
order rather than an obsolete initial order. Offscreen Qt regression covers
both phases, a simulated atomic manifest replacement failure, warning,
unsaved rule and name retention, preserved undo history, and eventual
successful Save of the draft without disturbing saved presentation order.
A mismatched externally changed state list cannot be restored in place;
the UI warns the operator to save their draft before manually reloading.
Reordering affects presentation only, not runtime match priority.

**Explicit confirmation before deleting an unsaved Studio draft
(November 7, 2026):** The old Delete flow asked only whether to delete
the highlighted saved state, then committed its removal and cleared the
editor's unsaved flag before loading the next state. Draft WHEN rules,
THEN actions, state-name edits and their history could therefore be lost
without the normal *Discard unsaved changes?* confirmation. Delete now
requires both the delete confirmation and, when the editor is dirty, the
existing explicit unsaved-discard confirmation **before** any manifest or
image mutation. Declining either preserves both draft and saved state;
a failure after confirmation also retains the draft. The existing atomic
manifest writer leaves saved files untouched if deletion cannot commit.
Offscreen Qt tests exercise cancel, failed delete and successful delete
with unsaved rules/actions; a source-level regression checks atomic write
failure preserves the state, images and original manifest. This does not
make state deletion reversible after both confirmations are accepted.

**Successful drag-reorder selection integrity (November 7, 2026):**
A successful Qt list drag can select the moved row while the Studio editor
still targets another saved state (or is editing a new unsaved state). This
mismatch made the highlighted selection misleading even though the reordered
manifest itself was correct. The list now suppresses transient selection
signals during the drop; after either a successful drag or failed reorder
recovery, it aligns the visible selection with `editing_key` without changing
the editor, confirming draft discard, or clearing undo history. New-state
drafts remain unselected. The saved manifest still changes only presentation
`order`, never runtime recognition precedence. Python regression verifies
that state definitions, actions and image bytes survive reordering and invalid
key sets fail closed; offscreen Qt regressions cover selected-state drafts,
new-state drafts, later Save, and a successful drag followed by a failed one.
This does not replace hardware/GUI acceptance of real mouse dragging.

**Legacy editor auto-recapture preserves visual targeting (November 7, 2026):**
When an imported/legacy saved state has detection templates but no full-screen
capture, the Studio renders a reconstructed preview and marks it for recapture.
Opening the rule or action editor with a connected MuMu can automatically fetch
a real screenshot. Previously this path called the new-state canvas loader,
silently clearing the state's saved region boxes, tap targets and drag timing
before the editor even opened. Studio now uses the same replacement semantics
as the explicit Capture/Replace button for an existing selected state: only the
image changes, while visual regions, gestures and drag durations are retained.
New-state editing still initializes a fresh canvas. The automatic screenshot
is marked as an unsaved visual edit and never writes the manifest until Save.
Source-level regression confirms template-only two-region tap/drag metadata
survives legacy loading; offscreen Qt covers both rule and action editor entry,
a cancelled editor dialog, exact retained overlays and a subsequent successful
recapture Save. Physical MuMu capture/coordinate accuracy is still unverified.

**Unified read-only recognition diagnostics (November 7, 2026):**
The Studio's single-screenshot “Test detection” previously duplicated the
per-state recognition explanations already exposed by offline replay.
Differences in labeling, PASS/FAIL formatting and unevaluated condition
reporting could make identical saved conditions appear to behave differently
depending on which inspection button was used. Live/saved one-frame testing
now calls the same `describe_recognition_evidence` formatter as offline
replay, including selected vs overridden passive WAIT states, per-rule
outcomes and threshold evidence. The read-only test also catches OpenCV
inspection and malformed imported state exceptions as a visible warning
instead of allowing a Qt slot exception to escape; neither path sends
input or writes workflow data. Offscreen Qt regression compares the complete
dialog evidence to the production detector's shared formatter and verifies
no Android actions, save or manifest changes on failed OpenCV inspection.
This remains a single-frame test, not a live run or MuMu acceptance.

**Malformed imported gesture guard (November 7, 2026):** Saved Studio
states can be imported or edited outside the application. Previously a
missing or malformed `taps`, `tap` or `gestures` field could raise an
unhandled `TypeError` when switching states, leaving Qt highlighting the
new row while the editor still targeted the previous state. Saved touch
coordinates and gesture dictionaries now receive explicit type, normalized
bounds, kind and duration validation before any saved screenshot is read.
Invalid inputs raise a contextual `ValueError`, allowing the existing
selection rollback and warning path to preserve editor/manifest state.
Current recorded tap/drag pairs and legacy v1 single-tap fallback remain
supported. Python regressions cover malformed imported fields plus normal
tap/drag metadata; offscreen Qt verifies failure warnings, no image reads,
no manifest writes, unchanged editor target and restored selection. This
is authoring-time input safety, not physical Android input validation.

**Imported state-control validation before editor activation (November 7,
2026):** External or legacy workflow manifests can contain malformed state
names, retry and passive flags, timer fields or retry modes. The base recorder
previously activated the selected state and then passed imported values
straight into Qt controls, including integer division on the pre-action delay.
Invalid data could trigger a Qt slot exception after switching the editor's
target or silently clamp values without warning. The saved-state loader now
checks the editable label, boolean state flags, pre-action delay, retry mode,
retry interval and maximum extra taps *before loading any screenshot*. It
reuses existing range validators and raises a readable ValueError so Studio's
existing failed-selection rollback preserves the previous target and UI.
Source-level tests cover malformed labels/flags/timings and valid maximum
timing limits; offscreen Qt verifies warning, prior target, unchanged controls,
no disk writes, no image reads and intact manifest. This does not alter the
runtime's separate manifest preflight or claim real MuMu behavior.

### Previous platform priority queue (historical)

1. **P0 OCR readiness:** fix standard installer/backend and verify UI OCR autofill.
2. **P0 screenshot bottleneck:** run the isolated stationary-screen `tools/collect_hardware_evidence.py --capture-comparison --serial <MuMu>` workflow to collect 120s PNG/raw capture profiles, compare image fidelity and assess performance on the same emulator.
3. **P1 text-action completeness:** verified field focus, keyboard/IME adapter and safe handling of unsupported characters.
4. **P1 operator visibility:** dependency checks in GUI, per-action dry-run traces, target screenshot snapshots, failure reason and retry status.
5. **P2 composition:** reusable workflows/subflows, variable extraction from OCR, conditional branching and loops with stop policies.
6. **P2 packaging & scalability:** concurrency benchmarks and acceptance on real Windows hardware; enforce branch protection before merging.

Every milestone requires its own documented tests, evidence and acceptance
criteria. Prioritize completing the current control issue before spinning up
new, duplicate active branches or claiming a production release.

**Imported WHEN/THEN state-switch preflight (November 7, 2026):**
When Studio selects an externally modified saved state, it now validates that
the recognition condition collection and action chain have list structure and
that every rule/action passes the shared production preflight **before** the
base editor reads saved images or asks to discard a current draft. Invalid
OCR regular expressions, unsupported detectors, malformed list entries and
unsafe touch targets show a contextual warning and restore the previous
selection without changing editor rules/actions or writing workflow files.
Valid imported condition/action chains remain editable; Windows offscreen Qt
tests cover failure rollback, preservation of unsaved drafts and valid imports.
This does not establish physical MuMu reliability or change merge gates.

**Saved-state selection before draft discard (November 7, 2026):**
The shared RecorderDialog now validates and decodes the target saved state
*before* asking to discard edits to the current state. Previously the
confirmation appeared first, so accepting could throw away unsaved Studio
rules, actions or the label before a corrupt target screenshot, malformed
import or unsafe capture path was detected. Failed destination loads now
warn and restore selection without prompting to discard or mutating draft
fields; valid targets still require explicit discard approval. Offscreen Qt
regressions cover corrupt destination with retained draft, zero image reads
for unsafe captures, and valid target with declined discard. This is a
local authoring safeguard, not a change to runtime matching or a claim of
physical Windows/MuMu acceptance.

**Strict imported THEN action value validation (November 7, 2026):**
The shared action preflight no longer truncates fractional gesture durations
or Android key codes, treats boolean values as numeric times/key codes, types
non-string JSON values as their Python representation, or accepts string
coordinates by implicit numeric conversion. Targets must contain two finite
numeric coordinates, gesture timing and key codes must use integer values,
wait duration must be a finite integer or float, and ADB text must be an
actual supported string. Valid fractional-second waits, integral command
defaults, and legacy normalized fractional coordinates remain supported.
The same validator is used before live input, dry runs, Studio imports,
saving, and read-only action previews. Source-level and offscreen Windows
Qt regressions exercise unsafe coercions and valid compatibility; no
device action is sent during a failed preflight. Physical Windows/MuMu
acceptance and protected dev required-check enforcement remain outstanding.


**Unified THEN text redaction in Studio summaries (November 7, 2026):**
Previously dry-run traces and offline action preview hid text payloads, but
the visible THEN action list reused `action_summary` and displayed the
first 35 characters of each `text` or `field_text` payload. Both list
entries now show the same non-sensitive descriptions as the saved replay
and unsaved preview: "Type text (contents hidden)" or "Tap selected field
and type text (contents hidden)". A single shared `action_summary` formats
all read-only action plans after preflight, avoiding duplicate redaction
paths. The action editor still opens with the full intended text, and
workflow manifests are not rewritten. Pure Python and offscreen Qt tests
verify that neither list nor preview exposes example payloads and that
editing remains intact. This does not encrypt saved workflow text,
change Android input, or substitute for physical Windows/MuMu acceptance.


**Fail-closed draft diagnostic preflight and stale-result cleanup
(November 7, 2026):** The unsaved WHEN "Test conditions" button now uses
the shared recognition validator *before* requesting a MuMu screenshot.
A malformed OCR regular expression or imported rule cannot trigger an
unnecessary screenshot/legacy preview replacement, mutate draft flags,
or leave a previous PASS result visible. If capture fails or no usable
screenshot exists, the status explicitly says the test is unavailable
rather than retaining a stale match. Draft THEN action preview now
also catches errors during legacy/explicit chain resolution, not just
errors formatting individual actions, so malformed in-memory drafts
become warnings instead of uncaught Qt slot exceptions. Offscreen Qt
regressions cover invalid OCR before capture, failed screenshot after
a prior match status, malformed action chains, no Android input, no
workflow writes and unchanged draft state. Live hardware acceptance and
protected dev required-check enforcement remain separate blockers.


**Empty active action chain preflight (November 7, 2026):** An
externally imported active state with `action_chain: []` used to
pass runtime startup validation and recognize its screen without
performing an action, yielding a confusing apparently stalled
workflow. The engine now rejects any non-passive state whose
resolved chain has no steps before device input; saved screenshot
replay rejects it before matching, Studio rejects the imported
saved state without discarding a current draft, and the unsaved
THEN preview shows an actionable error instead of "No configured
action." Passive WAIT states continue to accept empty or dormant
action data, and the established legacy gesture/tap alternatives
remain supported. Source-level startup/replay and offscreen Qt
regressions cover these paths; no gate was weakened. Physical
Windows/MuMu acceptance and protected dev CI enforcement remain
release blockers.


**All legacy THEN alternatives preflight in unsaved preview
(November 7, 2026):** The draft-only THEN preview previously validated
only the first legacy gesture when two paired image regions were configured.
A malformed second target or swipe duration could therefore be overlooked
in the preview even though the runner's saved startup preflight correctly
rejects every alternative. The preview now checks all legacy alternatives
using the same production action validator before showing its first-region
example; error feedback includes the failing alternative's number. Explicit
sequential action chains retain their existing behavior, and passive WAIT
states continue to skip dormant actions. Offscreen Qt regressions cover an
invalid second gesture, repair and successful preview, passive WAIT, and
zero screenshot/manifest I/O. Real Windows/MuMu acceptance and enforced
required checks on dev remain outstanding.


**Malformed legacy gesture entries in unsaved THEN preview
(November 7, 2026):** The draft action preview previously converted
each nonempty canvas gesture with `dict(step)` outside the preview's
error handler, and filtered away falsey non-None values. Corrupt or
programmatically imported second-region entries could therefore throw
an uncaught Qt slot exception or be overlooked while the first-region
action was displayed as valid. The preview now uses read-only references
to every non-None gesture and lets the shared production validator
reject malformed entries, including empty strings/lists and falsey
values, with an indexed legacy alternative error. No gesture data is
mutated and no screenshot or Android input is attempted. Source-level
startup and offscreen Windows Qt regressions cover malformed secondary
entries, unchanged manifest, stale-preview cleanup and successful
repair. Protected dev required checks and actual MuMu hardware
acceptance remain open release gates.


**Saved one-frame detection preflights THEN actions (November 7,
2026):** Studio's read-only "Test detection" previously used
`OmniDetector` and correctly checked recognition rules, but did not
run the saved-action startup validator used by `OmniRunner` and
offline screenshot replay. As a result, it could display an ACTION
match for a workflow that would refuse to start due to a malformed
later command or a nonwinning state's unsafe action. Test detection
now preflights *all* saved action chains using the same shared
validator before acquiring a MuMu screenshot or inspecting the frame.
An invalid command produces a contextual recognition warning instead
of an apparently ready state. Offscreen Windows Qt regression verifies
a nonwinning state's second invalid field-text target is caught before
ADB capture, detector inspection, Android input or workflow writes;
valid saved detection remains covered by its existing read-only test.
This is one-frame diagnostic parity, not live hardware acceptance.
Required-check enforcement on protected dev and real Windows/MuMu
validation remain outstanding.


**Imported saved-state matcher preflight before draft discard (November 7,
2026):** The Studio saved-state selector already rejected malformed imported
WHEN rules and THEN chains before prompting to discard current edits, but
could still activate a state with unsafe recognition settings such as a NaN
threshold, unsupported image match mode, non-numeric priority, or
condition-only mode without any WHEN conditions. The editor now reuses the
production `validate_recognition_manifest` preflight on an isolated view of
the selected destination state before reading image files or asking to discard
an unsaved draft. A different broken state in the same workflow does not
prevent opening a valid state for repairs. Two offscreen Windows Qt regression
tests cover imported recognition faults, retained unsaved rules/actions/name,
no screenshot reads, no manifest writes and valid-state isolation. The runtime
continues to validate the entire workflow on start; this UI guard does not
weaken that gate. Physical Windows/MuMu acceptance and enforced required
checks on protected `dev` remain outstanding.


**Qt regression correction — imported control validation order
(November 7, 2026):** Windows offscreen Qt CI exposed five failures after
the new matcher preflight because invalid saved recorder controls
(`retry`, pre-action delay, retry mode/interval and extra taps) were
reported by the runtime matcher validator before the established
recorder-level "Invalid saved" diagnostic. The Studio preflight now invokes
the existing shared `_validate_saved_editor_settings` validator before
the additional isolated matcher preflight, preserving previous warnings
and unsaved-draft safeguards without weakening either validation path.
No tests or CI checks were relaxed. This correction must pass both Python
and Windows Qt checks at its own commit before being marked green.


**New feature — beginner-friendly ALL (AND) / ANY (OR) WHEN logic
(November 7, 2026):** The Studio's WHEN tab now offers an explicit
"Match all" or "Match any" selector for text, pixel and color
recognition conditions, supporting game menus, alternate prompts and
different valid screen cues without duplicating states. The matching
engine and read-only replay share the same `condition_match` state
field, which defaults to legacy AND when absent. The saved image
template remains a separate mandatory gate when enabled; OR never
bypasses it. The unsaved tester reports overall MATCH/NO MATCH,
AND/OR semantics and each rule's PASS/FAIL. The choice is included
in Studio undo/redo and atomically saved with both screenshot edits
and settings-only changes; choosing default AND omits the extra
field for backward compatibility. Invalid saved modes are rejected
before discarding a current draft. Engine, replay, persistence and
offscreen Windows Qt regressions cover correctness and usability.
Nested rule groups, OCR variables and advanced branching remain future
incremental capabilities requiring their own tests and safe preview.


**AND/OR CI test correction (November 7, 2026):** The initial feature
head `0c7e156` ran the full Python and Windows Qt suites. A new replay
assertion failed in each platform because it compared an unindented
string against a list whose display lines deliberately start with two
spaces; the actual replay evidence and condition match were correct.
The test now checks for the expected phrase within the individual
evidence lines, without changing production behavior or suppressing
the original assertion. Added imported invalid `condition_match`
coverage to the existing unsaved-draft preservation regression.
The correction must pass all six exact-head CI checks independently.


**Beginner guided quick-start for WHEN rules and THEN actions
(November 7, 2026):** The Studio now exposes compact quick-start selectors
alongside the existing full-featured +Condition and +Action tools.
In WHEN, users can choose text appearing/disappearing or pixel/area
colors matching/differing; the existing visual rule editor opens with
the detector type and NOT flag preselected. In THEN, novice-friendly
choices cover tap/double-tap, swipe, hold, tap-then-type, type into
focused field, wait, Back/Home/Enter, game launch and completion marker.
Each choice launches the existing validated action editor for
review/confirmation, rather than sending device input. Screenshot
capture is requested only for actions requiring a visual touch path;
wait, keys, text into focused field, launch and completion no longer
ask MuMu for a screenshot solely to construct that action.
Legacy recorded gestures remain ahead of newly appended actions
unless explicitly reset; the existing undo/redo, Save, and dry-run
workflow handles the same saved manifest schema. Offscreen Qt
regressions verify preset selection, no write/ADB side effects,
safe cancellation and missing-frame behavior, undo/redo and
settings-only persistence. No new runtime action types or CI gate
relaxation; real Windows/MuMu acceptance remains pending.


**Windows Qt guided-preset data fix (November 7, 2026):** The new
offscreen Windows Qt test revealed that using Python tuple user-data
for `QComboBox` could not be found reliably with `findData`,
resulting in `currentData() is None` in the guided WHEN setup.
Presets now use stable string identifiers (`ocr_not`, `pixel`,
etc.), decoded only when the user opens the existing rule editor.
The test explicitly asserts that every selected preset exists
before it is activated. The choices, recognition behavior, and
safety boundaries are unchanged; CI gates are not weakened.


**CI root-cause correction: coherent benchmark frame-rate reporting
(November 7, 2026):** A Linux Python 3.12 worker intermittently failed
the existing strict `frame count and FPS disagree` guard during a
short ten-worker synthetic benchmark. The benchmark stored a duration
rounded to milliseconds but calculated frame rate from the unrounded
duration; in a ~0.18-second run the two values could legitimately
differ beyond the unchanged discrepancy tolerance. It now calculates
reported FPS from the *same rounded measured duration* written to the
device result. No frames, timestamps, capture errors, hardware
acceptance, or threshold values are fabricated or waived; the
consistency guard remains intact. A deterministic short synthetic
clock regression verifies 5 frames in 0.08749 seconds report 0.087 s,
with an FPS that reconciles exactly with that reported duration.
The fix is narrowly required to keep the existing CI quality gate
meaningful; it is not a resumption of multi-device implementation.


**Feature — read-only screenshot action target map (November 7,
2026):** Added a beginner-friendly THEN control to visualize numbered
tap/double-tap/hold/field-focus targets and swipe/drag arrows over an
in-memory copy of the loaded original screenshot. A plain-English
side panel lists all actions, including nonvisual waits/keys, and
redacts text contents. Legacy two-region gestures appear as explicitly
labeled *alternatives*, never an invented sequential action chain;
passive WAIT has no touch markers. The existing draft text preview
and new map share the same production action preflight; invalid
drafts fail closed. Missing/stale saved captures report unavailability
without invoking MuMu or changing workflow files. Offscreen Qt
regressions check target drawing, source-image immutability, redaction,
validation, zero screenshot/device writes and paired legacy mapping.
The UI is a read-only guide, not simulated playback or hardware
acceptance; merge gates remain enforced-check protection on dev and
physical Windows/MuMu acceptance.


**New beginner feature — per-state image confidence and read-only
similarity tester (November 7, 2026):** Studio's WHEN tab now offers
a simple optional custom image-confidence percentage (50–100%, one
decimal), defaulting to the workflow's existing image threshold when
not enabled. Higher values are stricter, without changing OCR/color
condition semantics. A read-only check compares the currently loaded
full screenshot to the **saved image template** through the same
production matcher and reports numeric similarity, cutoff and
MATCH/NO MATCH; it does not call MuMu or execute actions. Unsaved
template region changes and WHEN rules are explicitly excluded from
this one-frame result. The control participates in Studio undo/redo,
state selection and atomic settings-only/new-capture saves; disabling
the override removes the per-state threshold field. The shared
workflow writer validates finite unit-interval values before writes,
including programmatic calls. Legacy and external workflows without
a per-state threshold retain the original global default. Actual
Windows/MuMu acceptance and protected-dev required-check enforcement
remain separate release gates.


**Confidence UI regression coverage (November 7, 2026):**
offscreen Windows Qt tests exercise custom 94.5% threshold toggling,
undo/redo, atomic settings-only save, restoration, removal of the
override, and unchanged saved screenshot/template bytes. A separate
read-only screenshot test injects a known 89% production template
score, showing MATCH at the workflow's 82% default and NO MATCH at a
draft 94% cutoff while proving no ADB capture or manifest writes.
Source-level writer tests reject NaN, Infinity, negative, >100%,
boolean and string thresholds on new recording and settings updates
without altering the previous manifest. Exact-head CI must verify
the complete suite before declaring success.


**Imported confidence compatibility follow-up (November 7, 2026):**
the new percentage control accepts the engine's entire valid 0–100%
range so older manifests with low custom image thresholds are not
silently clamped when editing unrelated fields. A tooltip warns
that low scores risk false positives. Settings-only callers that
omit the new optional `threshold` field retain existing saved
overrides, while the current Studio explicitly sends None to
clear its override. Source and offscreen Qt tests verify a saved
40% threshold survives name editing and legacy settings updates.
No matching thresholds, validations, or CI gates are weakened.

**Beginner OCR comparisons (current core-Studio iteration):** Text checks may
now explicitly use **contains**, **equals**, **starts with** or **ends with**
(ignoring case), selected in the visual Rule Editor without requiring regex.
Saved rules lacking `text_match` continue to mean contains, and existing
`regex: true` rules retain their behavior. The editor only writes an
override for non-default comparisons, and the shared runtime/preflight path
rejects unsupported modes or conflicting imported regex/comparison settings
before capture or input. Unsaved previews, live matching and saved screenshot
replay share the same evaluator and human-readable condition descriptions.
Source and offscreen Qt tests cover successful/negative comparisons, OCR
caching, legacy regex, visual editing and persisted condition-only matching.
No physical acceptance or protected-branch claim is implied.

**Read-only recognition measurements (current Studio iteration):** Unsaved
WHEN checks and saved screenshot replay now explain numeric pixel/zone
maximum color difference versus configured tolerance and report whether OCR
returned no text or recognized a bounded-length count of characters.
These observations are derived from the *same* image and OCR cache used for
the actual pass/fail decision; NOT-rule explanations identify the base check
and reversal. Raw recognized OCR text is never included in diagnostics.
Saved-state inspection uses production matcher priority/selection rules;
unsaved previews still do not claim to select a state. New source and
offscreen Qt regressions cover status/explanations, negation, OCR caching,
no secret text in trace output and read-only no-input behavior. No physical
acceptance or required-check enforcement claim is implied.

**Overlapping-state rank diagnostics (current Studio iteration):** Saved
screenshot replay and read-only recognition inspection now explain *why* a
passing state lost: passive WAIT precedence, lower measured image confidence,
lower explicit priority at equal confidence, or stable state-key tie-break
when all other ranks tie. The output includes relevant state priority values
without changing their ordering or saving any workflow data. The replay
results' reason column surfaces the comparison for lower-ranked matches;
existing WAIT result labels remain unchanged. Matching continues to use
the exact production comparator (WAIT, confidence, priority, key).
Engine, replay, and offscreen Qt regressions assert those four cases
and the absence of device input or manifest writes. CI and Windows/MuMu
physical acceptance remain independent release gates.

**Editable state selection priority (current Studio iteration):** WHEN now
exposes optional numeric state priority with explicit guidance: a passive
WAIT always wins, confidence ranks ahead of priority, and priority only
breaks equal-confidence matches. Blank means the default priority of
zero. The plain-text numeric field preserves large finite imported values
without spinbox clamping, validates nonfinite/malformed input before any
screenshot or manifest write, and participates in the existing bounded
draft Undo/Redo and state-switch boundaries. Both recapture/new-state and
settings-only saves pass through the same finite-number validator and
atomic workflow writers; legacy settings editors that do not specify a
priority preserve it. Regression coverage checks round-tripping, undo,
invalid inputs, image stability and no Android input. No detector ordering,
required CI gate, or physical hardware acceptance threshold is changed.

**Offline screenshot authoring in Studio (current core-Studio iteration):**
Users can now choose **Load screenshot from file** immediately beside the
MuMu capture control and draw the same recognition regions and tap/swipe
targets without connecting an emulator. Only explicitly selected PNG/JPG/BMP
8-bit color portrait captures with compatible 9:16 geometry (or supported
749×1369 legacy format) are accepted; unsupported ratios, alpha/grayscale,
invalid bytes and oversized files fail before touching the editor. Selected
images are normalized with the production screenshot transform; replacing
a screenshot preserves existing unsaved region and gesture targets. The
editor is marked unsaved and does not persist images or touch an emulator
until the user explicitly saves, and the UI states that imported captures
do not establish live-device correctness. Source and offscreen Qt tests
cover decode/geometry rejection, cancellation, preserved unsaved edits,
new-state offline saves, existing-state recapture and zero ADB input.
Physical Windows/MuMu acceptance and enforced `dev` checks remain release
requirements.

**Reuse saved screenshots across states (current Studio iteration):** Alongside
live capture and offline file import, Studio now offers **Reuse saved
screenshot** to pick an existing recorded state's actual capture in the
same workflow without switching the current draft. Selection keeps the
draft state name, recognition zones and tap/swipe targets intact, marks
the screenshot as unsaved, and requires the normal explicit Save State
before generating any new files or replacing image templates. Sources
use the recorder's path-containment guard and require a readable,
normalized 540×960 color screenshot; missing, unsafe, corrupt,
unrecorded and canceled inputs never replace an editor draft. No
reconstructed legacy screenshot is represented as an actual capture.
Offscreen Qt regressions verify new-state reuse, source independence,
cancellation, unsafe paths, lack of ADB input and no premature writes.
Physical Windows/MuMu acceptance and enforced required checks remain
release gates.

**Combined unsaved recognition check (current Studio iteration):** A new
**Test full draft WHEN** read-only control evaluates the current unsaved
AND/OR rules and custom image confidence together, on a single screenshot.
For existing saved states it constructs an isolated in-memory one-state
view and calls the production detector's `inspect` matcher; it never
rewrites anchors or mutates the saved manifest. Image threshold failures
short-circuit OCR exactly as in live matching and report per-rule evidence
only when evaluated. For a new condition-only state it uses the same
validated OCR/color/pixel predicate without requiring a first save.
New image-template states and unsaved region/image recaptures are not
invented as saved templates: the test clearly requires an actual saved
template first. The result explicitly excludes other-state ranking,
action readiness and live two-frame safety confirmation. Validation occurs
before any optional screenshot read, and failures clear stale success text.
Engine/replay and Windows Qt regressions cover correct matching, thresholds,
no leaked OCR output, validation and no device input or disk changes.
No physical Windows/MuMu acceptance or `dev` protection claim is implied.

**Undo/redo visual mark edits (current Studio iteration):** The bounded
30-step draft history now includes manually drawn legacy detection
regions, taps and drag paths/durations alongside existing OCR/condition
rules and action lists. Mark snapshots store only region and gesture
metadata, never screenshot pixels. Canvas change signals record a
snapshot before each actual mark change; undo/redo restores the paired
tap aliases and duration controls consistently and conservatively
marks any restored visual targets as needing a visual save. Screenshot
file import, saved-image reuse, live capture, state switch and Save
begin fresh histories rather than allowing undo across incompatible
images or states. Offscreen Qt regression tests cover region, tap,
drag, undo/redo, screenshot-boundary reset and no premature writes or
device input. Hardware acceptance and enforced required-check
merge gates are unchanged.

**Prevent stale Studio preview results (current iteration):** The Studio now
invalidates previously successful read-only recognition results whenever
its screenshot frame object changes, including imported file captures,
saved screenshot reuse, live MuMu capture and older recorded-state
loads. Per-rule, image-similarity and combined-draft verdicts no longer
remain visibly matched for pixels that have been replaced. Changes to
draft fields or legacy canvas gestures also reset the unsaved action
preview and combined recognition verdict; existing rules/AND-OR edits
continue invalidating their specific checks. No capture, ADB input or
manifest write is performed by invalidation. Offscreen Qt regressions
verify prior MATCH status disappears after offline screenshot replacement
and unsaved action preview disappears after gesture edits. This is
preview freshness, not multi-frame live-state confirmation; physical
Windows/MuMu acceptance and protected CI remain release gates.

**Unsaved WHEN cross-screen audit (current Studio iteration):** Studio now
checks the current unsaved recognition rules against all recorded full-screen
workflow captures without saving or touching MuMu. This extends the
single-screenshot combined draft preview to surface potential false
positives: matching a *different* state's saved screenshot is reported
as a possible collision, not as proof the draft would win normal
multi-state selection. For existing states it uses the same isolated
in-memory production detector and current unsaved AND/OR, optional
template and custom threshold. New condition-only drafts can run without
a saved template; new or modified image-template drafts cannot claim
validity without saving their updated anchors first. Invalid rules fail
before reading saved captures. Missing, unsafe, damaged and wrong-size
captures remain unavailable, never counted as passing. The batch read
shares the existing replay path/geometry checks, reports per-screen
recognition evidence and read-only counts, and explicitly excludes live
two-frame confirmation and action readiness. Qt/source regressions
verify cross-screen matches, invalid input/capture containment, saved
manifest immutability and absence of ADB input. No multi-device or
merge-gate changes.

**Read-only mapped actions from saved replay (current Studio iteration):**
The saved screenshot audit now lets a user open a numbered target map
for the *actually selected* action sequence on each readable recording.
A click reopens and validates the live manifest and its complete saved
action collection, safely reloads the selected recorded screenshot,
re-evaluates recognition and resolves the winning action chain with the
production trigger (including legacy two-region alternatives and WAIT
overrides). No stale table verdict alone can authorize a map, and no
device input, manifest write or screenshot acquisition is performed.
Unavailable/unknown rows cannot show action maps, and changed unsafe
capture names are rejected even if an earlier replay was successful.
The saved-action map distinguishes itself from draft previews and
explicitly excludes live two-frame confirmation; private text contents
remain hidden. Source and offscreen Qt tests cover WAIT selection,
stale capture refusal, invalid non-winning actions, button state and
disk/device immutability. Hardware acceptance and enforced `dev`
checks remain merge blockers.

**Per-action dry-run trace in action maps (current Studio iteration):**
Both unsaved draft target maps and saved-screenshot replay target maps now
show a validated, ordered, explicitly non-executing per-step trace
alongside numbered visual markers. The trace describes normalized target
positions and runner-equivalent effects for taps, double taps, holds,
drags/swipes, field selection and text entry (text contents always
hidden), key events, delays, game launch and run completion. It also
shows the configured initial delay after stable recognition. Saved
replay obtains this delay from a newly revalidated winning state;
unsaved maps use the current editor value. WAIT outcomes show no
actions. Device resolution/cropping mapping, live session permission,
fresh two-frame confirmation, and real execution remain untested; no
runtime behavior was changed. Source and Windows Qt regressions cover
step sequence, secret redaction, delays, invalid targets and UI text.
Release/merge hardware and required-check gates remain unchanged.

**Legacy border-target map compatibility (current Studio iteration):**
Saved workflows may legitimately store normalized touch coordinates
at the inclusive legacy right/bottom edge (540,960), which the live
runner clamps to pixel (539,959). The read-only ActionMapDialog used
to reject those valid legacy touches because new screen-coordinate
picking intentionally requires points strictly inside the image.
Action maps now prevalidate the full actionable chain before Qt
painting, then use the production runner's normalized-to-screen
mapping to render inclusive endpoints consistently. A try/finally
always closes the painter if graphical rendering unexpectedly raises.
New visual selections remain strict and no runner/touch behavior
changes. Source/Windows Qt tests cover saved-edge replay, tap,
double-tap, swipe and field touch targets, redacted content, out-of-
range rejection and absence of device input. Physical Windows/MuMu
acceptance and enforced `dev` status checks remain release gates.

**Edit legacy inclusive-edge actions without corrupting targets (current
Studio iteration):** The visual action editor now accepts old saved tap,
double-tap, hold, field-text, swipe and drag endpoints at the inclusive
540×960 normalized corner, using the same production target validator
as replay and the live runner. Opening, switching action kinds, canceling
the picker, or saving an unchanged action preserves the exact stored
legacy endpoints instead of rejecting or silently modifying them.
Only the visual picker *preview* clamps inclusive endpoints into the
drawable 539×959 corner; actively repicking records the newly selected
strictly in-screen coordinates. Out-of-range legacy targets still fail
closed. No additional coordinate entry UI is introduced and no Android
input or workflow write happens in the editor. Offscreen Qt tests cover
all supported point and swipe kinds, picker cancel, mode switching,
repicking, preservation, and invalid point rejection. The runner and
release gates are unchanged.

**Match runner gesture geometry in saved/draft maps (current Studio
iteration):** Production OmniRunner accepts legacy hold, swipe and drag
commands using a single `point` for stationary gestures or a mix of
`start`/`end` fields. The visual action-map drawing path incorrectly
assumed `hold.point` and `swipe/drag.start/end` always existed; valid
saved workflows could therefore raise a KeyError mid-draw. Maps now
derive start and end with the exact production `action.get('start',
action.get('point'))` / `end` fallback, draw an arrow only for actual
motion, and always mark the first touch. Action prevalidation and the
finally-closed painter remain in place, with no change to runner input
or stored workflow data. Source and Windows Qt regressions cover
legacy gesture field variants, inclusive edge coordinates, redacted
trace details, immutability and fail-closed invalid targets. Actual
MuMu touch acceptance and protected status checks remain outstanding.

**Preserve uncommon legacy gesture formats in ActionEditor (current Studio
iteration):** The production runner and saved replay accept hold gestures
with start/end paths, swipes with only point, mixed point/end paths and
the legacy drag kind. The visual action editor previously required
hold.point and swipe.start/end to prepopulate, so an unchanged edit
could fail, erase a valid target or silently turn an old drag into swipe.
The editor now displays the runner's effective target on the screenshot
for each supported shape while retaining the original serialized kind
and target fields when a user changes only duration, switches kind away
and back, cancels the picker or saves unchanged. A successful explicit
visual repick converts the selection to the modern point or swipe
encoding, as appropriate; this is never done on a canceled picker.
The existing strict new-target picker, invalid-target rejection and
redaction rules are preserved. New offscreen Qt regressions exercise
old hold/swipe/drag variants, duration changes, kind switching and
explicit repicks. No live Android input or workflow write is involved.
Release acceptance and protected checks are unchanged.

**Keep moving hold geometry intact on explicit repick (current Studio
iteration):** In the previous legacy gesture editor enhancement, opening
a hold with different saved start/end points correctly preserved it on
an unchanged Save, but the picker still offered a *single point* on
retarget, converting a moving Android swipe into a stationary hold.
The editor now recognizes moving hold paths and chooses the full swipe
selection UI, validates/preserves both endpoints and the hold kind even
after switching action types away and back. Stationary holds continue
to use a point picker, and only an accepted repick modernizes their
coordinate representation. Existing cancellation, strict new target
validation and file/device immutability are unchanged. Windows Qt
regressions verify path versus point picker modes and both endpoints.

**Invalidate stale image-MATCH statuses on draft mode/region/undo changes
(current Studio iteration):** An earlier saved-template MATCH could remain
visible when Studio switched to condition-only recognition, after moving
the draft's image rectangles, or after restoring a prior draft snapshot.
Even though full combined-draft matching already refused unsaved image
templates, the individual image result could mislead visual authors.
Studio now clears that result on condition-only mode changes, marks it
stale after drawn anchor-region changes (saving is necessary before
testing the revised template), and resets it on undo/redo. Existing
recognition tests keep explicitly distinguishing read-only saved anchors
from unsaved regions; screenshots, OCR, manifest files and Android
input are never touched merely to invalidate status. Offscreen Windows
Qt tests verify mode toggle, region editing and undo/redo never leave
old MATCH verdicts visible. Physical Windows/MuMu acceptance and
enforced branch protections remain outstanding.

**Fail-closed imported OCR text (current Studio safety increment):**
OCR condition content is now required to be an actual string at the shared
recognition preflight, rather than silently coercing JSON null, numbers,
booleans or objects into searchable text. This applies equally to unsaved
rule preview, Studio Save and live detector startup before OCR inference,
including negated rules. Existing string comparisons and regex behavior are
unchanged. Regression cases assert that invalid values fail before any OCR
call, and remain invalid when loaded from the saved workflow. This is not
physical MuMu verification; the dev protection and hardware gates remain.

**Malformed comparison labels in Studio (current safety increment):**
The OCR condition list now handles an imported unsaved comparison value that
is an array, object, null or number without throwing while displaying the
rule. Invalid comparisons are explicitly labeled, and the unchanged shared
recognition preflight still rejects them before a screenshot, OCR inference,
Save or live execution. Source tests cover malformed and valid labels;
offscreen Qt tests cover displaying/editing context, refusal to test/save,
and unchanged workflow files/ADB input. This corrects the draft display
path only, not matching semantics or physical MuMu acceptance.

**Unsaved image-region preview freshness (current Studio increment):**
The separate read-only image similarity button now checks that the currently
drawn detection rectangles still match the selected state's saved template
rectangles before calculating and displaying MATCH/NO MATCH. A changed
unsaved region no longer compares an old anchor and presents its score as
proof of the edited region; the UI instead requests an explicit Save.
Restoring the saved regions permits the original saved-template comparison,
including when the screenshot itself has changed for read-only recognition
inspection. The guard does not send Android commands or write workflow
files, and an offscreen Qt regression covers edit/undo, score suppression,
read-only behavior and intact existing captures. Live recognition, condition
rules and template thresholds remain unchanged; hardware validation and
enforced dev checks are still required before merge.

**Bounded offline saved-capture decoding (current Studio increment):**
Saved-screenshot replay, unsaved WHEN batch audits, and saved-action maps now
reject zero-byte and greater-than-32-MiB recorded captures before calling
OpenCV to decode the file. This reuses the existing manual screenshot-import
size ceiling and reports the affected capture as unavailable, not matched.
The read-only source tests exercise empty and sparse oversized captures
through all three replay paths, assert no capture decoding, recognition,
device input or manifest writes, and preserve normal saved screenshot replay.
This is an input file-size guard, not a guarantee against every compressed
image decompression hazard; normalized 540×960 frame checks still apply after
decoding. Live capture, matching thresholds and hardware acceptance remain
unchanged; dev required-status enforcement must still be enabled before merge.

**Bounded saved-anchor decoding before engine startup (current Studio increment):**
The shared 32-MiB image-file ceiling now also protects recorded image
anchors, before either the legacy all-template ScreenDetector loader or
the mixed image/condition-only OmniDetector loader invokes OpenCV.
Empty, inaccessible, or oversized anchors are rejected as WorkflowErrors
during preflight, before a Runner or device can execute input. The guard
follows existing safe filename and symlink-containment checks and keeps
saved region/anchor shape checks in place. The existing screenshot import
and read-only saved-capture replay paths use the same shared size constant
to prevent limits drifting. Regression coverage exercises both loader
routes with zero-byte and sparse oversized files, verifies no image decode,
OCR or device commands, and confirms normal anchors still recognize.
Physical Windows/MuMu acceptance and enforced required dev checks remain
unverified; the PR stays draft/unmerged.

**Pixel recognition zone/point consistency (current Studio increment):**
Imported, copied and authored pixel conditions now require the sampled
pixel to lie inside their selected rectangular zone (inclusive left/top,
exclusive right/bottom). Previously the zone could indicate one feature
while live recognition actually sampled a different part of the screen,
including a negated rule; such a mismatch could produce a false action.
The existing default full-screen zone still accepts legacy pixel rules
that omit a rectangle, and valid screen-edge selections remain supported.
The shared recognition preflight rejects inconsistent rules before
offline preview OCR, Studio Save, or live detector startup. Pure engine
tests cover positive boundary behavior, default full-screen rules and
fail-closed invalid/negated imports; offscreen Qt regressions verify the
invalid rule is rejected without OCR, emulator capture or manifest edits.
Real MuMu acceptance and enforced required `dev` checks remain outstanding.

**Fail-closed direct detector screenshots (current Studio increment):**
The production detector's shared `detect`/`inspect` evaluator now checks
that every direct-caller frame is a nonempty uint8 three-channel color
image before normalization, image template matching, or OCR. The live
runner already validates captured device pixels; this closes the
read-only Studio / external caller gap where an invalid direct image
could raise an unrelated OpenCV error or accept an unintended alpha/float
format. Source regressions cover missing, empty, grayscale, four-channel
and floating-point frames for both interfaces and still verify normal
matching. Offscreen Qt regression verifies a malformed combined-draft
screenshot clears stale MATCH status, does not call the template matcher,
OCR, ADB or workflow writes, and leaves manifest bytes unchanged.
Neither normalization policy for valid screenshots nor live action
authorization changed. Actual Windows/MuMu acceptance and enforced
required `dev` checks remain outstanding; PR #2 stays draft.

**Malformed unsaved action labels remain editable (current Studio increment):**
The unsaved action-list summary no longer casts an imported/copy-pasted
hold, swipe, or drag duration without validating its type first. Invalid
string, list, object, null, boolean and fractional values now display
an explicit invalid-duration label rather than crashing the Studio list;
non-object action entries also receive a safe invalid-action label.
This is presentation hardening only: the existing action validator still
rejects these commands before dry-run previews, saved action maps, actual
Save or device input, without silently converting their values. Pure
summary and offscreen Qt regressions check the visible labels, strict
preview/Save failure, no OCR, screenshots, Android input or workflow-file
changes. Valid action summaries and permitted durations are unchanged.
Real Windows/MuMu acceptance and enforced `dev` checks are still required.

**Malformed unsaved WHEN rules remain visible without unsafe overlays (current Studio increment):**
Copied/imported unsaved rules that are not JSON objects can no longer crash
the condition summary or Studio's canvas-overlay refresh. Rules with an
invalid rectangle are also omitted from visual overlays, so the canvas
painter never receives malformed geometry. The original invalid rule stays
in the editable/removable rule list: a non-object rule explicitly requests
removal/replacement instead of opening an editor that expects an object.
Shared strict recognition preflight remains unchanged: testing and Save
refuse invalid rules before OCR, device screenshot capture or manifest
writes. Pure tests cover safe non-object condition summaries and
fail-closed recognition; offscreen Qt tests cover malformed object/non-object
rectangles, list visibility, canvas repaint, refusal to test/save, safe
removal, and unchanged stored workflow bytes. This improves Studio
resilience, not recognition semantics or real MuMu acceptance.
Required `dev` status protection and physical hardware checks still block merge.

**Fail-closed editing of malformed unsaved entries (current Studio increment):**
Studio now runs the same strict recognition/action validators before
opening existing draft RuleEditor and ActionEditor dialogs. Previously
malformed imported/copied rules (e.g., a null or noninteger rectangle)
and actions (e.g., dict/list/string gesture duration) could pass a
safe list display but throw when the modal editor tried to initialize
its controls. Invalid entries stay visible and removable; requesting
Edit instead produces an explicit replacement instruction without
requesting an emulator screenshot, constructing a modal editor, or
overwriting the unsaved draft. Valid entries continue to use the normal
visual editing path. In addition, ActionEditor result extraction now
handles invalid dialog return values by warning rather than throwing.
Offscreen Qt regressions check both editors and invariants; all
original strict preview, Save and runtime validation remain enforced.
Real Windows/MuMu acceptance and `dev` required-check protection
are still outstanding.

**Validated emulator screenshots before Studio recapture (current increment):**
The shared visual-authoring frame helper now requires a nonempty, three-
channel uint8 screenshot before normalization. Studio's on-demand MuMu
capture for rule/action editing uses that helper instead of normalizing
unvalidated device frames. Empty, grayscale, four-channel or floating-
point frames now produce a capture warning without replacing the saved
screen, detection boxes, gestures or unsaved editor state. This is the
authoring/Qt fallback path; the live Runner already checks screenshot
pixels separately. Pure tests verify rejection occurs before resize,
and Windows offscreen Qt tests verify invalid recaptures leave the
existing workflow files and canvas untouched, while a subsequent valid
capture preserves the selected zones/gestures. Real Windows/MuMu capture
acceptance and enforced required checks on `dev` remain outstanding;
PR #2 stays draft/unmerged.
