---
name: Stage home position
description: The stage home / safe parking position for this setup is [0, 0, 0] mm on X/Y/Z.
type: project
---

Home position for the 3-axis SPiiPlus stage is **[0.0, 0.0, 0.0] mm**.

**Why:** User set this as the canonical start state and confirmed it should be returned to before every experiment starts and before the connection is closed — so any leftover offset from a previous run is removed and we always begin from the same reference.

**How to apply:**
- Before any experiment run, `move_to([0, 0, 0])` first.
- On shutdown / disconnect, ensure the stage is back at `[0, 0, 0]` before `CloseComm`.
- Do NOT treat "position at connect time" as home — always use literal `[0, 0, 0]`.
- The physical travel range is ±25 mm per axis, so `[0, 0, 0]` is mid-range and safe.
