"""One test per confirmed code-review finding.

These are deliberately kept together and named after the defect rather than
the feature. Each docstring states what *used* to happen, because a
regression here is not a cosmetic failure: seven of the nine findings could
put the beam or the stage somewhere the operator did not ask for.
"""

import threading

import pytest

from labgate.actions import ActionTier, coerce_and_validate
from labgate.auth import Identity, Role
from labgate.devices.base import ParamSpec
from labgate.errors import DeviceError, TransitionError, ValidationFailed
from helpers import auth


def _connect_all(client):
    for device in ("stage", "laser", "camera", "white_light"):
        client.post(f"/devices/{device}/connect", headers=auth(client, "alice"))


def _act(client, device, action, params=None, user="alice"):
    return client.post(f"/devices/{device}/actions/{action}",
                       json={"params": params or {}}, headers=auth(client, user))


# --------------------------------------------------------------------- F1
def test_unknown_laser_state_is_not_coerced_to_off(cfg):
    """`state()` used to write `bool(controller.is_on)`, turning UNKNOWN into
    a confident False. The beam interlock reads exactly this field, so a
    laser whose last toggle failed would have been reported as safe and
    motion allowed straight through it."""
    from unittest.mock import MagicMock
    import labgate.devices.rig as rig

    cfg.hardware = {"laser": {"ip": "192.168.244.10"}}
    adapter = rig.RigLaser(cfg)
    controller = MagicMock()
    controller.is_on = None                      # a toggle that never confirmed
    adapter._controller = controller

    assert adapter.state().detail["output_on"] is None


def test_devicectl_refuses_motion_when_the_beam_state_is_unknown(platform):
    """The other half of F1: None must reach the interlock as 'possibly on'."""
    laser = platform.registry.by_kind("laser")
    stage = platform.registry.by_kind("stage")
    laser.connect()
    stage.connect()
    stage.act_enable_axes()
    laser.output_on = None

    operator = Identity(user_id="darina", roles={Role.OPERATOR})
    with pytest.raises(DeviceError, match="output is on"):
        platform.devicectl.invoke("stage", "jog",
                                  {"axis": 0, "distance_mm": 0.1}, operator)


# --------------------------------------------------------------------- F2
def test_stop_paths_do_not_queue_behind_the_device_lock(platform):
    """`halt` used to take the per-device lock. The executor holds that lock
    for a whole line traverse, so the stop would have blocked until the
    exposure it was trying to interrupt had finished — a stop that waits for
    its own target is not a stop."""
    stage = platform.registry.by_kind("stage")
    stage.connect()
    operator = Identity(user_id="darina", roles={Role.OPERATOR})

    lock = platform.devicectl._lock_for("stage")
    done = threading.Event()

    def call_halt():
        platform.devicectl.invoke("stage", "halt", {}, operator)
        done.set()

    with lock:                                   # simulate a run in progress
        worker = threading.Thread(target=call_halt, daemon=True)
        worker.start()
        assert done.wait(timeout=5.0), "halt blocked on the device lock"


def test_ordinary_actions_still_take_the_device_lock(platform):
    """F2's fix must not have removed exclusivity from everything else."""
    stage = platform.registry.by_kind("stage")
    stage.connect()
    stage.act_enable_axes()
    operator = Identity(user_id="darina", roles={Role.OPERATOR})

    lock = platform.devicectl._lock_for("stage")
    done = threading.Event()

    def call_jog():
        platform.devicectl.invoke("stage", "jog",
                                  {"axis": 0, "distance_mm": 0.1}, operator)
        done.set()

    with lock:
        threading.Thread(target=call_jog, daemon=True).start()
        assert not done.wait(timeout=0.5), "jog ignored the device lock"
    assert done.wait(timeout=5.0)                # completes once the lock frees


# --------------------------------------------------------------------- F3
def test_estop_waits_for_the_run_and_then_closes_the_beam_again(client, good_spec):
    """estop used to fire abort and return immediately. Abort is cooperative:
    the executor only observes the flag between repetitions, and until it
    does it can re-open the shutter estop had just closed. The caller was
    told 'ok' while the beam was live."""
    _connect_all(client)
    client.platform.registry.by_kind("stage").time_scale = 0.05
    plan = client.post("/plans", json={"spec": good_spec.model_dump()},
                       headers=auth(client, "alice")).json()
    pid = plan["plan_id"]
    client.post(f"/plans/{pid}/approve", headers=auth(client, "bob"))
    client.post(f"/plans/{pid}/execute", headers=auth(client, "alice"))

    body = client.post("/system/estop", headers=auth(client, "alice")).json()

    assert body["still_running"] is None, body["errors"]
    assert client.platform.registry.by_kind("laser").output_on is False
    assert not client.platform.engine.queue_snapshot().get("running")


# --------------------------------------------------------------------- F9
def test_estop_treats_a_plan_that_already_finished_as_success(client, good_spec,
                                                              monkeypatch):
    """A plan completing between the queue snapshot and the abort call makes
    `abort` raise TransitionError. That was being reported as an estop
    failure — the one moment the operator most needs a clear answer."""
    _connect_all(client)
    snapshots = iter([{"running": "plan-gone", "queued": []}])

    real_snapshot = client.platform.engine.queue_snapshot
    monkeypatch.setattr(client.platform.engine, "queue_snapshot",
                        lambda: next(snapshots, real_snapshot()))
    monkeypatch.setattr(client.platform.engine, "abort",
                        lambda *a, **k: (_ for _ in ()).throw(
                            TransitionError("already terminal")))

    body = client.post("/system/estop", headers=auth(client, "alice")).json()
    assert body["ok"] is True and body["errors"] == []
    assert body["aborted_plans"] == []


# --------------------------------------------------------------------- F4
def test_connect_is_interlocked_like_any_other_move(client):
    """`connect()` enables the axes and drives an unchecked move to origin;
    `disconnect()` homes first. Both were reachable with the beam live, so
    bringing the stage up would have scribed a line across the sample."""
    _connect_all(client)
    client.platform.registry.by_kind("laser").output_on = True

    response = client.post("/devices/stage/connect", headers=auth(client, "alice"))
    assert response.status_code == 502
    assert "moves the stage" in response.json()["detail"]

    response = client.post("/devices/stage/disconnect", headers=auth(client, "alice"))
    assert response.status_code == 502


def test_admin_may_cycle_the_stage_with_the_beam_live_and_it_is_recorded(client):
    _connect_all(client)
    client.platform.registry.by_kind("laser").output_on = True

    assert client.post("/devices/stage/connect",
                       headers=auth(client, "root")).status_code == 200
    override = [e for e in client.platform.audit.read_all()
                if e["event"] == "interlock_override"][-1]
    assert override["payload"]["action"] == "connect"


def test_lifecycle_is_refused_while_a_plan_owns_the_rig(platform, good_spec):
    """Disconnecting mid-run would pull the instrument out from under the
    executor. No role overrides this one."""
    from labgate.lifecycle import PlanState

    class BusyEngine:
        def queue_snapshot(self):
            return {"running": "plan-1", "queued": []}

    platform.devicectl._engine = BusyEngine()
    admin = Identity(user_id="root", roles={Role.ADMIN})
    with pytest.raises(TransitionError, match="plan is running"):
        platform.devicectl.guard_lifecycle("stage", admin, "disconnect")


# --------------------------------------------------------------------- F5
def test_pixel_format_resolves_to_the_real_sdk_constant_name():
    """The SDK spells it `PixelType_Gvsp_RGB8_Packed`; the config spells it
    `RGB8Packed`. Without the alias table every getattr returned None and
    *every* real capture would have failed at connect time."""
    from labgate.devices.camera_mvs import _pixel_constant

    class FakeMvCC:
        PixelType_Gvsp_RGB8_Packed = 0x02180014
        PixelType_Gvsp_BGR8_Packed = 0x02180015
        PixelType_Gvsp_Mono8 = 0x01080001

    assert _pixel_constant(FakeMvCC, "RGB8Packed") == 0x02180014
    assert _pixel_constant(FakeMvCC, "rgb8_packed") == 0x02180014
    assert _pixel_constant(FakeMvCC, "BGR8Packed") == 0x02180015
    assert _pixel_constant(FakeMvCC, "Mono8") == 0x01080001
    assert _pixel_constant(FakeMvCC, "YUV422") is None


# --------------------------------------------------------------------- F6
class _FakeCam:
    """Records SDK writes and can be told to reject one of them."""

    def __init__(self, reject: str | None = None):
        self.reject, self.calls = reject, []

    def _ret(self, what):
        self.calls.append(what)
        return 0x80000004 if what == self.reject else 0

    def MV_CC_SetEnumValue(self, node, value):
        return self._ret(node)

    def MV_CC_SetFloatValue(self, node, value):
        return self._ret(node)


class _FakeMvCC:
    PixelType_Gvsp_RGB8_Packed = 0x02180014
    PixelType_Gvsp_Mono8 = 0x01080001


def _camera(cfg, **section):
    from labgate.devices.camera_mvs import MvsCamera
    cfg.hardware = {"camera": {"auto_exposure": "off", "exposure_time_us": 8000.0,
                               "gain_db": 4.0, "pixel_format": "RGB8Packed",
                               **section}}
    return MvsCamera(cfg)


def test_a_rejected_camera_setting_is_not_swallowed(cfg):
    """Return codes were being discarded. connect() reported success while
    the camera sat at some other exposure — captures would silently run at
    the wrong settings with nothing anywhere saying so."""
    camera = _camera(cfg)
    camera._cam = _FakeCam(reject="ExposureTime")
    with pytest.raises(DeviceError, match="ExposureTime"):
        camera._apply_config(_FakeMvCC)


def test_unknown_auto_exposure_mode_is_refused(cfg):
    camera = _camera(cfg, auto_exposure="sometimes")
    camera._cam = _FakeCam()
    with pytest.raises(DeviceError, match="auto_exposure"):
        camera._apply_config(_FakeMvCC)


def test_unknown_pixel_format_is_refused(cfg):
    camera = _camera(cfg, pixel_format="YUV422")
    camera._cam = _FakeCam()
    with pytest.raises(DeviceError, match="unknown camera.pixel_format"):
        camera._apply_config(_FakeMvCC)


def test_exposure_is_skipped_when_auto_exposure_is_on(cfg):
    """Writing ExposureTime while ExposureAuto is continuous is rejected by
    the camera, so the happy path must not attempt it."""
    camera = _camera(cfg, auto_exposure="continuous")
    camera._cam = _FakeCam()
    camera._apply_config(_FakeMvCC)
    assert "ExposureTime" not in camera._cam.calls
    assert camera._cam.calls == ["ExposureAuto", "Gain", "PixelFormat"]


# --------------------------------------------------------------------- F7
@pytest.mark.parametrize("state,manual", [
    ("KeyOff", True),
    ("Interlock open", True),
    ("Emission off", False),        # idle, not a fault — used to be flagged
    ("Standby", False),
    ("Running", False),
])
def test_only_a_key_or_interlock_counts_as_needing_hands(cfg, state, manual):
    """`"off" in state` matched ordinary idle names, so preflight declared
    the rig physically unusable whenever the laser simply was not emitting —
    and told the operator to go turn a key that was already on."""
    from unittest.mock import MagicMock
    import labgate.devices.rig as rig

    cfg.hardware = {"laser": {"ip": "192.168.244.10"}}
    adapter = rig.RigLaser(cfg)
    controller = MagicMock()
    controller.status.return_value = {"ActualStateName": state,
                                      "Errors": [], "Warnings": []}
    adapter._controller = controller

    check = {c.check: c for c in adapter.diagnose()}["laser.state"]
    assert check.manual is manual


# --------------------------------------------------------------------- F8
def test_axis_gaps_are_rejected_by_validation_not_by_an_indexerror():
    """`min(axes)`/`max(axes)` admitted the hole: with axes [0, 2], axis 1
    passed validation and then raised IndexError inside the adapter. That is
    not a LabgateError, so the API answered 500 instead of a 422 naming the
    bad parameter."""
    from labgate.devices.declarations import stage_actions

    jog = next(a for a in stage_actions(range_lo=-25.0, range_hi=25.0,
                                        v_min=0.1, v_max=10.0, clamp_mm=5.0,
                                        axes=[0, 2])
               if a.name == "jog")
    axis = next(p for p in jog.params if p.name == "axis")
    assert axis.allowed == [0.0, 2.0]

    coerce_and_validate(jog, {"axis": 2, "distance_mm": 0.1})     # in the set
    with pytest.raises(ValidationFailed, match=r"axis: 1 is not one of \[0, 2\]"):
        coerce_and_validate(jog, {"axis": 1, "distance_mm": 0.1})


def test_allowed_values_do_not_disturb_ordinary_ranges():
    """A param with no `allowed` list keeps pure min/max behaviour."""
    from labgate.actions import ActionSpec

    spec = ActionSpec(name="x", tier=ActionTier.READ, params=[
        ParamSpec(name="v", type="float", min=0.0, max=10.0)])
    assert coerce_and_validate(spec, {"v": 7.5}) == {"v": 7.5}
    with pytest.raises(ValidationFailed, match="above maximum"):
        coerce_and_validate(spec, {"v": 11.0})


def test_the_axis_gap_reaches_the_api_as_a_422(client):
    """End to end: the whole point of F8 was the status code."""
    _connect_all(client)
    _act(client, "stage", "enable_axes")
    stage = client.platform.registry.by_kind("stage")
    bad_axis = max(a.params[0].allowed for a in stage.actions()
                   if a.name == "jog")[-1] + 1
    response = _act(client, "stage", "jog",
                    {"axis": int(bad_axis), "distance_mm": 0.1})
    assert response.status_code == 422
