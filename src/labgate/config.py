"""Platform configuration.

Reads the existing config/default.yaml (single source of hardware truth)
plus an optional `labgate:` section. Everything has safe defaults so the
platform boots in simulation mode with no config file at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class StageBounds(BaseModel):
    range_mm: tuple[float, float] = (-25.0, 25.0)
    max_velocity_mm_s: float = 10.0
    min_velocity_mm_s: float = 0.1
    max_step_mm: float = 5.0


class LaserBounds(BaseModel):
    attenuator_percent: tuple[float, float] = (0.0, 100.0)
    pp_divider_min: int = 1
    # Calibrated at 5 mm/s; used by validation for the short-line warning.
    accel_distance_mm: float = 0.79
    toggle_settle_s: float = 0.2


class Bounds(BaseModel):
    stage: StageBounds = Field(default_factory=StageBounds)
    laser: LaserBounds = Field(default_factory=LaserBounds)


class LabgateConfig(BaseModel):
    mode: Literal["sim", "rig"] = "sim"
    storage_dir: Path = Path("labgate_data")
    tokens_file: Path | None = None
    bounds: Bounds = Field(default_factory=Bounds)
    # Raw hardware config (stage/laser sections of default.yaml) passed
    # through to rig adapters untouched.
    hardware: dict = Field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "LabgateConfig":
        if path is None:
            return cls()
        raw = yaml.safe_load(Path(path).read_text()) or {}
        section = raw.get("labgate", {})
        cfg = cls(**section)
        cfg.hardware = {k: raw[k] for k in ("stage", "laser", "synchronization") if k in raw}
        stage = raw.get("stage", {})
        if "range_mm" in stage:
            cfg.bounds.stage.range_mm = tuple(stage["range_mm"])
        sync = raw.get("synchronization", {})
        for point in sync.get("calibration", []):
            cfg.bounds.laser.accel_distance_mm = point.get(
                "accel_distance_mm", cfg.bounds.laser.accel_distance_mm
            )
        return cfg
