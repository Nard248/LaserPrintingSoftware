"""Exposure backends (incl. SyncExposure with fake controllers), the repaired
experiment layer's importability/API alignment, the MCP wrapper, and the demo."""

import threading

import pytest

from labgate.exposure import SimExposure, SyncExposure
from helpers import ALICE, BOB


class FakeStageController:
    """Mirrors the StageController surface PrintSynchronizer + SyncExposure use."""

    def __init__(self):
        self.axes = [0, 1, 2]
        self.range_span_mm = 50.0
        self.velocity = 1.0
        self.moves = []

    def set_velocity(self, v):
        self.velocity = float(v)

    def current_velocity_setpoint(self, axis):
        return self.velocity

    def move_absolute(self, target, clamp_mm=None, wait=True):
        assert clamp_mm == self.range_span_mm, "sync must use full-travel clamp"
        self.moves.append(list(target))

    def move_absolute_async(self, target, clamp_mm=None):
        assert clamp_mm == self.range_span_mm
        self.moves.append(list(target))

    def wait_for_motion(self):
        pass


class FakeLaserController:
    def __init__(self):
        self.events = []
        self.is_on = False

    def on(self):
        self.is_on = True
        self.events.append("on")

    def off(self):
        self.is_on = False
        self.events.append("off")


class _Adapter:
    def __init__(self, controller):
        self.controller = controller


NO_ABORT = threading.Event()


# ------------------------------------------------------------- SyncExposure
def test_sync_exposure_runs_calibrated_path(cfg):
    stage_ctrl = FakeStageController()
    laser_ctrl = FakeLaserController()
    backend = SyncExposure(_Adapter(stage_ctrl), _Adapter(laser_ctrl),
                           {"synchronization": {"strategy": "sequential"}})
    done = backend.write_line((0, 0, 6), (5, 0, 6), 5.0, 2, NO_ABORT)
    assert done
    assert stage_ctrl.velocity == 5.0  # write velocity set before sync path
    # rep 1: reposition to start + print to end; rep 2 zig-zags back
    assert stage_ctrl.moves[0] == [0, 0, 6] and stage_ctrl.moves[1] == [5, 0, 6]
    assert stage_ctrl.moves[-1] == [0, 0, 6]
    assert laser_ctrl.events[-1] == "off" and not laser_ctrl.is_on


def test_sync_exposure_aborts_between_reps(cfg):
    stage_ctrl = FakeStageController()
    backend = SyncExposure(_Adapter(stage_ctrl), _Adapter(FakeLaserController()),
                           {"synchronization": {"strategy": "sequential"}})
    flag = threading.Event()
    flag.set()
    assert backend.write_line((0, 0, 6), (5, 0, 6), 5.0, 3, flag) is False
    assert stage_ctrl.moves == []  # aborted before any motion


def test_sync_exposure_requires_connected_adapters(cfg):
    from labgate.errors import DeviceError
    backend = SyncExposure(_Adapter(None), _Adapter(None), {})
    with pytest.raises(DeviceError):
        backend.write_line((0, 0, 0), (1, 0, 0), 5.0, 1, NO_ABORT)


# --------------------------------------------- repaired experiment layer
def test_experiment_layer_imports_without_vendor_wheel():
    import importlib.util
    assert importlib.util.find_spec("SPiiPlusPython") is None
    from laser_printing.controllers.sync import PrintSynchronizer  # noqa: F401
    from laser_printing.experiment.base import BaseExperiment  # noqa: F401
    from laser_printing.experiment.motion_profiling import MotionProfiling  # noqa: F401
    from laser_printing.experiment.parameter_sweep import ParameterSweep  # noqa: F401


def test_motion_profiling_uses_current_stage_api(tmp_path):
    """Drives _run_condition with a fake stage exposing ONLY the real
    StageController surface — the pre-repair code called move_to etc. and
    would fail here."""
    import numpy as np
    from laser_printing.experiment.motion_profiling import MotionProfiling

    class ProfilingStage(FakeStageController):
        def set_acceleration(self, axis, a):
            pass

        def set_jerk(self, axis, j):
            pass

        def record_profile(self, n_samples, period=1,
                           variables="FVEL(0), FPOS(0)",
                           array_name="_profile_buf", wait=True):
            assert wait is True
            t = np.arange(n_samples) * 0.001064
            vel = np.minimum(5.0, t / 0.357 * 5.0)
            pos = np.cumsum(vel) * 0.001064
            return np.vstack([vel, pos])

    exp = MotionProfiling(
        experiment_id="t", output_dir=str(tmp_path), z_focus=6.0,
        x_start=0.0, x_end=2.0, y_line=0.0, n_samples=1000,
    )
    exp.stage = ProfilingStage()
    exp.output_dir = tmp_path
    metrics = exp._run_condition(0, {"acceleration": 5, "jerk": 5,
                                     "target_velocity_mm_s": 5.0})
    assert metrics["accel_time_s"] > 0
    assert (tmp_path / "a_5_j_5" / "raw_profile.csv").exists()


# ------------------------------------------------------------------ MCP
def test_mcp_wrapper_tools(client, good_spec):
    pytest.importorskip("mcp")
    import anyio
    import httpx
    from labgate.mcp_server import create_mcp_server

    http = httpx.Client(transport=client._transport, base_url="http://labgate",
                        headers={"Authorization": f"Bearer {client.tokens['alice']}"})
    server = create_mcp_server(http)
    tools = anyio.run(server.list_tools)
    names = {t.name for t in tools}
    assert {"get_capabilities", "submit_plan", "approve_plan", "execute_plan",
            "get_results", "dry_run", "abort_plan", "get_plan",
            "get_devices"} <= names

    # tool functions hit the real API through the injected client
    import json
    result = anyio.run(server.call_tool, "submit_plan",
                       {"spec": good_spec.model_dump()})
    if isinstance(result, tuple):  # (content_blocks, structured) in newer SDKs
        content, structured = result
        payload = structured.get("result", structured) if isinstance(
            structured, dict) else json.loads(content[0].text)
    else:  # plain list of content blocks
        payload = json.loads(result[0].text)
    assert payload["state"] == "validated"


# ------------------------------------------------------------------ demo
def test_experimentalist_demo_runs_to_completion(monkeypatch, capsys):
    import sys
    sys.path.insert(0, "examples")
    import experimentalist_demo
    monkeypatch.setattr(sys, "argv", ["demo"])
    assert experimentalist_demo.main() == 0
    out = capsys.readouterr().out
    assert "expected 403" in out and "final state: completed" in out
