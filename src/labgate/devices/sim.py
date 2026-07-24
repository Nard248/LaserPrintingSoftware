"""Simulated adapters — the full platform with zero hardware.

Simulation is a first-class mode (requirement F11): it runs CI, lets the
chat-platform integration be tested safely, and is the default until the
rig is explicitly configured. Sim adapters enforce the same bounds as the
real ones so a spec that passes in simulation is bound-safe on the rig.
"""

from __future__ import annotations

import io
import struct
import time
import zlib

from ..config import LabgateConfig
from ..errors import DeviceError
from .base import Capability, DeviceAdapter, DeviceState, ParamSpec


def _tiny_png(width: int = 64, height: int = 48, shade: int = 128) -> bytes:
    """Minimal valid grayscale PNG without external deps."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes([shade]) * width for _ in range(height))
    out = io.BytesIO()
    out.write(b"\x89PNG\r\n\x1a\n")
    out.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)))
    out.write(chunk(b"IDAT", zlib.compress(raw)))
    out.write(chunk(b"IEND", b""))
    return out.getvalue()


class SimStage(DeviceAdapter):
    kind = "stage"

    def __init__(self, cfg: LabgateConfig, device_id: str = "stage") -> None:
        self.device_id = device_id
        self._bounds = cfg.bounds.stage
        self._pos = [0.0, 0.0, 0.0]
        self._connected = False
        self.time_scale = 0.0  # 0 => instant moves (tests); 1 => real-time

    def capabilities(self) -> list[Capability]:
        lo, hi = self._bounds.range_mm
        axis = lambda n: ParamSpec(  # noqa: E731
            name=n, type="float", unit="mm", min=lo, max=hi
        )
        vel = ParamSpec(
            name="velocity_mm_s", type="float", unit="mm/s",
            min=self._bounds.min_velocity_mm_s, max=self._bounds.max_velocity_mm_s,
        )
        return [
            Capability(
                device_id=self.device_id, name="move_absolute",
                description="Move stage to an absolute XYZ position.",
                params=[axis("x_mm"), axis("y_mm"), axis("z_mm"), vel],
            ),
        ]

    def state(self) -> DeviceState:
        return DeviceState(
            device_id=self.device_id, kind=self.kind, connected=self._connected,
            detail={"position_mm": list(self._pos)},
        )

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def safe_state(self) -> None:
        pass  # a stationary sim stage is safe

    # --- actions (called only by the executor) ---
    def move_absolute(self, target_mm, velocity_mm_s: float | None = None) -> float:
        if not self._connected:
            raise DeviceError("stage not connected")
        lo, hi = self._bounds.range_mm
        for v in target_mm:
            if not lo <= v <= hi:
                raise DeviceError(f"target {v} mm outside travel range [{lo}, {hi}]")
        vel = velocity_mm_s or 1.0
        dist = max(abs(t - p) for t, p in zip(target_mm, self._pos))
        duration = dist / vel if vel > 0 else 0.0
        if self.time_scale:
            time.sleep(duration * self.time_scale)
        self._pos = list(target_mm)
        return duration


class SimLaser(DeviceAdapter):
    kind = "laser"

    def __init__(self, cfg: LabgateConfig, device_id: str = "laser") -> None:
        self.device_id = device_id
        self._bounds = cfg.bounds.laser
        self._connected = False
        self.output_on = False
        self.attenuator_percent = 0.0
        self.pp_divider = 1

    def capabilities(self) -> list[Capability]:
        a_lo, a_hi = self._bounds.attenuator_percent
        return [
            Capability(
                device_id=self.device_id, name="set_power",
                description="Set attenuator percentage and pulse-picker divider.",
                params=[
                    ParamSpec(name="attenuator_percent", type="float", unit="%", min=a_lo, max=a_hi),
                    ParamSpec(name="pp_divider", type="int", unit="", min=self._bounds.pp_divider_min),
                ],
            ),
            Capability(device_id=self.device_id, name="output_on",
                       description="Enable laser output (fires the beam)."),
            Capability(device_id=self.device_id, name="output_off",
                       description="Close laser output."),
        ]

    def state(self) -> DeviceState:
        return DeviceState(
            device_id=self.device_id, kind=self.kind, connected=self._connected,
            detail={
                "output_on": self.output_on,
                "attenuator_percent": self.attenuator_percent,
                "pp_divider": self.pp_divider,
            },
        )

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self.output_on = False
        self._connected = False

    def safe_state(self) -> None:
        self.output_on = False

    # --- actions ---
    def set_power(self, attenuator_percent: float, pp_divider: int = 1) -> None:
        a_lo, a_hi = self._bounds.attenuator_percent
        if not a_lo <= attenuator_percent <= a_hi:
            raise DeviceError(f"attenuator {attenuator_percent}% outside [{a_lo}, {a_hi}]")
        if pp_divider < self._bounds.pp_divider_min:
            raise DeviceError(f"pp_divider {pp_divider} < {self._bounds.pp_divider_min}")
        self.attenuator_percent = attenuator_percent
        self.pp_divider = pp_divider

    def on(self) -> None:
        if not self._connected:
            raise DeviceError("laser not connected")
        self.output_on = True

    def off(self) -> None:
        self.output_on = False


class SimCamera(DeviceAdapter):
    kind = "camera"

    def __init__(self, cfg: LabgateConfig, device_id: str = "camera") -> None:
        self.device_id = device_id
        self._connected = False
        self.captures = 0

    def capabilities(self) -> list[Capability]:
        return [
            Capability(
                device_id=self.device_id, name="capture",
                description="Capture one image and store it as a run artifact.",
                params=[ParamSpec(name="label", type="str")], mutates=False,
            ),
        ]

    def state(self) -> DeviceState:
        return DeviceState(device_id=self.device_id, kind=self.kind,
                           connected=self._connected, detail={"captures": self.captures})

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def safe_state(self) -> None:
        pass

    def capture(self, label: str) -> bytes:
        if not self._connected:
            raise DeviceError("camera not connected")
        self.captures += 1
        return _tiny_png(shade=(64 + 32 * (self.captures % 5)))


class SimWhiteLight(DeviceAdapter):
    kind = "white_light"

    def __init__(self, cfg: LabgateConfig, device_id: str = "white_light") -> None:
        self.device_id = device_id
        self._connected = False
        self.on_state = False

    def capabilities(self) -> list[Capability]:
        return [
            Capability(
                device_id=self.device_id, name="set_on",
                description="Turn the white-light illumination on or off.",
                params=[ParamSpec(name="on", type="bool")],
            ),
        ]

    def state(self) -> DeviceState:
        return DeviceState(device_id=self.device_id, kind=self.kind,
                           connected=self._connected, detail={"on": self.on_state})

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self.on_state = False
        self._connected = False

    def safe_state(self) -> None:
        self.on_state = False

    def set_on(self, on: bool) -> None:
        if not self._connected:
            raise DeviceError("white light not connected")
        self.on_state = on


def build_sim_adapters(cfg: LabgateConfig) -> list[DeviceAdapter]:
    return [SimStage(cfg), SimLaser(cfg), SimCamera(cfg), SimWhiteLight(cfg)]
