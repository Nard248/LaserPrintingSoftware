"""Exposure backends — HOW a write_line physically happens.

This is the one seam where the stage↔laser synchronization problem lives
(architecture proposal §5). The executor decides WHAT to expose and when
to abort; the backend decides HOW a single line is exposed:

    SimExposure  — explicit on/move/off against sim adapters (tests, CI,
                   agent-integration work). No timing model.
    SyncExposure — delegates to laser_printing's PrintSynchronizer, which
                   times laser POSTs against the stage's calibrated
                   acceleration profile (velocity_timed strategy).

Abort granularity for both: between repetitions. A single line traverse
is the atomic unit of exposure — interrupting mid-line would leave a
half-written feature AND is not physically abortable faster than the
laser-off settle time anyway.
"""

from __future__ import annotations

import threading
from typing import Protocol, Sequence

from .errors import DeviceError


class AbortSignal(Protocol):
    def is_set(self) -> bool: ...


class ExposureBackend(Protocol):
    def write_line(
        self,
        start_mm: Sequence[float],
        end_mm: Sequence[float],
        velocity_mm_s: float,
        repetitions: int,
        abort_flag: AbortSignal,
    ) -> bool:
        """Expose one line, zig-zag over repetitions.

        Returns True if completed, False if it stopped early because the
        abort flag was set (never mid-traverse). Laser is off on return.
        """


class SimExposure:
    """Explicit on/move/off sequence against the sim (or any) adapters."""

    def __init__(self, stage_adapter, laser_adapter) -> None:
        self._stage = stage_adapter
        self._laser = laser_adapter

    def write_line(self, start_mm, end_mm, velocity_mm_s, repetitions,
                   abort_flag) -> bool:
        self._stage.move_absolute(start_mm)  # reposition, laser off
        current, target = start_mm, end_mm
        for _ in range(repetitions):
            if abort_flag.is_set():
                return False
            self._laser.on()
            try:
                self._stage.move_absolute(target, velocity_mm_s=velocity_mm_s)
            finally:
                self._laser.off()  # never leave the beam on if the move faults
            current, target = target, current
        return True


class SyncExposure:
    """Synchronized exposure via laser_printing.PrintSynchronizer.

    Built lazily from the rig adapters' live controllers on first use
    (the controllers only exist after adapter.connect()). Each repetition
    runs as a canonical two-point path [(start, off), (end, on)] through
    execute_path, which handles repositioning, the velocity_timed firing
    window, sequential fallback for short lines, and laser-off-at-end.
    """

    def __init__(self, stage_adapter, laser_adapter, hardware_cfg: dict) -> None:
        self._stage_adapter = stage_adapter
        self._laser_adapter = laser_adapter
        self._hardware_cfg = hardware_cfg
        self._sync = None

    def _get_sync(self):
        if self._sync is None:
            from laser_printing.controllers.sync import PrintSynchronizer

            stage_ctrl = self._stage_adapter.controller
            laser_ctrl = self._laser_adapter.controller
            if stage_ctrl is None or laser_ctrl is None:
                raise DeviceError(
                    "sync exposure requires connected stage and laser adapters")
            self._sync = PrintSynchronizer.from_config(
                self._hardware_cfg, laser_ctrl, stage_ctrl)
        return self._sync

    def write_line(self, start_mm, end_mm, velocity_mm_s, repetitions,
                   abort_flag) -> bool:
        sync = self._get_sync()
        stage_ctrl = self._stage_adapter.controller
        # PrintSynchronizer reads the stage's velocity SETPOINTS to compute
        # the firing window — set the commanded write velocity first.
        stage_ctrl.set_velocity(float(velocity_mm_s))
        a, b = list(start_mm), list(end_mm)
        for _ in range(repetitions):
            if abort_flag.is_set():
                return False
            sync.execute_path([(a, False), (b, True)])  # reposition + print
            a, b = b, a  # zig-zag
        return True
