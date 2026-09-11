"""The gate in front of the device control plane.

Plans get validated + approved. Device actions get *this* instead: a small,
deterministic set of interlocks applied before anything reaches hardware.

Order of checks (all must pass, cheapest and most important first):

  1. the action exists and is declared by that device
  2. its tier is invocable at all
  3. the caller holds the role that tier needs (`expose` = admin)
  4. the device is connected, if the action needs it
  5. no plan is running or queued               (exclusivity with the executor)
  6. the beam is off, for motion actions        (motion safety interlock)
  7. parameters are in their declared bounds
  8. the per-device lock is acquired            (no interleaving with a run)

Only then is the adapter method called, and the outcome audited.
"""

from __future__ import annotations

import threading
from typing import Any

from .actions import (
    INVOCABLE_TIERS,
    ActionResult,
    ActionSpec,
    ActionTier,
    bind,
    coerce_and_validate,
)
from .audit import AuditLog
from .auth import Identity, Role, require_role
from .errors import AuthError, DeviceError, TransitionError, ValidationFailed
from .registry import CapabilityRegistry

#: Minimum role per tier. `expose` — opening the shutter outside an approved
#: plan — is admin-only: the rig owner can bring the instrument up and test it
#: physically, while an ordinary operator still cannot fire the laser without
#: a second person signing for a plan.
TIER_ROLE: dict[ActionTier, Role | None] = {
    ActionTier.READ: None,              # any authenticated platform role
    ActionTier.PREPARE: Role.OPERATOR,
    ActionTier.MOTION: Role.OPERATOR,
    ActionTier.EXPOSE: Role.ADMIN,
}


class DeviceControl:
    """Validates, interlocks, executes and audits one device action."""

    def __init__(self, registry: CapabilityRegistry, audit: AuditLog,
                 engine, allow_manual_beam: bool = False) -> None:
        self._registry = registry
        self._audit = audit
        self._engine = engine
        self._allow_manual_beam = allow_manual_beam
        self._fallback_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------
    def describe(self, device_id: str) -> list[ActionSpec]:
        """Actions this device offers that are actually invocable."""
        adapter = self._registry.adapter(device_id)
        return [a for a in adapter.actions() if a.tier in INVOCABLE_TIERS]

    def describe_all(self) -> dict[str, list[ActionSpec]]:
        return {a.device_id: self.describe(a.device_id)
                for a in self._registry.adapters()}

    # ------------------------------------------------------------------
    def _lock_for(self, device_id: str) -> threading.Lock:
        """Share the executor's per-device lock so a device action can never
        interleave with a running plan's use of the same instrument."""
        getter = getattr(self._engine, "_lock_for", None)
        if callable(getter):
            return getter(device_id)
        return self._fallback_locks.setdefault(device_id, threading.Lock())

    def _run_is_active(self) -> bool:
        snapshot = self._engine.queue_snapshot()
        return bool(snapshot.get("running") or snapshot.get("queued"))

    def _beam_is_on(self) -> bool:
        """True only when a laser positively reports output on.

        An unreachable or unknown laser is treated as *possibly on* — the
        conservative reading — so motion is refused rather than allowed.
        """
        try:
            laser = self._registry.by_kind("laser")
        except Exception:  # noqa: BLE001 — no laser registered at all
            return False
        try:
            detail = laser.state().detail
        except Exception:  # noqa: BLE001 — cannot read it, assume the worst
            return True
        value = detail.get("output_on")
        return True if value is None else bool(value)

    # ------------------------------------------------------------------
    def guard_lifecycle(self, device_id: str, identity: Identity,
                        operation: str) -> bool:
        """Gate connect/disconnect, which are motion commands in disguise.

        StageController.connect() enables the axes and drives an unchecked
        move to [0, 0, 0]; disconnect() homes before closing the link. Those
        are full-travel traverses, so they need the same beam interlock the
        declared motion actions carry — otherwise bringing a device up with
        the shutter open would scribe a line across the sample.

        Returns True when an admin overrode the beam interlock.
        """
        if self._run_is_active():
            raise TransitionError(
                f"a plan is running or queued; '{operation}' is refused while the "
                "execution engine owns the rig (abort it first, or wait)")
        adapter = self._registry.adapter(device_id)
        if adapter.kind != "stage" or not self._beam_is_on():
            return False
        if not identity.has_role(Role.ADMIN):
            raise DeviceError(
                f"laser output is on (or its state is unknown); '{operation}' moves "
                f"the stage and is refused until the beam is confirmed off")
        self._audit.append("interlock_override", identity.user_id, {
            "device_id": device_id, "action": operation,
            "interlock": "blocked_when_beam_on",
            "reason": "admin running device lifecycle with the beam live"})
        return True

    # ------------------------------------------------------------------
    def invoke(self, device_id: str, action: str, params: dict[str, Any],
               identity: Identity) -> ActionResult:
        adapter = self._registry.adapter(device_id)

        spec = next((a for a in adapter.actions() if a.name == action), None)
        if spec is None:
            available = sorted(a.name for a in self.describe(device_id))
            raise ValidationFailed(
                f"device '{device_id}' has no action '{action}'; "
                f"available: {available}")

        # 2. tier must be invocable at all
        if spec.tier not in INVOCABLE_TIERS:
            raise AuthError(
                f"action '{action}' is tier '{spec.tier}' and is not available "
                "through device control; it must go through the plan approval path")

        # 3. role. `expose` needs admin — unless the lab has explicitly opened
        # manual beam control to operators via labgate.allow_manual_beam.
        needed = TIER_ROLE.get(spec.tier)
        if spec.tier is ActionTier.EXPOSE and self._allow_manual_beam:
            needed = Role.OPERATOR
        if needed is not None:
            try:
                require_role(identity, needed)
            except AuthError:
                if spec.tier is ActionTier.EXPOSE:
                    raise AuthError(
                        f"'{action}' opens the shutter outside an approved plan and "
                        f"requires the '{Role.ADMIN}' role; user "
                        f"'{identity.user_id}' has {sorted(identity.roles)}. Submit a "
                        "plan for approval instead, or set labgate.allow_manual_beam "
                        "to let operators do this.") from None
                raise
        elif not (identity.has_role(Role.OPERATOR) or identity.has_role(Role.APPROVER)):
            raise AuthError(
                f"user '{identity.user_id}' has no role granting device access")

        # 4. connected
        if spec.requires_connected and not adapter.state().connected:
            raise DeviceError(
                f"device '{device_id}' is not connected — "
                f"POST /devices/{device_id}/connect first")

        # 5/6. interlocks (stop paths bypass both, by design)
        overrode_beam_interlock = False
        if not spec.always_allowed:
            # Exclusivity with the executor is a correctness invariant, not a
            # policy: interleaving with a running plan can corrupt an exposure.
            # Nobody overrides it — admin included. `halt` and /system/estop
            # remain available to stop a run.
            if spec.blocked_during_run and self._run_is_active():
                raise TransitionError(
                    f"a plan is running or queued; '{action}' is refused while the "
                    "execution engine owns the rig (abort it first, or wait)")
            if spec.blocked_when_beam_on and self._beam_is_on():
                # An admin who has just opened the shutter deliberately must be
                # able to move the stage, or manual testing is impossible. The
                # override is recorded rather than silent.
                if identity.has_role(Role.ADMIN):
                    overrode_beam_interlock = True
                else:
                    raise DeviceError(
                        f"laser output is on (or its state is unknown); '{action}' is "
                        "refused until the beam is confirmed off")

        # 7. parameters
        kwargs = coerce_and_validate(spec, params or {})
        fn = bind(adapter, spec)

        # 8. execute. Stop paths deliberately do NOT take the device lock:
        # the executor holds it for an entire line traverse (all repetitions),
        # so acquiring it here would make `halt` and `output_off` wait for the
        # exposure they are trying to interrupt. A stop that queues behind the
        # thing it is stopping is not a stop.
        if overrode_beam_interlock:
            self._audit.append("interlock_override", identity.user_id, {
                "device_id": device_id, "action": action,
                "interlock": "blocked_when_beam_on",
                "reason": "admin moving the stage with the beam live"})
        if spec.tier is ActionTier.EXPOSE:
            self._audit.append("beam_opened_manually", identity.user_id, {
                "device_id": device_id, "action": action,
                "note": "shutter opened outside an approved plan"})
        self._audit_attempt(spec, device_id, action, identity, kwargs)
        try:
            if spec.always_allowed:
                data = fn(**kwargs)          # pre-empt; no lock
            else:
                with self._lock_for(device_id):
                    data = fn(**kwargs)
        except Exception as exc:
            if spec.tier is not ActionTier.READ:
                self._audit.append("device_action_failed", identity.user_id, {
                    "device_id": device_id, "action": action,
                    "error_type": type(exc).__name__, "error": str(exc)})
            raise

        result = ActionResult(device_id=device_id, action=action,
                              detail=_detail_of(data),
                              data=data if isinstance(data, dict) else {})
        if spec.tier is not ActionTier.READ:
            self._audit.append("device_action", identity.user_id, {
                "device_id": device_id, "action": action,
                "tier": str(spec.tier), "params": kwargs})
        return result

    # ------------------------------------------------------------------
    def _audit_attempt(self, spec: ActionSpec, device_id: str, action: str,
                       identity: Identity, kwargs: dict) -> None:
        # Motion is the interesting case for forensics: record the intent
        # before it happens, so a fault mid-move still leaves a trace.
        if spec.tier is ActionTier.MOTION:
            self._audit.append("device_action_attempt", identity.user_id, {
                "device_id": device_id, "action": action, "params": kwargs})


def _detail_of(data: Any) -> str:
    if isinstance(data, dict):
        return str(data.get("detail", ""))
    return "" if data is None else str(data)
