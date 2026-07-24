"""The Experiment Specification — the single artifact that crosses the trust boundary.

Declarative, versioned, serializable, re-runnable without any AI. All units
are explicit in field names (mm, mm/s, percent, seconds). The AI (or any
client) authors *intent* at this level; expansion into device commands
happens in the executor, below the trust boundary.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

Point3 = tuple[float, float, float]


class SetLaserPower(BaseModel):
    op: Literal["set_laser_power"] = "set_laser_power"
    attenuator_percent: float
    pp_divider: int = 1


class WriteLine(BaseModel):
    op: Literal["write_line"] = "write_line"
    start_mm: Point3
    end_mm: Point3
    velocity_mm_s: float
    repetitions: int = 1


class WriteArray(BaseModel):
    op: Literal["write_array"] = "write_array"
    x_start_mm: float
    x_end_mm: float
    y_start_mm: float
    y_pitch_mm: float
    line_count: int
    z_mm: float
    velocity_mm_s: float
    repetitions: int = 1


class MoveStage(BaseModel):
    op: Literal["move_stage"] = "move_stage"
    target_mm: Point3


class SetWhiteLight(BaseModel):
    op: Literal["set_white_light"] = "set_white_light"
    on: bool


class CaptureImage(BaseModel):
    op: Literal["capture_image"] = "capture_image"
    label: str
    wl_on: bool = True


class Wait(BaseModel):
    op: Literal["wait"] = "wait"
    seconds: float


Operation = Annotated[
    Union[SetLaserPower, WriteLine, WriteArray, MoveStage, SetWhiteLight, CaptureImage, Wait],
    Field(discriminator="op"),
]


class ExperimentSpec(BaseModel):
    spec_version: Literal["1.0"] = "1.0"
    title: str
    description: str = ""
    operations: list[Operation] = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)
