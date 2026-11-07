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
2. Start with the 120-second one-MuMu benchmark and label 10× estimates as extrapolations. Real ten-instance acceptance still requires ten distinct MuMu devices running concurrently for at least 120 seconds, including representative workflow OCR/image recognition and per-device FPS/latency/errors.
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
| 6. Capture performance & frame arbitration | Profile ADB screenshots and reject silent capture stalls or changing source geometry; reuse selected-session preview frames only within an acquisition-time freshness window; reject stale input | Production PNG and separately labeled diagnostic raw benchmarks measure >=0.8 screenshot-equivalent FPS per instance for >=120s and <=6s gaps; raw alone never satisfies acceptance; verify recovery and <=6s input freshness, no extra encoders | **Partial: PNG/raw geometry and selected-device alias-safe H.264 reuse implemented; real 0.719 FPS still fails** |
| 7. Complete input & text primitives | Screenshot-selected field tap + ASCII text, later verified focus/readback, Unicode/IME path, copy/paste where permitted, multitouch and scroll | Safe ASCII validated before tap; ownership checked before tap and typing; real focus and readback, Unicode and gestures still outstanding | **Partial: visual field input implemented, unverified on hardware** |
| 8. Universal state graph | Reusable condition AND/OR/NOT groups, timers, priorities, branch/loop/subworkflow, state variables, timeout/recovery, dead-end handling | Unit/fuzz tests and dry-run replay for every transition; unknown/passive states never tap; bounded retries and lease cancellation verified | **Partial** |
| 9. Recording & inspection | Record full multi-step runs; visual condition editing; state auto-suggestions; live trace/timeline, screenshot diff and side-by-side debugger | Build/edit/debug a workflow with no manual X/Y; replay saved images from unknown starting state; exported traces contain exact actions and matched evidence | **Partial** |
| 10. Device & session resilience | Per-device health/capabilities, process relaunch, reconnect, orientation, startup, cleanup, concurrency leases, telemetry | Recovery tests for emulator/game crashes; no double input; sustained ten-device test separately from one-device projections | **Partial; hardware incomplete** |
| 11. Workflow portability | Versioned schema, non-destructive migration, import/export, screen resolution/aspect scaling, template/rule versioning | Import a legacy workflow plus new workflows; compare outputs across two resolutions and orientations with zero data loss | **Partial** |
| 12. Production readiness | Install/update packaging, searchable docs, permissions, audit logs, security/privacy constraints and release governance | New Windows PC installs and passes diagnostics; complete hardware acceptance checklist; exact-head CI required/enforced on protected `dev`; PR merged only after gates | **Blocked by hardware and branch protection** |

### Near-term priority queue

1. **P0 OCR readiness:** fix standard installer/backend and verify UI OCR autofill.
2. **P0 screenshot bottleneck:** run the isolated stationary-screen `tools/collect_hardware_evidence.py --capture-comparison --serial <MuMu>` workflow to collect 120s PNG/raw capture profiles, compare image fidelity and assess performance on the same emulator. The normal collector now pins its video and benchmark to a requested serial. Optimize the measured ADB bottleneck; consider video-frame reuse only with freshness checks and without extra encoders.
3. **P1 text-action completeness:** verified field focus, keyboard/IME adapter and safe handling of unsupported characters (never silently mangle text).
4. **P1 operator visibility:** dependency checks in GUI, per-action dry-run traces, target screenshot snapshots, failure reason and retry status.
5. **P2 composition:** reusable workflows/subflows, variable extraction from OCR, conditional branching and loops with stop policies.
6. **P2 packaging & scalability:** concurrency benchmarks and acceptance on real Windows hardware; enforce branch protection before merging.

Every milestone requires its own documented tests, evidence and acceptance
criteria. Prioritize completing the current control issue before spinning up
new, duplicate active branches or claiming a production release.
