"""Interactive smoke test: laser toggle + small stage jog.

Conservative, instrumented, with hard safety clamps:
    - |stage step| <= SAFE_STEP_MM  (default 0.2 mm)
    - Stage is always moved to HOME_POSITION before and after anything else
    - Laser original state (on/off + attenuator + divider) is restored at exit
    - All latencies measured with time.perf_counter

Run:
    .venv312/Scripts/python scripts/smoke_test.py
"""
from __future__ import annotations

import statistics
import time
from contextlib import contextmanager

import requests
import SPiiPlusPython as sp

LASER_URL = "http://192.168.244.10/phoebe/v0/Basic"
STAGE_IP = "10.0.0.100"
STAGE_PORT = 701
AXES = [0, 1, 2]
AXES_TERMINATED = AXES + [-1]  # SPiiPlus multi-axis terminator
HOME_POSITION = [0.0, 0.0, 0.0]
SAFE_STEP_MM = 0.2
JOG_DISTANCE_MM = 0.05  # 50 microns
JOG_VELOCITY_MM_S = 1.0


# ---------- Laser ----------------------------------------------------------

def laser_status() -> dict:
    r = requests.get(LASER_URL, timeout=5)
    r.raise_for_status()
    return r.json()


def laser_set_output(enable: bool) -> float:
    """POST EnableOutput or CloseOutput; return elapsed seconds for the POST."""
    endpoint = "EnableOutput" if enable else "CloseOutput"
    t0 = time.perf_counter()
    r = requests.post(f"{LASER_URL}/{endpoint}", json={}, timeout=5)
    r.raise_for_status()
    return time.perf_counter() - t0


def laser_wait_until(enabled_state: bool, timeout_s: float = 2.0) -> tuple[float, int]:
    """Poll status until IsOutputEnabled matches `enabled_state`.
    Returns (wait_seconds, poll_count)."""
    t0 = time.perf_counter()
    polls = 0
    while True:
        s = laser_status()
        polls += 1
        if bool(s["IsOutputEnabled"]) == enabled_state:
            return time.perf_counter() - t0, polls
        if time.perf_counter() - t0 > timeout_s:
            raise TimeoutError(
                f"Laser did not reach IsOutputEnabled={enabled_state} in {timeout_s}s "
                f"(status={s['ActualStateName']}, enabled={s['IsOutputEnabled']})"
            )


def laser_toggle_latency_test(n_cycles: int = 3) -> None:
    """Measure post+poll roundtrip for off→on and on→off transitions."""
    print("\n[LASER] Toggle latency test")
    before = laser_status()
    was_enabled = bool(before["IsOutputEnabled"])
    target_pct = before["TargetAttenuatorPercentage"]
    target_div = before["TargetPpDivider"]
    print(f"  Initial: IsOutputEnabled={was_enabled}  "
          f"TargetAttenuator={target_pct}%  TargetDivider={target_div}")

    off_post_ms, off_wait_ms, off_polls = [], [], []
    on_post_ms, on_wait_ms, on_polls = [], [], []

    try:
        # We always start each cycle with laser ON (to match pre-existing state).
        if not was_enabled:
            laser_set_output(True)
            laser_wait_until(True)

        for i in range(n_cycles):
            # ON -> OFF
            t_post = laser_set_output(False)
            t_wait, polls = laser_wait_until(False)
            off_post_ms.append(t_post * 1000)
            off_wait_ms.append(t_wait * 1000)
            off_polls.append(polls)

            # OFF -> ON
            t_post = laser_set_output(True)
            t_wait, polls = laser_wait_until(True)
            on_post_ms.append(t_post * 1000)
            on_wait_ms.append(t_wait * 1000)
            on_polls.append(polls)

            print(f"  cycle {i+1}: "
                  f"off post={off_post_ms[-1]:.1f}ms settle={off_wait_ms[-1]:.1f}ms ({off_polls[-1]} polls) | "
                  f"on  post={on_post_ms[-1]:.1f}ms settle={on_wait_ms[-1]:.1f}ms ({on_polls[-1]} polls)")

        def stats(vs):
            return f"min={min(vs):.1f} med={statistics.median(vs):.1f} mean={statistics.mean(vs):.1f} max={max(vs):.1f}"

        print(f"  OFF POST ms:   {stats(off_post_ms)}")
        print(f"  OFF settle ms: {stats(off_wait_ms)}  (polls: min={min(off_polls)} max={max(off_polls)})")
        print(f"  ON  POST ms:   {stats(on_post_ms)}")
        print(f"  ON  settle ms: {stats(on_wait_ms)}   (polls: min={min(on_polls)} max={max(on_polls)})")

    finally:
        # Restore prior enable state
        now = laser_status()
        if bool(now["IsOutputEnabled"]) != was_enabled:
            laser_set_output(was_enabled)
            laser_wait_until(was_enabled)
        print(f"  Restored IsOutputEnabled={was_enabled}.")


# ---------- Stage ----------------------------------------------------------

@contextmanager
def stage_connection():
    handler = sp.OpenCommEthernetTCP(STAGE_IP, STAGE_PORT)
    try:
        yield handler
    finally:
        sp.CloseComm(handler, failure_check=True)


def stage_get_position(handler) -> list[float]:
    return [float(sp.GetFPosition(handler, ax, sp.SYNCHRONOUS, True)) for ax in AXES]


def stage_enable(handler) -> None:
    sp.EnableM(handler, AXES_TERMINATED, sp.SYNCHRONOUS, True)
    for axis in AXES:
        sp.WaitMotorEnabled(handler, axis, 1, -1, True)
        if axis in (0, 1):
            sp.CommutExt(handler, axis, -1, -1, -1, sp.SYNCHRONOUS, True)
            sp.WaitMotorCommutated(handler, axis, 1, -1, True)


def stage_set_velocity_all(handler, v_mm_s: float) -> None:
    for axis in AXES:
        sp.SetVelocity(handler, axis, v_mm_s, sp.SYNCHRONOUS, True)


def stage_move_absolute(handler, target: list[float]) -> None:
    """Move to absolute target; clamp step relative to current position."""
    current = stage_get_position(handler)
    for i, (c, t) in enumerate(zip(current, target)):
        step = abs(t - c)
        if step > SAFE_STEP_MM:
            raise RuntimeError(
                f"Refusing move: axis {i} step {step:.3f} mm exceeds SAFE_STEP_MM={SAFE_STEP_MM}. "
                f"current={current} target={target}"
            )
    sp.ToPointM(handler, sp.MotionFlags.ACSC_AMF_WAIT, AXES_TERMINATED,
                [float(x) for x in target], sp.SYNCHRONOUS, True)
    sp.GoM(handler, AXES_TERMINATED, sp.SYNCHRONOUS, True)
    for axis in AXES:
        sp.WaitMotionEnd(handler, axis, 5000, True)


def stage_home(handler) -> None:
    """Drive to HOME_POSITION, clamped."""
    print(f"  Moving to HOME {HOME_POSITION}...")
    t0 = time.perf_counter()
    stage_move_absolute(handler, HOME_POSITION)
    dt = (time.perf_counter() - t0) * 1000
    print(f"  Arrived at {stage_get_position(handler)}  (elapsed {dt:.1f} ms)")


def stage_jog_test(handler) -> None:
    """Small +JOG then -JOG on X. Home before, home after."""
    print("\n[STAGE] Jog test (+/- {:.3f} mm on X) @ {} mm/s".format(
        JOG_DISTANCE_MM, JOG_VELOCITY_MM_S))
    stage_enable(handler)
    stage_set_velocity_all(handler, JOG_VELOCITY_MM_S)
    stage_home(handler)

    p0 = stage_get_position(handler)
    target_plus = [p0[0] + JOG_DISTANCE_MM, p0[1], p0[2]]
    t0 = time.perf_counter()
    stage_move_absolute(handler, target_plus)
    dt_plus = (time.perf_counter() - t0) * 1000
    p1 = stage_get_position(handler)
    print(f"  +X: moved {p0} -> {p1}  (elapsed {dt_plus:.1f} ms; "
          f"dx measured = {p1[0] - p0[0]:+.6f} mm, expected +{JOG_DISTANCE_MM})")

    target_minus = [p1[0] - JOG_DISTANCE_MM, p1[1], p1[2]]
    t0 = time.perf_counter()
    stage_move_absolute(handler, target_minus)
    dt_minus = (time.perf_counter() - t0) * 1000
    p2 = stage_get_position(handler)
    print(f"  -X: moved {p1} -> {p2}  (elapsed {dt_minus:.1f} ms; "
          f"dx measured = {p2[0] - p1[0]:+.6f} mm, expected -{JOG_DISTANCE_MM})")

    stage_home(handler)


# ---------- Main -----------------------------------------------------------

if __name__ == "__main__":
    print("=" * 64)
    print("SMOKE TEST: laser toggle + 50 um stage jog")
    print(f"SAFE_STEP_MM={SAFE_STEP_MM}  HOME={HOME_POSITION}  "
          f"JOG={JOG_DISTANCE_MM} mm  VEL={JOG_VELOCITY_MM_S} mm/s")
    print("=" * 64)

    laser_toggle_latency_test(n_cycles=3)

    with stage_connection() as h:
        stage_jog_test(h)

    print("\nSMOKE TEST COMPLETE.")
