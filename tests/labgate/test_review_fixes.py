"""Regression tests for the confirmed findings of the adversarial review."""

import threading
import time

import pytest
from pydantic import ValidationError

from labgate.config import LabgateConfig
from labgate.lifecycle import PlanState
from labgate.results import RunResults, UnknownArtifactError
from labgate.spec import ExperimentSpec, SetLaserPower, SetWhiteLight, WriteArray
from helpers import ALICE, BOB, auth


def _submit_and_approve(platform, spec):
    record = platform.store.create(spec, ALICE)
    platform.store.set_validated(
        record.plan_id, platform.validator.validate(spec), "validator")
    platform.store.approve(record.plan_id, BOB)
    return record.plan_id


# --- critical: abort must interrupt a WriteArray mid-op -------------------
def test_abort_interrupts_write_array(platform):
    spec = ExperimentSpec(
        title="big array",
        operations=[
            SetLaserPower(attenuator_percent=30),
            WriteArray(x_start_mm=-5, x_end_mm=0, y_start_mm=-20, y_pitch_mm=0.01,
                       line_count=2000, z_mm=6.0, velocity_mm_s=5.0),
        ],
    )
    # slow the sim stage slightly so the array takes real time
    platform.registry.by_kind("stage").time_scale = 0.001
    plan_id = _submit_and_approve(platform, spec)
    platform.engine.start(plan_id, "alice")
    time.sleep(0.10)  # let a few lines run
    platform.engine.abort(plan_id, "alice")
    platform.engine.wait(plan_id)
    record = platform.store.get(plan_id)
    assert record.state == PlanState.ABORTED
    assert not platform.registry.by_kind("laser").output_on
    # far fewer than line_count lines were exposed
    run = RunResults(platform.cfg.storage_dir, plan_id)
    aborted = [e for e in run.events() if e["event"] == "aborted"]
    # either mid-array checkpoint is fine — the point is it did NOT wait
    # for the whole array to finish
    assert aborted and aborted[0]["payload"]["at"] in (
        *(f"array line {n}" for n in range(2000)), "between line repetitions")
    lines_written = sum(1 for e in run.events() if e["event"] == "op_finished")
    assert lines_written < 2  # the write_array op never finished


# --- major: WL restored after capture ------------------------------------
def test_capture_restores_white_light(platform, good_spec):
    plan_id = _submit_and_approve(platform, good_spec)
    platform.engine.start(plan_id, "alice")
    platform.engine.wait(plan_id)
    assert platform.store.get(plan_id).state == PlanState.COMPLETED
    assert platform.registry.by_kind("white_light").on_state is False


# --- major: pre-run safe state (stale laser forced off before motion) ----
def test_stale_laser_forced_off_before_run(platform, good_spec):
    laser = platform.registry.by_kind("laser")
    laser.connect()
    laser.output_on = True  # left on by "someone else"
    plan_id = _submit_and_approve(platform, good_spec)
    platform.engine.start(plan_id, "alice")
    platform.engine.wait(plan_id)
    run = RunResults(platform.cfg.storage_dir, plan_id)
    events = run.events()
    assert events[0]["event"] == "run_started"
    assert platform.store.get(plan_id).state == PlanState.COMPLETED


# --- major: non-finite floats rejected at the schema boundary -------------
def test_spec_rejects_infinity_and_nan():
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({
            "title": "inf", "operations": [{"op": "wait", "seconds": float("inf")}]})
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({
            "title": "nan",
            "operations": [{"op": "set_laser_power", "attenuator_percent": float("nan")}]})


# --- minor: unknown fields rejected at the trust boundary -----------------
def test_spec_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({
            "title": "extra",
            "operations": [{"op": "wait", "seconds": 1, "bypass_safety": True}]})


# --- minor: artifact name sanitization ------------------------------------
def test_artifact_traversal_rejected(tmp_path):
    run = RunResults(tmp_path, "plan_x")
    for bad in ("../audit.jsonl", "..", "a/b.png", "..\\secrets", ""):
        with pytest.raises(UnknownArtifactError):
            run.artifact_path(bad)
    run.save_artifact("ok.png", b"data")
    assert run.artifact_path("ok.png").read_bytes() == b"data"


def test_artifact_traversal_over_http(client, good_spec):
    headers = auth(client, "alice")
    response = client.post("/plans", json={"spec": good_spec.model_dump()},
                           headers=headers)
    plan_id = response.json()["plan_id"]
    response = client.get(
        f"/plans/{plan_id}/results/artifacts/..%2Faudit.jsonl", headers=headers)
    assert response.status_code == 404


# --- major: reads require a platform role ---------------------------------
def test_zero_role_token_cannot_read(client):
    from labgate.auth import Identity
    token = client.platform.tokens.issue(Identity(user_id="ghost", roles=set()))
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/capabilities", headers=headers).status_code == 403
    assert client.get("/plans", headers=headers).status_code == 403


def test_approver_can_read(client):
    assert client.get("/capabilities", headers=auth(client, "bob")).status_code == 200


# --- critical: rig from_config receives the section directly --------------
def test_rig_adapters_pass_config_section(cfg, monkeypatch):
    import labgate.devices.rig as rig
    cfg.hardware = {"laser": {"ip": "10.9.9.9", "api_base": "/x"},
                    "stage": {"ip": "10.8.8.8"}}
    seen = {}

    class FakeController:
        def connect(self):
            seen["connected"] = True

    def fake_from_config(section):
        seen["laser_cfg"] = section
        return FakeController()

    import laser_printing.controllers.laser as laser_mod
    monkeypatch.setattr(laser_mod.LaserController, "from_config", fake_from_config)
    adapter = rig.RigLaser(cfg)
    adapter.connect()
    assert seen["laser_cfg"] == {"ip": "10.9.9.9", "api_base": "/x"}
    assert seen["connected"] is True
    adapter.connect()  # idempotent: no second construction
    assert adapter.state().connected


# --- critical: RigLaser.safe_state surfaces a failed off ------------------
def test_rig_laser_safe_state_raises_on_failure(cfg):
    import labgate.devices.rig as rig
    from labgate.errors import DeviceError

    class BrokenController:
        def off(self):
            raise RuntimeError("network down")

    adapter = rig.RigLaser(cfg)
    adapter._controller = BrokenController()
    adapter.output_on = True
    with pytest.raises(DeviceError, match="MAY STILL BE ON"):
        adapter.safe_state()


# --- minor: empty YAML sections don't crash config load -------------------
def test_config_load_tolerates_empty_sections(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("laser:\nstage:\nsynchronization:\nlabgate:\n")
    cfg = LabgateConfig.load(path)
    assert cfg.mode == "sim"


# --- major: dry-run WriteArray parity + travel ---------------------------
def test_dryrun_array_end_position_parity(cfg):
    from labgate.dryrun import DryRunEstimator
    spec = ExperimentSpec(
        title="parity",
        operations=[
            SetLaserPower(attenuator_percent=30),
            WriteArray(x_start_mm=-5, x_end_mm=0, y_start_mm=0, y_pitch_mm=0.1,
                       line_count=2, z_mm=6, velocity_mm_s=5, repetitions=2),
        ],
    )
    report = DryRunEstimator(cfg).estimate(spec)
    assert report.exposure_events == 4
    # even repetitions end back at x_start; travel to array start is counted
    assert report.motion_distance_mm > 4 * 5  # 4 traverses of 5 mm + travel
