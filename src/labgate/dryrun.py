"""Dry-run estimator (requirement F6).

Computes what a plan would do without touching hardware: duration, motion
distance, exposure events, spatial envelope. This is what the approving
human sees next to the validation report — enough for informed consent
rather than a reflexive click.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .config import LabgateConfig
from .spec import (
    CaptureImage,
    ExperimentSpec,
    MoveStage,
    SetLaserPower,
    SetWhiteLight,
    Wait,
    WriteArray,
    WriteLine,
)

_CAPTURE_S = 0.5  # nominal camera capture time


class OpEstimate(BaseModel):
    op_index: int
    op: str
    duration_s: float
    detail: str = ""


class DryRunReport(BaseModel):
    total_duration_s: float
    motion_distance_mm: float
    exposure_events: int
    bounding_box_mm: dict = Field(default_factory=dict)
    per_op: list[OpEstimate] = Field(default_factory=list)


class DryRunEstimator:
    def __init__(self, cfg: LabgateConfig) -> None:
        self._cfg = cfg

    def estimate(self, spec: ExperimentSpec) -> DryRunReport:
        settle = self._cfg.bounds.laser.toggle_settle_s
        pos = (0.0, 0.0, 0.0)
        total_s = 0.0
        dist_mm = 0.0
        exposures = 0
        points: list[tuple[float, float, float]] = [pos]
        per_op: list[OpEstimate] = []

        def seg(a, b) -> float:
            return max(abs(x - y) for x, y in zip(a, b))

        for i, op in enumerate(spec.operations):
            duration = 0.0
            detail = ""
            if isinstance(op, MoveStage):
                duration = seg(pos, op.target_mm) / 1.0  # default travel velocity 1 mm/s
                dist_mm += seg(pos, op.target_mm)
                pos = op.target_mm
                points.append(pos)
            elif isinstance(op, WriteLine):
                line = seg(op.start_mm, op.end_mm)
                travel = seg(pos, op.start_mm)
                duration = travel / 1.0 + op.repetitions * (line / op.velocity_mm_s + 2 * settle)
                dist_mm += travel + op.repetitions * line
                exposures += op.repetitions
                pos = op.end_mm if op.repetitions % 2 else op.start_mm
                points += [op.start_mm, op.end_mm]
                detail = f"{op.repetitions}x {line:.3f} mm at {op.velocity_mm_s} mm/s"
            elif isinstance(op, WriteArray):
                line = abs(op.x_end_mm - op.x_start_mm)
                y_end = op.y_start_mm + (op.line_count - 1) * op.y_pitch_mm
                n = op.line_count * op.repetitions
                start = (op.x_start_mm, op.y_start_mm, op.z_mm)
                travel = seg(pos, start)
                y_steps = (op.line_count - 1) * abs(op.y_pitch_mm)
                duration = travel / 1.0 + n * (line / op.velocity_mm_s + 2 * settle) \
                    + y_steps / 1.0
                dist_mm += travel + n * line + y_steps
                exposures += n
                points += [start, (op.x_end_mm, y_end, op.z_mm)]
                # each line ends where its repetitions leave the stage
                end_x = op.x_end_mm if op.repetitions % 2 else op.x_start_mm
                pos = (end_x, y_end, op.z_mm)
                detail = f"{op.line_count} lines x {op.repetitions} reps, pitch {op.y_pitch_mm} mm"
            elif isinstance(op, Wait):
                duration = op.seconds
            elif isinstance(op, CaptureImage):
                duration = _CAPTURE_S
            elif isinstance(op, (SetLaserPower, SetWhiteLight)):
                duration = 0.1
            total_s += duration
            per_op.append(OpEstimate(op_index=i, op=op.op, duration_s=round(duration, 3),
                                     detail=detail))

        xs, ys, zs = zip(*points)
        return DryRunReport(
            total_duration_s=round(total_s, 3),
            motion_distance_mm=round(dist_mm, 3),
            exposure_events=exposures,
            bounding_box_mm={
                "min": [min(xs), min(ys), min(zs)],
                "max": [max(xs), max(ys), max(zs)],
            },
            per_op=per_op,
        )
