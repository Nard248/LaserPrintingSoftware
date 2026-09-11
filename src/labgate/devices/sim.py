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

from ..actions import ActionSpec
from ..config import LabgateConfig
from ..errors import DeviceError
from .base import Capability, CheckResult, DeviceAdapter, DeviceState, ParamSpec
from .declarations import (
    camera_actions,
    laser_actions,
    stage_actions,
    white_light_actions,
)


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

    HOME = [0.0, 0.0, 0.0]

    def __init__(self, cfg: LabgateConfig, device_id: str = "stage") -> None:
        self.device_id = device_id
        self._bounds = cfg.bounds.stage
        self._pos = [0.0, 0.0, 0.0]
        self._connected = False
        self._axes_enabled = False
        self._velocity = [1.0, 1.0, 1.0]
        self._accel: dict[int, float] = {}
        self._jerk: dict[int, float] = {}
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
            detail={
                "position_mm": list(self._pos),
                "axes_enabled": self._axes_enabled,
                "velocity_mm_s": list(self._velocity),
                "acceleration_mm_s2": dict(self._accel),
                "jerk_mm_s3": dict(self._jerk),
                "moving": False,
            },
        )

    def connect(self) -> None:
        self._connected = True
        self._axes_enabled = True  # sim: connect implies enable+commutate

    def disconnect(self) -> None:
        self._connected = False
        self._axes_enabled = False

    def safe_state(self) -> None:
        pass  # a stationary sim stage is safe

    def diagnose(self) -> list[CheckResult]:
        lo, hi = self._bounds.range_mm
        checks = [CheckResult(
            check="stage.simulated", ok=True, severity="info",
            detail="simulated stage — no hardware is being driven")]
        checks.append(CheckResult(
            check="stage.connected", ok=self._connected,
            severity="blocker" if not self._connected else "info",
            detail="connected" if self._connected else "not connected",
            remedy="" if self._connected else f"POST /devices/{self.device_id}/connect"))
        inside = all(lo <= v <= hi for v in self._pos)
        checks.append(CheckResult(
            check="stage.position_in_range", ok=inside,
            severity="info" if inside else "blocker",
            detail=f"position {self._pos} vs travel [{lo}, {hi}] mm",
            remedy="" if inside else f"POST /devices/{self.device_id}/actions/home"))
        return checks

    # -- device actions (interactive control plane) ---------------------
    def actions(self) -> list[ActionSpec]:
        lo, hi = self._bounds.range_mm
        vmin, vmax = self._bounds.min_velocity_mm_s, self._bounds.max_velocity_mm_s
        clamp = self._bounds.max_step_mm
        return stage_actions(lo, hi, vmin, vmax, clamp, self._axis_ids())

    def _axis_ids(self) -> list[int]:
        return [0, 1, 2]

    def act_enable_axes(self) -> dict:
        self._axes_enabled = True
        return {"detail": "axes enabled", "axes_enabled": True}

    def act_home(self) -> dict:
        self._require_enabled()
        self._pos = list(self.HOME)
        return {"detail": f"homed to {self.HOME}", "position_mm": list(self._pos)}

    def act_jog(self, axis: int, distance_mm: float) -> dict:
        self._require_enabled()
        delta = [0.0, 0.0, 0.0]
        delta[int(axis)] = float(distance_mm)
        return self.act_move_relative(dx_mm=delta[0], dy_mm=delta[1], dz_mm=delta[2])

    def act_move_relative(self, dx_mm: float = 0.0, dy_mm: float = 0.0,
                          dz_mm: float = 0.0) -> dict:
        self._require_enabled()
        target = [p + d for p, d in zip(self._pos, (dx_mm, dy_mm, dz_mm))]
        return self.act_move_absolute(x_mm=target[0], y_mm=target[1], z_mm=target[2])

    def act_move_absolute(self, x_mm: float, y_mm: float, z_mm: float,
                          velocity_mm_s: float | None = None) -> dict:
        self._require_enabled()
        target = [x_mm, y_mm, z_mm]
        clamp = self._bounds.max_step_mm
        for i, (cur, tgt) in enumerate(zip(self._pos, target)):
            if abs(tgt - cur) > clamp + 1e-9:
                raise DeviceError(
                    f"axis {i}: step {abs(tgt - cur):.3f} mm exceeds the interactive "
                    f"clamp of {clamp} mm — use a plan for long moves")
        duration = self.move_absolute(target, velocity_mm_s)
        return {"detail": f"moved to {target}", "position_mm": list(self._pos),
                "duration_s": round(duration, 4)}

    def act_set_velocity(self, velocity_mm_s: float) -> dict:
        self._velocity = [float(velocity_mm_s)] * 3
        return {"detail": f"velocity set to {velocity_mm_s} mm/s",
                "velocity_mm_s": list(self._velocity)}

    def act_set_acceleration(self, axis: int, acceleration_mm_s2: float) -> dict:
        self._accel[int(axis)] = float(acceleration_mm_s2)
        return {"detail": f"axis {axis} acceleration set", "acceleration": self._accel}

    def act_set_jerk(self, axis: int, jerk_mm_s3: float) -> dict:
        self._jerk[int(axis)] = float(jerk_mm_s3)
        return {"detail": f"axis {axis} jerk set", "jerk": self._jerk}

    def act_halt(self) -> dict:
        return {"detail": "halt issued (sim stage is never in motion)"}

    def act_position(self) -> dict:
        return {"detail": f"position {self._pos}", "position_mm": list(self._pos)}

    def _require_enabled(self) -> None:
        if not self._axes_enabled:
            raise DeviceError(
                f"stage axes are not enabled — "
                f"POST /devices/{self.device_id}/actions/enable_axes first")

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
        self.allow_manual_beam = getattr(cfg, "allow_manual_beam", False)

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

    # -- device actions -------------------------------------------------
    def actions(self) -> list[ActionSpec]:
        a_lo, a_hi = self._bounds.attenuator_percent
        return laser_actions(a_lo, a_hi, self._bounds.pp_divider_min,
                             allow_manual_beam=self.allow_manual_beam)

    def act_set_power(self, attenuator_percent: float,
                      pp_divider: int | None = None) -> dict:
        self.set_power(attenuator_percent,
                       self.pp_divider if pp_divider is None else int(pp_divider))
        return {"detail": f"attenuator {self.attenuator_percent}%, "
                          f"divider {self.pp_divider}",
                "attenuator_percent": self.attenuator_percent,
                "pp_divider": self.pp_divider}

    def act_output_off(self) -> dict:
        self.off()
        return {"detail": "output closed", "output_on": self.output_on}

    def act_output_on(self) -> dict:
        self.on()
        return {"detail": "output OPEN — beam is live", "output_on": self.output_on}

    def act_status(self) -> dict:
        return {"detail": "simulated laser status",
                "output_on": self.output_on,
                "attenuator_percent": self.attenuator_percent,
                "pp_divider": self.pp_divider,
                "errors": [], "warnings": [], "state_name": "Simulated"}

    def diagnose(self) -> list[CheckResult]:
        return [
            CheckResult(check="laser.simulated", ok=True, severity="info",
                        detail="simulated laser — no beam exists"),
            CheckResult(
                check="laser.connected", ok=self._connected,
                severity="blocker" if not self._connected else "info",
                detail="connected" if self._connected else "not connected",
                remedy="" if self._connected
                       else f"POST /devices/{self.device_id}/connect"),
            CheckResult(
                check="laser.output_off", ok=not self.output_on,
                severity="warning" if self.output_on else "info",
                detail="output is ON" if self.output_on else "output is off",
                remedy=f"POST /devices/{self.device_id}/actions/output_off"
                       if self.output_on else ""),
        ]


class SimCamera(DeviceAdapter):
    kind = "camera"

    # Mirrors the MV-CS200-10GC envelope so a call accepted here is accepted
    # on the rig. The real adapter reads its true limits from the SDK.
    EXPOSURE_RANGE_US = (15.0, 1_000_000.0)
    GAIN_RANGE_DB = (0.0, 24.0)

    def __init__(self, cfg: LabgateConfig, device_id: str = "camera") -> None:
        self.device_id = device_id
        self._connected = False
        self.captures = 0
        self.exposure_time_us = 20000.0
        self.gain_db = 0.0
        self.auto_exposure = "off"

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

    # -- device actions -------------------------------------------------
    def actions(self) -> list[ActionSpec]:
        return camera_actions(self.EXPOSURE_RANGE_US[0], self.EXPOSURE_RANGE_US[1],
                              self.GAIN_RANGE_DB[0], self.GAIN_RANGE_DB[1])

    def act_snapshot(self, label: str | None = None) -> dict:
        image = self.capture(label or "snapshot")
        # The service layer persists `image_bytes`; it is not JSON-serialised.
        return {"detail": f"captured {len(image)} bytes",
                "label": label or "snapshot", "bytes": len(image),
                "image_bytes": image}

    def act_set_exposure(self, exposure_time_us: float) -> dict:
        self.exposure_time_us = float(exposure_time_us)
        return {"detail": f"exposure {self.exposure_time_us} us",
                "exposure_time_us": self.exposure_time_us}

    def act_set_gain(self, gain_db: float) -> dict:
        self.gain_db = float(gain_db)
        return {"detail": f"gain {self.gain_db} dB", "gain_db": self.gain_db}

    def act_set_auto_exposure(self, mode: str) -> dict:
        self.auto_exposure = mode
        return {"detail": f"auto-exposure {mode}", "auto_exposure": mode}

    def act_settings(self) -> dict:
        return {"detail": "simulated camera settings",
                "exposure_time_us": self.exposure_time_us,
                "gain_db": self.gain_db,
                "auto_exposure": self.auto_exposure,
                "pixel_format": "RGB8Packed",
                "resolution": [64, 48]}

    def diagnose(self) -> list[CheckResult]:
        return [
            CheckResult(check="camera.simulated", ok=True, severity="info",
                        detail="simulated camera — synthetic frames"),
            CheckResult(
                check="camera.connected", ok=self._connected,
                # not a blocker: a print can run without imaging, it just
                # cannot produce inspection images
                severity="warning" if not self._connected else "info",
                detail=("connected" if self._connected
                        else "not connected — capture_image will be unavailable"),
                remedy="" if self._connected
                       else f"POST /devices/{self.device_id}/connect"),
        ]


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

    # -- device actions -------------------------------------------------
    def actions(self) -> list[ActionSpec]:
        return white_light_actions()

    def act_set_on(self, on: bool) -> dict:
        self.set_on(on)
        return {"detail": f"white light {'on' if on else 'off'}", "on": self.on_state}

    def diagnose(self) -> list[CheckResult]:
        return [CheckResult(
            check="white_light.connected", ok=self._connected,
            severity="warning" if not self._connected else "info",
            detail=("connected" if self._connected
                    else "not connected — imaging illumination unavailable"),
            remedy="" if self._connected
                   else f"POST /devices/{self.device_id}/connect")]


def build_sim_adapters(cfg: LabgateConfig) -> list[DeviceAdapter]:
    return [SimStage(cfg), SimLaser(cfg), SimCamera(cfg), SimWhiteLight(cfg)]
