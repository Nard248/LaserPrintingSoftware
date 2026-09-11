"""Shared action declarations for each device kind.

Simulated and real adapters declare the *same* action surface with the same
bounds — one source of truth. That is what makes simulation meaningful: a
call that is accepted in sim is accepted on the rig, and one refused in sim
is refused there too.
"""

from __future__ import annotations

from ..actions import ActionSpec, ActionTier
from .base import ParamSpec


def _axis_param(axes: list[int]) -> ParamSpec:
    """Membership, not a range: a configuration of axes [0, 2] must not admit
    axis 1 simply because it falls between the endpoints. Letting it through
    only defers the failure to an IndexError deep in the adapter, which
    surfaces as a 500 instead of a 422."""
    return ParamSpec(name="axis", type="int", unit="",
                     min=min(axes), max=max(axes), allowed=[float(a) for a in axes],
                     description="0 = X, 1 = Y, 2 = Z")


def stage_actions(range_lo: float, range_hi: float, v_min: float, v_max: float,
                  clamp_mm: float, axes: list[int]) -> list[ActionSpec]:
    """Interactive stage control: bring it up, home it, nudge it, stop it."""
    pos = lambda n, d: ParamSpec(  # noqa: E731
        name=n, type="float", unit="mm", min=range_lo, max=range_hi, description=d)
    vel = ParamSpec(name="velocity_mm_s", type="float", unit="mm/s",
                    min=v_min, max=v_max, required=False,
                    description="Override the standing velocity for this move.")
    return [
        ActionSpec(
            name="enable_axes", tier=ActionTier.PREPARE,
            description=("Enable and commutate the servo axes. Nothing moves until "
                         "this has succeeded."),
        ),
        ActionSpec(
            name="home", tier=ActionTier.MOTION,
            description="Move to the fixed home position [0, 0, 0].",
            blocked_when_beam_on=True,
        ),
        ActionSpec(
            name="jog", tier=ActionTier.MOTION,
            description=(f"Nudge one axis by a signed distance. Limited to "
                         f"{clamp_mm} mm per call."),
            params=[
                _axis_param(axes),
                ParamSpec(name="distance_mm", type="float", unit="mm",
                          min=-clamp_mm, max=clamp_mm,
                          description="Signed distance to travel."),
            ],
            blocked_when_beam_on=True,
        ),
        ActionSpec(
            name="move_relative", tier=ActionTier.MOTION,
            description=f"Offset all axes. Each axis limited to {clamp_mm} mm per call.",
            params=[
                ParamSpec(name="dx_mm", type="float", unit="mm",
                          min=-clamp_mm, max=clamp_mm, required=False),
                ParamSpec(name="dy_mm", type="float", unit="mm",
                          min=-clamp_mm, max=clamp_mm, required=False),
                ParamSpec(name="dz_mm", type="float", unit="mm",
                          min=-clamp_mm, max=clamp_mm, required=False),
            ],
            blocked_when_beam_on=True,
        ),
        ActionSpec(
            name="move_absolute", tier=ActionTier.MOTION,
            description=(f"Move to an absolute position. Each axis may travel at most "
                         f"{clamp_mm} mm per call — longer moves belong in a plan."),
            params=[pos("x_mm", "Absolute X"), pos("y_mm", "Absolute Y"),
                    pos("z_mm", "Absolute Z"), vel],
            blocked_when_beam_on=True,
        ),
        ActionSpec(
            name="set_velocity", tier=ActionTier.PREPARE,
            description="Set the standing velocity applied to subsequent moves.",
            params=[ParamSpec(name="velocity_mm_s", type="float", unit="mm/s",
                              min=v_min, max=v_max)],
        ),
        ActionSpec(
            name="set_acceleration", tier=ActionTier.PREPARE,
            description="Set per-axis acceleration.",
            params=[_axis_param(axes),
                    ParamSpec(name="acceleration_mm_s2", type="float", unit="mm/s^2",
                              min=0.1, max=1000.0)],
        ),
        ActionSpec(
            name="set_jerk", tier=ActionTier.PREPARE,
            description="Set per-axis jerk.",
            params=[_axis_param(axes),
                    ParamSpec(name="jerk_mm_s3", type="float", unit="mm/s^3",
                              min=0.1, max=10000.0)],
        ),
        ActionSpec(
            name="halt", tier=ActionTier.MOTION,
            description="Stop all axes immediately.",
            always_allowed=True,  # a stop must never be interlocked out
        ),
        ActionSpec(
            name="position", tier=ActionTier.READ,
            description="Read the live encoder position.",
        ),
    ]


def laser_actions(att_lo: float, att_hi: float, divider_min: int,
                  allow_manual_beam: bool = False) -> list[ActionSpec]:
    """Laser control. Firing it is declared, but reachable only by admin
    (or by an operator when the lab has set allow_manual_beam)."""
    specs = [
        ActionSpec(
            name="set_power", tier=ActionTier.PREPARE,
            description=("Set attenuator percentage and pulse-picker divider. "
                         "Arms the laser; does not open the shutter."),
            params=[
                ParamSpec(name="attenuator_percent", type="float", unit="%",
                          min=att_lo, max=att_hi),
                ParamSpec(name="pp_divider", type="int", min=divider_min,
                          required=False),
            ],
        ),
        ActionSpec(
            name="output_off", tier=ActionTier.PREPARE,
            description="Force the output closed and confirm it. Always permitted.",
            always_allowed=True,
        ),
        ActionSpec(
            name="status", tier=ActionTier.READ,
            description=("Full firmware status: state name, measured output power, "
                         "frequency, and any errors or warnings the laser reports."),
        ),
    ]
    specs.append(ActionSpec(
        name="output_on", tier=ActionTier.EXPOSE,
        description=(
            "Open the shutter WITHOUT an approved plan — the beam goes live. "
            "Requires the admin role"
            + (", or the operator role since allow_manual_beam is set."
               if allow_manual_beam else
               " (set labgate.allow_manual_beam to let operators do it too).")
        ),
    ))
    return specs


def camera_actions(exp_lo: float, exp_hi: float,
                   gain_lo: float, gain_hi: float) -> list[ActionSpec]:
    """Camera setup and inspection. Capturing an image changes nothing physical."""
    return [
        ActionSpec(
            name="snapshot", tier=ActionTier.READ,
            description=("Grab a single frame and store it as an artifact. "
                         "Non-mutating — safe at any time."),
            params=[ParamSpec(name="label", type="str", required=False,
                              description="Artifact name; defaults to a timestamp.")],
        ),
        ActionSpec(
            name="set_exposure", tier=ActionTier.PREPARE,
            description="Set the exposure time.",
            params=[ParamSpec(name="exposure_time_us", type="float", unit="us",
                              min=exp_lo, max=exp_hi)],
        ),
        ActionSpec(
            name="set_gain", tier=ActionTier.PREPARE,
            description="Set analogue gain.",
            params=[ParamSpec(name="gain_db", type="float", unit="dB",
                              min=gain_lo, max=gain_hi)],
        ),
        ActionSpec(
            name="set_auto_exposure", tier=ActionTier.PREPARE,
            description="Set the auto-exposure mode.",
            params=[ParamSpec(name="mode", type="str",
                              choices=["off", "once", "continuous"])],
        ),
        ActionSpec(
            name="settings", tier=ActionTier.READ,
            description="Read current exposure, gain, pixel format and resolution.",
        ),
    ]


def white_light_actions() -> list[ActionSpec]:
    return [
        ActionSpec(
            name="set_on", tier=ActionTier.PREPARE,
            description="Switch the white-light illumination on or off.",
            params=[ParamSpec(name="on", type="bool")],
        ),
    ]
