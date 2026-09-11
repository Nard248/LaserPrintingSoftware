"""Device actions — the interactive control plane.

Plans (spec.py) are for experiments: they are validated, approved by a
second person, queued and executed. That ceremony is right for anything
that fires the laser, and wrong for "nudge Z by 0.1 mm to find focus".

This module defines the *other* path: direct device actions, governed by
risk tier instead of by human approval. The trust boundary is not
weakened — it is made proportionate:

    read     no state change at all                  → any platform role
    prepare  connect, enable, configure; no motion   → operator
    motion   moves the stage; never opens the beam   → operator, interlocked
    expose   opens the shutter                       → ADMIN ONLY

`expose` is reachable, but only by an identity holding the `admin` role.
That is the difference between "an operator cannot fire the laser without a
second person signing for it" — still true — and "nobody can ever fire it
interactively", which would make bringing up and physically testing a rig
impossible. Admin is the rig owner; the audit trail records every use.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Callable

from pydantic import BaseModel, Field

from .devices.base import ParamSpec
from .errors import ValidationFailed


class ActionTier(StrEnum):
    READ = "read"
    PREPARE = "prepare"
    MOTION = "motion"
    EXPOSE = "expose"


#: Tiers a device action may be invoked at. `expose` is included but carries
#: the strictest role requirement — see labgate.devicectl.TIER_ROLE.
INVOCABLE_TIERS = {ActionTier.READ, ActionTier.PREPARE, ActionTier.MOTION,
                   ActionTier.EXPOSE}


class ActionSpec(BaseModel):
    """Machine-readable declaration of one thing a device can be told to do."""

    name: str
    tier: ActionTier
    description: str = ""
    params: list[ParamSpec] = Field(default_factory=list)

    #: Refuse unless the device is connected (almost everything except connect).
    requires_connected: bool = True
    #: Refuse while the execution engine has a plan running or queued.
    blocked_during_run: bool = True
    #: Refuse while the laser reports output on (motion safety).
    blocked_when_beam_on: bool = False
    #: Always allowed, even mid-run — the stop paths.
    always_allowed: bool = False

    def param(self, name: str) -> ParamSpec | None:
        return next((p for p in self.params if p.name == name), None)


class ActionResult(BaseModel):
    """What an action returns to the caller."""

    device_id: str
    action: str
    ok: bool = True
    detail: str = ""
    data: dict = Field(default_factory=dict)


def coerce_and_validate(spec: ActionSpec, raw: dict[str, Any]) -> dict[str, Any]:
    """Check caller-supplied parameters against the declared ParamSpecs.

    Same discipline the plan validator applies: unknown fields are refused,
    required fields must be present, numbers must be finite and in range.
    Returns the coerced kwargs to hand to the adapter method.
    """
    declared = {p.name: p for p in spec.params}
    unknown = set(raw) - set(declared)
    if unknown:
        raise ValidationFailed(
            f"{spec.name}: unknown parameter(s) {sorted(unknown)}; "
            f"expected {sorted(declared)}")

    out: dict[str, Any] = {}
    for name, p in declared.items():
        if name not in raw:
            if p.required:
                raise ValidationFailed(f"{spec.name}: missing required parameter '{name}'")
            continue
        out[name] = _coerce_one(spec.name, p, raw[name])
    return out


def _coerce_one(action: str, p: ParamSpec, value: Any) -> Any:
    if p.type == "bool":
        if not isinstance(value, bool):
            raise ValidationFailed(f"{action}.{p.name}: expected a boolean")
        return value

    if p.type == "str":
        if not isinstance(value, str):
            raise ValidationFailed(f"{action}.{p.name}: expected a string")
        if p.choices and value not in p.choices:
            raise ValidationFailed(
                f"{action}.{p.name}: '{value}' not one of {p.choices}")
        return value

    if p.type in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationFailed(f"{action}.{p.name}: expected a number")
        number = float(value)
        # Infinity / NaN slip past naive range checks — refuse them outright.
        if number != number or number in (float("inf"), float("-inf")):
            raise ValidationFailed(f"{action}.{p.name}: must be a finite number")
        if p.type == "int":
            if float(value) != int(value):
                raise ValidationFailed(f"{action}.{p.name}: expected an integer")
            number = int(value)
        if p.min is not None and number < p.min:
            raise ValidationFailed(
                f"{action}.{p.name}: {number} below minimum {p.min}"
                + (f" {p.unit}" if p.unit else ""))
        if p.max is not None and number > p.max:
            raise ValidationFailed(
                f"{action}.{p.name}: {number} above maximum {p.max}"
                + (f" {p.unit}" if p.unit else ""))
        return number

    raise ValidationFailed(f"{action}.{p.name}: unsupported parameter type {p.type!r}")


def bind(adapter: Any, spec: ActionSpec) -> Callable[..., Any]:
    """Resolve the adapter method implementing `spec`.

    Convention: an action named "jog" is implemented by `adapter.act_jog`.
    The `act_` prefix keeps the invocable surface explicit — an adapter's
    other public methods are not reachable from the network.
    """
    fn = getattr(adapter, f"act_{spec.name}", None)
    if fn is None or not callable(fn):
        raise ValidationFailed(
            f"device '{getattr(adapter, 'device_id', '?')}' declares action "
            f"'{spec.name}' but implements no act_{spec.name}()")
    return fn
