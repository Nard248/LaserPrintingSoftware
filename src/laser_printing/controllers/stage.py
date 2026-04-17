"""Layer 1 stage primitives.

Builds friendly primitive actions on top of the raw SPiiPlus transport in
`laser_printing.hardware.stage_tcp`:

    stage.connect()
    stage.position()             -> [x, y, z] mm
    stage.move_absolute([x,y,z]) -> safe clamp, blocks until done
    stage.move_relative([dx,dy,dz])
    stage.jog(axis, distance_mm)
    stage.set_velocity(v)        # scalar or per-axis
    stage.record_profile(...)    # data collection for calibration
    stage.disconnect()           # always returns to HOME_POSITION first

Home is **always [0, 0, 0] mm** (project convention). Every connect moves
there first; every disconnect moves there before closing. Clamp defaults
to 5 mm per axis - enough for any normal experiment - but individual calls
can lower it (e.g. jog tests use 0.2 mm).
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Sequence

import numpy as np

from laser_printing.hardware.stage_tcp import StageTcp

logger = logging.getLogger(__name__)

HOME_POSITION: list[float] = [0.0, 0.0, 0.0]
DEFAULT_MAX_STEP_MM = 5.0


class StageSafetyError(RuntimeError):
    """Raised when a move exceeds the per-axis clamp."""


class StageController:
    """Layer 1 primitives for the 3-axis SPiiPlus stage.

    Example:
        with StageController.from_config(config["stage"]) as stage:
            stage.set_velocity(2.0)
            stage.move_absolute([1, 0, 0])     # move to X=1 mm
            stage.move_relative([0, 0.1, 0])   # +100 um in Y
            p = stage.position()
    """

    def __init__(
        self,
        ip: str = StageTcp.DEFAULT_IP,
        port: int = StageTcp.DEFAULT_PORT,
        axes: Sequence[int] = (0, 1, 2),
        commutation_axes: Sequence[int] = (0, 1),
        default_velocity: Sequence[float] | float = 1.0,
        max_step_mm: float = DEFAULT_MAX_STEP_MM,
        range_mm: tuple[float, float] = (-25.0, 25.0),
        wait_timeout_ms: int = 30_000,
    ):
        self._tcp = StageTcp(ip, port)
        self._axes = list(axes)
        self._commutation_axes = set(commutation_axes)
        self._range_mm = tuple(range_mm)
        self._max_step_mm = float(max_step_mm)
        self._wait_timeout_ms = int(wait_timeout_ms)

        if isinstance(default_velocity, (int, float)):
            self._velocities = [float(default_velocity)] * len(self._axes)
        else:
            self._velocities = [float(v) for v in default_velocity]
        if len(self._velocities) != len(self._axes):
            raise ValueError(
                f"default_velocity length {len(self._velocities)} "
                f"does not match axes length {len(self._axes)}"
            )
        self._velocity_cache: list[float | None] = [None] * len(self._axes)

    @classmethod
    def from_config(cls, cfg: dict) -> "StageController":
        return cls(
            ip=cfg["ip"],
            port=cfg.get("port", StageTcp.DEFAULT_PORT),
            axes=cfg.get("axes", (0, 1, 2)),
            commutation_axes=cfg.get("commutation_axes", (0, 1)),
            default_velocity=cfg.get("default_velocity", 1.0),
            max_step_mm=cfg.get("max_step_mm", DEFAULT_MAX_STEP_MM),
            range_mm=tuple(cfg.get("range_mm", (-25.0, 25.0))),
        )

    # -- Properties ------------------------------------------------------

    @property
    def axes(self) -> list[int]:
        return list(self._axes)

    @property
    def range_mm(self) -> tuple[float, float]:
        return self._range_mm

    @property
    def max_step_mm(self) -> float:
        return self._max_step_mm

    @property
    def tcp(self) -> StageTcp:
        """Escape hatch to the raw transport for advanced operations."""
        return self._tcp

    # -- Lifecycle -------------------------------------------------------

    def connect(self, home: Sequence[float] | None = None) -> None:
        """Open TCP, enable+commutate axes, set default velocities,
        then move to HOME_POSITION.

        `home` override is intentionally NOT supported from this signature
        to avoid foot-guns - the project convention is literal [0, 0, 0].
        The kwarg is kept for API compatibility but must equal HOME_POSITION
        if provided.
        """
        if home is not None and list(home) != HOME_POSITION:
            raise ValueError(
                "Home position is fixed at [0, 0, 0] for this project "
                f"(got {list(home)})."
            )
        self._tcp.open()
        logger.info("Stage TCP opened at %s:%d", self._tcp.ip, self._tcp.port)

        # Enable all axes + commutate brushless ones.
        self._tcp.enable_axes(self._axes)
        for axis in self._axes:
            self._tcp.wait_motor_enabled(axis, timeout_ms=-1)
            if axis in self._commutation_axes:
                self._tcp.commutate(axis)
                logger.debug("Axis %d commutated", axis)

        # Apply default velocities.
        self.set_velocity(self._velocities)

        # Drive to [0, 0, 0] using clamp-free absolute move (initial
        # homing may need a larger step than the usual clamp allows).
        logger.info("Homing stage to %s", HOME_POSITION)
        self._move_absolute_unchecked(HOME_POSITION)

    def disconnect(self) -> None:
        """Return to HOME_POSITION (unchecked) and close the TCP."""
        if self._tcp._handler is None:
            return
        try:
            logger.info("Stage returning to HOME %s before disconnect",
                        HOME_POSITION)
            self._move_absolute_unchecked(HOME_POSITION)
        except Exception:
            logger.warning("Failed to home on disconnect", exc_info=True)
        finally:
            self._tcp.close()
            logger.info("Stage TCP closed")

    def __enter__(self) -> "StageController":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()

    # -- Feedback --------------------------------------------------------

    def position(self) -> list[float]:
        return [self._tcp.get_fposition(axis) for axis in self._axes]

    def velocity_feedback(self) -> list[float]:
        return [self._tcp.get_fvelocity(axis) for axis in self._axes]

    # -- Velocity / profile ----------------------------------------------

    def set_velocity(self, v: Sequence[float] | float) -> None:
        """Set velocity on every axis.

        `v` is either a scalar (applied to all) or a list matching `axes`.
        Values are cached so redundant updates are skipped.
        """
        if isinstance(v, (int, float)):
            vs = [float(v)] * len(self._axes)
        else:
            vs = [float(x) for x in v]
        if len(vs) != len(self._axes):
            raise ValueError(
                f"set_velocity: expected {len(self._axes)} values, got {len(vs)}"
            )
        for i, axis in enumerate(self._axes):
            if self._velocity_cache[i] != vs[i]:
                self._tcp.set_velocity(axis, vs[i])
                self._velocity_cache[i] = vs[i]
        self._velocities = vs

    def set_acceleration(self, axis: int, a_mm_s2: float) -> None:
        self._tcp.set_acceleration(axis, a_mm_s2)

    def set_jerk(self, axis: int, j_mm_s3: float) -> None:
        self._tcp.set_jerk(axis, j_mm_s3)

    def current_velocity_setpoint(self, axis: int | None = None):
        """Velocity last set (by axis index, or list if None)."""
        if axis is None:
            return list(self._velocities)
        return self._velocities[self._axes.index(axis)]

    # -- Motion commands -------------------------------------------------

    def move_absolute(self, target: Sequence[float],
                      clamp_mm: float | None = None,
                      wait: bool = True) -> None:
        """Move to absolute [x,y,z] with safety clamp.

        Args:
            target: destination in mm.
            clamp_mm: maximum per-axis step allowed (defaults to instance
                `max_step_mm`). Raises `StageSafetyError` if exceeded.
            wait: block until motion completes when True.
        """
        target = [float(t) for t in target]
        if len(target) != len(self._axes):
            raise ValueError(
                f"target has {len(target)} coords, expected {len(self._axes)}"
            )
        current = self.position()
        self._check_range(target)
        self._check_step(current, target, clamp_mm)

        self._tcp.to_point(self._axes, target)
        self._tcp.go(self._axes)
        if wait:
            self._wait_for_motion()

    def move_relative(self, delta: Sequence[float],
                      clamp_mm: float | None = None,
                      wait: bool = True) -> None:
        """Move by [dx,dy,dz] with safety clamp."""
        delta = [float(d) for d in delta]
        if len(delta) != len(self._axes):
            raise ValueError(
                f"delta has {len(delta)} coords, expected {len(self._axes)}"
            )
        current = self.position()
        target = [c + d for c, d in zip(current, delta)]
        self.move_absolute(target, clamp_mm=clamp_mm, wait=wait)

    def jog(self, axis: int, distance_mm: float,
            clamp_mm: float | None = None, wait: bool = True) -> None:
        """Move a single axis by the signed `distance_mm`.

        Convenience wrapper over `move_relative`. Other axes stay put.
        """
        if axis not in self._axes:
            raise ValueError(f"Unknown axis {axis}; active axes are {self._axes}")
        delta = [0.0] * len(self._axes)
        delta[self._axes.index(axis)] = float(distance_mm)
        self.move_relative(delta, clamp_mm=clamp_mm, wait=wait)

    def move_absolute_async(self, target: Sequence[float],
                            clamp_mm: float | None = None):
        """Start an absolute move without waiting. Returns the waitblock."""
        target = [float(t) for t in target]
        self._check_range(target)
        self._check_step(self.position(), target, clamp_mm)
        self._tcp.to_point(self._axes, target)
        wb = self._tcp.make_waitblock()
        self._tcp.go(self._axes, waitblock=wb)
        return wb

    def wait_for_motion(self) -> None:
        self._wait_for_motion()

    # -- Data collection -------------------------------------------------

    def record_profile(self, n_samples: int, period: int = 1,
                       variables: str = "FVEL(0), FPOS(0)",
                       array_name: str = "_profile_buf") -> np.ndarray:
        """Declare an on-controller array, start collection, read it back.

        Call this AFTER starting a non-blocking move so the collection
        captures the ramp. Returns numpy array of shape (n_vars, n_samples).
        """
        n_vars = variables.count(",") + 1
        self._tcp.declare_real_array(array_name, n_vars, n_samples)
        self._tcp.start_data_collection(array_name, n_samples, period, variables)
        return self._tcp.read_real_array(array_name, n_vars, n_samples)

    # -- Internal helpers ------------------------------------------------

    def _wait_for_motion(self) -> None:
        for axis in self._axes:
            self._tcp.wait_motion_end(axis, self._wait_timeout_ms)

    def _check_range(self, target: Sequence[float]) -> None:
        lo, hi = self._range_mm
        for i, (axis, t) in enumerate(zip(self._axes, target)):
            if not (lo <= t <= hi):
                raise StageSafetyError(
                    f"Target axis {axis} = {t:.3f} mm is outside "
                    f"physical range [{lo}, {hi}]"
                )

    def _check_step(self, current: Sequence[float], target: Sequence[float],
                    clamp_mm: float | None) -> None:
        limit = self._max_step_mm if clamp_mm is None else float(clamp_mm)
        for i, (c, t) in enumerate(zip(current, target)):
            step = abs(t - c)
            if step > limit + 1e-9:
                raise StageSafetyError(
                    f"Refusing to move axis {self._axes[i]}: step "
                    f"{step:.4f} mm exceeds clamp {limit:.4f} mm "
                    f"(current={c:.4f}, target={t:.4f})"
                )

    def _move_absolute_unchecked(self, target: Sequence[float]) -> None:
        """Absolute move without the per-axis clamp (still range-checked).

        Used only for the dedicated home sequence, where the initial
        distance from the current position can exceed a normal clamp.
        """
        target = [float(t) for t in target]
        self._check_range(target)
        self._tcp.to_point(self._axes, target)
        self._tcp.go(self._axes)
        self._wait_for_motion()
