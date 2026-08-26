# Milestone 1 — "labgate" API Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the L0–L3 API platform (`labgate`) that fronts the 2PP rig's four devices (stage, laser, camera, white-light) behind one validated, auditable, UI-agnostic REST API, fully runnable in simulation mode on any machine.

**Architecture:** A new `src/labgate/` package implements the deterministic side of the approved architecture: device adapters declaring bounded capabilities (L2), a capability registry + validation engine + plan lifecycle + execution engine + audit log (L3), and a FastAPI surface generating OpenAPI (the integration contract). The existing `laser_printing` controllers become the *real* backends behind adapter interfaces; simulated backends make everything testable off the rig. The AI/chat side (chat.photonics.ai, Milestone 2) is a pure client of this API and appears here only as an integration guide.

**Tech Stack:** Python 3.12 (rig) / 3.13 (dev), FastAPI + pydantic v2, pytest, existing `laser_printing` package (StageController, LaserController, PrintSynchronizer), `SPiiPlusPython` (rig only, lazy-imported).

## Global Constraints

- `requires-python` becomes `>=3.12,<3.14` (3.12 mandatory on the rig for the ACS cp312 wheel; 3.13 allowed for simulation-mode dev). Comment in pyproject must say why.
- The AI/client never bypasses the API; nothing in `labgate.api` or above imports `SPiiPlusPython` or `laser_printing.hardware` eagerly.
- All quantities carry explicit units in field names (`_mm`, `_mm_s`, `_percent`, `_um`, `_s`).
- Stage travel hard bounds ±25.0 mm/axis; velocity bound 0.1–10.0 mm/s (config); attenuator 0–100 %; pp_divider ≥ 1 (config `config/default.yaml` stays the single source; labgate reads it).
- Proposer ≠ approver enforced server-side; approval requires role `approver`.
- Safe-state order on any fault: laser off FIRST, then stage stop, then WL off.
- Every state-changing API call lands in the append-only audit log (JSONL).
- Simulation mode must run the entire test suite with zero hardware and zero vendor imports.
- No module-level side effects (no root-logger mutation, no device I/O at import).

## File Structure

```
src/labgate/
  __init__.py            # version only
  config.py              # LabgateConfig (pydantic-settings style, reads YAML + env)
  ids.py                 # id generation (plan ids, run ids)
  errors.py              # LabgateError hierarchy
  auth.py                # TokenStore, Identity, Role, require_role
  audit.py               # AuditLog (append-only JSONL)
  spec.py                # ExperimentSpec + operation models (pydantic, versioned)
  registry.py            # Capability, ParamSpec, CapabilityRegistry
  validation.py          # ValidationEngine -> ValidationReport
  lifecycle.py           # PlanRecord, PlanState, PlanStore (state machine)
  dryrun.py              # DryRunEstimator -> DryRunReport
  executor.py            # ExecutionEngine (thread-based), safe_state()
  results.py             # RunResults store (telemetry JSONL + artifacts dir)
  devices/
    __init__.py
    base.py              # DeviceAdapter ABC + DeviceState
    sim.py               # SimStage, SimLaser, SimCamera, SimWhiteLight
    rig.py               # RigStage, RigLaser (lazy imports of laser_printing) + stubs CameraStub, WhiteLightStub
  api/
    __init__.py
    app.py               # create_app() FastAPI factory + routes
    schemas.py           # request/response models
    deps.py              # auth dependency, platform singleton wiring
tests/labgate/
  conftest.py            # platform fixture in sim mode, tokens
  test_spec.py test_registry.py test_validation.py test_lifecycle.py
  test_auth.py test_dryrun.py test_executor.py test_api.py
Docs/integration/chat-platform-integration.md   # M2 contract for Aram
```

Design rules locked here: `labgate` core never imports `laser_printing` except inside `devices/rig.py` function bodies; adapters expose capabilities as *data*; the executor is the only caller of adapter action methods; the API layer holds no business logic.

---

### Task 1: Package scaffold, errors, ids, config
**Files:** Create `src/labgate/{__init__,errors,ids,config}.py`, `tests/labgate/conftest.py` (skeleton)
**Produces:** `LabgateError`, `ValidationFailed`, `TransitionError`, `AuthError`, `DeviceError`; `new_id(prefix) -> str`; `LabgateConfig.load(path|None)` exposing `mode: "sim"|"rig"`, `bounds` (stage range/velocity, laser attenuator/pp_divider), `storage_dir`.
Steps: failing test for config defaults + id uniqueness → implement → pass → commit.

### Task 2: Experiment Specification models
**Files:** Create `src/labgate/spec.py`, `tests/labgate/test_spec.py`
**Produces:** pydantic models: `ExperimentSpec{spec_version:'1.0', title, operations:list[Operation], metadata}` where `Operation` is a discriminated union on `op`: `set_laser_power{attenuator_percent, pp_divider}`, `write_line{start_mm:[x,y,z], end_mm, velocity_mm_s, repetitions}`, `write_array{x_start_mm,x_end_mm,y_start_mm,y_pitch_mm,line_count,velocity_mm_s,repetitions}`, `move_stage{target_mm}`, `capture_image{label, wl_on:bool}`, `set_white_light{on:bool}`, `wait{seconds}`. Round-trips JSON. Unknown `op` rejected.
Steps: failing tests (parse/reject/round-trip) → implement → pass → commit.

### Task 3: Device adapter interface + capability registry
**Files:** Create `src/labgate/devices/base.py`, `src/labgate/registry.py`, tests
**Produces:** `ParamSpec{name,type,unit,min,max,description}`, `Capability{device,name,params:list[ParamSpec],description,mutates:bool}`, `DeviceAdapter` ABC: `device_id`, `kind` ('stage'|'laser'|'camera'|'white_light'), `capabilities() -> list[Capability]`, `state() -> DeviceState`, `connect()/disconnect()`, `safe_state()`, plus per-kind action methods documented per adapter. `CapabilityRegistry.register(adapter)`, `.snapshot() -> dict` (JSON-able, feeds `GET /capabilities`).
Steps: failing tests (registration, snapshot shape, duplicate id rejected) → implement → pass → commit.

### Task 4: Simulated adapters for all four devices
**Files:** Create `src/labgate/devices/sim.py`, `tests/labgate/test_sim_devices.py`
**Produces:** `SimStage` (position tracking, travel-range enforcement, move timing = distance/velocity), `SimLaser` (on/off/attenuator/pp_divider state, refuses attenuator outside 0–100), `SimCamera.capture(label) -> bytes` (tiny synthetic PNG via numpy), `SimWhiteLight` (on/off). Each declares capabilities with bounds from config. All instant-connect, no I/O.
Steps: failing tests per device → implement → pass → commit.

### Task 5: Auth, roles, audit log
**Files:** Create `src/labgate/auth.py`, `src/labgate/audit.py`, tests
**Produces:** `Role` enum (`operator`,`approver`,`admin`); `Identity{user_id, display_name, roles}`; `TokenStore.load(cfg)/issue(identity)->token/resolve(token)->Identity` (tokens from config file for now; pluggable later — chat platform identities map to tokens); `AuditLog.append(event_type, actor, payload)` writing JSONL with ISO timestamps; `AuditLog.read_all()` for tests.
Steps: failing tests (resolve, unknown token → AuthError, audit round-trip) → implement → pass → commit.

### Task 6: Validation engine
**Files:** Create `src/labgate/validation.py`, `tests/labgate/test_validation.py`
**Produces:** `ValidationEngine(registry, cfg).validate(spec) -> ValidationReport{ok, checks:list[CheckResult{check,ok,detail}]}`. Checks: (V1) every op maps to a registered capability; (V2) numeric bounds via ParamSpec; (V3) geometry inside travel range incl. array extent `y_start + (line_count-1)*y_pitch`; (V4) mutual-constraint rules table — v1 rules: `capture_image(wl_on=True)` requires a `set_white_light(on)` or wl_on flag (auto-satisfied), laser must be set before any `write_*` op, laser off implied at spec end (executor enforces); (V5) line length vs. 2× accel distance warning (from sync calibration: 0.79 mm at 5 mm/s) — warning not failure.
Steps: failing tests (each check, pass+fail cases) → implement → pass → commit.

### Task 7: Plan lifecycle + store
**Files:** Create `src/labgate/lifecycle.py`, `tests/labgate/test_lifecycle.py`
**Produces:** `PlanState` enum: `draft, validated, approved, queued, running, completed, failed, aborted, rejected`; `PlanRecord{plan_id, spec, proposer, approver|None, state, validation_report|None, created_at, history:list}`; `PlanStore` (in-memory dict + JSON persistence under storage_dir): `create(spec, proposer)`, `set_validated(id, report)`, `approve(id, approver)` (raises `TransitionError` on wrong state; raises `AuthError` if approver.user_id == proposer.user_id), `transition(id, to_state)` guarded by allowed-transitions map.
Steps: failing tests (happy path, illegal transition, self-approval rejected) → implement → pass → commit.

### Task 8: Dry-run estimator
**Files:** Create `src/labgate/dryrun.py`, `tests/labgate/test_dryrun.py`
**Produces:** `DryRunEstimator(cfg).estimate(spec) -> DryRunReport{total_duration_s, motion_distance_mm, exposure_events, bounding_box_mm{min,max}, per_op:list}` computed from op parameters (velocity, distances, laser settle 0.2 s per toggle, capture 0.5 s nominal).
Steps: failing tests (known spec → known numbers) → implement → pass → commit.

### Task 9: Execution engine + results
**Files:** Create `src/labgate/executor.py`, `src/labgate/results.py`, `tests/labgate/test_executor.py`
**Produces:** `RunResults(storage_dir, plan_id)`: `event(type, payload)` → telemetry JSONL; `save_artifact(name, bytes)` → artifacts dir; `manifest()`. `ExecutionEngine(registry, store, audit, results_factory)`: `start(plan_id)` (requires state `approved`; sets `queued→running`; runs ops in a worker thread; per-device `threading.Lock`; op dispatch table; on exception → `safe_state_all()` (laser→stage→WL order) then state `failed`; on success laser off + state `completed`), `abort(plan_id)` cooperative flag checked between ops → `aborted` + safe state. Camera captures saved as artifacts.
Steps: failing tests (sim run completes with events+artifact; fault injection → failed + laser off; abort mid-run) → implement → pass → commit.

### Task 10: FastAPI surface
**Files:** Create `src/labgate/api/{app,schemas,deps}.py`, `tests/labgate/test_api.py`
**Produces:** `create_app(cfg|None) -> FastAPI`. Routes (all JSON, bearer token): `GET /health` (open), `GET /capabilities`, `GET /devices`, `POST /plans` {spec} → plan (auto-validates → `validated` or `rejected` with report), `GET /plans`, `GET /plans/{id}`, `POST /plans/{id}/dry-run`, `POST /plans/{id}/approve` (role approver, ≠ proposer), `POST /plans/{id}/execute`, `POST /plans/{id}/abort`, `GET /plans/{id}/results`, `GET /plans/{id}/results/artifacts/{name}`. OpenAPI title/description document the lifecycle. 401/403/404/409 semantics tested via `TestClient`.
Steps: failing endpoint tests (auth, full happy path sim run through HTTP, self-approval 403) → implement → pass → commit.

### Task 11: Rig adapters + lazy-import fix in laser_printing
**Files:** Create `src/labgate/devices/rig.py`; Modify `src/laser_printing/controllers/__init__.py`, `src/laser_printing/hardware/__init__.py` (lazy attribute imports so laser code imports without SPiiPlusPython); `tests/labgate/test_rig_adapters.py` (import + laser-adapter-against-mock tests only)
**Produces:** `RigStage` wrapping `StageController.from_config` (import inside `connect()`), `RigLaser` wrapping `LaserController.from_config` (mockable transport), `CameraStub`/`WhiteLightStub` raising `DeviceError('pending SDK — see Q-H1/Q-H2')` with declared capabilities. `build_adapters(cfg)` factory selects sim/rig per device from config.
Steps: failing tests (module imports on macOS, laser adapter drives mocked LaserHttp, stubs refuse connect) → implement → pass → commit.

### Task 12: Packaging, entry point, integration guide
**Files:** Modify `pyproject.toml` (deps + `labgate-serve` script + widened python pin), Create `Docs/integration/chat-platform-integration.md`, update `config/default.yaml` with `labgate:` section (mode, storage_dir, tokens file), `config/tokens.example.yaml`.
**Produces:** `labgate-serve` console entry (`uvicorn labgate.api.app:create_app` factory); integration guide for Aram: auth, lifecycle walkthrough with curl examples, the two-agent mapping table, artifact download, OpenAPI location, error semantics.
Steps: install -e check → run server smoke (curl /health) → full pytest → commit.

## Self-Review
- Spec coverage: F1–F12 → Tasks 3–11 (F1/F2: T3–T4/T11; F3: T2; F4: T7/T10; F5: T6; F6: T8; F7: T9; F8: T9/T10; F9: T5; F10: T5/T7/T10; F11: T4/conftest; F12: T10). N1: T10/T12; N2: registry snapshot is MCP-shaped (T3); N3: adapter ABC (T3); N4: T12; N5: global constraint; N7: T2 field naming. Sync encapsulation: RigStage/executor reuse PrintSynchronizer in a later hardware-bringup task on the rig itself — out of local scope, flagged in guide.
- Placeholder scan: camera/WL real SDKs intentionally stubbed pending Q-H1/Q-H2 (a requirement, not a placeholder).
- Type consistency: names above are the canonical ones; tests import from `labgate` package root re-exports where convenient.