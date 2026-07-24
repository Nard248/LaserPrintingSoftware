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


Operation = Annotated[
    Union[SetLaserPower, WriteLine, WriteArray, MoveStage, SetWhiteLight, CaptureImage, Wait],
    Field(discriminator="op"),
]


class ExperimentSpec(_StrictModel):
    spec_version: Literal["1.0"] = "1.0"
    title: str
    description: str = ""
    operations: list[Operation] = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)
