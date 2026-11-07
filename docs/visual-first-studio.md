# Visual-first OmniFlow Studio

This is an **active implementation plan**, tracked by issue #1. The GitHub repository is still being populated from the offline OmniFlow v0.1.0 package; this branch is **not installable** until the full code and test assets have been committed and verified.

## Primary user flow

1. Choose emulator and Android game; live preview and saved screenshots are equivalent editors.
2. Name the state in plain language (e.g., "When claim button is visible").
3. Draw one or more detection zones on the screenshot; detection can combine image, OCR substring/regex, pixel, median pixel color, negative conditions, and explicit Wait states.
4. Choose the desired action from a readable list; tap, double-tap, hold and swipe targets are clicked or dragged directly on the screenshot. No mandatory coordinate input.
5. Choose optional pre-action delay, verification, bounded retry, and Wait-state precedence.
6. Save once. Preview and dry-run the state without touching the game.
7. Reuse the same library from any recorded state across devices and schedules.

## Storage and compatibility

Store normalized screen coordinates as machine data (540x960 reference); rescale automatically through the actual MuMu device size. Older SwordXAuto region/tap/drag manifests remain readable without destructive migration. Condition-only states are allowed without a dummy image template, but must contain at least one real condition. Action states must have at least one action.

## Safety and performance constraints

- Never click on an unknown or passive/Wait state.
- Do not use fixed elapsed time to infer that a Match/Cancel toggle is safe.
- Require visually confirmed state transitions and limit retries.
- Limit CPU contention with one bounded worker per emulator (10 target); video decoding for the currently selected emulator only.
- Scheduler must atomically claim occurrences and lease emulator devices; no dual control.
- CI must verify the **full** application and all inherited tests; deliberately fail if the complete baseline is missing.
- Windows MuMu UI/capture and ten-instance benchmark remain outstanding, not presumed green.
