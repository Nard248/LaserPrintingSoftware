"""Layer 1 hardware controllers.

    LaserController - on/off, attenuator, pp_divider; polled settle.
    StageController - move_absolute/relative, jog, clamp-protected; home=[0,0,0].
    PrintSynchronizer - Layer 2 coordinator, imported from .sync.

Attributes are resolved lazily (PEP 562): importing this package does NOT
require SPiiPlusPython, so laser-only code (and the labgate platform in
simulation mode) works on machines without the ACS vendor wheel. Stage
classes import the wheel only when first accessed.
"""

from typing import Any

__all__ = [
    "LaserController",
    "ToggleMetrics",
    "StageController",
    "StageSafetyError",
    "HOME_POSITION",
]

_LASER = {"LaserController", "ToggleMetrics"}
_STAGE = {"StageController", "StageSafetyError", "HOME_POSITION"}


def __getattr__(name: str) -> Any:
    if name in _LASER:
        from laser_printing.controllers import laser
        return getattr(laser, name)
    if name in _STAGE:
        from laser_printing.controllers import stage  # imports SPiiPlusPython
        return getattr(stage, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
