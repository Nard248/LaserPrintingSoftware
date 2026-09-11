"""Hikrobot MV-CS200-10GC camera via the MVS SDK.

A 20 MP GigE colour camera (IMX183). The vendor ships Python bindings as
loose modules rather than a package, so they are added to sys.path and
imported lazily inside method bodies — exactly like the ACS wheel, which is
what keeps this module importable on a laptop with no MVS installed.

SDK lifecycle (from the vendor sample):

    EnumDevices -> CreateHandle -> OpenDevice
      -> Set{Enum,Float,Int}Value ...
      -> StartGrabbing -> GetImageBuffer / FreeImageBuffer -> StopGrabbing
    -> CloseDevice -> DestroyHandle

Grabbing is left running between snapshots: starting a GigE stream costs
hundreds of milliseconds, and an idle stream costs nothing but bandwidth we
are not otherwise using.
"""

from __future__ import annotations

import ctypes
import importlib.util
import io
import os
import sys
import threading
from pathlib import Path

from ..actions import ActionSpec
from ..config import LabgateConfig
from ..errors import DeviceError
from .base import Capability, CheckResult, DeviceAdapter, DeviceState, ParamSpec
from .declarations import camera_actions

#: Where the vendor puts the Python bindings, in order of preference.
DEFAULT_SDK_PATHS = [
    r"C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport",
    r"C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport64",
    "/opt/MVS/Samples/64/Python/MvImport",
]

AUTO_EXPOSURE_MODES = {"off": 0, "once": 1, "continuous": 2}

#: The SDK spells its constants PixelType_Gvsp_RGB8_Packed, while humans (and
#: the MVS GUI) write "RGB8Packed". Accept both rather than silently failing
#: to set the format and then rejecting every frame.
PIXEL_FORMAT_ALIASES = {
    "rgb8packed": "RGB8_Packed",
    "rgb8_packed": "RGB8_Packed",
    "bgr8packed": "BGR8_Packed",
    "bgr8_packed": "BGR8_Packed",
    "mono8": "Mono8",
}


def _pixel_constant(MvCC, name: str):
    """Resolve a configured pixel-format name to its SDK constant."""
    canonical = PIXEL_FORMAT_ALIASES.get(str(name).strip().lower(), str(name).strip())
    for candidate in (canonical, canonical.replace("_", "")):
        value = getattr(MvCC, f"PixelType_Gvsp_{candidate}", None)
        if value is not None:
            return value
    return None


def _sdk_dir(cfg_path: str | None) -> str | None:
    """First existing candidate: explicit config, then MVS_PATH, then defaults."""
    candidates = [cfg_path, os.environ.get("MVS_PATH"), *DEFAULT_SDK_PATHS]
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            return str(candidate)
    return None


class MvsCamera(DeviceAdapter):
    """Real MV-CS200-10GC adapter."""

    kind = "camera"

    def __init__(self, cfg: LabgateConfig, device_id: str = "camera") -> None:
        self.device_id = device_id
        self._cfg = cfg
        self._section = dict(cfg.hardware.get("camera") or {})
        self._cam = None            # MvCC.MvCamera handle
        self._grabbing = False
        self._lock = threading.Lock()
        self._model = ""
        self._serial = ""
        self._resolution: tuple[int, int] | None = None
        # Envelope; replaced with the camera's own limits once opened.
        self._exposure_range = (15.0, 1_000_000.0)
        self._gain_range = (0.0, 24.0)

    # ------------------------------------------------------------- SDK
    def _mvcc(self):
        """Import the vendor bindings, adding their directory to sys.path."""
        directory = _sdk_dir(self._section.get("sdk_path"))
        if directory is None:
            raise DeviceError(
                "MVS SDK not found. Install MVS and/or set camera.sdk_path in the "
                "config (or the MVS_PATH environment variable) to the directory "
                "containing MvCameraControl_class.py")
        if directory not in sys.path:
            sys.path.append(directory)
        try:
            import MvCameraControl_class as MvCC  # noqa: N813
        except Exception as exc:  # noqa: BLE001
            raise DeviceError(
                f"could not import the MVS bindings from {directory}: {exc}") from exc
        return MvCC

    @staticmethod
    def _check(ret: int, what: str) -> None:
        if ret != 0:
            raise DeviceError(f"MVS {what} failed (0x{ret & 0xFFFFFFFF:x})")

    def _transport_mask(self, MvCC) -> int:
        transport = str(self._section.get("transport", "gige")).lower()
        if transport == "usb":
            return MvCC.MV_USB_DEVICE
        if transport == "both":
            return MvCC.MV_GIGE_DEVICE | MvCC.MV_USB_DEVICE
        return MvCC.MV_GIGE_DEVICE

    # --------------------------------------------------------- lifecycle
    def connect(self) -> None:
        if self._cam is not None:
            return  # idempotent — never open a second handle on one camera
        MvCC = self._mvcc()

        devices = MvCC.MV_CC_DEVICE_INFO_LIST()
        self._check(MvCC.MvCamera.MV_CC_EnumDevices(self._transport_mask(MvCC), devices),
                    "EnumDevices")
        if devices.nDeviceNum == 0:
            raise DeviceError(
                "no MVS camera found on this subnet. Check the Ethernet link, that "
                "the camera has power, and that its IP is on the same subnet as "
                "this machine")

        index = self._select(MvCC, devices)
        info = ctypes.cast(devices.pDeviceInfo[index],
                           ctypes.POINTER(MvCC.MV_CC_DEVICE_INFO)).contents
        self._model, self._serial = self._identify(MvCC, info)

        cam = MvCC.MvCamera()
        self._check(cam.MV_CC_CreateHandle(info), "CreateHandle")
        try:
            self._check(cam.MV_CC_OpenDevice(MvCC.MV_ACCESS_Exclusive, 0), "OpenDevice")
        except DeviceError:
            cam.MV_CC_DestroyHandle()
            raise
        self._cam = cam

        try:
            self._read_limits(MvCC)
            self._apply_config(MvCC)
            self._check(cam.MV_CC_StartGrabbing(), "StartGrabbing")
            self._grabbing = True
        except Exception:
            self.disconnect()   # never leave a half-open handle behind
            raise

    def _select(self, MvCC, devices) -> int:
        """Pick the configured camera by serial, else the first enumerated."""
        wanted = self._section.get("serial")
        if not wanted:
            return 0
        for i in range(devices.nDeviceNum):
            info = ctypes.cast(devices.pDeviceInfo[i],
                               ctypes.POINTER(MvCC.MV_CC_DEVICE_INFO)).contents
            _, serial = self._identify(MvCC, info)
            if serial == str(wanted):
                return i
        raise DeviceError(
            f"no camera with serial '{wanted}' among {devices.nDeviceNum} found")

    @staticmethod
    def _identify(MvCC, info) -> tuple[str, str]:
        def text(chars) -> str:
            return "".join(chr(c) for c in chars if c).strip()
        if info.nTLayerType == MvCC.MV_GIGE_DEVICE:
            gige = info.SpecialInfo.stGigEInfo
            return text(gige.chModelName), text(gige.chSerialNumber)
        usb = info.SpecialInfo.stUsb3VInfo
        return text(usb.chModelName), text(usb.chSerialNumber)

    def disconnect(self) -> None:
        cam, self._cam = self._cam, None
        if cam is None:
            return
        try:
            if self._grabbing:
                cam.MV_CC_StopGrabbing()
        except Exception:  # noqa: BLE001 — teardown is best-effort
            pass
        self._grabbing = False
        for step in (cam.MV_CC_CloseDevice, cam.MV_CC_DestroyHandle):
            try:
                step()
            except Exception:  # noqa: BLE001
                pass

    def safe_state(self) -> None:
        """A camera has no unsafe state — it emits nothing."""

    # ------------------------------------------------------- parameters
    def _read_limits(self, MvCC) -> None:
        """Replace the declared envelope with the camera's real limits."""
        exposure = self._get_float(MvCC, "ExposureTime", full=True)
        if exposure:
            self._exposure_range = (exposure[1], exposure[2])
        gain = self._get_float(MvCC, "Gain", full=True)
        if gain:
            self._gain_range = (gain[1], gain[2])
        width = self._get_int(MvCC, "Width")
        height = self._get_int(MvCC, "Height")
        if width and height:
            self._resolution = (width, height)

    def _apply_config(self, MvCC) -> None:
        """Apply configured settings, checking every return code.

        Silently dropping a rejected value would leave the camera in some
        other state while connect() still reported success — captures would
        then run at the wrong exposure with nothing anywhere saying so.
        """
        section = self._section
        mode = str(section.get("auto_exposure", "off")).lower()
        if mode not in AUTO_EXPOSURE_MODES:
            raise DeviceError(
                f"camera.auto_exposure must be one of "
                f"{sorted(AUTO_EXPOSURE_MODES)}, got {mode!r}")
        self._check(self._cam.MV_CC_SetEnumValue("ExposureAuto",
                                                 AUTO_EXPOSURE_MODES[mode]),
                    "SetEnumValue(ExposureAuto)")
        if mode == "off" and section.get("exposure_time_us") is not None:
            self._check(
                self._cam.MV_CC_SetFloatValue(
                    "ExposureTime", float(section["exposure_time_us"])),
                f"SetFloatValue(ExposureTime={section['exposure_time_us']}) "
                f"— permitted range {self._exposure_range}")
        if section.get("gain_db") is not None:
            self._check(
                self._cam.MV_CC_SetFloatValue("Gain", float(section["gain_db"])),
                f"SetFloatValue(Gain={section['gain_db']}) "
                f"— permitted range {self._gain_range}")

        pixel = section.get("pixel_format", "RGB8Packed")
        value = _pixel_constant(MvCC, pixel)
        if value is None:
            raise DeviceError(
                f"unknown camera.pixel_format {pixel!r}; expected one of "
                f"{sorted(set(PIXEL_FORMAT_ALIASES.values()))}")
        self._check(self._cam.MV_CC_SetEnumValue("PixelFormat", value),
                    f"SetEnumValue(PixelFormat={pixel})")

    def _get_float(self, MvCC, key: str, full: bool = False):
        holder = MvCC.MVCC_FLOATVALUE()
        ctypes.memset(ctypes.byref(holder), 0, ctypes.sizeof(MvCC.MVCC_FLOATVALUE))
        if self._cam.MV_CC_GetFloatValue(key, holder) != 0:
            return None
        return (holder.fCurValue, holder.fMin, holder.fMax) if full else holder.fCurValue

    def _get_int(self, MvCC, key: str):
        holder = MvCC.MVCC_INTVALUE()
        ctypes.memset(ctypes.byref(holder), 0, ctypes.sizeof(MvCC.MVCC_INTVALUE))
        if self._cam.MV_CC_GetIntValue(key, holder) != 0:
            return None
        return holder.nCurValue

    def _require(self):
        if self._cam is None:
            raise DeviceError("camera not connected")
        return self._cam

    # ------------------------------------------------------------ frames
    def capture(self, label: str) -> bytes:
        """Grab one frame and return it as PNG bytes."""
        MvCC = self._mvcc()
        cam = self._require()
        timeout = int(self._section.get("grab_timeout_ms", 10000))

        with self._lock:
            if not self._grabbing:
                self._check(cam.MV_CC_StartGrabbing(), "StartGrabbing")
                self._grabbing = True

            frame = MvCC.MV_FRAME_OUT()
            ctypes.memset(ctypes.byref(frame), 0, ctypes.sizeof(frame))
            self._check(cam.MV_CC_GetImageBuffer(frame, timeout), "GetImageBuffer")
            try:
                info = frame.stFrameInfo
                width, height = info.nWidth, info.nHeight
                self._resolution = (width, height)
                raw = bytes(bytearray(
                    ctypes.cast(frame.pBufAddr,
                                ctypes.POINTER(ctypes.c_ubyte * info.nFrameLen)
                                ).contents))
                pixel_type = info.enPixelType
            finally:
                cam.MV_CC_FreeImageBuffer(frame)

        return self._encode_png(MvCC, raw, width, height, pixel_type)

    @staticmethod
    def _encode_png(MvCC, raw: bytes, width: int, height: int, pixel_type) -> bytes:
        from PIL import Image

        if pixel_type == MvCC.PixelType_Gvsp_RGB8_Packed:
            image = Image.frombytes("RGB", (width, height), raw[: width * height * 3])
        elif pixel_type == MvCC.PixelType_Gvsp_Mono8:
            image = Image.frombytes("L", (width, height), raw[: width * height])
        else:
            raise DeviceError(
                f"unsupported pixel format 0x{pixel_type:x}; set camera.pixel_format "
                "to RGB8Packed or Mono8")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    # ------------------------------------------------------ introspection
    def capabilities(self) -> list[Capability]:
        return [Capability(
            device_id=self.device_id, name="capture",
            description="Capture one image and store it as a run artifact.",
            params=[ParamSpec(name="label", type="str")], mutates=False)]

    def state(self) -> DeviceState:
        detail: dict = {
            "model": self._model, "serial": self._serial,
            "grabbing": self._grabbing,
        }
        if self._resolution:
            detail["resolution"] = list(self._resolution)
        if self._cam is not None:
            try:
                MvCC = self._mvcc()
                detail["exposure_time_us"] = self._get_float(MvCC, "ExposureTime")
                detail["gain_db"] = self._get_float(MvCC, "Gain")
            except Exception as exc:  # noqa: BLE001 — state must not raise
                detail["error"] = str(exc)
        return DeviceState(device_id=self.device_id, kind=self.kind,
                           connected=self._cam is not None, detail=detail)

    def actions(self) -> list[ActionSpec]:
        return camera_actions(self._exposure_range[0], self._exposure_range[1],
                              self._gain_range[0], self._gain_range[1])

    # ---------------------------------------------------------- actions
    def act_snapshot(self, label: str | None = None) -> dict:
        image = self.capture(label or "snapshot")
        return {"detail": f"captured {len(image)} bytes from {self._model or 'camera'}",
                "label": label or "snapshot", "bytes": len(image),
                "resolution": list(self._resolution) if self._resolution else None,
                "image_bytes": image}

    def act_set_exposure(self, exposure_time_us: float) -> dict:
        MvCC = self._mvcc()
        cam = self._require()
        # Auto-exposure would immediately overwrite a manual value; turning it
        # off is the intent behind setting an explicit exposure.
        cam.MV_CC_SetEnumValue("ExposureAuto", AUTO_EXPOSURE_MODES["off"])
        self._check(cam.MV_CC_SetFloatValue("ExposureTime", float(exposure_time_us)),
                    "SetFloatValue(ExposureTime)")
        return {"detail": f"exposure {exposure_time_us} us (auto-exposure off)",
                "exposure_time_us": self._get_float(MvCC, "ExposureTime")}

    def act_set_gain(self, gain_db: float) -> dict:
        MvCC = self._mvcc()
        cam = self._require()
        self._check(cam.MV_CC_SetFloatValue("Gain", float(gain_db)),
                    "SetFloatValue(Gain)")
        return {"detail": f"gain {gain_db} dB", "gain_db": self._get_float(MvCC, "Gain")}

    def act_set_auto_exposure(self, mode: str) -> dict:
        cam = self._require()
        self._check(cam.MV_CC_SetEnumValue("ExposureAuto", AUTO_EXPOSURE_MODES[mode]),
                    "SetEnumValue(ExposureAuto)")
        return {"detail": f"auto-exposure {mode}", "auto_exposure": mode}

    def act_settings(self) -> dict:
        MvCC = self._mvcc()
        self._require()
        return {
            "detail": f"{self._model or 'camera'} settings",
            "model": self._model, "serial": self._serial,
            "exposure_time_us": self._get_float(MvCC, "ExposureTime"),
            "exposure_range_us": list(self._exposure_range),
            "gain_db": self._get_float(MvCC, "Gain"),
            "gain_range_db": list(self._gain_range),
            "resolution": list(self._resolution) if self._resolution else None,
            "grabbing": self._grabbing,
        }

    # -------------------------------------------------------- diagnose
    def diagnose(self) -> list[CheckResult]:
        checks: list[CheckResult] = []

        directory = _sdk_dir(self._section.get("sdk_path"))
        checks.append(CheckResult(
            check="camera.sdk", ok=directory is not None,
            severity="warning" if directory is None else "info",
            detail=(f"MVS bindings at {directory}" if directory
                    else "MVS SDK not found on this machine"),
            remedy="" if directory else
                   "install MVS and set camera.sdk_path (or MVS_PATH) to the "
                   "MvImport directory",
            manual=directory is None))
        if directory is None:
            return checks

        if importlib.util.find_spec("PIL") is None:
            checks.append(CheckResult(
                check="camera.pillow", ok=False, severity="warning",
                detail="Pillow is not installed; frames cannot be encoded to PNG",
                remedy="pip install pillow"))

        connected = self._cam is not None
        checks.append(CheckResult(
            check="camera.connected", ok=connected,
            # A print can run without imaging — it just cannot be inspected.
            severity="warning" if not connected else "info",
            detail=(f"{self._model} (serial {self._serial})" if connected
                    else "not connected — capture_image will be unavailable"),
            remedy="" if connected else f"POST /devices/{self.device_id}/connect"))

        if not connected:
            # Enumerating is read-only and answers the common question:
            # "is the camera even on the network?"
            try:
                MvCC = self._mvcc()
                devices = MvCC.MV_CC_DEVICE_INFO_LIST()
                MvCC.MvCamera.MV_CC_EnumDevices(self._transport_mask(MvCC), devices)
                found = devices.nDeviceNum
                checks.append(CheckResult(
                    check="camera.visible", ok=found > 0,
                    severity="warning" if found == 0 else "info",
                    detail=f"{found} MVS camera(s) visible on the subnet",
                    remedy="" if found else
                           "check the camera has power, the Ethernet cable is seated, "
                           "and its IP is on this machine's subnet (use the MVS app)",
                    manual=found == 0))
            except Exception as exc:  # noqa: BLE001
                checks.append(CheckResult(
                    check="camera.visible", ok=False, severity="warning",
                    detail=f"enumeration failed: {exc}",
                    remedy="open the MVS application and confirm the camera appears",
                    manual=True))
        else:
            checks.append(CheckResult(
                check="camera.grabbing", ok=self._grabbing,
                severity="warning" if not self._grabbing else "info",
                detail="stream running" if self._grabbing else "stream not started",
                remedy="" if self._grabbing
                       else f"POST /devices/{self.device_id}/actions/snapshot"))
        return checks
