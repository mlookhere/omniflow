# Workflow Chains

## Create a chain

1. In **Dashboard**, choose **Edit priorities / modes** next to **Workflow chain**.
2. Enter a chain name. Choose a saved workflow and select a **Start trigger image/state** and a distinct **Completed image/state** from that workflow. The states are created in Studio with the existing screenshot/image/OCR tools.
3. Choose **Dynamic**, **Timed (minutes)** or **Run count (full completions)**, then click **Add workflow**.
4. Add additional workflows. The top row has highest priority; **Move up / Move down** changes the order. Optionally turn off **Repeat the full chain** to finish after each configured stage has completed once.
5. Click **Save chain**. Choose the saved chain on the Dashboard, select MuMu and the Android package, and click **Start chain**. **Pause / Resume** and **Stop** still apply to the entire device session.

Chain configuration is stored atomically in `%LOCALAPPDATA%\\OmniFlow\\chains.json` on Windows, separate from individual workflow manifests. Editing chains does not modify or overwrite recorded workflow images/actions. The existing standalone **Start** and schedule controls remain unchanged.

## What each mode does

| Mode | When it starts | When it advances |
| --- | --- | --- |
| **Dynamic** | When its saved start trigger becomes visible. Inside the workflow, recognition can continue from any saved state. | Once the completed screen is confirmed, prefer the next ready workflow. If no other trigger is present, the same Dynamic workflow may start another full run when its trigger reappears. |
| **Timed** | Clock begins when the first trigger is selected, not while waiting for it to appear. | Repeat until the time budget expires. **Never stop in the middle of a run**: wait until the completed screen is confirmed, even if the timer has passed. Manual Pause freezes the budget. |
| **Run count** | When its trigger is visible. | Count **confirmed completed screens**, not individual taps/steps. Repeat until N full runs finish, then prefer the next workflow. |

A counted or timed workflow retains first claim on the device until its quota is satisfied. If its start trigger is unavailable between runs, another eligible Dynamic workflow may run and finish, after which the awaiting workflow is checked again. A completed timed/count stage cannot exceed its configured quota just because another workflow is not ready.

After a Dynamic completion, the next rows are considered in priority-list order, wrapping to earlier Dynamic rows as a fallback; only a **visible trigger** authorizes a new workflow. No workflows execute concurrently on one MuMu. The active workflow remains the exclusive owner of the emulator; a higher-priority trigger never interrupts a run already in progress.

A chain cycle is complete once every Dynamic row has finished at least once and every Timed/Run-count row has reached its limit. When **Repeat the full chain** is enabled, quota tracking resets and the chain watches triggers again. Otherwise the session stops automatically. Until a trigger appears, the worker remains idle without sending input.

## Completion semantics and safeguards

The completed screen is a **saved state** already defined in Studio. It may use a small moving image searched over a larger region, OCR text, or ordinary image recognition. It is checked on two consecutive fresh screenshots by the existing runner. When confirmed, that state is **observational**: its saved tap/gesture is intentionally not executed in chain mode. A static completion screen cannot count twice.

Legacy `end` and `count_run` controls still work for independent workflows; in chain mode, **only the explicitly selected completed state** advances the stage. A disappeared/unrecognized completion never counts as success. The chain will wait for the completed image even if the time budget passes. Stop or recover the game manually if a stage cannot finish.

Unknown screens, passive WAIT rules, image thresholds, device ownership leases, pause, screenshot age limits, and two-frame action confirmation are preserved. The idle chain scanner uses **one screenshot** to evaluate only the configured trigger state in each eligible workflow; it never sends taps. Starting a workflow then requires the runner's own fresh confirmation frames. This does not increase screenshot capture FPS.

## Test checklist

- Create two workflows A/B with distinct trigger and completed images. Start Dynamic A, confirm its completed screen, then show B's trigger: B should start.
- Keep B unavailable; show A's trigger again. A should run fully before B, without counting its prior completed screen twice.
- Set A to **Timed: 1 minute**. Start its final run near the time limit. It must finish on the completed image even after the minute expires, then hand off.
- Set A to **Run count: 3**. Show its completed image three separate times with new triggers; verify exactly 3 runs and then handoff.
- Pause halfway through a Timed run; the budget should not elapse while paused, and the action state must reconfirm on resume.
- Introduce an unknown screen, a missing completed image, and a false-match candidate; no input or completion should be inferred from them.
- Reopen the saved chain and verify priorities, modes, limits, and trigger IDs; run a legacy single workflow to confirm unchanged behavior.

**Limits:** The existing ZIP Export/Import feature currently backs up individual workflows and profiles, **not** the new `chains.json`; keep a copy of that file when moving installations. Real MuMu end-to-end chain testing and latency measurements are separate from automated CI and are not yet certified.
