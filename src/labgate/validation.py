"""Deterministic validation engine (requirement F5).

Validates an ExperimentSpec against hard bounds, travel range, and mutual
device constraints. The AI's output is untrusted input: a model-authored
spec passes through exactly the same checks as a hand-typed one. The
report is designed to be readable by both the approving human and the
planning agents (they receive it via the API and can self-correct).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .config import LabgateConfig
from .errors import ValidationFailed
from .spec import (
    CaptureImage,
    ExperimentSpec,
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

# Ops that expose the laser (need power configured + WL off)
_WRITE_OPS = (WriteLine, WriteArray, WritePowerSweepArray, ZStack, PrintStl)
# Write ops that carry their own power setting
_SELF_POWERED = (WritePowerSweepArray, ZStack, PrintStl)


class CheckResult(BaseModel):
    check: str
    ok: bool
    severity: str = "error"  # "error" | "warning"
    detail: str = ""


class ValidationReport(BaseModel):
    ok: bool
    checks: list[CheckResult] = Field(default_factory=list)

    def summary(self) -> str:
        failed = [c for c in self.checks if not c.ok and c.severity == "error"]
        warns = [c for c in self.checks if not c.ok and c.severity == "warning"]
        return (
            f"{'PASS' if self.ok else 'FAIL'}: "
            f"{len(self.checks)} checks, {len(failed)} errors, {len(warns)} warnings"
        )


class ValidationEngine:
    def __init__(self, cfg: LabgateConfig, geometry=None) -> None:
        self._cfg = cfg
        self._geometry = geometry  # GeometryService; required for print_stl

    def validate(self, spec: ExperimentSpec) -> ValidationReport:
        checks: list[CheckResult] = []
        checks += self._check_bounds(spec)
        checks += self._check_geometry(spec)
        checks += self._check_ordering(spec)
        checks += self._check_exposure_uniformity(spec)
        ok = all(c.ok for c in checks if c.severity == "error")
        return ValidationReport(ok=ok, checks=checks)

    # --- V2: numeric bounds ---
    def _check_bounds(self, spec: ExperimentSpec) -> list[CheckResult]:
        out: list[CheckResult] = []
        b = self._cfg.bounds
        a_lo, a_hi = b.laser.attenuator_percent
        v_lo, v_hi = b.stage.min_velocity_mm_s, b.stage.max_velocity_mm_s
        for i, op in enumerate(spec.operations):
            if isinstance(op, SetLaserPower):
                if not a_lo <= op.attenuator_percent <= a_hi:
                    out.append(CheckResult(
                        check=f"op[{i}].attenuator_percent", ok=False,
                        detail=f"{op.attenuator_percent}% outside [{a_lo}, {a_hi}]"))
                if op.pp_divider < b.laser.pp_divider_min:
                    out.append(CheckResult(
                        check=f"op[{i}].pp_divider", ok=False,
                        detail=f"{op.pp_divider} < {b.laser.pp_divider_min}"))
            vel = getattr(op, "velocity_mm_s", None)
            if vel is not None and not v_lo <= vel <= v_hi:
                out.append(CheckResult(
                    check=f"op[{i}].velocity_mm_s", ok=False,
                    detail=f"{vel} mm/s outside [{v_lo}, {v_hi}]"))
            if isinstance(op, Wait) and op.seconds < 0:
                out.append(CheckResult(check=f"op[{i}].seconds", ok=False,
                                       detail="negative wait"))
            if isinstance(op, (WriteLine, WriteArray, WritePowerSweepArray, ZStack)) \
                    and op.repetitions < 1:
                out.append(CheckResult(check=f"op[{i}].repetitions", ok=False,
                                       detail="repetitions must be >= 1"))
            if isinstance(op, WriteArray) and op.line_count < 1:
                out.append(CheckResult(check=f"op[{i}].line_count", ok=False,
                                       detail="line_count must be >= 1"))
            if isinstance(op, WritePowerSweepArray):
                for j, p in enumerate(op.attenuator_percent_per_line):
                    if not a_lo <= p <= a_hi:
                        out.append(CheckResult(
                            check=f"op[{i}].attenuator_percent_per_line[{j}]",
                            ok=False, detail=f"{p}% outside [{a_lo}, {a_hi}]"))
            if isinstance(op, ZStack):
                for p in op.powers():
                    if not a_lo <= p <= a_hi:
                        out.append(CheckResult(
                            check=f"op[{i}].power_ladder", ok=False,
                            detail=f"{p}% outside [{a_lo}, {a_hi}]"))
                        break
                if op.power_step_percent <= 0 \
                        and op.end_power_percent != op.start_power_percent:
                    out.append(CheckResult(
                        check=f"op[{i}].power_step_percent", ok=False,
                        detail="power_step_percent must be > 0 for a power range"))
            if isinstance(op, PrintStl):
                if not a_lo <= op.attenuator_percent <= a_hi:
                    out.append(CheckResult(
                        check=f"op[{i}].attenuator_percent", ok=False,
                        detail=f"{op.attenuator_percent}% outside [{a_lo}, {a_hi}]"))
                step_um = {"mm": 1000.0, "micron": 1.0, "nm": 0.001}[op.unit] \
                    * op.step_size
                if step_um < 1.0:
                    out.append(CheckResult(
                        check=f"op[{i}].step_size", ok=True, severity="warning",
                        detail=f"step {step_um:.2f} um is very fine — slicing "
                               "may be slow and the path very long"))
        if not out:
            out.append(CheckResult(check="bounds", ok=True, detail="all parameters in bounds"))
        return out

    # --- V3: geometry inside travel range (via the shared path expansion,
    # so validation sees the SAME trajectory the executor will run — for
    # print_stl that means the actual deterministically sliced path) ---
    def _check_geometry(self, spec: ExperimentSpec) -> list[CheckResult]:
        from .pathing import op_to_path

        lo, hi = self._cfg.bounds.stage.range_mm
        out: list[CheckResult] = []
        for i, op in enumerate(spec.operations):
            if isinstance(op, PrintStl) and self._geometry is None:
                out.append(CheckResult(
                    check=f"op[{i}].print_stl", ok=False,
                    detail="platform has no geometry service configured"))
                continue
            try:
                path = op_to_path(op, self._geometry)
            except ValidationFailed as exc:
                out.append(CheckResult(check=f"op[{i}].{op.op}", ok=False,
                                       detail=str(exc)))
                continue
            bad = 0
            for point, _laser in path:
                for axis_name, value in zip("xyz", point):
                    if not lo <= value <= hi:
                        bad += 1
                        if bad <= 3:  # report the first few, count the rest
                            out.append(CheckResult(
                                check=f"op[{i}].geometry.{axis_name}", ok=False,
                                detail=f"{value} mm outside travel range [{lo}, {hi}]"))
            if bad > 3:
                out.append(CheckResult(
                    check=f"op[{i}].geometry", ok=False,
                    detail=f"...and {bad - 3} more out-of-range coordinates"))
        if not out:
            out.append(CheckResult(check="geometry", ok=True,
                                   detail="all coordinates inside travel range"))
        return out

    # --- V4: mutual constraints / ordering ---
    def _check_ordering(self, spec: ExperimentSpec) -> list[CheckResult]:
        out: list[CheckResult] = []
        power_set = False
        wl_on = False
        for i, op in enumerate(spec.operations):
            if isinstance(op, SetLaserPower):
                power_set = True
            if isinstance(op, SetWhiteLight):
                wl_on = op.on
            if isinstance(op, _SELF_POWERED):
                power_set = True  # these ops set their own attenuator/divider
            if isinstance(op, _WRITE_OPS) and not power_set:
                out.append(CheckResult(
                    check=f"op[{i}].laser_power_unset", ok=False,
                    detail="write operation before any set_laser_power"))
            if isinstance(op, _WRITE_OPS) and wl_on:
                out.append(CheckResult(
                    check=f"op[{i}].wl_during_exposure", ok=False,
                    detail="white light must be off during laser exposure"))
            if isinstance(op, CaptureImage) and op.wl_on and not wl_on:
                # Informational only: the executor switches WL on for the
                # capture and RESTORES it afterwards (executor._dispatch),
                # so tracked wl_on stays correct for later exposure checks.
                out.append(CheckResult(
                    check=f"op[{i}].wl_auto", ok=True, severity="warning",
                    detail="white light will be switched on for the capture and restored after"))
        if not out:
            out.append(CheckResult(check="ordering", ok=True, detail="operation order consistent"))
        return out

    # --- V5: exposure-uniformity warning (short lines vs accel distance) ---
    def _check_exposure_uniformity(self, spec: ExperimentSpec) -> list[CheckResult]:
        out: list[CheckResult] = []
        min_len = 2 * self._cfg.bounds.laser.accel_distance_mm
        for i, op in enumerate(spec.operations):
            length = None
            if isinstance(op, WriteLine):
                length = max(abs(e - s) for s, e in zip(op.start_mm, op.end_mm))
            elif isinstance(op, (WriteArray, WritePowerSweepArray, ZStack)):
                length = abs(op.x_end_mm - op.x_start_mm)
            if length is not None and length < min_len:
                out.append(CheckResult(
                    check=f"op[{i}].line_length", ok=False, severity="warning",
                    detail=(f"line {length:.3f} mm < 2x accel distance {min_len:.3f} mm — "
                            "exposure may be non-uniform (sequential fallback)")))
        if not out:
            out.append(CheckResult(check="exposure_uniformity", ok=True,
                                   detail="all lines long enough for uniform exposure"))
        return out
