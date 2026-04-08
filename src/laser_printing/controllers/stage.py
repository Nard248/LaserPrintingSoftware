"""
Controller for the ACS SPiiPlus motion stage via Ethernet TCP.

The stage has 3 axes (X=0, Y=1, Z=2) with +-25mm range and ~1 micron
precision. Axes 0 and 1 are brushless linear motors that require
commutation at startup. The controller communicates over Ethernet TCP
using the SPiiPlusPython library (C bindings).

Hardware: Standa 8MTL120XY stage, ACS SPiiPlus controller
Protocol: Ethernet TCP at <ip>:<port> via SPiiPlusPython library

SPiiPlus multi-axis motion notes:
    - Axes list must end with -1 as a termination marker for coordinated moves.
    - ACSC_AMF_WAIT flag means "wait for previous motion to finish before starting".
    - GoM() starts the buffered move; WaitMotionEnd() blocks until arrival.
    - Commutation is required once after power-on for brushless axes (0, 1).
"""

from __future__ import annotations

import logging
from typing import Sequence

import SPiiPlusPython as sp

logger = logging.getLogger(__name__)


class StageController:
    """Controls the ACS SPiiPlus 3-axis motion stage.

    Usage:
        with StageController.from_config(config["stage"]) as stage:
            stage.set_velocity([5.0, 5.0, 5.0])
            stage.move_to([1.0, 2.0, 6.0])
            pos = stage.get_position()
    """

    def __init__(self, ip: str, port: int, axes: list[int],
                 commutation_axes: list[int] | None = None,
                 default_velocity: list[float] | None = None):
        """Initialize controller (does NOT connect yet).

        Args:
            ip: Controller IP address (e.g. "10.0.0.100").
            port: Controller TCP port (e.g. 701).
            axes: List of axis indices to control, e.g. [0, 1, 2] for X, Y, Z.
            commutation_axes: Axes requiring brushless motor commutation (default: [0, 1]).
            default_velocity: Default velocity per axis in mm/s (default: [1, 1, 1]).
        """
        self._ip = ip
        self._port = port
        self._axes = list(axes)
        # SPiiPlus requires -1 terminator for multi-axis coordinated motion
        self._axes_with_term = self._axes + [-1]
        self._commutation_axes = commutation_axes if commutation_axes is not None else [0, 1]
        self._default_velocity = default_velocity or [1.0] * len(axes)
        self._handler = None
        self._velocities: list[float] = [0.0] * len(axes)
        self._home_position: list[float] = [0.0] * len(axes)

    @classmethod
    def from_config(cls, stage_config: dict) -> StageController:
        """Create a StageController from the 'stage' section of the config."""
        return cls(
            ip=stage_config["ip"],
            port=stage_config["port"],
            axes=stage_config["axes"],
            commutation_axes=stage_config.get("commutation_axes", [0, 1]),
            default_velocity=stage_config.get("default_velocity"),
        )

    # -- Lifecycle --------------------------------------------------------

    def connect(self, home_position: list[float] | None = None) -> None:
        """Connect to the controller, enable and commutate axes.

        Args:
            home_position: Position to return to on cleanup. Defaults to
                current position at connect time (all zeros if stage was just homed).
        """
        logger.info("Connecting to stage at %s:%d ...", self._ip, self._port)
        self._handler = sp.OpenCommEthernetTCP(self._ip, self._port)

        self._enable_axes()
        self.set_velocity(self._default_velocity)

        if home_position is not None:
            self._home_position = list(home_position)
        else:
            self._home_position = self.get_position()

        logger.info("Stage connected. Home position: %s", self._home_position)

    def disconnect(self) -> None:
        """Return to home position and close the connection."""
        if self._handler is None:
            return
        try:
            logger.info("Stage returning to home position %s ...", self._home_position)
            self.move_to(self._home_position)
        except Exception:
            logger.warning("Failed to return to home during disconnect.", exc_info=True)
        try:
            sp.CloseComm(self._handler, failure_check=True)
            logger.info("Stage connection closed.")
        except Exception:
            logger.warning("Failed to close stage connection.", exc_info=True)
        self._handler = None

    def __enter__(self) -> StageController:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()

    @property
    def handler(self):
        """Raw SPiiPlus communication handle (for advanced/direct use)."""
        if self._handler is None:
            raise RuntimeError("Stage not connected. Call connect() first.")
        return self._handler

    @property
    def axes(self) -> list[int]:
        """Active axis indices (without the -1 terminator)."""
        return list(self._axes)

    @property
    def axes_with_terminator(self) -> list[int]:
        """Axis indices with -1 terminator required by SPiiPlus for multi-axis moves."""
        return list(self._axes_with_term)

    # -- Motion commands --------------------------------------------------

    def set_velocity(self, velocities: Sequence[float] | float) -> None:
        """Set axis velocities in mm/s.

        Args:
            velocities: Either a single value (applied to all axes) or a
                list with one value per axis.
        """
        if isinstance(velocities, (int, float)):
            velocities = [float(velocities)] * len(self._axes)
        velocities = [float(v) for v in velocities]

        if velocities == self._velocities:
            return

        for axis, vel in zip(self._axes, velocities):
            sp.SetVelocity(self.handler, axis, vel, sp.SYNCHRONOUS, True)
        self._velocities = velocities
        logger.debug("Velocities set to %s mm/s", velocities)

    def set_acceleration(self, axis: int, acceleration: float) -> None:
        """Set acceleration for a single axis (mm/s^2).

        Note: Not all SPiiPlus firmware versions expose SetAcceleration.
        Falls back with a warning if unavailable.
        """
        if hasattr(sp, "SetAcceleration"):
            sp.SetAcceleration(self.handler, axis, acceleration, sp.SYNCHRONOUS, True)
            logger.debug("Acceleration for axis %d set to %.1f mm/s^2", axis, acceleration)
        else:
            logger.warning(
                "sp.SetAcceleration not available in this SPiiPlusPython version. "
                "Set acceleration via the SPiiPlus IDE instead."
            )

    def set_jerk(self, axis: int, jerk: float) -> None:
        """Set jerk for a single axis (mm/s^3).

        Note: Not all SPiiPlus firmware versions expose SetJerk.
        Falls back with a warning if unavailable.
        """
        if hasattr(sp, "SetJerk"):
            sp.SetJerk(self.handler, axis, jerk, sp.SYNCHRONOUS, True)
            logger.debug("Jerk for axis %d set to %.1f mm/s^3", axis, jerk)
        else:
            logger.warning(
                "sp.SetJerk not available in this SPiiPlusPython version. "
                "Set jerk via the SPiiPlus IDE instead."
            )

    def move_to(self, point: Sequence[float], wait: bool = True) -> None:
        """Move all axes to the given position.

        Args:
            point: Target position [x, y, z] in mm.
            wait: If True (default), block until motion completes.
        """
        point = [float(p) for p in point]
        sp.ToPointM(
            self.handler, sp.MotionFlags.ACSC_AMF_WAIT,
            self._axes_with_term, point, sp.SYNCHRONOUS, True
        )
        sp.GoM(self.handler, self._axes_with_term, sp.SYNCHRONOUS, True)

        if wait:
            self.wait_for_motion()

    def move_to_non_blocking(self, point: Sequence[float]) -> sp.ACSC_WAITBLOCK:
        """Start a move without waiting for completion.

        Returns the waitblock handle that can be used to check completion.
        This is essential for the synchronizer — it starts the move, then
        times the laser on/off during the constant-velocity region.

        Args:
            point: Target position [x, y, z] in mm.

        Returns:
            SPiiPlus waitblock handle for the motion.
        """
        point = [float(p) for p in point]
        sp.ToPointM(
            self.handler, sp.MotionFlags.ACSC_AMF_WAIT,
            self._axes_with_term, point, sp.SYNCHRONOUS, True
        )
        waitblock = sp.ACSC_WAITBLOCK()
        sp.GoM(self.handler, self._axes_with_term, waitblock, True)
        return waitblock

    def wait_for_motion(self, timeout_ms: int = -1) -> None:
        """Block until all axes have finished moving.

        Args:
            timeout_ms: Timeout in milliseconds. -1 = wait indefinitely.
        """
        for axis in self._axes:
            sp.WaitMotionEnd(self.handler, axis, timeout_ms, True)

    def get_position(self) -> list[float]:
        """Read the current position of all axes (mm)."""
        position = []
        for axis in self._axes:
            pos = sp.GetFPosition(self.handler, axis, sp.SYNCHRONOUS, True)
            position.append(float(pos))
        return position

    def get_velocity_feedback(self) -> list[float]:
        """Read the current actual velocity of all axes (mm/s)."""
        velocities = []
        for axis in self._axes:
            vel = sp.GetFVelocity(self.handler, axis, sp.SYNCHRONOUS, False)
            velocities.append(float(vel))
        return velocities

    # -- Data collection (for motion profiling) ---------------------------

    def declare_data_array(self, name: str, rows: int, cols: int) -> None:
        """Declare a real-valued array on the controller for data collection.

        Args:
            name: Variable name (e.g. "profile_data").
            rows: Number of rows (typically 2 for [velocity, position]).
            cols: Number of columns (number of samples).
        """
        try:
            sp.DeclareVariable(
                handle=self.handler, type=4,
                name=f"{name}({rows})({cols})",
                wait=sp.SYNCHRONOUS, failure_check=True
            )
        except Exception:
            # Variable may already exist from a previous run
            logger.debug("Array '%s' may already be declared, continuing.", name)

    def start_data_collection(self, array_name: str, n_samples: int,
                              period: int = 1,
                              variables: str = "FVEL(0), FPOS(0)") -> None:
        """Start collecting motion data into a controller-side array.

        Args:
            array_name: Name of the previously declared array.
            n_samples: Number of samples to collect.
            period: Sample period in servo cycles.
            variables: Comma-separated SPiiPlus variable names to record.
        """
        sp.DataCollectionExt(
            handle=self.handler, flags=0,
            axis=sp.Axis.ACSC_AXIS_0, array=array_name,
            nSample=n_samples, period=period,
            vars=variables, wait=sp.SYNCHRONOUS, failure_check=True
        )

    def read_data_array(self, array_name: str, rows: int,
                        cols: int) -> "numpy.ndarray":
        """Read a data array back from the controller.

        Returns:
            numpy array of shape (rows, cols).
        """
        import numpy as np
        data = sp.ReadReal(
            handle=self.handler,
            nBuf=sp.GeneralDefinition.ACSC_NONE,
            var=array_name,
            from1=0, to1=rows - 1,
            from2=0, to2=cols - 1,
            wait=sp.SYNCHRONOUS, failure_check=True
        )
        return np.array(data, dtype=float)

    # -- Internal ---------------------------------------------------------

    def _enable_axes(self) -> None:
        """Enable all axes and commutate brushless motors."""
        sp.EnableM(self.handler, self._axes_with_term, sp.SYNCHRONOUS, True)
        for axis in self._axes:
            sp.WaitMotorEnabled(self.handler, axis, 1, -1, True)
            if axis in self._commutation_axes:
                sp.CommutExt(self.handler, axis, -1, -1, -1, sp.SYNCHRONOUS, True)
                sp.WaitMotorCommutated(self.handler, axis, 1, -1, True)
                logger.debug("Axis %d commutated.", axis)
        logger.info("All axes enabled.")
