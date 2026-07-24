"""Layer 2 synchronizer - coordinated laser + stage motion.

The important timing facts we measured on this setup:

    Laser POST (enable/close)  : ~10-50 ms round trip
    Laser physical settle      : ~150-350 ms AFTER POST returns
    Stage GetFPosition         : ~0.18 ms (100x faster than laser)

That means the laser command latency dominates. To fire uniformly across
the constant-velocity portion of a print line we must:

    1. Send the ON POST *before* the stage reaches the print-start point.
    2. Wait for the stage acceleration to finish.
    3. Let the stage traverse the coast region.
    4. Send the OFF POST *before* the stage reaches the print-end point.
    5. Wait for deceleration.

This module exposes three strategies:

    - "sequential": move, wait, toggle laser. Simple; laser fires while
      the stage is stationary between moves. Fine for positioning tests.
    - "velocity_timed": non-blocking move + calibrated sleeps that account
      for both acceleration time AND laser pre-fire lead time.
    - "hardware_trigger": stub for future SPiiPlus TTL trigger support.

Path convention (see `path_generation.coordinates`):
    path[i] = (point_i, laser_on_during_move_from_point_{i-1}_to_point_i)
    path[0].laser is ignored; path[0] is treated as initial reposition.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

import numpy as np

if TYPE_CHECKING:  # hints only — keeps this module importable without SPiiPlusPython
    from laser_printing.controllers.laser import LaserController
    from laser_printing.controllers.stage import StageController

logger = logging.getLogger(__name__)


@dataclass
class CalibrationPoint:
    """Measured acceleration behaviour at a given commanded velocity."""
    velocity_mm_s: float
    accel_time_s: float
    accel_distance_mm: float


@dataclass
class LaserLatency:
    """Measured laser toggle latency (both on and off settle windows).

    The synchronizer uses `on_lead_s` as the time to pre-fire the ON POST
    before the desired "laser-on at point X" instant. Same for off.
    """
    on_lead_s: float = 0.20   # default from measured settle ~200-270 ms
    off_lead_s: float = 0.20
    settle_timeout_s: float = 1.0


class PrintSynchronizer:
    """Coordinates laser firing with stage motion for uniform exposure."""

    def __init__(
        self,
        laser: LaserController,
        stage: StageController,
        strategy: str = "velocity_timed",
        velocity_tolerance: float = 0.05,
        calibration: Sequence[CalibrationPoint] | None = None,
        laser_latency: LaserLatency | None = None,
    ):
        self._laser = laser
        self._stage = stage
        self._strategy = strategy
        self._velocity_tolerance = float(velocity_tolerance)
        self._calibration: list[CalibrationPoint] = list(calibration or [])
        self._calibration.sort(key=lambda c: c.velocity_mm_s)
        self._lat = laser_latency or LaserLatency()

    @classmethod
    def from_config(cls, config: dict, laser: LaserController,
                    stage: StageController) -> "PrintSynchronizer":
        sync_cfg = config.get("synchronization", {})
        cal = [CalibrationPoint(**p)
               for p in sync_cfg.get("calibration", [])]
        lat = LaserLatency(
            on_lead_s=sync_cfg.get("laser_on_lead_s", 0.20),
            off_lead_s=sync_cfg.get("laser_off_lead_s", 0.20),
            settle_timeout_s=sync_cfg.get("laser_settle_timeout_s", 1.0),
        )
        return cls(
            laser=laser, stage=stage,
            strategy=sync_cfg.get("strategy", "velocity_timed"),
            velocity_tolerance=sync_cfg.get("velocity_tolerance_fraction", 0.05),
            calibration=cal,
            laser_latency=lat,
        )

    # -- Public API -------------------------------------------------------

    def execute_path(self, path: list[tuple[Sequence[float], bool]]) -> None:
        """Execute a canonical path (see module docstring for convention).

        Path is a list of `(point, laser_on)` tuples where `laser_on` is
        the laser state during the segment arriving at `point`. `path[0]`
        is treated as an initial reposition with laser off.
        """
        if not path:
            return

        first_point, _ = path[0]
        self._laser.off()  # guarantee off before the first move
        # Path moves are intentional, range-checked geometry — use the full
        # travel span as the clamp. clamp_mm=None would apply the DEFAULT
        # 5 mm typo clamp and fault any longer repositioning or line.
        self._stage.move_absolute(first_point, clamp_mm=self._stage.range_span_mm)

        for i in range(1, len(path)):
            target, laser_on = path[i]
            prev = path[i - 1][0]
            if laser_on:
                self._print_segment(prev, target)
            else:
                self._reposition(target)

        # Always leave the laser off after a path.
        self._laser.off()

    def execute_print_line(self, start: Sequence[float],
                           end: Sequence[float]) -> None:
        """Print a single line start -> end with the configured strategy."""
        self._print_segment(start, end)

    def add_calibration(self, point: CalibrationPoint) -> None:
        """Insert or replace the calibration at `point.velocity_mm_s`."""
        self._calibration = [c for c in self._calibration
                             if abs(c.velocity_mm_s - point.velocity_mm_s) > 0.01]
        self._calibration.append(point)
        self._calibration.sort(key=lambda c: c.velocity_mm_s)

    # -- Strategy dispatch -----------------------------------------------

    def _print_segment(self, start: Sequence[float], end: Sequence[float]) -> None:
        if self._strategy == "sequential":
            self._print_sequential(start, end)
        elif self._strategy == "velocity_timed":
            self._print_velocity_timed(start, end)
        elif self._strategy == "hardware_trigger":
            raise NotImplementedError("hardware_trigger strategy not yet wired up")
        else:
            raise ValueError(f"Unknown strategy: {self._strategy}")

    def _reposition(self, target: Sequence[float]) -> None:
        self._laser.off()
        self._stage.move_absolute(target, clamp_mm=self._stage.range_span_mm)

    # -- Strategy implementations ----------------------------------------

    def _print_sequential(self, start: Sequence[float],
                          end: Sequence[float]) -> None:
        """Move with laser on; no timing compensation.

        Laser fires during accel AND decel - non-uniform exposure at line
        endpoints. Use for positioning tests, not production prints.
        """
        self._laser.on()
        self._stage.move_absolute(end, clamp_mm=self._stage.range_span_mm)
        self._laser.off()

    def _print_velocity_timed(self, start: Sequence[float],
                              end: Sequence[float]) -> None:
        """Time the laser to fire only in the constant-velocity window.

        The timeline for a single segment of length L at velocity v is:

            motion start .....laser_on POST ..... accel done .......
                          |<-- on_lead_s -->|<------ coast ------>|...
                          |                                        |
                          |         laser OFF POST --->| ....      |...
                          |                  <- off_lead_s ->|     |
                          |                                        |
                          ........................................ motion end

        If the calibrated accel_distance does not fit inside L then the
        stage can't reach full velocity - we fall back to sequential.
        """
        start_arr = np.asarray(start, dtype=float)
        end_arr = np.asarray(end, dtype=float)
        line_vec = end_arr - start_arr
        line_length = float(np.linalg.norm(line_vec))
        if line_length < 1e-6:
            logger.warning("Zero-length print segment; skipping.")
            return

        # Commanded velocity along the path direction:
        # use axis-wise velocity components projected onto the path.
        vel_by_axis = np.array(
            [self._stage.current_velocity_setpoint(ax)
             for ax in self._stage.axes[:len(line_vec)]],
            dtype=float,
        )
        # Dominant axis velocity along the segment direction:
        direction = line_vec / line_length
        # Effective path velocity is min-axis-ratio-limited: if X moves
        # dx and has velocity vx, the path traverses dx at most at vx.
        # That's limited by min(|vel_i / dir_i|) over non-zero direction
        # components.
        with np.errstate(divide="ignore", invalid="ignore"):
            ratios = np.where(np.abs(direction) > 1e-9,
                              vel_by_axis / np.abs(direction),
                              np.inf)
        path_velocity = float(np.min(ratios))
        if not np.isfinite(path_velocity) or path_velocity <= 0:
            path_velocity = float(max(vel_by_axis))

        accel_time, accel_distance = self._get_accel_params(path_velocity)

        coast_distance = line_length - 2 * accel_distance
        if coast_distance <= 0:
            logger.warning(
                "Line length %.3f mm too short for accel distance %.3f mm "
                "x 2 at velocity %.2f mm/s; falling back to sequential.",
                line_length, accel_distance, path_velocity,
            )
            self._print_sequential(start, end)
            return
        coast_time = coast_distance / path_velocity

        # Pre-fire ON: start the POST so that by the time the laser
        # actually turns on, the stage is in the coast region.
        # Target: laser physically on at t0 + accel_time.
        # Send POST at t0 + max(0, accel_time - on_lead_s).
        on_delay = max(0.0, accel_time - self._lat.on_lead_s)
        # Similarly send OFF POST before the coast region ends.
        off_after_on = coast_time - (self._lat.on_lead_s - self._lat.off_lead_s)
        off_after_on = max(0.0, off_after_on)

        # Launch the non-blocking motion.
        self._stage.move_absolute_async(end, clamp_mm=self._stage.range_span_mm)
        t0 = time.perf_counter()

        # Wait until it's time to send the ON POST.
        _precise_sleep(on_delay)
        self._laser.on()

        # Wait through coast - but account for the ON POST duration that
        # already elapsed. on_delay + laser.on() took at least
        # (on_delay + on_lead_s). Subtract that from coast_time so we
        # end up firing OFF at the right moment.
        elapsed = time.perf_counter() - t0
        target_off_time = accel_time + coast_time - self._lat.off_lead_s
        _precise_sleep(max(0.0, target_off_time - elapsed))
        self._laser.off()

        # Wait for the motion to finish (deceleration phase).
        self._stage.wait_for_motion()

    # -- Calibration lookup ----------------------------------------------

    def _get_accel_params(self, velocity: float) -> tuple[float, float]:
        """Return (accel_time_s, accel_distance_mm) for the commanded velocity.

        If no calibration exists, fall back to a conservative estimate.
        If the velocity is outside the calibration range we clamp to the
        nearest endpoint (no extrapolation).
        """
        if not self._calibration:
            logger.warning(
                "No calibration; using fallback accel_time=0.35s, "
                "accel_distance=0.5*v*t"
            )
            t = 0.35
            return t, 0.5 * velocity * t

        for c in self._calibration:
            if abs(c.velocity_mm_s - velocity) < 0.01:
                return c.accel_time_s, c.accel_distance_mm

        vs = [c.velocity_mm_s for c in self._calibration]
        ts = [c.accel_time_s for c in self._calibration]
        ds = [c.accel_distance_mm for c in self._calibration]
        # np.interp clamps at the endpoints, which is what we want.
        return float(np.interp(velocity, vs, ts)), float(np.interp(velocity, vs, ds))


# ---- high-resolution sleep --------------------------------------------

def _precise_sleep(dt: float) -> None:
    """Sleep that is accurate to a few ms on Windows.

    `time.sleep()` has ~15 ms resolution on Windows. We coarse-sleep most
    of the duration, then busy-wait the last 2 ms.
    """
    if dt <= 0:
        return
    deadline = time.perf_counter() + dt
    if dt > 0.003:
        time.sleep(dt - 0.002)
    while time.perf_counter() < deadline:
        pass
