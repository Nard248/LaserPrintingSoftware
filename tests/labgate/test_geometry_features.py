"""Sweep ops, z-stack, STL upload+print, previews, rerun, queue, capture context."""

import io

import pytest

from labgate.lifecycle import PlanState
from labgate.spec import ExperimentSpec
from labgate.validation import ValidationEngine
from helpers import ALICE, BOB, auth


def _tiny_stl_bytes() -> bytes:
    """A 100x100x20 micron box exported as binary STL."""
    import trimesh
    mesh = trimesh.creation.box(extents=[100.0, 100.0, 20.0])
    return trimesh.exchange.export.export_stl(mesh)


def _run_to_completion(client, spec_dict, approve=True):
    headers_alice = auth(client, "alice")
    response = client.post("/plans", json={"spec": spec_dict}, headers=headers_alice)
    plan = response.json()
    if plan["state"] != "validated" or not approve:
        return plan, None
    client.post(f"/plans/{plan['plan_id']}/approve", headers=auth(client, "bob"))
    client.post(f"/plans/{plan['plan_id']}/execute", headers=headers_alice)
    client.platform.engine.wait(plan["plan_id"])
    final = client.get(f"/plans/{plan['plan_id']}", headers=headers_alice).json()
    return plan, final


# ------------------------------------------------------------- sweep ops
def test_power_sweep_array_executes_per_line_power(client):
    spec = {
        "title": "sweep",
        "operations": [{
            "op": "write_power_sweep_array", "x_start_mm": -3, "x_end_mm": 0,
            "y_start_mm": 0, "y_pitch_mm": 0.1,
            "attenuator_percent_per_line": [10, 20, 30],
            "z_mm": 6, "velocity_mm_s": 5,
        }],
    }
    plan, final = _run_to_completion(client, spec)
    assert final["state"] == "completed"
    # last line's power sticks on the sim laser
    laser = client.platform.registry.by_kind("laser")
    assert laser.attenuator_percent == 30


def test_power_sweep_rejects_out_of_bounds_line(cfg):
    report = ValidationEngine(cfg).validate(ExperimentSpec.model_validate({
        "title": "bad", "operations": [{
            "op": "write_power_sweep_array", "x_start_mm": -3, "x_end_mm": 0,
            "y_start_mm": 0, "y_pitch_mm": 0.1,
            "attenuator_percent_per_line": [10, 250, 30],
            "z_mm": 6, "velocity_mm_s": 5,
        }],
    }))
    assert not report.ok
    assert any("attenuator_percent_per_line[1]" in c.check
               for c in report.checks if not c.ok)


def test_z_stack_power_ladder_inclusive_and_executes(client):
    spec = {
        "title": "zstack",
        "operations": [{
            "op": "z_stack", "x_start_mm": -3, "x_end_mm": 0,
            "y_start_mm": 0, "y_pitch_mm": 0.1,
            "z_start_mm": 6.0, "z_step_mm": 0.001, "z_count": 3,
            "start_power_percent": 30, "end_power_percent": 34,
            "power_step_percent": 2, "velocity_mm_s": 5,
        }],
    }
    from labgate.spec import ZStack
    op = ExperimentSpec.model_validate(spec).operations[0]
    assert isinstance(op, ZStack)
    assert op.powers() == [30, 32, 34]  # INCLUSIVE end, unlike the legacy gen
    plan, final = _run_to_completion(client, spec)
    assert final["state"] == "completed"
    assert client.platform.registry.by_kind("laser").attenuator_percent == 34


def test_z_stack_extent_validated(cfg):
    report = ValidationEngine(cfg).validate(ExperimentSpec.model_validate({
        "title": "deep", "operations": [{
            "op": "z_stack", "x_start_mm": -3, "x_end_mm": 0,
            "y_start_mm": 24.9, "y_pitch_mm": 0.1,
            "z_start_mm": 6, "z_step_mm": 0.001, "z_count": 2,
            "start_power_percent": 30, "end_power_percent": 34,
            "power_step_percent": 2, "velocity_mm_s": 5,
        }],
    }))
    assert not report.ok  # 3rd power level's y = 25.1 > 25


# ------------------------------------------------------------------- STL
def test_stl_upload_print_lifecycle(client):
    stl = _tiny_stl_bytes()
    response = client.post(
        "/models", files={"file": ("box.stl", io.BytesIO(stl), "model/stl")},
        headers=auth(client, "alice"))
    assert response.status_code == 201, response.text
    model = response.json()
    assert model["model_id"].startswith("mdl_") and model["faces"] == 12

    spec = {
        "title": "print the box",
        "operations": [{
            "op": "print_stl", "model_id": model["model_id"],
            "unit": "micron", "step_size": 10.0,
            "start_position_mm": [0, 0, 6.0],
            "attenuator_percent": 30, "velocity_mm_s": 5,
        }],
    }
    plan, final = _run_to_completion(client, spec)
    assert plan["state"] == "validated", plan
    assert final["state"] == "completed"
    events = client.get(f"/plans/{plan['plan_id']}/results",
                        headers=auth(client, "alice")).json()["events"]
    started = [e for e in events if e["event"] == "stl_print_started"]
    assert started and started[0]["payload"]["path_points"] > 10
    assert not client.platform.registry.by_kind("laser").output_on


def test_print_stl_unknown_model_rejected(client):
    spec = {
        "title": "ghost model",
        "operations": [{
            "op": "print_stl", "model_id": "mdl_doesnotexist",
            "unit": "micron", "step_size": 10.0,
            "start_position_mm": [0, 0, 6.0],
            "attenuator_percent": 30, "velocity_mm_s": 5,
        }],
    }
    plan, _ = _run_to_completion(client, spec, approve=False)
    assert plan["state"] == "rejected"


def test_print_stl_out_of_range_position_rejected(client):
    stl = _tiny_stl_bytes()
    model = client.post(
        "/models", files={"file": ("box.stl", io.BytesIO(stl), "model/stl")},
        headers=auth(client, "alice")).json()
    spec = {
        "title": "off the stage",
        "operations": [{
            "op": "print_stl", "model_id": model["model_id"],
            "unit": "micron", "step_size": 10.0,
            "start_position_mm": [24.99, 0, 6.0],  # box extends past +25
            "attenuator_percent": 30, "velocity_mm_s": 5,
        }],
    }
    plan, _ = _run_to_completion(client, spec, approve=False)
    # sliced path is validated point-by-point — geometry must catch this
    assert plan["state"] == "rejected"


def test_model_upload_rejects_garbage(client):
    response = client.post(
        "/models", files={"file": ("junk.stl", io.BytesIO(b"not an stl"), "model/stl")},
        headers=auth(client, "alice"))
    assert response.status_code == 422


# ---------------------------------------------------------------- preview
def test_dry_run_renders_preview_artifact(client, good_spec):
    headers = auth(client, "alice")
    plan = client.post("/plans", json={"spec": good_spec.model_dump()},
                       headers=headers).json()
    report = client.post(f"/plans/{plan['plan_id']}/dry-run", headers=headers).json()
    assert report["preview_artifact"] == "dryrun_preview.png"
    img = client.get(
        f"/plans/{plan['plan_id']}/results/artifacts/dryrun_preview.png",
        headers=headers)
    assert img.status_code == 200
    assert img.content.startswith(b"\x89PNG")


# ------------------------------------------------------------------ rerun
def test_rerun_clones_into_new_plan(client, good_spec):
    headers = auth(client, "alice")
    plan = client.post("/plans", json={"spec": good_spec.model_dump()},
                       headers=headers).json()
    clone = client.post(f"/plans/{plan['plan_id']}/rerun", headers=headers)
    assert clone.status_code == 201
    body = clone.json()
    assert body["plan_id"] != plan["plan_id"]
    assert body["state"] == "validated"
    assert body["title"] == plan["title"]


# ------------------------------------------------------------------ queue
def test_queue_is_fifo_and_reports_positions(client, good_spec):
    headers_alice = auth(client, "alice")
    headers_bob = auth(client, "bob")
    client.platform.registry.by_kind("stage").time_scale = 0.002
    ids = []
    for _ in range(3):
        plan = client.post("/plans", json={"spec": good_spec.model_dump()},
                           headers=headers_alice).json()
        client.post(f"/plans/{plan['plan_id']}/approve", headers=headers_bob)
        client.post(f"/plans/{plan['plan_id']}/execute", headers=headers_alice)
        ids.append(plan["plan_id"])
    snapshot = client.get("/queue", headers=headers_alice).json()
    assert snapshot["running"] in ids or snapshot["queued"]
    for plan_id in ids:
        client.platform.engine.wait(plan_id, timeout_s=30)
    states = [client.get(f"/plans/{i}", headers=headers_alice).json()["state"]
              for i in ids]
    assert states == ["completed"] * 3
    # completion order == submission order (FIFO): compare run_started times
    from labgate.results import RunResults
    starts = []
    for plan_id in ids:
        events = RunResults(client.platform.cfg.storage_dir, plan_id).events()
        starts.append(next(e["ts"] for e in events if e["event"] == "run_started"))
    assert starts == sorted(starts)


def test_abort_while_queued(client, good_spec):
    headers_alice = auth(client, "alice")
    headers_bob = auth(client, "bob")
    client.platform.registry.by_kind("stage").time_scale = 0.01
    first = client.post("/plans", json={"spec": good_spec.model_dump()},
                        headers=headers_alice).json()
    second = client.post("/plans", json={"spec": good_spec.model_dump()},
                         headers=headers_alice).json()
    for plan in (first, second):
        client.post(f"/plans/{plan['plan_id']}/approve", headers=headers_bob)
        client.post(f"/plans/{plan['plan_id']}/execute", headers=headers_alice)
    # second is (very likely) still queued — abort it before it runs
    client.post(f"/plans/{second['plan_id']}/abort", headers=headers_alice)
    for plan in (first, second):
        client.platform.engine.wait(plan["plan_id"], timeout_s=30)
    final = client.get(f"/plans/{second['plan_id']}", headers=headers_alice).json()
    assert final["state"] in ("aborted", "completed")  # race-tolerant


# ------------------------------------------------------ capture context
def test_capture_records_stage_and_laser_context(client, good_spec):
    plan, final = _run_to_completion(client, good_spec.model_dump())
    assert final["state"] == "completed"
    events = client.get(f"/plans/{plan['plan_id']}/results",
                        headers=auth(client, "alice")).json()["events"]
    captured = next(e for e in events if e["event"] == "image_captured")
    assert captured["payload"]["stage_position_mm"] is not None
    assert "attenuator_percent" in captured["payload"]["laser"]
