"""Regression tests for the rig-readiness review findings (laser-safety
guarantees in the sync stack, cache poisoning, halt call shape, velocities)."""

import sys
import types
from unittest.mock import MagicMock

import pytest

from labgate.exposure import SyncExposure
from labgate.errors import DeviceError


class FaultingStage:
    """StageController-shaped fake whose print move raises mid-segment."""

    def __init__(self, fail_on_move_to=None):
        self.axes = [0, 1, 2]
        self.range_span_mm = 50.0
        self.velocity = 1.0
        self.moves = []
        self.fail_on_move_to = fail_on_move_to

    def set_velocity(self, v):
        self.velocity = float(v)

    def current_velocity_setpoint(self, axis):
        return self.velocity

    def move_absolute(self, target, clamp_mm=None, wait=True):
        if self.fail_on_move_to is not None and list(target) == self.fail_on_move_to:
            raise RuntimeError("SPiiPlus link dropped")
        self.moves.append(list(target))

    def move_absolute_async(self, target, clamp_mm=None):
        self.moves.append(list(target))

    def wait_for_motion(self):
        pass


class RecordingLaser:
    def __init__(self):
        self.events = []
        self.is_on_flag = False

    def on(self):
        self.is_on_flag = True
        self.events.append("on")

    def off(self):
        self.is_on_flag = False
        self.events.append("off")


def _sync(stage, laser, strategy="sequential"):
    from laser_printing.controllers.sync import PrintSynchronizer
    return PrintSynchronizer(laser=laser, stage=stage, strategy=strategy)


# --- critical [0]: beam off when a print move faults ----------------------
def test_sequential_fault_forces_laser_off():
    stage = FaultingStage(fail_on_move_to=[5.0, 0.0, 6.0])
    laser = RecordingLaser()
    with pytest.raises(RuntimeError, match="link dropped"):
        _sync(stage, laser).execute_path([([0, 0, 6], False), ([5.0, 0.0, 6.0], True)])
    assert not laser.is_on_flag
    assert laser.events[-1] == "off"


def test_mid_path_reposition_fault_forces_laser_off():
    # segment 1 prints fine; the laser-off reposition afterwards faults —
    # execute_path's finally must still leave the beam off
    stage = FaultingStage(fail_on_move_to=[9.0, 9.0, 6.0])
    laser = RecordingLaser()
    path = [([0, 0, 6], False), ([2, 0, 6], True), ([9.0, 9.0, 6.0], False)]
    with pytest.raises(RuntimeError):
        _sync(stage, laser).execute_path(path)
    assert not laser.is_on_flag


# --- critical [1]: cache poisoning + force-off ----------------------------
def _make_laser_controller():
    from laser_printing.controllers.laser import LaserController
    controller = LaserController(ip="10.0.0.1", settle_timeout_s=0.05,
                                 poll_interval_s=0.0)
    http = MagicMock()
    controller._http = http
    return controller, http


def test_failed_on_poisons_cache_and_force_off_posts():
    controller, http = _make_laser_controller()
    # firmware accepts the ON POST but never reports enabled -> settle timeout
    http.get_status.return_value = {"IsOutputEnabled": False, "ActualStateName": "?"}
    with pytest.raises(TimeoutError):
        controller.on()
    assert controller._state.output_enabled is None  # unknown, not stale-False

    # plain off() must NOT short-circuit now (None != False), and force=True
    # must post CloseOutput regardless
    http.close_output.reset_mock()
    controller.off(force=True)
    http.close_output.assert_called_once()
    assert controller._state.output_enabled is False


def test_plain_off_still_short_circuits_on_known_state():
    controller, http = _make_laser_controller()
    http.get_status.return_value = {"IsOutputEnabled": False}
    controller._state.output_enabled = False
    controller.off()  # known-off: no POST needed
    http.close_output.assert_not_called()
    controller.off(force=True)  # but force always posts
    http.close_output.assert_called_once()


# --- major [4]/[10]: halt call shape --------------------------------------
def test_halt_all_uses_file_call_convention(monkeypatch):
    stub = types.ModuleType("SPiiPlusPython")
    stub.SYNCHRONOUS = 7
    stub.KillM = MagicMock()
    monkeypatch.setitem(sys.modules, "SPiiPlusPython", stub)
    for mod in ("laser_printing.hardware.stage_tcp",):
        sys.modules.pop(mod, None)
    from laser_printing.hardware import stage_tcp
    tcp = stage_tcp.StageTcp()
    tcp._handler = "HANDLE"
    tcp.halt_all([0, 1, 2])
    stub.KillM.assert_called_once_with("HANDLE", [0, 1, 2, -1], 7, True)
    sys.modules.pop("laser_printing.hardware.stage_tcp", None)


# --- major [2]: reposition at travel velocity, write velocity restored ----
def test_sync_exposure_travel_then_write_velocity(cfg):
    class VelocityTrackingStage(FaultingStage):
        def __init__(self):
            super().__init__()
            self.move_velocities = []

        def move_absolute(self, target, clamp_mm=None, wait=True):
            self.move_velocities.append(self.velocity)
            super().move_absolute(target, clamp_mm=clamp_mm, wait=wait)

    stage = VelocityTrackingStage()
    laser = RecordingLaser()

    class _Adapter:
        def __init__(self, controller):
            self.controller = controller

    backend = SyncExposure(_Adapter(stage), _Adapter(laser),
                           {"synchronization": {"strategy": "sequential"}})
    import threading
    done = backend.write_line((0, 0, 6), (5, 0, 6), 0.2, 1, threading.Event())
    assert done
    # first move (approach) at travel velocity, not the slow write velocity
    assert stage.move_velocities[0] == SyncExposure.TRAVEL_VELOCITY_MM_S
    # the print segment ran at the write velocity
    assert 0.2 in stage.move_velocities
    # travel velocity restored afterwards
    assert stage.velocity == SyncExposure.TRAVEL_VELOCITY_MM_S


# --- major [6]: shutdown safe-states laser before stage disconnect --------
def test_shutdown_orders_laser_before_stage(platform):
    order = []
    for adapter in platform.registry.adapters():
        adapter.connect()
        real_safe = adapter.safe_state
        adapter.safe_state = (lambda a=adapter, r=real_safe:
                              (order.append(("safe", a.kind)), r())[1])
        real_disc = adapter.disconnect
        adapter.disconnect = (lambda a=adapter, r=real_disc:
                              (order.append(("disc", a.kind)), r())[1])
    platform.engine.shutdown()
    safe_events = [k for e, k in order if e == "safe"]
    assert safe_events.index("laser") < safe_events.index("stage")
    # every disconnect happens after ALL safe-states
    first_disc = order.index(next(e for e in order if e[0] == "disc"))
    assert all(e[0] == "safe" for e in order[:first_disc])


# --- minor [9]: RigLaser.state reads live controller state ----------------
def test_rig_laser_state_reflects_live_beam(cfg):
    import labgate.devices.rig as rig
    adapter = rig.RigLaser(cfg)
    controller = MagicMock()
    controller.is_on = True
    adapter._controller = controller
    assert adapter.state().detail["output_on"] is True
    controller.is_on = False
    assert adapter.state().detail["output_on"] is False


# --- major [5]: out-of-range velocity scales physically, not clamped ------
def test_accel_params_scale_outside_calibration():
    from laser_printing.controllers.sync import CalibrationPoint, PrintSynchronizer
    sync = PrintSynchronizer(
        laser=RecordingLaser(), stage=FaultingStage(),
        calibration=[CalibrationPoint(5.0, 0.357, 0.79)],
    )
    t, d = sync._get_accel_params(1.0)
    accel = 5.0 / 0.357
    assert t == pytest.approx(1.0 / accel)
    assert d == pytest.approx(1.0 ** 2 / (2 * accel))
    assert (t, d) != (0.357, 0.79)  # NOT clamped to the 5 mm/s entry