"""The gate in front of the device control plane.

Plans get validated + approved. Device actions get *this* instead: a small,
deterministic set of interlocks applied before anything reaches hardware.

Order of checks (all must pass, cheapest and most important first):

  1. the action exists and is declared by that device
  2. its tier is invocable at all               (never `expose`)
  3. the caller holds the role that tier needs
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

#: Minimum role per tier. `expose` is absent — it is never invocable here.
TIER_ROLE: dict[ActionTier, Role | None] = {
    ActionTier.READ: None,              # any authenticated platform role
    ActionTier.PREPARE: Role.OPERATOR,
    ActionTier.MOTION: Role.OPERATOR,
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
        if spec.tier == ActionTier.MOTION and not spec.always_allowed \
                and self._opens_beam(spec) and not self._allow_manual_beam:
            raise AuthError(
                f"action '{action}' would open the beam; manual beam control is "
                "disabled (labgate.allow_manual_beam)")

        # 3. role
        needed = TIER_ROLE.get(spec.tier)
        if needed is not None:
            require_role(identity, needed)
        elif not (identity.has_role(Role.OPERATOR) or identity.has_role(Role.APPROVER)):
            raise AuthError(
                f"user '{identity.user_id}' has no role granting device access")

        # 4. connected
        if spec.requires_connected and not adapter.state().connected:
            raise DeviceError(
                f"device '{device_id}' is not connected — "
                f"POST /devices/{device_id}/connect first")

        # 5/6. interlocks (stop paths bypass both, by design)
        if not spec.always_allowed:
            if spec.blocked_during_run and self._run_is_active():
                raise TransitionError(
                    f"a plan is running or queued; '{action}' is refused while the "
                    "execution engine owns the rig (abort it first, or wait)")
            if spec.blocked_when_beam_on and self._beam_is_on():
                raise DeviceError(
                    f"laser output is on (or its state is unknown); '{action}' is "
                    "refused until the beam is confirmed off")

        # 7. parameters
        kwargs = coerce_and_validate(spec, params or {})
        fn = bind(adapter, spec)

        # 8. execute under the device lock
        self._audit_attempt(spec, device_id, action, identity, kwargs)
        try:
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
    def _opens_beam(self, spec: ActionSpec) -> bool:
        return spec.name in {"output_on", "beam_on", "fire"}

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
