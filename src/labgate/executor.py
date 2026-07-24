"""Execution engine (requirement F7) — the only component that commands hardware.

Expands an approved plan's operations into adapter calls in a worker
thread. Owns per-device locks, cooperative abort (checked between ops,
between array lines, and between line repetitions — never longer than one
line traverse away), and fault handling: on ANY exception the platform
drives devices to safe state in fixed order (laser off FIRST, then stage,
then white light) and marks the plan failed. The AI never gets closer to
hardware than this file's dispatch table.
"""

from __future__ import annotations

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
    SetLaserPower,
    SetWhiteLight,
    Wait,
    WriteArray,
    WriteLine,
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
    ) -> None:
        self._registry = registry
        self._store = store
        self._audit = audit
        self._cfg = cfg
        self._exposure = exposure or SimExposure(
            registry.by_kind("stage"), registry.by_kind("laser"))
        self._device_locks: dict[str, threading.Lock] = {}
        self._abort_flags: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._run_lock = threading.Lock()  # one run at a time on one rig

    def _lock_for(self, device_id: str) -> threading.Lock:
        return self._device_locks.setdefault(device_id, threading.Lock())

    # ------------------------------------------------------------------
    def start(self, plan_id: str, actor: str) -> None:
        record = self._store.get(plan_id)
        if record.state != PlanState.APPROVED:
            raise TransitionError(
                f"plan must be approved before execution (is {record.state})")
        # Flag exists before the plan is externally visible as queued, so
        # abort() cannot race the transition.
        self._abort_flags[plan_id] = threading.Event()
        self._store.transition(plan_id, PlanState.QUEUED, actor)
        thread = threading.Thread(
            target=self._run, args=(plan_id, actor), name=f"labgate-run-{plan_id}",
            daemon=True,
        )
        self._threads[plan_id] = thread
        thread.start()

    def abort(self, plan_id: str, actor: str) -> None:
        flag = self._abort_flags.get(plan_id)
        if flag is None:
            raise TransitionError(f"plan {plan_id} is not executing")
        self._audit.append("abort_requested", actor, {"plan_id": plan_id})
        flag.set()

    def wait(self, plan_id: str, timeout_s: float = 60.0) -> None:
        """Block until the run thread finishes (used by tests and shutdown)."""
        thread = self._threads.get(plan_id)
        if thread is not None:
            thread.join(timeout_s)

    def shutdown(self) -> None:
        """Best-effort: abort any live run and disconnect all devices."""
        for plan_id, flag in list(self._abort_flags.items()):
            flag.set()
            self.wait(plan_id, timeout_s=10.0)
        for adapter in self._registry.adapters():
            try:
                adapter.safe_state()
                adapter.disconnect()
            except Exception:  # noqa: BLE001 — shutdown must not cascade
                pass

    # ------------------------------------------------------------------
    def _run(self, plan_id: str, actor: str) -> None:
        record = self._store.get(plan_id)
        results = RunResults(Path(self._cfg.storage_dir), plan_id)
        abort_flag = self._abort_flags[plan_id]
        with self._run_lock:
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
            finally:
                self._abort_flags.pop(plan_id, None)
                self._threads.pop(plan_id, None)

    # ------------------------------------------------------------------
    def _safe_state_all(self, results: RunResults) -> None:
        """Safe-state order is fixed: laser first, then stage, then WL."""
        order = ["laser", "stage", "white_light", "camera"]
        adapters = sorted(
            self._registry.adapters(),
            key=lambda a: order.index(a.kind) if a.kind in order else 99,
        )
        for adapter in adapters:
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
            results.event("image_captured", {"label": op.label, "bytes": len(image)})
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
