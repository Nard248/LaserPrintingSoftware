"""Real-hardware adapters wrapping the existing laser_printing controllers.

All laser_printing imports happen inside method bodies: this module must
import cleanly on any machine (requirement N4/F11). The stage adapter
needs the proprietary SPiiPlusPython wheel and therefore only works on
the rig computer; the laser adapter needs only network access.

safe_state() here is allowed to RAISE on failure: the executor logs a
safe_state_error to telemetry and audit and continues down the safe-state
order. Silently reporting "safe" after a failed laser-off would be worse
than the failure itself.

Camera and white-light adapters are declared stubs until we receive the
SDK documentation (requirements doc Q-H1 / Q-H2) — they advertise their
capabilities so clients can plan against them, but refuse to connect.
"""

from __future__ import annotations

from ..config import LabgateConfig
from ..errors import DeviceError
from ..actions import ActionSpec
from .base import Capability, CheckResult, DeviceAdapter, DeviceState, ParamSpec
from .declarations import laser_actions, stage_actions, white_light_actions
from .sim import SimCamera, SimLaser, SimStage, SimWhiteLight

DEFAULT_TRAVEL_VELOCITY_MM_S = 1.0


class RigStage(DeviceAdapter):
    """Fronts laser_printing.controllers.stage.StageController."""

    kind = "stage"

    def __init__(self, cfg: LabgateConfig, device_id: str = "stage") -> None:
        self.device_id = device_id
        self._cfg = cfg
        self._controller = None
        # StageController.connect() enables + commutates as part of its own
        # bring-up, so a successful connect implies enabled axes.
        self._axes_enabled = False

    @property
    def controller(self):
        """Live StageController (None until connect). Used by SyncExposure."""
        return self._controller

    def capabilities(self) -> list[Capability]:
        # Same declared surface as the sim adapter — one source of shape.
        return SimStage(self._cfg, self.device_id).capabilities()

    def state(self) -> DeviceState:
        detail: dict = {"axes_enabled": self._axes_enabled}
        if self._controller is not None:
            try:
                detail["position_mm"] = list(self._controller.position())
                detail["velocity_feedback_mm_s"] = list(
                    self._controller.velocity_feedback())
                detail["velocity_setpoint_mm_s"] = list(
                    self._controller.current_velocity_setpoint())
            except Exception as exc:  # noqa: BLE001 — state must not raise
                detail["error"] = str(exc)
        return DeviceState(device_id=self.device_id, kind=self.kind,
                           connected=self._controller is not None, detail=detail)

    def connect(self) -> None:
        if self._controller is not None:
            return  # idempotent: never open a second SPiiPlus link
        from laser_printing.controllers.stage import StageController  # SPiiPlusPython
        # from_config takes the stage SECTION directly (it reads cfg["ip"]).
        controller = StageController.from_config(self._cfg.hardware.get("stage", {}))
        controller.connect()   # opens TCP, enables + commutates axes, homes
        self._controller = controller
        self._axes_enabled = True

    def disconnect(self) -> None:
        if self._controller is not None:
            controller, self._controller = self._controller, None
            self._axes_enabled = False
            controller.disconnect()

    def safe_state(self) -> None:
        """Stop motion immediately. StageController.halt() is best-effort
        by contract (never raises); the laser is already off by safe-state
        ordering before this runs."""
        if self._controller is not None:
            self._controller.halt()

    def move_absolute(self, target_mm, velocity_mm_s: float | None = None):
        if self._controller is None:
            raise DeviceError("stage not connected")
        # Velocity is set explicitly on EVERY move so a previous op's write
        # velocity can never leak into a travel move.
        self._controller.set_velocity(
            velocity_mm_s if velocity_mm_s is not None else DEFAULT_TRAVEL_VELOCITY_MM_S)
        lo, hi = self._cfg.bounds.stage.range_mm
        # Platform plans are validated against the absolute travel range, so
        # override the per-call typo clamp (default 5 mm) with the full span;
        # otherwise a validated 8 mm line would trip StageSafetyError mid-run.
        self._controller.move_absolute(list(target_mm), clamp_mm=(hi - lo))


    # -- interactive control plane --------------------------------------
    def actions(self) -> list[ActionSpec]:
        b = self._cfg.bounds.stage
        lo, hi = b.range_mm
        axes = list(self._cfg.hardware.get("stage", {}).get("axes", [0, 1, 2]))
        return stage_actions(lo, hi, b.min_velocity_mm_s, b.max_velocity_mm_s,
                             b.max_step_mm, axes)

    def _require(self):
        if self._controller is None:
            raise DeviceError("stage not connected")
        return self._controller

    def act_enable_axes(self) -> dict:
        """Enable and commutate the servo axes.

        StageController.connect() already does this; exposing it separately
        lets an operator re-enable axes after a fault without tearing down
        and re-opening the SPiiPlus link.
        """
        controller = self._require()
        tcp = controller.tcp
        axes = controller.axes
        tcp.enable_axes(axes)
        for axis in axes:
            tcp.wait_motor_enabled(axis, timeout_ms=10_000)
        commutation = set(self._cfg.hardware.get("stage", {})
                          .get("commutation_axes", [0, 1]))
        for axis in axes:
            if axis in commutation:
                tcp.commutate(axis)
        self._axes_enabled = True
        return {"detail": f"axes {axes} enabled"
                          f"{f', commutated {sorted(commutation)}' if commutation else ''}",
                "axes_enabled": True}

    def act_home(self) -> dict:
        controller = self._require()
        self._assert_enabled()
        lo, hi = self._cfg.bounds.stage.range_mm
        controller.set_velocity(DEFAULT_TRAVEL_VELOCITY_MM_S)
        controller.move_absolute([0.0, 0.0, 0.0], clamp_mm=(hi - lo))
        return {"detail": "homed to [0, 0, 0]",
                "position_mm": list(controller.position())}

    def act_jog(self, axis: int, distance_mm: float) -> dict:
        controller = self._require()
        self._assert_enabled()
        controller.jog(int(axis), float(distance_mm),
                       clamp_mm=self._cfg.bounds.stage.max_step_mm)
        return {"detail": f"jogged axis {axis} by {distance_mm} mm",
                "position_mm": list(controller.position())}

    def act_move_relative(self, dx_mm: float = 0.0, dy_mm: float = 0.0,
                          dz_mm: float = 0.0) -> dict:
        controller = self._require()
        self._assert_enabled()
        controller.move_relative([dx_mm, dy_mm, dz_mm],
                                 clamp_mm=self._cfg.bounds.stage.max_step_mm)
        return {"detail": f"moved by [{dx_mm}, {dy_mm}, {dz_mm}] mm",
                "position_mm": list(controller.position())}

    def act_move_absolute(self, x_mm: float, y_mm: float, z_mm: float,
                          velocity_mm_s: float | None = None) -> dict:
        controller = self._require()
        self._assert_enabled()
        controller.set_velocity(velocity_mm_s if velocity_mm_s is not None
                                else DEFAULT_TRAVEL_VELOCITY_MM_S)
        # Interactive moves keep the per-call clamp: long travel belongs in a
        # plan, where the whole trajectory is validated and approved.
        controller.move_absolute([x_mm, y_mm, z_mm],
                                 clamp_mm=self._cfg.bounds.stage.max_step_mm)
        return {"detail": f"moved to [{x_mm}, {y_mm}, {z_mm}]",
                "position_mm": list(controller.position())}

    def act_set_velocity(self, velocity_mm_s: float) -> dict:
        self._require().set_velocity(float(velocity_mm_s))
        return {"detail": f"velocity set to {velocity_mm_s} mm/s"}

    def act_set_acceleration(self, axis: int, acceleration_mm_s2: float) -> dict:
        self._require().set_acceleration(int(axis), float(acceleration_mm_s2))
        return {"detail": f"axis {axis} acceleration set to {acceleration_mm_s2}"}

    def act_set_jerk(self, axis: int, jerk_mm_s3: float) -> dict:
        self._require().set_jerk(int(axis), float(jerk_mm_s3))
        return {"detail": f"axis {axis} jerk set to {jerk_mm_s3}"}

    def act_halt(self) -> dict:
        if self._controller is not None:
            self._controller.halt()   # best-effort by contract; never raises
        return {"detail": "halt issued"}

    def act_position(self) -> dict:
        controller = self._require()
        return {"detail": "live encoder position",
                "position_mm": list(controller.position()),
                "velocity_feedback_mm_s": list(controller.velocity_feedback())}

    def _assert_enabled(self) -> None:
        if not self._axes_enabled:
            raise DeviceError(
                f"stage axes are not enabled — POST "
                f"/devices/{self.device_id}/actions/enable_axes first")

    def diagnose(self) -> list[CheckResult]:
        checks: list[CheckResult] = []
        section = self._cfg.hardware.get("stage", {})
        endpoint = f"{section.get('ip', '?')}:{section.get('port', '?')}"

        import importlib.util
        have_sdk = importlib.util.find_spec("SPiiPlusPython") is not None
        checks.append(CheckResult(
            check="stage.sdk", ok=have_sdk,
            severity="blocker" if not have_sdk else "info",
            detail=("SPiiPlusPython importable" if have_sdk
                    else "SPiiPlusPython (ACS ADK wheel) is not installed"),
            remedy="" if have_sdk else
                   "install the ACS SPiiPlus ADK wheel into this environment",
            manual=not have_sdk))

        connected = self._controller is not None
        checks.append(CheckResult(
            check="stage.connected", ok=connected,
            severity="blocker" if not connected else "info",
            detail=f"controller at {endpoint}" if connected
                   else f"not connected ({endpoint})",
            remedy="" if connected else f"POST /devices/{self.device_id}/connect"))

        checks.append(CheckResult(
            check="stage.axes_enabled", ok=self._axes_enabled,
            severity="blocker" if not self._axes_enabled else "info",
            detail="servo axes enabled and commutated" if self._axes_enabled
                   else "servo axes are NOT enabled — no motion is possible",
            remedy="" if self._axes_enabled
                   else f"POST /devices/{self.device_id}/actions/enable_axes"))

        if connected:
            try:
                position = list(self._controller.position())
                lo, hi = self._cfg.bounds.stage.range_mm
                inside = all(lo <= v <= hi for v in position)
                checks.append(CheckResult(
                    check="stage.position_in_range", ok=inside,
                    severity="info" if inside else "blocker",
                    detail=f"position {position} vs travel [{lo}, {hi}] mm",
                    remedy="" if inside
                           else f"POST /devices/{self.device_id}/actions/home"))
            except Exception as exc:  # noqa: BLE001
                checks.append(CheckResult(
                    check="stage.readable", ok=False, severity="blocker",
                    detail=f"could not read position: {exc}",
                    remedy="check the Ethernet link to the ACS controller",
                    manual=True))
        return checks


class RigLaser(DeviceAdapter):
    """Fronts laser_printing.controllers.laser.LaserController (HTTP only)."""

    kind = "laser"

    def __init__(self, cfg: LabgateConfig, device_id: str = "laser") -> None:
        self.device_id = device_id
        self._cfg = cfg
        self._controller = None
        self.output_on = False

    @property
    def controller(self):
        """Live LaserController (None until connect). Used by SyncExposure."""
        return self._controller

    def capabilities(self) -> list[Capability]:
        return SimLaser(self._cfg, self.device_id).capabilities()

    def state(self) -> DeviceState:
        if self._controller is not None:
            # Live cached value from the controller — SyncExposure toggles the
            # beam through the controller directly, bypassing this adapter.
            live = self._controller.is_on
            if live is not None:
                self.output_on = bool(live)
        return DeviceState(device_id=self.device_id, kind=self.kind,
                           connected=self._controller is not None,
                           detail={"output_on": self.output_on})

    def connect(self) -> None:
        if self._controller is not None:
            return  # idempotent
        from laser_printing.controllers.laser import LaserController
        # from_config takes the laser SECTION directly (it reads cfg["ip"]).
        controller = LaserController.from_config(self._cfg.hardware.get("laser", {}))
        controller.connect()  # reads real hardware state into the cache
        self._controller = controller
        self.output_on = bool(controller.is_on)

    def disconnect(self) -> None:
        if self._controller is None:
            return
        controller, self._controller = self._controller, None
        try:
            controller.off(force=True)
        except Exception:  # noqa: BLE001 — disconnect is best-effort cleanup
            pass
        self.output_on = False

    def safe_state(self) -> None:
        """Force output off. RAISES if the laser cannot be confirmed off —
        the executor records it; independent hardware interlocks (L0) remain
        the last line of defense.

        force=True is essential: after a failed on() the controller cache is
        unknown/stale and a plain off() could short-circuit without POSTing.
        """
        if self._controller is None:
            self.output_on = False
            return
        try:
            self._controller.off(force=True)
        except Exception as exc:
            raise DeviceError(f"LASER MAY STILL BE ON — off() failed: {exc}") from exc
        self.output_on = False

    def set_power(self, attenuator_percent: float, pp_divider: int = 1) -> None:
        if self._controller is None:
            raise DeviceError("laser not connected")
        self._controller.set_attenuator(attenuator_percent)
        self._controller.set_pp_divider(pp_divider)

    def on(self) -> None:
        if self._controller is None:
            raise DeviceError("laser not connected")
        self._controller.on()
        self.output_on = True

    def off(self) -> None:
        if self._controller is not None:
            self._controller.off()
        self.output_on = False


    # -- interactive control plane --------------------------------------
    def actions(self) -> list[ActionSpec]:
        b = self._cfg.bounds.laser
        lo, hi = b.attenuator_percent
        return laser_actions(lo, hi, b.pp_divider_min,
                             allow_manual_beam=self._cfg.allow_manual_beam)

    def _require(self):
        if self._controller is None:
            raise DeviceError("laser not connected")
        return self._controller

    def act_set_power(self, attenuator_percent: float,
                      pp_divider: int | None = None) -> dict:
        controller = self._require()
        controller.set_attenuator(float(attenuator_percent))
        if pp_divider is not None:
            controller.set_pp_divider(int(pp_divider))
        return {"detail": f"attenuator {attenuator_percent}%"
                          + (f", divider {pp_divider}" if pp_divider is not None else ""),
                "attenuator_percent": controller.attenuator_pct,
                "pp_divider": controller.pp_divider}

    def act_output_off(self) -> dict:
        """force=True: after a failed toggle the cached state may be stale,
        and a cache-respecting off() would do nothing at all."""
        controller = self._require()
        controller.off(force=True)
        self.output_on = False
        return {"detail": "output closed and confirmed", "output_on": False}

    def act_output_on(self) -> dict:
        controller = self._require()
        controller.on()
        self.output_on = True
        return {"detail": "output OPEN — the beam is live", "output_on": True}

    def act_status(self) -> dict:
        """Full firmware status, including the errors and warnings the laser
        reports but which nothing in the platform surfaced before."""
        controller = self._require()
        raw = controller.status()
        return {
            "detail": f"state {raw.get('ActualStateName', '?')}",
            "output_on": controller.is_on,
            "attenuator_percent": controller.attenuator_pct,
            "pp_divider": controller.pp_divider,
            "power_w": controller.power_w(),
            "frequency_hz": controller.frequency_hz(),
            "state_name": raw.get("ActualStateName"),
            "errors": list(raw.get("Errors") or []),
            "warnings": list(raw.get("Warnings") or []),
            "emission_warning_active": raw.get("IsEmissionWarningActive"),
        }

    def diagnose(self) -> list[CheckResult]:
        checks: list[CheckResult] = []
        section = self._cfg.hardware.get("laser", {})
        host = section.get("ip", "?")

        connected = self._controller is not None
        checks.append(CheckResult(
            check="laser.connected", ok=connected,
            severity="blocker" if not connected else "info",
            detail=f"HTTP endpoint {host}" if connected else f"not connected ({host})",
            remedy="" if connected else f"POST /devices/{self.device_id}/connect"))
        if not connected:
            return checks

        try:
            raw = self._controller.status()
        except Exception as exc:  # noqa: BLE001
            checks.append(CheckResult(
                check="laser.reachable", ok=False, severity="blocker",
                detail=f"status request failed: {exc}",
                remedy=f"check the network path to {host} and that the laser is "
                       "powered on at the head",
                manual=True))
            return checks

        errors = list(raw.get("Errors") or [])
        warnings = list(raw.get("Warnings") or [])
        state = raw.get("ActualStateName")

        checks.append(CheckResult(
            check="laser.errors", ok=not errors,
            severity="blocker" if errors else "info",
            detail=f"firmware errors: {errors}" if errors else "no firmware errors",
            remedy="clear the fault at the laser controller" if errors else "",
            manual=bool(errors)))
        checks.append(CheckResult(
            check="laser.warnings", ok=not warnings,
            severity="warning" if warnings else "info",
            detail=f"firmware warnings: {warnings}" if warnings else "no warnings"))

        # The states that need a hand on the hardware — a key switch or an
        # interlock is not something the API can resolve for you.
        needs_hands = state and any(
            token in str(state).lower() for token in ("key", "interlock", "off"))
        checks.append(CheckResult(
            check="laser.state", ok=not needs_hands,
            severity="blocker" if needs_hands else "info",
            detail=f"reported state: {state}",
            remedy=("turn the key switch on the laser head and clear any enclosure "
                    "interlock, then re-check") if needs_hands else "",
            manual=bool(needs_hands)))

        checks.append(CheckResult(
            check="laser.output_off", ok=not bool(self._controller.is_on),
            severity="warning" if self._controller.is_on else "info",
            detail="output is ON" if self._controller.is_on else "output is off",
            remedy=f"POST /devices/{self.device_id}/actions/output_off"
                   if self._controller.is_on else ""))
        return checks


class CameraStub(DeviceAdapter):
    """Placeholder until the camera SDK is known (Q-H1)."""

    kind = "camera"

    def __init__(self, cfg: LabgateConfig, device_id: str = "camera") -> None:
        self.device_id = device_id

    def capabilities(self) -> list[Capability]:
        return [Capability(
            device_id=self.device_id, name="capture",
            description="Capture one image (NOT YET AVAILABLE — pending camera SDK, Q-H1).",
            params=[ParamSpec(name="label", type="str")], mutates=False,
        )]

    def state(self) -> DeviceState:
        return DeviceState(device_id=self.device_id, kind=self.kind, connected=False,
                           detail={"status": "pending camera SDK (Q-H1)"})

    def connect(self) -> None:
        raise DeviceError("camera adapter pending SDK documentation (Q-H1)")

    def disconnect(self) -> None:
        pass

    def safe_state(self) -> None:
        pass

    def capture(self, label: str) -> bytes:
        raise DeviceError("camera adapter pending SDK documentation (Q-H1)")


class WhiteLightStub(DeviceAdapter):
    """Placeholder until the WL source interface is known (Q-H2)."""

    kind = "white_light"

    def __init__(self, cfg: LabgateConfig, device_id: str = "white_light") -> None:
        self.device_id = device_id

    def capabilities(self) -> list[Capability]:
        return [Capability(
            device_id=self.device_id, name="set_on",
            description="Toggle white light (NOT YET AVAILABLE — pending interface docs, Q-H2).",
            params=[ParamSpec(name="on", type="bool")],
        )]

    def state(self) -> DeviceState:
        return DeviceState(device_id=self.device_id, kind=self.kind, connected=False,
                           detail={"status": "pending WL interface docs (Q-H2)"})

    def connect(self) -> None:
        raise DeviceError("white-light adapter pending interface documentation (Q-H2)")

    def disconnect(self) -> None:
        pass

    def safe_state(self) -> None:
        pass

    def set_on(self, on: bool) -> None:
        raise DeviceError("white-light adapter pending interface documentation (Q-H2)")

    def actions(self) -> list[ActionSpec]:
        return white_light_actions()

    def act_set_on(self, on: bool) -> dict:
        raise DeviceError("white-light adapter pending interface documentation (Q-H2)")

    def diagnose(self) -> list[CheckResult]:
        return [CheckResult(
            check="white_light.driver", ok=False, severity="warning",
            detail="no white-light driver yet (requirements Q-H2)",
            remedy="supply the WL control interface (serial/USB/vendor DLL) so an "
                   "adapter can be written",
            manual=True)]


def build_rig_adapters(cfg: LabgateConfig) -> list[DeviceAdapter]:
    """Rig mode: real stage, laser and camera; white light still simulated.

    The camera falls back to the simulated adapter when the MVS SDK is not
    installed, so a rig machine without MVS still runs everything else
    rather than failing to start.
    """
    from .camera_mvs import MvsCamera, _sdk_dir

    camera_cfg = cfg.hardware.get("camera") or {}
    if _sdk_dir(camera_cfg.get("sdk_path")) is not None:
        camera: DeviceAdapter = MvsCamera(cfg)
    else:
        camera = SimCamera(cfg)
    return [RigStage(cfg), RigLaser(cfg), camera, SimWhiteLight(cfg)]
