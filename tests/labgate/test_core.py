"""Spec models, config, ids, registry, sim devices."""

import pytest
from pydantic import ValidationError

from labgate.config import LabgateConfig
from labgate.devices.sim import SimCamera, SimLaser, SimStage, build_sim_adapters
from labgate.errors import DeviceError, LabgateError
from labgate.ids import new_id
from labgate.registry import CapabilityRegistry
from labgate.spec import ExperimentSpec


def test_ids_unique_and_prefixed():
    ids = {new_id("plan") for _ in range(100)}
    assert len(ids) == 100
    assert all(i.startswith("plan_") for i in ids)


def test_config_defaults_are_sim_mode():
    cfg = LabgateConfig.load(None)
    assert cfg.mode == "sim"
    assert cfg.bounds.stage.range_mm == (-25.0, 25.0)


def test_spec_round_trip(good_spec):
    reloaded = ExperimentSpec.model_validate_json(good_spec.model_dump_json())
    assert reloaded == good_spec
    assert reloaded.operations[1].op == "write_array"


def test_spec_rejects_unknown_op():
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({
            "title": "bad", "operations": [{"op": "fire_the_laser_forever"}],
        })


def test_spec_requires_operations():
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({"title": "empty", "operations": []})


def test_registry_snapshot_and_duplicate(cfg):
    registry = CapabilityRegistry()
    for adapter in build_sim_adapters(cfg):
        registry.register(adapter)
    snap = registry.snapshot()
    assert {d["kind"] for d in snap["devices"]} == {"stage", "laser", "camera", "white_light"}
    names = {c["name"] for c in snap["capabilities"]}
    assert {"move_absolute", "set_power", "capture", "set_on"} <= names
    with pytest.raises(LabgateError):
        registry.register(SimStage(cfg))  # duplicate device_id


def test_sim_stage_enforces_travel_range(cfg):
    stage = SimStage(cfg)
    stage.connect()
    stage.move_absolute((1.0, 2.0, 3.0))
    assert stage.state().detail["position_mm"] == [1.0, 2.0, 3.0]
    with pytest.raises(DeviceError):
        stage.move_absolute((30.0, 0.0, 0.0))


def test_sim_laser_bounds_and_safe_state(cfg):
    laser = SimLaser(cfg)
    laser.connect()
    laser.set_power(30, 2)
    laser.on()
    assert laser.output_on
    laser.safe_state()
    assert not laser.output_on
    with pytest.raises(DeviceError):
        laser.set_power(101, 1)


def test_sim_camera_produces_png(cfg):
    cam = SimCamera(cfg)
    cam.connect()
    image = cam.capture("test")
    assert image.startswith(b"\x89PNG")
