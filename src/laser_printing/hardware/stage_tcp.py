"""Layer 0 transport for the ACS SPiiPlus motion controller.

Thin wrapper over `SPiiPlusPython` that exposes the raw primitives as
simple Python methods. No caching, no policy, no safety clamps - that all
lives one layer up in controllers/stage.py. The goal here is that any
higher-level feature can be implemented without re-learning the SPiiPlus
calling convention (axis terminator, SYNCHRONOUS flag, etc.).

SPiiPlus conventions baked in:
    - Multi-axis calls need an axis list terminated by -1 (e.g. [0, 1, 2, -1])
    - `sp.SYNCHRONOUS` is used for blocking calls; pass an ACSC_WAITBLOCK
      to run asynchronously
    - `ACSC_AMF_WAIT` tells ToPointM to queue the move and wait for the
      previous one to finish before starting
    - GetFPosition returns mm (after the controller's MFLAGS scaling)
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Sequence

import SPiiPlusPython as sp


def terminated(axes: Sequence[int]) -> list[int]:
    """Append the -1 multi-axis terminator required by SPiiPlus."""
    axes = list(axes)
    if not axes or axes[-1] != -1:
        axes = axes + [-1]
    return axes


class StageTcp:
    """Raw SPiiPlus TCP transport."""

    DEFAULT_IP = "10.0.0.100"
    DEFAULT_PORT = 701

    def __init__(self, ip: str = DEFAULT_IP, port: int = DEFAULT_PORT):
        self.ip = ip
        self.port = port
        self._handler = None

    # -- Connection lifecycle --------------------------------------------

    def open(self) -> None:
        if self._handler is not None:
            return
        self._handler = sp.OpenCommEthernetTCP(self.ip, self.port)

    def close(self) -> None:
        if self._handler is None:
            return
        try:
            sp.CloseComm(self._handler, failure_check=True)
        finally:
            self._handler = None

    @property
    def handle(self):
        if self._handler is None:
            raise RuntimeError("StageTcp not open; call open() first.")
        return self._handler

    def __enter__(self) -> "StageTcp":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # -- Axis enable / commutation ---------------------------------------

    def enable_axes(self, axes: Sequence[int]) -> None:
        sp.EnableM(self.handle, terminated(axes), sp.SYNCHRONOUS, True)

    def wait_motor_enabled(self, axis: int, timeout_ms: int = -1) -> None:
        sp.WaitMotorEnabled(self.handle, axis, 1, timeout_ms, True)

    def commutate(self, axis: int) -> None:
        sp.CommutExt(self.handle, axis, -1, -1, -1, sp.SYNCHRONOUS, True)
        sp.WaitMotorCommutated(self.handle, axis, 1, -1, True)

    # -- Set motion profile ----------------------------------------------

    def set_velocity(self, axis: int, v_mm_s: float) -> None:
        sp.SetVelocity(self.handle, axis, float(v_mm_s), sp.SYNCHRONOUS, True)

    def set_acceleration(self, axis: int, a_mm_s2: float) -> None:
        sp.SetAcceleration(self.handle, axis, float(a_mm_s2), sp.SYNCHRONOUS, True)

    def set_jerk(self, axis: int, j_mm_s3: float) -> None:
        sp.SetJerk(self.handle, axis, float(j_mm_s3), sp.SYNCHRONOUS, True)

    # -- Motion commands -------------------------------------------------

    def to_point(self, axes: Sequence[int], point: Sequence[float],
                 flags: int | None = None) -> None:
        """Queue an absolute move; does not start it (call `go`)."""
        if flags is None:
            flags = sp.MotionFlags.ACSC_AMF_WAIT
        sp.ToPointM(self.handle, flags, terminated(axes),
                   [float(p) for p in point], sp.SYNCHRONOUS, True)

    def go(self, axes: Sequence[int], waitblock=None) -> object:
        """Start the queued motion.

        If `waitblock` is None, blocks synchronously. Otherwise the
        waitblock is populated and returned so the caller can poll.
        """
        if waitblock is None:
            sp.GoM(self.handle, terminated(axes), sp.SYNCHRONOUS, True)
            return None
        sp.GoM(self.handle, terminated(axes), waitblock, True)
        return waitblock

    def wait_motion_end(self, axis: int, timeout_ms: int = -1) -> None:
        sp.WaitMotionEnd(self.handle, axis, timeout_ms, True)

    def halt_all(self, axes: Sequence[int]) -> None:
        """Stop motion on all given axes immediately.

        The SPiiPlus API exposes halt/kill under different names across ADK
        versions (KillM/HaltM multi-axis, Kill/Halt per-axis) — probe in
        order of preference. NEEDS ON-RIG VERIFICATION against the installed
        SPiiPlusPython wheel; raises RuntimeError if no variant exists so the
        caller can log it.
        """
        # Argument shape follows this file's proven (..., wait, failure_check)
        # convention — e.g. EnableM(handle, axes, sp.SYNCHRONOUS, True).
        for name in ("KillM", "HaltM"):
            fn = getattr(sp, name, None)
            if fn is not None:
                fn(self.handle, terminated(axes), sp.SYNCHRONOUS, True)
                return
        for name in ("Kill", "Halt"):
            fn = getattr(sp, name, None)
            if fn is not None:
                for axis in axes:
                    fn(self.handle, axis, sp.SYNCHRONOUS, True)
                return
        raise RuntimeError(
            "No halt/kill function found in SPiiPlusPython (tried KillM, "
            "HaltM, Kill, Halt) — check the ADK version on the rig.")

    # -- Feedback --------------------------------------------------------

    def get_fposition(self, axis: int) -> float:
        return float(sp.GetFPosition(self.handle, axis, sp.SYNCHRONOUS, True))

    def get_fvelocity(self, axis: int) -> float:
        return float(sp.GetFVelocity(self.handle, axis, sp.SYNCHRONOUS, False))

    # -- Data collection -------------------------------------------------

    def declare_real_array(self, name: str, rows: int, cols: int) -> None:
        """Declare a real-valued controller-side 2D array.

        Re-declaration raises in SPiiPlus, so we catch-and-ignore it.
        """
        try:
            sp.DeclareVariable(
                handle=self.handle, type=4,
                name=f"{name}({rows})({cols})",
                wait=sp.SYNCHRONOUS, failure_check=True,
            )
        except Exception:
            pass

    def start_data_collection(self, array_name: str, n_samples: int,
                              period: int = 1,
                              variables: str = "FVEL(0), FPOS(0)") -> None:
        sp.DataCollectionExt(
            handle=self.handle, flags=0,
            axis=sp.Axis.ACSC_AXIS_0, array=array_name,
            nSample=n_samples, period=period,
            vars=variables, wait=sp.SYNCHRONOUS, failure_check=True,
        )

    def read_real_array(self, array_name: str, rows: int, cols: int):
        """Read back a previously declared 2D array; returns numpy array."""
        import numpy as np
        data = sp.ReadReal(
            handle=self.handle,
            nBuf=sp.GeneralDefinition.ACSC_NONE,
            var=array_name,
            from1=0, to1=rows - 1,
            from2=0, to2=cols - 1,
            wait=sp.SYNCHRONOUS, failure_check=True,
        )
        return np.array(data, dtype=float)

    @staticmethod
    def make_waitblock():
        return sp.ACSC_WAITBLOCK()


@contextmanager
def open_stage(ip: str = StageTcp.DEFAULT_IP,
               port: int = StageTcp.DEFAULT_PORT):
    """Convenience context manager yielding an opened StageTcp."""
    s = StageTcp(ip, port)
    s.open()
    try:
        yield s
    finally:
        s.close()
