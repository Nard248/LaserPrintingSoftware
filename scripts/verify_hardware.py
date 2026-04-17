"""Read-only hardware verification.

Does NOT move the stage. Does NOT toggle the laser output. Only reads
current state from both devices and measures command round-trip latency
so we can calibrate the synchronization model later.

Run:
    .venv312/Scripts/python scripts/verify_hardware.py
"""
from __future__ import annotations

import statistics
import time

import requests
import SPiiPlusPython as sp

LASER_URL = "http://192.168.244.10/phoebe/v0/Basic"
STAGE_IP = "10.0.0.100"
STAGE_PORT = 701
AXES = [0, 1, 2]  # X, Y, Z


def banner(title: str) -> None:
    print()
    print("=" * 64)
    print(title)
    print("=" * 64)


def verify_laser() -> None:
    banner("LASER (Phoebe @ 192.168.244.10) — read-only")

    # 1. Single status read with interesting fields
    r = requests.get(LASER_URL, timeout=5)
    r.raise_for_status()
    s = r.json()
    keys = [
        "IsOutputEnabled", "IsOutputOpen", "IsEmissionWarningActive",
        "ActualStateName", "ActualStateName2",
        "ActualAttenuatorPercentage", "TargetAttenuatorPercentage",
        "ActualPpDivider", "TargetPpDivider",
        "ActualOutputPower", "ActualOutputFrequency", "ActualHarmonic",
        "Errors", "Warnings",
    ]
    for k in keys:
        print(f"  {k:34s} {s.get(k)!r}")

    # 2. GET latency distribution (no side effects)
    n = 20
    samples_ms = []
    for _ in range(n):
        t0 = time.perf_counter()
        requests.get(LASER_URL, timeout=5).raise_for_status()
        samples_ms.append((time.perf_counter() - t0) * 1000)
    print()
    print(f"  HTTP GET latency over {n} samples (ms):")
    print(f"    min={min(samples_ms):.1f}  median={statistics.median(samples_ms):.1f}  "
          f"mean={statistics.mean(samples_ms):.1f}  max={max(samples_ms):.1f}")
    print("  (Laser output state is NOT modified in this script.)")


def verify_stage() -> None:
    banner("STAGE (SPiiPlus @ 10.0.0.100:701) — read-only")

    handler = sp.OpenCommEthernetTCP(STAGE_IP, STAGE_PORT)
    try:
        # Positions + feedback velocities per axis
        print("  Current state per axis:")
        for axis in AXES:
            try:
                pos = sp.GetFPosition(handler, axis, sp.SYNCHRONOUS, True)
            except Exception as e:
                pos = f"err: {e}"
            try:
                vel = sp.GetFVelocity(handler, axis, sp.SYNCHRONOUS, False)
            except Exception as e:
                vel = f"err: {e}"
            print(f"    axis {axis}:  fpos = {pos!r:20s}  fvel = {vel!r}")

        # Read servo cycle (MFLAGS?) -- skip; only attempt non-destructive info calls.
        # Latency measurement: repeated GetFPosition for axis 0
        n = 50
        samples_ms = []
        for _ in range(n):
            t0 = time.perf_counter()
            sp.GetFPosition(handler, 0, sp.SYNCHRONOUS, True)
            samples_ms.append((time.perf_counter() - t0) * 1000)
        print()
        print(f"  GetFPosition latency over {n} samples (ms):")
        print(f"    min={min(samples_ms):.3f}  median={statistics.median(samples_ms):.3f}  "
              f"mean={statistics.mean(samples_ms):.3f}  max={max(samples_ms):.3f}")
    finally:
        sp.CloseComm(handler, failure_check=True)
        print("  Stage connection closed. No motion was commanded.")


if __name__ == "__main__":
    verify_laser()
    verify_stage()
    banner("DONE")
