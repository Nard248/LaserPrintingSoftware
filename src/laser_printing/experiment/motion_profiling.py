"""
Motion profiling experiment: characterize acceleration and jerk behavior.

Sweeps through combinations of acceleration and jerk settings while
recording velocity and position data from the controller's data collection
system. Produces:

    - Per-condition: velocity/position/acceleration/jerk profiles (CSV + plots)
    - Summary: settling time, overshoot, ripple, usable fraction per condition
    - Calibration data: acceleration time and distance for each velocity,
      which feeds directly into the PrintSynchronizer's calibration table.

This experiment does NOT use the laser — it's purely for motion characterization.
"""

from __future__ import annotations

import logging
from itertools import product
from typing import Any

import numpy as np
import pandas as pd

from laser_printing.experiment.base import BaseExperiment

logger = logging.getLogger(__name__)


class MotionProfiling(BaseExperiment):
    """Sweep acceleration/jerk values and record motion profiles.

    Example:
        exp = MotionProfiling(
            experiment_id="accel_jerk_sweep",
            output_dir="./results",
            z_focus=6.0,
            x_start=1.0, x_end=2.0, y_line=7.0,
            target_velocity=5.0,
            acceleration_values=[5, 10, 25, 50, 125, 150],
            jerk_values=[5, 10, 25, 50, 125, 150],
        )
        results = exp.run()
    """

    def __init__(
        self,
        experiment_id: str,
        output_dir: str,
        z_focus: float,
        x_start: float,
        x_end: float,
        y_line: float,
        target_velocity: float = 5.0,
        acceleration_values: list[float] | None = None,
        jerk_values: list[float] | None = None,
        n_samples: int = 6000,
        sample_period: int = 1,
        servo_cycle_s: float = 0.001064,
        velocity_tolerance: float = 0.05,
        config_path: str | None = None,
        description: str = "",
    ):
        super().__init__(experiment_id, output_dir, config_path, z_focus, description)

        self.x_start = x_start
        self.x_end = x_end
        self.y_line = y_line
        self.target_velocity = target_velocity
        self.acceleration_values = acceleration_values or [5, 10, 25, 50, 125]
        self.jerk_values = jerk_values or [5, 10, 25, 50, 125]
        self.n_samples = n_samples
        self.sample_period = sample_period
        self.servo_cycle_s = servo_cycle_s
        self.velocity_tolerance = velocity_tolerance

        self._start_point = (x_start, y_line, z_focus)
        self._end_point = (x_end, y_line, z_focus)
        self._array_name = "motion_profile_data"

    def _define_conditions(self) -> list[dict[str, Any]]:
        conditions = []
        for accel, jerk in product(self.acceleration_values, self.jerk_values):
            conditions.append({
                "acceleration": accel,
                "jerk": jerk,
                "target_velocity_mm_s": self.target_velocity,
            })
        return conditions

    def _run_condition(self, index: int,
                       condition: dict[str, Any]) -> dict[str, Any]:
        accel = condition["acceleration"]
        jerk = condition["jerk"]

        # Set motion profile for X axis (primary print axis)
        self.stage.set_velocity(self.target_velocity)
        self.stage.set_acceleration(0, accel)
        self.stage.set_jerk(0, jerk)

        # Ensure we're at start position
        self.stage.move_to(list(self._start_point))

        # Declare data collection array
        self.stage.declare_data_array(self._array_name, 2, self.n_samples)

        # Start non-blocking motion + data collection
        self.stage.move_to_non_blocking(list(self._end_point))
        self.stage.start_data_collection(
            self._array_name, self.n_samples,
            self.sample_period, "FVEL(0), FPOS(0)"
        )
        self.stage.wait_for_motion(timeout_ms=30000)

        # Read collected data
        data = self.stage.read_data_array(self._array_name, 2, self.n_samples)
        vel_array = data[0, :]
        pos_array = data[1, :]
        time_array = np.arange(len(vel_array)) * self.sample_period * self.servo_cycle_s

        # Analyze the motion profile
        metrics = self._analyze_profile(time_array, pos_array, vel_array)

        # Save per-condition data
        condition_dir = self.output_dir / f"a_{accel}_j_{jerk}"
        condition_dir.mkdir(parents=True, exist_ok=True)

        raw_df = pd.DataFrame({
            "time_s": time_array,
            "position_mm": pos_array,
            "velocity_mm_s": vel_array,
        })
        raw_df.to_csv(condition_dir / "raw_profile.csv", index=False)

        return metrics

    def _analyze_profile(self, time_arr: np.ndarray, pos_arr: np.ndarray,
                         vel_arr: np.ndarray) -> dict[str, Any]:
        """Extract key metrics from a recorded motion profile."""
        speed = np.abs(vel_arr)
        target = self.target_velocity
        tol = self.velocity_tolerance

        lower = target * (1 - tol)
        upper = target * (1 + tol)

        # Peak speed and overshoot
        peak_speed = float(np.max(speed))
        overshoot = float(max(0, peak_speed - target))

        # Time/distance to first reach target velocity
        reached = np.where(speed >= target)[0]
        if len(reached) > 0:
            first_idx = int(reached[0])
            accel_time = float(time_arr[first_idx] - time_arr[0])
            accel_distance = float(abs(pos_arr[first_idx] - pos_arr[0]))
        else:
            accel_time = float("nan")
            accel_distance = float("nan")

        # Settling: how long until speed stays within tolerance band
        in_band = (speed >= lower) & (speed <= upper)
        settle_time = float("nan")
        consecutive = 20
        for i in range(len(in_band) - consecutive + 1):
            if np.all(in_band[i:i + consecutive]):
                settle_time = float(time_arr[i] - time_arr[0])
                break

        # Ripple within tolerance band
        band_speeds = speed[in_band]
        ripple_std = float(np.std(band_speeds)) if len(band_speeds) > 5 else float("nan")

        # Usable fraction (percentage of motion spent in tolerance band)
        usable_fraction = float(np.mean(in_band)) if len(in_band) > 0 else 0.0

        return {
            "peak_speed_mm_s": peak_speed,
            "overshoot_mm_s": overshoot,
            "accel_time_s": accel_time,
            "accel_distance_mm": accel_distance,
            "settling_time_s": settle_time,
            "ripple_std_mm_s": ripple_std,
            "usable_fraction": usable_fraction,
        }
