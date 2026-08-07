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

    def execute_path(
        self,
        path: Sequence[tuple[Sequence[float], bool]],
        velocity_mm_s: float,
        abort_flag: AbortSignal,
    ) -> bool:
        """Execute a canonical [(point, laser_on)] path (STL prints).

        Abort is honored between path chunks. Laser is off on return.
        """


class SimExposure:
    """Explicit on/move/off sequence against the sim (or any) adapters."""

    def __init__(self, stage_adapter, laser_adapter) -> None:
        self._stage = stage_adapter
        self._laser = laser_adapter

    def write_line(self, start_mm, end_mm, velocity_mm_s, repetitions,
                   abort_flag) -> bool:
        if abort_flag.is_set():
            return False
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

    def execute_path(self, path, velocity_mm_s, abort_flag,
                     abort_check_every: int = 16) -> bool:
        try:
            for i, (point, laser_on) in enumerate(path):
                if i % abort_check_every == 0 and abort_flag.is_set():
                    return False
                if laser_on:
                    self._laser.on()
                    try:
                        self._stage.move_absolute(point, velocity_mm_s=velocity_mm_s)
                    finally:
                        self._laser.off()
                else:
                    self._stage.move_absolute(point)  # travel velocity
            return True
        finally:
            self._laser.off()


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

    TRAVEL_VELOCITY_MM_S = 1.0

    def execute_path(self, path, velocity_mm_s, abort_flag,
                     chunk_size: int = 24) -> bool:
        """Run a canonical path through PrintSynchronizer in chunks so the
        abort flag is polled at least every chunk_size segments. Continuation
        chunks are prefixed with a reposition to their anchor point (a
        near-no-op, since the stage is already there)."""
        if abort_flag.is_set():
            return False
        sync = self._get_sync()
        stage_ctrl = self._stage_adapter.controller
        stage_ctrl.set_velocity(float(velocity_mm_s))
        try:
            for start in range(0, len(path), chunk_size):
                if abort_flag.is_set():
                    return False
                chunk = [(list(p), bool(on))
                         for p, on in path[start:start + chunk_size]]
                if start > 0:
                    chunk = [(list(path[start - 1][0]), False)] + chunk
                sync.execute_path(chunk)
            return True
        finally:
            stage_ctrl.set_velocity(self.TRAVEL_VELOCITY_MM_S)

    def write_line(self, start_mm, end_mm, velocity_mm_s, repetitions,
                   abort_flag) -> bool:
        if abort_flag.is_set():
            return False
        sync = self._get_sync()
        stage_ctrl = self._stage_adapter.controller
        # Reposition to the line start at TRAVEL velocity — a validated slow
        # write velocity (e.g. 0.1 mm/s) over a long approach would blow the
        # 30 s motion timeout if used for travel.
        stage_ctrl.set_velocity(self.TRAVEL_VELOCITY_MM_S)
        stage_ctrl.move_absolute(list(start_mm),
                                 clamp_mm=stage_ctrl.range_span_mm)
        # PrintSynchronizer reads the stage's velocity SETPOINTS to compute
        # the firing window — set the commanded write velocity for the lines.
        stage_ctrl.set_velocity(float(velocity_mm_s))
        try:
            a, b = list(start_mm), list(end_mm)
            for _ in range(repetitions):
                if abort_flag.is_set():
                    return False
                # path-internal reposition is now a near-zero-length move
                sync.execute_path([(a, False), (b, True)])
                a, b = b, a  # zig-zag
            return True
        finally:
            stage_ctrl.set_velocity(self.TRAVEL_VELOCITY_MM_S)
