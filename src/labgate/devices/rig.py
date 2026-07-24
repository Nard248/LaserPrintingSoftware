"""Real-hardware adapters wrapping the existing laser_printing controllers.

All laser_printing imports happen inside method bodies: this module must
import cleanly on any machine (requirement N4/F11). The stage adapter
needs the proprietary SPiiPlusPython wheel and therefore only works on
the rig computer; the laser adapter needs only network access.

Camera and white-light adapters are declared stubs until we receive the
SDK documentation (requirements doc Q-H1 / Q-H2) — they advertise their
capabilities so clients can plan against them, but refuse to connect.
"""

from __future__ import annotations

from ..config import LabgateConfig
from ..errors import DeviceError
from .base import Capability, DeviceAdapter, DeviceState, ParamSpec
from .sim import SimCamera, SimLaser, SimStage, SimWhiteLight


class RigStage(DeviceAdapter):
    """Fronts laser_printing.controllers.stage.StageController."""

    kind = "stage"

    def __init__(self, cfg: LabgateConfig, device_id: str = "stage") -> None:
        self.device_id = device_id
        self._cfg = cfg
        self._controller = None

    def capabilities(self) -> list[Capability]:
        # Same declared surface as the sim adapter — one source of shape.
        return SimStage(self._cfg, self.device_id).capabilities()

    def state(self) -> DeviceState:
        detail: dict = {}
        if self._controller is not None:
            try:
                detail["position_mm"] = list(self._controller.position())
            except Exception as exc:  # noqa: BLE001 — state must not raise
                detail["error"] = str(exc)
        return DeviceState(device_id=self.device_id, kind=self.kind,
                           connected=self._controller is not None, detail=detail)

    def connect(self) -> None:
        from laser_printing.controllers.stage import StageController  # SPiiPlusPython
        controller = StageController.from_config({"stage": self._cfg.hardware.get("stage", {})})
        controller.connect()
        self._controller = controller

    def disconnect(self) -> None:
        if self._controller is not None:
            self._controller.disconnect()
            self._controller = None

    def safe_state(self) -> None:
        """Safe state = stop where we are; never home blindly mid-fault."""
        try:
            if self._controller is not None:
                self._controller.wait_for_motion()
        except Exception:  # noqa: BLE001 — must never raise
            pass

    def move_absolute(self, target_mm, velocity_mm_s: float | None = None):
        if self._controller is None:
            raise DeviceError("stage not connected")
        if velocity_mm_s is not None:
            self._controller.set_velocity(velocity_mm_s)
        self._controller.move_absolute(list(target_mm))


class RigLaser(DeviceAdapter):
    """Fronts laser_printing.controllers.laser.LaserController (HTTP only)."""

    kind = "laser"

    def __init__(self, cfg: LabgateConfig, device_id: str = "laser") -> None:
        self.device_id = device_id
        self._cfg = cfg
        self._controller = None
        self.output_on = False

    def capabilities(self) -> list[Capability]:
        return SimLaser(self._cfg, self.device_id).capabilities()

    def state(self) -> DeviceState:
        return DeviceState(device_id=self.device_id, kind=self.kind,
                           connected=self._controller is not None,
                           detail={"output_on": self.output_on})

    def connect(self) -> None:
        from laser_printing.controllers.laser import LaserController
        self._controller = LaserController.from_config(
            {"laser": self._cfg.hardware.get("laser", {})})

    def disconnect(self) -> None:
        self.safe_state()
        self._controller = None

    def safe_state(self) -> None:
        try:
            if self._controller is not None:
                self._controller.off()
        except Exception:  # noqa: BLE001 — must never raise
            pass
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


def build_rig_adapters(cfg: LabgateConfig) -> list[DeviceAdapter]:
    """Rig mode: real stage + laser; camera/WL simulated until SDKs arrive.

    Using sim camera/WL (rather than the stubs) keeps full plans executable
    on the rig today; swap in the real adapters once Q-H1/Q-H2 are answered.
    """
    return [RigStage(cfg), RigLaser(cfg), SimCamera(cfg), SimWhiteLight(cfg)]
