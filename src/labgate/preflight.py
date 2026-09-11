"""System readiness — "can I run an experiment right now, and if not, why?"

Every failing check carries a `remedy`. Some remedies are API calls the
caller can make immediately; others are physical instructions, because no
amount of software can turn a key switch. Those are flagged `manual: true`
so a chat agent can tell a person what to go and do instead of retrying.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .devices.base import CheckResult
from .registry import CapabilityRegistry

#: Devices whose absence blocks an experiment outright, versus those that
#: only limit what can be done.
ESSENTIAL_KINDS = {"stage", "laser"}


class PreflightReport(BaseModel):
    ready: bool
    mode: str
    summary: str
    blockers: int = 0
    warnings: int = 0
    #: Physical things a person must do; the API cannot do these for you.
    manual_steps: list[str] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)


def run_preflight(registry: CapabilityRegistry, engine, mode: str) -> PreflightReport:
    checks: list[CheckResult] = []

    # --- every registered device reports on itself -----------------------
    registered = {a.kind for a in registry.adapters()}
    for kind in sorted(ESSENTIAL_KINDS - registered):
        checks.append(CheckResult(
            check=f"{kind}.registered", ok=False, severity="blocker",
            detail=f"no {kind} device is registered on this platform",
            remedy="check the platform configuration and restart the server"))

    for adapter in registry.adapters():
        try:
            checks.extend(adapter.diagnose())
        except Exception as exc:  # noqa: BLE001 — a broken probe is itself a finding
            checks.append(CheckResult(
                check=f"{adapter.device_id}.diagnose", ok=False, severity="blocker",
                detail=f"self-test raised {type(exc).__name__}: {exc}",
                remedy=f"inspect the {adapter.device_id} adapter configuration"))

    # --- the rig must not already be busy --------------------------------
    try:
        snapshot = engine.queue_snapshot()
        busy = bool(snapshot.get("running"))
        depth = len(snapshot.get("queued") or [])
        checks.append(CheckResult(
            check="engine.idle", ok=not busy,
            severity="warning" if busy else "info",
            detail=(f"a plan is running ({snapshot['running']}), {depth} queued"
                    if busy else f"engine idle, {depth} queued"),
            remedy="wait for it to finish, or POST /plans/{id}/abort" if busy else ""))
    except Exception as exc:  # noqa: BLE001
        checks.append(CheckResult(
            check="engine.idle", ok=False, severity="warning",
            detail=f"could not read the execution queue: {exc}"))

    # --- simulation is not a fault, but it must never be a surprise ------
    if mode == "sim":
        checks.append(CheckResult(
            check="platform.mode", ok=True, severity="warning",
            detail=("running in SIMULATION — no hardware is being driven and "
                    "results are synthetic"),
            remedy="set labgate.mode: \"rig\" in the config and restart to drive "
                   "the real instruments"))
    else:
        checks.append(CheckResult(
            check="platform.mode", ok=True, severity="info",
            detail="running in RIG mode — commands reach real hardware"))

    blockers = [c for c in checks if not c.ok and c.severity == "blocker"]
    warnings = [c for c in checks if not c.ok and c.severity == "warning"]
    manual = [f"{c.check}: {c.remedy}" for c in checks
              if not c.ok and c.manual and c.remedy]

    ready = not blockers
    if ready:
        summary = (f"ready ({mode} mode)" if not warnings
                   else f"ready with {len(warnings)} warning(s) ({mode} mode)")
    else:
        summary = f"NOT ready — {len(blockers)} blocker(s)"
        if manual:
            summary += f", {len(manual)} needing hands on the instrument"

    return PreflightReport(
        ready=ready, mode=mode, summary=summary,
        blockers=len(blockers), warnings=len(warnings),
        manual_steps=manual, checks=checks,
    )
