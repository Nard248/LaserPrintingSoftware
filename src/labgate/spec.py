"""The Experiment Specification — the single artifact that crosses the trust boundary.

Declarative, versioned, serializable, re-runnable without any AI. All units
are explicit in field names (mm, mm/s, percent, seconds). The AI (or any
client) authors *intent* at this level; expansion into device commands
happens in the executor, below the trust boundary.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# This model IS the trust boundary: reject anything not explicitly declared
# (extra fields) and any non-finite number (Infinity/NaN would sail through
# range checks like `lo <= v <= hi`).
Finite = Annotated[float, Field(allow_inf_nan=False)]
Point3 = tuple[Finite, Finite, Finite]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetLaserPower(_StrictModel):
    op: Literal["set_laser_power"] = "set_laser_power"
    attenuator_percent: Finite
    pp_divider: int = 1


class WriteLine(_StrictModel):
    op: Literal["write_line"] = "write_line"
    start_mm: Point3
    end_mm: Point3
    velocity_mm_s: Finite
    repetitions: int = 1


class WriteArray(_StrictModel):
    op: Literal["write_array"] = "write_array"
    x_start_mm: Finite
    x_end_mm: Finite
    y_start_mm: Finite
    y_pitch_mm: Finite
    line_count: int
    z_mm: Finite
    velocity_mm_s: Finite
    repetitions: int = 1


class MoveStage(_StrictModel):
    op: Literal["move_stage"] = "move_stage"
    target_mm: Point3


class SetWhiteLight(_StrictModel):
    op: Literal["set_white_light"] = "set_white_light"
    on: bool


class CaptureImage(_StrictModel):
    op: Literal["capture_image"] = "capture_image"
    label: str
    wl_on: bool = True


class Wait(_StrictModel):
    op: Literal["wait"] = "wait"
    seconds: Finite


class WritePowerSweepArray(_StrictModel):
    """Parallel lines where each line gets its own attenuator setting —
    the lab's line-DOE / power-sweep workflow as one validated unit."""

    op: Literal["write_power_sweep_array"] = "write_power_sweep_array"
    x_start_mm: Finite
    x_end_mm: Finite
    y_start_mm: Finite
    y_pitch_mm: Finite
    attenuator_percent_per_line: list[Finite] = Field(min_length=1)
    z_mm: Finite
    velocity_mm_s: Finite
    pp_divider: int = 1
    repetitions: int = 1

    @property
    def line_count(self) -> int:
        return len(self.attenuator_percent_per_line)


class ZStack(_StrictModel):
    """2PP polymerization-threshold workflow: at each power level, a stack
    of z_count lines stepping Z at fixed Y; Y shifts by y_pitch per power.
    Unlike the legacy generator, end_power_percent is INCLUSIVE."""

    op: Literal["z_stack"] = "z_stack"
    x_start_mm: Finite
    x_end_mm: Finite
    y_start_mm: Finite
    y_pitch_mm: Finite
    z_start_mm: Finite
    z_step_mm: Finite
    z_count: int = Field(ge=1)
    start_power_percent: Finite
    end_power_percent: Finite
    power_step_percent: Finite
    velocity_mm_s: Finite
    pp_divider: int = 1
    repetitions: int = 1

    def powers(self) -> list[float]:
        """Inclusive power ladder start..end by step."""
        if self.power_step_percent <= 0:
            return [self.start_power_percent]
        out, p = [], self.start_power_percent
        while p <= self.end_power_percent + 1e-9:
            out.append(round(p, 6))
            p += self.power_step_percent
        return out or [self.start_power_percent]


class PrintStl(_StrictModel):
    """Print an uploaded STL model. The AI references the model and the
    process parameters; slicing into a toolpath happens deterministically
    below the trust boundary (labgate.geometry)."""

    op: Literal["print_stl"] = "print_stl"
    model_id: str
    unit: Literal["mm", "micron", "nm"] = "micron"
    step_size: Finite = Field(gt=0, description="discretization step, in `unit`")
    start_position_mm: Point3
    attenuator_percent: Finite
    velocity_mm_s: Finite
    pp_divider: int = 1
    repetition_count: int = Field(default=1, ge=1)
    is_ablation: bool = False


Operation = Annotated[
    Union[SetLaserPower, WriteLine, WriteArray, MoveStage, SetWhiteLight,
          CaptureImage, Wait, WritePowerSweepArray, ZStack, PrintStl],
    Field(discriminator="op"),
]


class ExperimentSpec(_StrictModel):
    spec_version: Literal["1.0"] = "1.0"
    title: str
    description: str = ""
    operations: list[Operation] = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)
