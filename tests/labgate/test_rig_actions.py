"""Rig adapter actions and diagnostics, driven against fakes.

No hardware and no vendor SDKs are present here, so these tests pin the
*call shapes* and the decision logic — which is exactly where the bugs that
only appear on the rig tend to live.
"""

from unittest.mock import MagicMock

import pytest

import labgate.devices.rig as rig
from labgate.errors import DeviceError


class FakeTcp:
    """Records the SPiiPlus calls the enable sequence is supposed to make."""

    def __init__(self):
        self.calls = []

    def enable_axes(self, axes):
        self.calls.append(("enable_axes", list(axes)))

    def wait_motor_enabled(self, axis, timeout_ms=-1):
        self.calls.append(("wait_motor_enabled", axis, timeout_ms))

    def commutate(self, axis):
        self.calls.append(("commutate", axis))


class FakeStageController:
    def __init__(self):
        self.tcp = FakeTcp()
        self.axes = [0, 1, 2]
        self._pos = [0.0, 0.0, 0.0]
        self.calls = []

    def position(self):
        return list(self._pos)

    def velocity_feedback(self):
        return [0.0, 0.0, 0.0]

    def current_velocity_setpoint(self, axis=None):
        return [1.0, 1.0, 1.0]

    def set_velocity(self, v):
        self.calls.append(("set_velocity", v))

    def set_acceleration(self, axis, a):
        self.calls.append(("set_acceleration", axis, a))

    def set_jerk(self, axis, j):
        self.calls.append(("set_jerk", axis, j))

    def move_absolute(self, target, clamp_mm=None, wait=True):
        self.calls.append(("move_absolute", list(target), clamp_mm))
        self._pos = list(target)

    def move_relative(self, delta, clamp_mm=None, wait=True):
        self.calls.append(("move_relative", list(delta), clamp_mm))
        self._pos = [p + d for p, d in zip(self._pos, delta)]

    def jog(self, axis, distance_mm, clamp_mm=None, wait=True):
        self.calls.append(("jog", axis, distance_mm, clamp_mm))
        self._pos[axis] += distance_mm

    def halt(self):
        self.calls.append(("halt",))


@pytest.fixture()
def rig_stage(cfg):
    cfg.hardware = {"stage": {"ip": "10.0.0.100", "port": 701,
                              "axes": [0, 1, 2], "commutation_axes": [0, 1]}}
    adapter = rig.RigStage(cfg)
    adapter._controller = FakeStageController()
    return adapter


# ----------------------------------------------------------------- stage
def test_enable_axes_runs_the_real_spiiplus_sequence(rig_stage):
    """enable -> wait for each -> commutate the brushless ones."""
    rig_stage.act_enable_axes()
    calls = rig_stage._controller.tcp.calls
    assert calls[0] == ("enable_axes", [0, 1, 2])
    assert [c for c in calls if c[0] == "wait_motor_enabled"] == [
        ("wait_motor_enabled", 0, 10_000),
        ("wait_motor_enabled", 1, 10_000),
        ("wait_motor_enabled", 2, 10_000),
    ]
    # only the configured commutation axes, not axis 2
    assert [c for c in calls if c[0] == "commutate"] == [
        ("commutate", 0), ("commutate", 1)]
    assert rig_stage.state().detail["axes_enabled"] is True


def test_motion_refused_until_axes_enabled(rig_stage):
    for call in (rig_stage.act_home,
                 lambda: rig_stage.act_jog(axis=0, distance_mm=0.1),
                 lambda: rig_stage.act_move_relative(dx_mm=0.1)):
        with pytest.raises(DeviceError, match="not enabled"):
            call()
    rig_stage.act_enable_axes()
    rig_stage.act_home()  # now allowed


def test_interactive_moves_keep_the_per_call_clamp(rig_stage):
    """Long travel belongs in a plan, where the whole path is validated."""
    rig_stage.act_enable_axes()
    rig_stage.act_jog(axis=1, distance_mm=0.5)
    jog = next(c for c in rig_stage._controller.calls if c[0] == "jog")
    assert jog[3] == rig_stage._cfg.bounds.stage.max_step_mm

    rig_stage.act_move_absolute(x_mm=1.0, y_mm=0.0, z_mm=0.0)
    move = [c for c in rig_stage._controller.calls if c[0] == "move_absolute"][-1]
    assert move[2] == rig_stage._cfg.bounds.stage.max_step_mm


def test_home_uses_full_travel_clamp(rig_stage):
    """Homing may legitimately cross more than the interactive clamp."""
    rig_stage.act_enable_axes()
    rig_stage._controller._pos = [10.0, 0.0, 0.0]
    rig_stage.act_home()
    move = [c for c in rig_stage._controller.calls if c[0] == "move_absolute"][-1]
    lo, hi = rig_stage._cfg.bounds.stage.range_mm
    assert move[1] == [0.0, 0.0, 0.0] and move[2] == hi - lo


def test_halt_never_raises_even_when_disconnected(cfg):
    adapter = rig.RigStage(cfg)
    assert adapter.act_halt()["detail"] == "halt issued"


def test_stage_diagnose_flags_missing_sdk_as_manual(cfg):
    checks = {c.check: c for c in rig.RigStage(cfg).diagnose()}
    assert checks["stage.sdk"].ok is False
    assert checks["stage.sdk"].manual is True      # a human must install it
    assert checks["stage.axes_enabled"].ok is False
    assert "enable_axes" in checks["stage.axes_enabled"].remedy


# ----------------------------------------------------------------- laser
def _rig_laser(cfg, status):
    cfg.hardware = {"laser": {"ip": "192.168.244.10"}}
    adapter = rig.RigLaser(cfg)
    controller = MagicMock()
    controller.status.return_value = status
    controller.is_on = False
    controller.attenuator_pct = 30.0
    controller.pp_divider = 1
    controller.power_w.return_value = 10.0
    controller.frequency_hz.return_value = 200_000.0
    adapter._controller = controller
    return adapter, controller


def test_laser_status_surfaces_firmware_errors(cfg):
    adapter, _ = _rig_laser(cfg, {
        "ActualStateName": "Running", "Errors": ["E42"], "Warnings": ["W1"],
        "IsEmissionWarningActive": True})
    data = adapter.act_status()
    assert data["errors"] == ["E42"] and data["warnings"] == ["W1"]
    assert data["power_w"] == 10.0 and data["frequency_hz"] == 200_000.0


def test_laser_output_off_forces_past_a_stale_cache(cfg):
    adapter, controller = _rig_laser(cfg, {"ActualStateName": "Running"})
    adapter.act_output_off()
    controller.off.assert_called_once_with(force=True)


def test_laser_diagnose_flags_key_switch_as_manual(cfg):
    adapter, _ = _rig_laser(cfg, {
        "ActualStateName": "KeyOff", "Errors": [], "Warnings": []})
    checks = {c.check: c for c in adapter.diagnose()}
    state = checks["laser.state"]
    assert state.ok is False and state.manual is True
    assert "key switch" in state.remedy


def test_laser_diagnose_blocks_on_firmware_errors(cfg):
    adapter, _ = _rig_laser(cfg, {
        "ActualStateName": "Running", "Errors": ["E7"], "Warnings": []})
    checks = {c.check: c for c in adapter.diagnose()}
    assert checks["laser.errors"].ok is False
    assert checks["laser.errors"].severity == "blocker"


def test_laser_diagnose_survives_an_unreachable_head(cfg):
    adapter, controller = _rig_laser(cfg, {})
    controller.status.side_effect = OSError("no route to host")
    checks = {c.check: c for c in adapter.diagnose()}
    assert checks["laser.reachable"].ok is False
    assert checks["laser.reachable"].manual is True


def test_beam_on_action_absent_unless_policy_allows(cfg):
    adapter, _ = _rig_laser(cfg, {})
    assert "output_on" not in [a.name for a in adapter.actions()]
    adapter._cfg.allow_manual_beam = True
    assert "output_on" in [a.name for a in adapter.actions()]


# ---------------------------------------------------------------- camera
def test_camera_module_imports_without_the_mvs_sdk():
    import importlib.util
    assert importlib.util.find_spec("MvCameraControl_class") is None
    from labgate.devices.camera_mvs import MvsCamera  # noqa: F401


def test_camera_reports_missing_sdk_rather_than_crashing(cfg):
    from labgate.devices.camera_mvs import MvsCamera
    checks = {c.check: c for c in MvsCamera(cfg).diagnose()}
    assert checks["camera.sdk"].ok is False
    assert checks["camera.sdk"].manual is True
    assert checks["camera.sdk"].severity == "warning"   # never blocks a print


def test_camera_connect_gives_an_actionable_error(cfg):
    from labgate.devices.camera_mvs import MvsCamera
    with pytest.raises(DeviceError, match="MVS SDK not found"):
        MvsCamera(cfg).connect()


def test_camera_sdk_path_resolution_prefers_config(tmp_path, monkeypatch):
    from labgate.devices import camera_mvs
    wanted = tmp_path / "MvImport"
    wanted.mkdir()
    other = tmp_path / "from_env"
    other.mkdir()
    monkeypatch.setenv("MVS_PATH", str(other))
    assert camera_mvs._sdk_dir(str(wanted)) == str(wanted)   # config wins
    assert camera_mvs._sdk_dir(None) == str(other)           # then the env var
    monkeypatch.delenv("MVS_PATH")
    assert camera_mvs._sdk_dir(str(tmp_path / "nope")) is None


def test_camera_png_encoding_round_trip():
    """The frame path converts raw SDK bytes into a real PNG."""
    from PIL import Image
    from labgate.devices.camera_mvs import MvsCamera

    class FakeMvCC:
        PixelType_Gvsp_RGB8_Packed = 0x02180014
        PixelType_Gvsp_Mono8 = 0x01080001

    raw = bytes([10, 20, 30] * (4 * 3))
    png = MvsCamera._encode_png(FakeMvCC, raw, 4, 3,
                                FakeMvCC.PixelType_Gvsp_RGB8_Packed)
    assert png.startswith(b"\x89PNG")
    assert Image.open(__import__("io").BytesIO(png)).size == (4, 3)


def test_camera_rejects_unsupported_pixel_format():
    from labgate.devices.camera_mvs import MvsCamera

    class FakeMvCC:
        PixelType_Gvsp_RGB8_Packed = 1
        PixelType_Gvsp_Mono8 = 2

    with pytest.raises(DeviceError, match="unsupported pixel format"):
        MvsCamera._encode_png(FakeMvCC, b"\x00" * 12, 2, 2, 0xDEAD)


def test_rig_falls_back_to_sim_camera_without_the_sdk(cfg):
    from labgate.devices.sim import SimCamera
    adapters = {a.device_id: a for a in rig.build_rig_adapters(cfg)}
    assert isinstance(adapters["camera"], SimCamera)
    assert type(adapters["stage"]).__name__ == "RigStage"
    assert type(adapters["laser"]).__name__ == "RigLaser"


def test_rig_and_sim_declare_the_same_action_surface(cfg):
    """A call accepted in simulation must be accepted on the rig."""
    from labgate.devices.sim import SimLaser, SimStage
    assert ([a.name for a in rig.RigStage(cfg).actions()]
            == [a.name for a in SimStage(cfg).actions()])
    assert ([a.name for a in rig.RigLaser(cfg).actions()]
            == [a.name for a in SimLaser(cfg).actions()])
