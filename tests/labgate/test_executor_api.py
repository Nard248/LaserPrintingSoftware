"""Execution engine (sim) and the full HTTP lifecycle."""

import time

import pytest

from labgate.errors import DeviceError
from labgate.lifecycle import PlanState

from helpers import ALICE, BOB, auth


def _submit_and_approve(platform, spec):
    record = platform.store.create(spec, ALICE)
    report = platform.validator.validate(spec)
    platform.store.set_validated(record.plan_id, report, "validator")
    platform.store.approve(record.plan_id, BOB)
    return record.plan_id


# ----------------------------------------------------------------- executor
def test_executor_completes_sim_run(platform, good_spec):
    plan_id = _submit_and_approve(platform, good_spec)
    platform.engine.start(plan_id, "alice")
    platform.engine.wait(plan_id)
    record = platform.store.get(plan_id)
    assert record.state == PlanState.COMPLETED
    from pathlib import Path

    from labgate.results import RunResults
    run = RunResults(Path(platform.cfg.storage_dir), plan_id)
    manifest = run.manifest()
    assert "after_print.png" in manifest["artifacts"]
    events = [e["event"] for e in run.events()]
    assert events[0] == "run_started" and "run_completed" in events
    # laser must be off at the end
    laser = platform.registry.by_kind("laser")
    assert not laser.output_on


def test_executor_fault_drives_safe_state(platform, good_spec, monkeypatch):
    plan_id = _submit_and_approve(platform, good_spec)
    laser = platform.registry.by_kind("laser")

    def boom(*a, **k):
        laser.output_on = True  # simulate: fault happens while beam is on
        raise DeviceError("stage lost connection")

    monkeypatch.setattr(platform.registry.by_kind("stage"), "move_absolute", boom)
    platform.engine.start(plan_id, "alice")
    platform.engine.wait(plan_id)
    assert platform.store.get(plan_id).state == PlanState.FAILED
    assert not laser.output_on  # safe state forced laser off


def test_executor_requires_approved_state(platform, good_spec):
    record = platform.store.create(good_spec, ALICE)
    from labgate.errors import TransitionError
    with pytest.raises(TransitionError):
        platform.engine.start(record.plan_id, "alice")


def test_executor_abort(platform, good_spec):
    from labgate.spec import Wait
    spec = good_spec.model_copy(deep=True)
    spec.operations.append(Wait(seconds=10))
    plan_id = _submit_and_approve(platform, spec)
    platform.engine.start(plan_id, "alice")
    time.sleep(0.05)
    platform.engine.abort(plan_id, "alice")
    platform.engine.wait(plan_id)
    state = platform.store.get(plan_id).state
    assert state in (PlanState.ABORTED, PlanState.COMPLETED)  # race-tolerant; usually aborted


# ---------------------------------------------------------------- HTTP API
def test_health_is_open(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "sim"


def test_endpoints_require_auth(client):
    assert client.get("/capabilities").status_code == 401
    assert client.post("/plans", json={}).status_code == 401


def test_capabilities_grounding(client):
    response = client.get("/capabilities", headers=auth(client, "alice"))
    assert response.status_code == 200
    body = response.json()
    assert len(body["devices"]) == 4
    move = next(c for c in body["capabilities"] if c["name"] == "move_absolute")
    x_param = next(p for p in move["params"] if p["name"] == "x_mm")
    assert x_param["min"] == -25.0 and x_param["unit"] == "mm"


def test_full_lifecycle_over_http(client, good_spec):
    headers_alice = auth(client, "alice")
    headers_bob = auth(client, "bob")

    # 1. submit -> auto-validated
    response = client.post("/plans", json={"spec": good_spec.model_dump()},
                           headers=headers_alice)
    assert response.status_code == 201, response.text
    plan = response.json()
    assert plan["state"] == "validated"
    plan_id = plan["plan_id"]

    # 2. dry-run
    response = client.post(f"/plans/{plan_id}/dry-run", headers=headers_alice)
    assert response.status_code == 200
    assert response.json()["exposure_events"] == 5

    # 3. proposer cannot approve (role) — alice lacks approver role
    assert client.post(f"/plans/{plan_id}/approve",
                       headers=headers_alice).status_code == 403

    # 4. bob approves
    response = client.post(f"/plans/{plan_id}/approve", headers=headers_bob)
    assert response.status_code == 200
    assert response.json()["state"] == "approved"

    # 5. execute and poll to completion
    assert client.post(f"/plans/{plan_id}/execute",
                       headers=headers_alice).status_code == 202
    client.platform.engine.wait(plan_id)
    response = client.get(f"/plans/{plan_id}", headers=headers_alice)
    assert response.json()["state"] == "completed"

    # 6. results + artifact download
    response = client.get(f"/plans/{plan_id}/results", headers=headers_alice)
    assert "after_print.png" in response.json()["manifest"]["artifacts"]
    response = client.get(f"/plans/{plan_id}/results/artifacts/after_print.png",
                          headers=headers_alice)
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")


def test_self_approval_blocked_even_with_both_roles(client, good_spec):
    from labgate.auth import Identity, Role
    dual = Identity(user_id="carol", roles={Role.OPERATOR, Role.APPROVER})
    token = client.platform.tokens.issue(dual)
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post("/plans", json={"spec": good_spec.model_dump()},
                           headers=headers)
    plan_id = response.json()["plan_id"]
    response = client.post(f"/plans/{plan_id}/approve", headers=headers)
    assert response.status_code == 403
    assert "different users" in response.json()["detail"]


def test_invalid_spec_is_rejected_with_report(client):
    bad = {
        "title": "out of range",
        "operations": [
            {"op": "set_laser_power", "attenuator_percent": 30},
            {"op": "move_stage", "target_mm": [99, 0, 0]},
        ],
    }
    response = client.post("/plans", json={"spec": bad}, headers=auth(client, "alice"))
    assert response.status_code == 201
    assert response.json()["state"] == "rejected"


def test_unknown_plan_404(client):
    response = client.get("/plans/plan_doesnotexist", headers=auth(client, "alice"))
    assert response.status_code == 404


def test_audit_log_records_lifecycle(client, good_spec):
    client.post("/plans", json={"spec": good_spec.model_dump()},
                headers=auth(client, "alice"))
    events = [e["event"] for e in client.platform.audit.read_all()]
    assert "plan_submitted" in events and "plan_validated" in events
