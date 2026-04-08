"""
Synchronized laser-stage execution engine.

This is the most critical module in the system. The core problem:

    When the stage moves from point A to point B, the velocity follows a
    trapezoidal profile:  accelerate -> constant velocity -> decelerate.
    The laser must fire ONLY during the constant-velocity region to ensure
    uniform exposure dose.

    Without synchronization, laser on/off happens between moves (while the
    stage is stationary), which causes incorrect exposure at line endpoints
    and makes multi-line STL printing fail.

Two strategies are implemented:

    1. "sequential" - Simple: move, wait, toggle laser. No overlap.
       Good for testing and non-critical operations (repositioning, etc.).

    2. "velocity_timed" - Uses calibrated timing from acceleration profiling
       experiments to fire the laser during the constant-velocity window.
       This is the production strategy for actual printing.

Future strategy (requires hardware investigation):
    3. "hardware_trigger" - Use the ACS controller's digital output to
       trigger the laser based on real-time velocity or position thresholds.
       This would be the ideal solution.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Sequence

from laser_printing.controllers.laser import LaserController
from laser_printing.controllers.stage import StageController

logger = logging.getLogger(__name__)


@dataclass
class CalibrationPoint:
    """Measured acceleration profile for a given velocity.

    These values come from the motor_profiling experiment, which measures
    how long (and how far) the stage takes to reach target velocity.
    """
    velocity_mm_s: float
    accel_time_s: float        # time from motion start to reaching target velocity
    accel_distance_mm: float   # distance traveled during acceleration


class PrintSynchronizer:
    """Coordinates laser firing with stage motion for uniform exposure.

    Usage:
        sync = PrintSynchronizer.from_config(config, laser, stage)

        # Execute a single print line (laser fires during constant-velocity region)
        sync.execute_print_line(start=[0, 0, 6], end=[10, 0, 6])

        # Execute a full path: list of (point, should_print) tuples
        sync.execute_path(path)
    """

    def __init__(self, laser: LaserController, stage: StageController,
                 strategy: str = "velocity_timed",
                 velocity_tolerance: float = 0.05,
                 calibration: list[CalibrationPoint] | None = None):
        self._laser = laser
        self._stage = stage
        self._strategy = strategy
        self._velocity_tolerance = velocity_tolerance
        self._calibration = calibration or []

    @classmethod
    def from_config(cls, config: dict, laser: LaserController,
                    stage: StageController) -> PrintSynchronizer:
        """Create from the full config dict."""
        sync_cfg = config["synchronization"]
        cal_points = [
            CalibrationPoint(**entry)
            for entry in sync_cfg.get("calibration", [])
        ]
        return cls(
            laser=laser,
            stage=stage,
            strategy=sync_cfg["strategy"],
            velocity_tolerance=sync_cfg.get("velocity_tolerance_fraction", 0.05),
            calibration=cal_points,
        )

    # -- Public API -------------------------------------------------------

    def execute_path(self, path: list[tuple[Sequence[float], bool]]) -> None:
        """Execute a complete motion path with coordinated laser control.

        Args:
            path: List of (point, laser_on) tuples. Each point is [x, y, z].
                  laser_on=True means the laser should fire DURING the move
                  TO this point. laser_on=False means reposition with laser off.
        """
        if not path:
            return

        # Move to the first point with laser off (initial positioning)
        first_point, _ = path[0]
        logger.info("Moving to start position %s", list(first_point))
        self._laser.disable_output()
        self._stage.move_to(first_point)

        for i in range(1, len(path)):
            target_point, should_print = path[i]
            prev_point = path[i - 1][0]

            if should_print:
                self._execute_print_move(prev_point, target_point)
            else:
                self._execute_reposition(target_point)

    def execute_print_line(self, start: Sequence[float],
                           end: Sequence[float]) -> None:
        """Execute a single print line: move from start to end with laser on
        during the constant-velocity region.

        This is the atomic operation that the synchronization strategy affects.
        """
        if self._strategy == "sequential":
            self._print_line_sequential(start, end)
        elif self._strategy == "velocity_timed":
            self._print_line_velocity_timed(start, end)
        else:
            raise ValueError(f"Unknown synchronization strategy: {self._strategy}")

    def add_calibration(self, point: CalibrationPoint) -> None:
        """Add or update a calibration point from a motor profiling experiment."""
        # Replace existing calibration for the same velocity if present
        self._calibration = [
            c for c in self._calibration
            if abs(c.velocity_mm_s - point.velocity_mm_s) > 0.01
        ]
        self._calibration.append(point)
        self._calibration.sort(key=lambda c: c.velocity_mm_s)
        logger.info("Calibration updated: %s", point)

    # -- Strategy implementations -----------------------------------------

    def _execute_print_move(self, from_point: Sequence[float],
                            to_point: Sequence[float]) -> None:
        """Move with laser firing during constant-velocity region."""
        if self._strategy == "sequential":
            self._print_line_sequential(from_point, to_point)
        elif self._strategy == "velocity_timed":
            self._print_line_velocity_timed(from_point, to_point)

    def _execute_reposition(self, target: Sequence[float]) -> None:
        """Move to a new position with laser off (repositioning)."""
        self._laser.disable_output()
        self._stage.move_to(target)

    def _print_line_sequential(self, start: Sequence[float],
                               end: Sequence[float]) -> None:
        """Sequential strategy: laser on, move, wait, laser off.

        Simple but inaccurate — the laser fires during acceleration and
        deceleration, causing non-uniform exposure at line endpoints.
        Acceptable for testing, not for production prints.
        """
        self._laser.enable_output()
        self._stage.move_to(end)
        self._laser.disable_output()

    def _print_line_velocity_timed(self, start: Sequence[float],
                                   end: Sequence[float]) -> None:
        """Velocity-timed strategy: fire laser only during constant-velocity window.

        Uses calibrated acceleration timing to compute when to enable and
        disable the laser during a non-blocking move.

        Timing diagram for a single line:
            |-- accel --|-- constant velocity --|-- decel --|
            ^           ^                       ^           ^
            motion      laser ON                laser OFF   motion
            start                                           end

        The accel_time and decel_time come from the calibration table.
        We assume symmetric acceleration/deceleration profiles.
        """
        import numpy as np

        # Compute line length and direction
        start_arr = np.array(start, dtype=float)
        end_arr = np.array(end, dtype=float)
        line_vec = end_arr - start_arr
        line_length = float(np.linalg.norm(line_vec))

        if line_length < 1e-6:
            logger.warning("Zero-length print line, skipping.")
            return

        # Get the current target velocity (X-axis primary)
        target_velocity = self._stage._velocities[0]
        accel_time, accel_distance = self._get_accel_params(target_velocity)

        # Compute timing for the constant-velocity region
        # total_time ≈ accel_time + (line_length - 2*accel_distance)/velocity + accel_time
        coast_distance = line_length - 2 * accel_distance
        if coast_distance <= 0:
            # Line is too short for the stage to reach full speed.
            # The stage will do a triangular velocity profile (accel then immediately decel).
            # Printing here is unreliable — warn and use sequential fallback.
            logger.warning(
                "Line length %.3f mm is shorter than acceleration distance "
                "%.3f mm x 2. Falling back to sequential strategy for this line.",
                line_length, accel_distance
            )
            self._print_line_sequential(start, end)
            return

        coast_time = coast_distance / target_velocity

        # Start non-blocking motion
        self._stage.move_to_non_blocking(list(end))

        # Wait through acceleration phase, then fire laser
        time.sleep(accel_time)
        self._laser.enable_output()

        # Keep laser on through constant-velocity phase
        time.sleep(coast_time)
        self._laser.disable_output()

        # Wait for motion to complete (deceleration)
        self._stage.wait_for_motion()

    def _get_accel_params(self, target_velocity: float) -> tuple[float, float]:
        """Look up or interpolate acceleration time and distance for a velocity.

        Returns:
            (accel_time_s, accel_distance_mm) tuple.
        """
        if not self._calibration:
            logger.warning(
                "No calibration data available. Using default estimate: "
                "accel_time = 0.35s, accel_distance = velocity * 0.35 * 0.5"
            )
            # Rough estimate: assume constant acceleration over ~0.35s
            est_time = 0.35
            est_dist = target_velocity * est_time * 0.5
            return est_time, est_dist

        # Exact match
        for cal in self._calibration:
            if abs(cal.velocity_mm_s - target_velocity) < 0.01:
                return cal.accel_time_s, cal.accel_distance_mm

        # Interpolate between two nearest calibration points
        import numpy as np
        vels = [c.velocity_mm_s for c in self._calibration]
        times = [c.accel_time_s for c in self._calibration]
        dists = [c.accel_distance_mm for c in self._calibration]

        interp_time = float(np.interp(target_velocity, vels, times))
        interp_dist = float(np.interp(target_velocity, vels, dists))

        logger.debug(
            "Interpolated accel params for %.1f mm/s: time=%.4fs, dist=%.4fmm",
            target_velocity, interp_time, interp_dist
        )
        return interp_time, interp_dist
