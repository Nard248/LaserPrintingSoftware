"""Execution engine (requirement F7) — the only component that commands hardware.

Approved plans are executed by a single worker thread consuming a FIFO
queue (one rig, strictly one run at a time, fair ordering — thread-lock
fairness is not guaranteed by Python, a queue is). The engine owns
per-device locks, cooperative abort (checked between ops, between array
lines, between line repetitions, and between STL path chunks), and fault
handling: on ANY exception the platform drives devices to safe state in
fixed order (laser off FIRST, then stage, then white light) and marks the
plan failed. The AI never gets closer to hardware than this dispatch table.
"""

from __future__ import annotations

import queue
import threading
import traceback
from pathlib import Path

from .audit import AuditLog
from .config import LabgateConfig
from .errors import TransitionError
from .exposure import ExposureBackend, SimExposure
from .lifecycle import PlanState, PlanStore
from .registry import CapabilityRegistry
from .results import RunResults
from .spec import (
    CaptureImage,
    MoveStage,
    PrintStl,
    SetLaserPower,
    SetWhiteLight,
    Wait,
    WriteArray,
    WriteLine,
    WritePowerSweepArray,
    ZStack,
)


class _AbortRequested(Exception):
    """Internal control-flow signal: cooperative abort observed mid-op."""


class ExecutionEngine:
    def __init__(
        self,
        registry: CapabilityRegistry,
        store: PlanStore,
        audit: AuditLog,
        cfg: LabgateConfig,
        exposure: ExposureBackend | None = None,
        geometry=None,
    ) -> None:
        self._registry = registry
        self._store = store
        self._audit = audit
        self._cfg = cfg
        self._exposure = exposure or SimExposure(
            registry.by_kind("stage"), registry.by_kind("laser"))
        self._geometry = geometry  # GeometryService; required for print_stl
        self._device_locks: dict[str, threading.Lock] = {}
        self._abort_flags: dict[str, threading.Event] = {}
        self._done: dict[str, threading.Event] = {}
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._queued_order: list[str] = []
        self._running_plan: str | None = None
        self._state_lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def _lock_for(self, device_id: str) -> threading.Lock:
        return self._device_locks.setdefault(device_id, threading.Lock())

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(
                target=self._worker_loop, name="labgate-executor", daemon=True)
            self._worker.start()

    # ------------------------------------------------------------------
    def start(self, plan_id: str, actor: str) -> None:
        record = self._store.get(plan_id)
        if record.state != PlanState.APPROVED:
            raise TransitionError(
                f"plan must be approved before execution (is {record.state})")
        # Flag exists before the plan is externally visible as queued, so
        # abort() cannot race the transition.
        self._abort_flags[plan_id] = threading.Event()
        self._done[plan_id] = threading.Event()
        self._store.transition(plan_id, PlanState.QUEUED, actor)
        with self._state_lock:
            self._queued_order.append(plan_id)
        self._ensure_worker()
        self._queue.put(plan_id)
        self._audit.append("plan_queued", actor, {"plan_id": plan_id})

    def abort(self, plan_id: str, actor: str) -> None:
        flag = self._abort_flags.get(plan_id)
        if flag is None:
            raise TransitionError(f"plan {plan_id} is not queued or executing")
        self._audit.append("abort_requested", actor, {"plan_id": plan_id})
        flag.set()

    def wait(self, plan_id: str, timeout_s: float = 60.0) -> None:
        """Block until the plan's run finishes (used by tests and shutdown)."""
        done = self._done.get(plan_id)
        if done is not None:
            done.wait(timeout_s)

    def queue_snapshot(self) -> dict:
        """Current execution queue: the running plan plus FIFO waiters."""
        with self._state_lock:
            return {"running": self._running_plan,
                    "queued": list(self._queued_order)}

    def shutdown(self) -> None:
        """Best-effort: abort queued+running plans, stop the worker, then
        safe-state ALL devices in the fixed laser-first order, then
        disconnect (same order) — the stage must never home under a
        possibly-live beam."""
        for plan_id, flag in list(self._abort_flags.items()):
            flag.set()
        if self._worker is not None and self._worker.is_alive():
            self._queue.put(None)  # sentinel
            self._worker.join(timeout=15.0)
        ordered = self._ordered_adapters()
        for adapter in ordered:
            try:
                adapter.safe_state()
            except Exception:  # noqa: BLE001 — shutdown must not cascade
                pass
        for adapter in ordered:
            try:
                adapter.disconnect()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    def _worker_loop(self) -> None:
        while True:
            plan_id = self._queue.get()
            if plan_id is None:
                return
            with self._state_lock:
                if plan_id in self._queued_order:
                    self._queued_order.remove(plan_id)
                self._running_plan = plan_id
            try:
                abort_flag = self._abort_flags.get(plan_id)
                if abort_flag is not None and abort_flag.is_set():
                    # aborted while still queued — no hardware was touched
                    self._store.transition(plan_id, PlanState.ABORTED, "executor")
                    self._audit.append("run_aborted", "executor",
                                       {"plan_id": plan_id, "while": "queued"})
                    RunResults(Path(self._cfg.storage_dir), plan_id).event(
                        "aborted", {"at": "while queued"})
                else:
                    self._run(plan_id)
            except Exception:  # noqa: BLE001 — the worker must survive anything
                self._audit.append("executor_error", "executor",
                                   {"plan_id": plan_id,
                                    "traceback": traceback.format_exc(limit=8)})
            finally:
                with self._state_lock:
                    self._running_plan = None
                self._abort_flags.pop(plan_id, None)
                done = self._done.get(plan_id)
                if done is not None:
                    done.set()

    # ------------------------------------------------------------------
    def _run(self, plan_id: str) -> None:
        record = self._store.get(plan_id)
        actor = "executor"
        results = RunResults(Path(self._cfg.storage_dir), plan_id)
        abort_flag = self._abort_flags[plan_id]
        self._store.transition(plan_id, PlanState.RUNNING, actor)
        self._audit.append("run_started", actor, {"plan_id": plan_id})
        results.event("run_started", {"title": record.spec.title})
        try:
            for adapter in self._registry.adapters():
                if not adapter.state().connected:
                    adapter.connect()
            # Known-safe baseline BEFORE any motion: a laser left on by a
            # previous session/web-UI user must not survive repositioning.
            self._safe_state_all(results)
            for i, op in enumerate(record.spec.operations):
                if abort_flag.is_set():
                    raise _AbortRequested(f"before op {i}")
                results.event("op_started", {"index": i, "op": op.op})
                self._dispatch(op, results, abort_flag)
                results.event("op_finished", {"index": i, "op": op.op})
            if abort_flag.is_set():  # abort during the final op's tail
                raise _AbortRequested("after final op")
            # normal end: laser off regardless of what the spec said
            self._safe_state_all(results)
            self._store.transition(plan_id, PlanState.COMPLETED, actor)
            results.event("run_completed", {})
            self._audit.append("run_completed", actor, {"plan_id": plan_id})
        except _AbortRequested as abort_point:
            results.event("aborted", {"at": str(abort_point)})
            self._safe_state_all(results)
            self._store.transition(plan_id, PlanState.ABORTED, actor)
            self._audit.append("run_aborted", actor, {"plan_id": plan_id})
        except Exception as exc:  # noqa: BLE001 — any fault -> safe state
            # Client-visible telemetry gets type+message only; the full
            # traceback goes to the server-side audit log.
            results.event("fault", {"error_type": type(exc).__name__,
                                    "error": str(exc)})
            self._safe_state_all(results)
            try:
                self._store.transition(plan_id, PlanState.FAILED, actor)
            except TransitionError:
                pass  # already terminal
            self._audit.append("run_failed", actor, {
                "plan_id": plan_id, "error": str(exc),
                "traceback": traceback.format_exc(limit=8),
            })

    # ------------------------------------------------------------------
    def _ordered_adapters(self):
        """Adapters in the fixed safe-state order: laser, stage, WL, camera."""
        order = ["laser", "stage", "white_light", "camera"]
        return sorted(
            self._registry.adapters(),
            key=lambda a: order.index(a.kind) if a.kind in order else 99,
        )

    def _safe_state_all(self, results: RunResults) -> None:
        """Safe-state order is fixed: laser first, then stage, then WL."""
        for adapter in self._ordered_adapters():
            try:
                adapter.safe_state()
            except Exception as exc:  # noqa: BLE001 — log, keep going down the list
                results.event("safe_state_error",
                              {"device": adapter.device_id, "error": str(exc)})
                self._audit.append("safe_state_error", "executor",
                                   {"device": adapter.device_id, "error": str(exc)})

    # ------------------------------------------------------------------
    def _dispatch(self, op, results: RunResults, abort_flag: threading.Event) -> None:
        stage = self._registry.by_kind("stage")
        laser = self._registry.by_kind("laser")
        camera = self._registry.by_kind("camera")
        wl = self._registry.by_kind("white_light")

        if isinstance(op, SetLaserPower):
            with self._lock_for(laser.device_id):
                laser.set_power(op.attenuator_percent, op.pp_divider)
        elif isinstance(op, MoveStage):
            with self._lock_for(stage.device_id):
                stage.move_absolute(op.target_mm)
        elif isinstance(op, WriteLine):
            self._write_line(stage, laser, op.start_mm, op.end_mm,
                             op.velocity_mm_s, op.repetitions, abort_flag)
        elif isinstance(op, WriteArray):
            for line_index in range(op.line_count):
                if abort_flag.is_set():
                    raise _AbortRequested(f"array line {line_index}")
                y = op.y_start_mm + line_index * op.y_pitch_mm
                self._write_line(
                    stage, laser,
                    (op.x_start_mm, y, op.z_mm), (op.x_end_mm, y, op.z_mm),
                    op.velocity_mm_s, op.repetitions, abort_flag,
                )
        elif isinstance(op, WritePowerSweepArray):
            for line_index, power in enumerate(op.attenuator_percent_per_line):
                if abort_flag.is_set():
                    raise _AbortRequested(f"sweep line {line_index}")
                with self._lock_for(laser.device_id):
                    laser.set_power(power, op.pp_divider)
                y = op.y_start_mm + line_index * op.y_pitch_mm
                self._write_line(
                    stage, laser,
                    (op.x_start_mm, y, op.z_mm), (op.x_end_mm, y, op.z_mm),
                    op.velocity_mm_s, op.repetitions, abort_flag,
                )
        elif isinstance(op, ZStack):
            for power_index, power in enumerate(op.powers()):
                with self._lock_for(laser.device_id):
                    laser.set_power(power, op.pp_divider)
                y = op.y_start_mm + power_index * op.y_pitch_mm
                for level in range(op.z_count):
                    if abort_flag.is_set():
                        raise _AbortRequested(
                            f"z_stack power {power}% level {level}")
                    z = op.z_start_mm + level * op.z_step_mm
                    self._write_line(
                        stage, laser,
                        (op.x_start_mm, y, z), (op.x_end_mm, y, z),
                        op.velocity_mm_s, op.repetitions, abort_flag,
                    )
        elif isinstance(op, PrintStl):
            if self._geometry is None:
                raise TransitionError("platform has no geometry service")
            path = self._geometry.slice_stl(op)  # cache hit — validated earlier
            with self._lock_for(laser.device_id):
                laser.set_power(op.attenuator_percent, op.pp_divider)
            results.event("stl_print_started",
                          {"model_id": op.model_id, "path_points": len(path)})
            with self._lock_for(stage.device_id), self._lock_for(laser.device_id):
                completed = self._exposure.execute_path(
                    path, op.velocity_mm_s, abort_flag)
            if not completed:
                raise _AbortRequested("during STL path")
        elif isinstance(op, SetWhiteLight):
            with self._lock_for(wl.device_id):
                wl.set_on(op.on)
        elif isinstance(op, CaptureImage):
            wl_was_on = bool(wl.state().detail.get("on", False))
            if op.wl_on and not wl_was_on:
                with self._lock_for(wl.device_id):
                    wl.set_on(True)
            with self._lock_for(camera.device_id):
                image = camera.capture(op.label)
            if op.wl_on and not wl_was_on:
                # restore: WL must not silently stay on into a later exposure
                with self._lock_for(wl.device_id):
                    wl.set_on(False)
            results.save_artifact(f"{op.label}.png", image)
            # capture context: what the Specialist agent needs for analysis
            results.event("image_captured", {
                "label": op.label, "bytes": len(image),
                "stage_position_mm": stage.state().detail.get("position_mm"),
                "laser": laser.state().detail,
            })
        elif isinstance(op, Wait):
            if op.seconds > 0 and abort_flag.wait(op.seconds):
                raise _AbortRequested("during wait")
        else:  # pragma: no cover — spec discriminator makes this unreachable
            raise TransitionError(f"unknown operation: {op.op}")

    def _write_line(self, stage, laser, start_mm, end_mm, velocity_mm_s,
                    repetitions, abort_flag: threading.Event) -> None:
        """Expose one line via the configured backend (sim or synchronized).

        The sync problem stays quarantined behind the ExposureBackend seam
        (see labgate/exposure.py and architecture proposal §5). Abort is
        honored between repetitions; a single line traverse is the atomic
        unit of exposure.
        """
        with self._lock_for(stage.device_id), self._lock_for(laser.device_id):
            completed = self._exposure.write_line(
                start_mm, end_mm, velocity_mm_s, repetitions, abort_flag)
        if not completed:
            raise _AbortRequested("between line repetitions")
