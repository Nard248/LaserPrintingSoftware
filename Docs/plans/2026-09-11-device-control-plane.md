# Device Control Plane, Diagnostics & Camera Integration — Plan

**Problem.** The platform exposes exactly one workflow: propose → validate → approve →
execute. Everything the underlying `laser_printing` package can do *outside* that
workflow — enabling stage axes, homing, jogging, tuning motion profiles, reading laser
firmware status, connecting devices at all — is unreachable over the API. There is also
no real camera driver.

**Goal.** Be able to bring the rig up, diagnose it, and drive every device interactively
over the API, without weakening the approval gate that protects laser exposure.

---

## 1. Coverage gap (measured)

| Capability in `laser_printing` | API today |
| --- | --- |
| `StageTcp.enable_axes` / `commutate` / `wait_motor_enabled` | missing |
| `StageController.connect()` / `disconnect()` | missing |
| `jog(axis, mm)` / `move_relative()` | missing |
| `set_velocity` / `set_acceleration` / `set_jerk` | missing |
| `halt()` | missing (only via plan abort) |
| `record_profile()` | missing |
| `position()` / `velocity_feedback()` | partial (thin `/devices`) |
| `LaserController.status()` (errors, warnings, state name) | missing |
| `power_w()` / `frequency_hz()` (measured output) | missing |
| `on()` / `off()` / `set_pp_divider` standalone | missing |
| Camera (any real driver) | missing — stub only |

## 2. Design: a second control plane with its own gate

Plans stay exactly as they are. A parallel **device control plane** is added, governed by
risk tiers rather than by human approval:

| Tier | Contains | Gate |
| --- | --- | --- |
| `read` | status, diagnose, snapshot, position | any platform role |
| `prepare` | connect, disconnect, enable axes, set velocity/accel/jerk, set attenuator, set pp_divider | operator; refuses if a plan is running |
| `motion` | home, jog, move_relative, move_absolute, halt | operator; bounds-checked; **refused while the beam is on**; refused while a plan is running; halt always permitted |
| `expose` | opening the laser shutter | **not reachable here** — plan + approval only (config flag `allow_manual_beam`, default false) |

### Invariants (enforced in code, covered by tests)
1. A device action can never open the shutter while `allow_manual_beam` is false.
2. Motion actions are refused when the laser reports output on.
3. Device actions and plan execution are mutually exclusive — same per-device locks,
   plus a refusal when the engine has a running/queued plan.
4. Every non-`read` action is audit-logged with the acting identity.
5. Parameters are validated against the same declared bounds the planner sees.

## 3. Diagnostics & status

- `GET /system/status` — mode, version, uptime, config summary, per-device rollup, queue depth, storage.
- `GET /system/preflight` — **readiness checklist**. Each failing check carries a
  `remedy`, which is either an API call or a *physical* instruction ("turn the laser key
  switch"). This is what answers "what do I still need to turn on by hand?".
- `POST /devices/{id}/diagnose` — per-device self-test (reachability, SDK import, axis
  enable state, firmware errors) — read-only, never actuates.
- `POST /system/estop` — force every device to safe state immediately, laser first.

## 4. Camera (MV-CS200-10GC, Hikrobot MVS SDK)

Lifecycle from the vendor notebook:
`EnumDevices → CreateHandle → OpenDevice → Set*Value → StartGrabbing → GetImageBuffer →
FreeImageBuffer → StopGrabbing → CloseDevice → DestroyHandle`.

- New `devices/camera_mvs.py`; `MvCameraControl_class` imported **lazily** inside methods
  (same pattern as the ACS wheel) so the platform still runs on machines without MVS.
- SDK path resolved from config or `MVS_PATH`/default Windows install location.
- Settings surfaced as `prepare` actions: `exposure_time_us`, `gain`, `auto_exposure`,
  `pixel_format`; bounds read from the camera itself via `MV_CC_GetFloatValue` min/max.
- `snapshot` is a `read` action (non-mutating) returning a PNG artifact.
- `capture_image` plan op gains optional `exposure_time_us` / `gain` overrides.

## 5. Implementation order

1. **A** — action framework (`actions.py`), adapter contract extension, sim adapters.
2. **B** — device control service with the gate (`devicectl.py`), API endpoints.
3. **C** — diagnostics + preflight + e-stop.
4. **D** — rig adapter actions (stage/laser real).
5. **E** — camera MVS adapter.
6. **F** — tests, OpenAPI/Postman regeneration, docs.

## 6. Open decisions (for Mushegh)

- **D1** Should standalone beam-on ever be allowed for alignment? Default: **no**
  (`allow_manual_beam: false`). If yes, it should still require the `approver` role.
- **D2** Should jogging require approval? Default: **no** — it is bounded motion with no
  beam, and requiring a second person would make alignment impractical.
- **D3** Who owns the hard-bounds table that device actions are validated against?
  (Same question as Q-S1 in the requirements doc.)
