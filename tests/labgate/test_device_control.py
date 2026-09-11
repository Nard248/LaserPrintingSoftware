"""The device control plane: actions, interlocks, diagnostics, preflight.

These tests exist to pin the *safety* properties of the second control
plane. The plan path is protected by human approval; this path is protected
by tiering and interlocks, and those must not silently erode.
"""

import pytest

from labgate.actions import ActionTier, coerce_and_validate
from labgate.auth import Identity, Role
from labgate.devices.base import ParamSpec
from labgate.errors import ValidationFailed
from labgate.lifecycle import PlanState
from helpers import ALICE, BOB, auth


def _connect_all(client):
    for device in ("stage", "laser", "camera", "white_light"):
        client.post(f"/devices/{device}/connect", headers=auth(client, "alice"))


def _act(client, device, action, params=None, user="alice"):
    return client.post(f"/devices/{device}/actions/{action}",
                       json={"params": params or {}}, headers=auth(client, user))


# ----------------------------------------------------------- declaration
def test_every_declared_action_is_implemented(platform):
    """A declared action with no act_* method is a latent 500."""
    from labgate.actions import bind
    for adapter in platform.registry.adapters():
        for spec in adapter.actions():
            bind(adapter, spec)  # raises if missing


def test_no_adapter_declares_an_expose_action(platform):
    """Opening the shutter must never be reachable from this plane."""
    for adapter in platform.registry.adapters():
        for spec in adapter.actions():
            assert spec.tier is not ActionTier.EXPOSE, (
                f"{adapter.device_id}.{spec.name} is tier 'expose'")


def test_sim_and_declared_bounds_are_discoverable(client):
    response = client.get("/devices/stage/actions", headers=auth(client, "alice"))
    assert response.status_code == 200
    jog = next(a for a in response.json() if a["name"] == "jog")
    distance = next(p for p in jog["params"] if p["name"] == "distance_mm")
    assert distance["min"] == -5.0 and distance["max"] == 5.0
    assert jog["tier"] == "motion" and jog["blocked_when_beam_on"] is True


# ------------------------------------------------------------- lifecycle
def test_action_refused_until_connected(client):
    response = _act(client, "stage", "jog", {"axis": 2, "distance_mm": 0.1})
    assert response.status_code == 502
    assert "not connected" in response.json()["detail"]


def test_bring_up_then_home_and_jog(client):
    _connect_all(client)
    assert _act(client, "stage", "enable_axes").status_code == 200
    assert _act(client, "stage", "home").status_code == 200
    response = _act(client, "stage", "jog", {"axis": 2, "distance_mm": 0.25})
    assert response.status_code == 200
    assert response.json()["data"]["position_mm"] == [0.0, 0.0, 0.25]


def test_stage_actions_require_enabled_axes(platform):
    """The real reason "the stage does not move": axes not commutated."""
    from labgate.errors import DeviceError
    stage = platform.registry.by_kind("stage")
    stage.connect()
    stage._axes_enabled = False          # as a rig stage is before enable
    with pytest.raises(DeviceError, match="not enabled"):
        stage.act_home()
    stage.act_enable_axes()
    stage.act_home()                     # now permitted


# ------------------------------------------------------------ parameters
def test_bounds_rejected(client):
    _connect_all(client)
    response = _act(client, "stage", "jog", {"axis": 0, "distance_mm": 6.0})
    assert response.status_code == 422
    assert "above maximum" in response.json()["detail"]


def test_unknown_and_missing_parameters_rejected(client):
    _connect_all(client)
    assert _act(client, "stage", "jog",
                {"axis": 0, "nudge": 1}).status_code == 422
    assert _act(client, "stage", "jog", {"axis": 0}).status_code == 422  # no distance


def test_non_finite_parameter_rejected():
    spec_params = [ParamSpec(name="distance_mm", type="float", min=-5, max=5)]
    from labgate.actions import ActionSpec
    spec = ActionSpec(name="jog", tier=ActionTier.MOTION, params=spec_params)
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationFailed, match="finite"):
            coerce_and_validate(spec, {"distance_mm": bad})


def test_choices_enforced():
    from labgate.actions import ActionSpec
    spec = ActionSpec(name="set_auto_exposure", tier=ActionTier.PREPARE,
                      params=[ParamSpec(name="mode", type="str",
                                        choices=["off", "once", "continuous"])])
    coerce_and_validate(spec, {"mode": "once"})
    with pytest.raises(ValidationFailed, match="not one of"):
        coerce_and_validate(spec, {"mode": "sometimes"})


# ----------------------------------------------------------------- roles
def test_motion_requires_operator(client):
    _connect_all(client)
    response = _act(client, "stage", "jog",
                    {"axis": 0, "distance_mm": 0.1}, user="bob")  # approver only
    assert response.status_code == 403


def test_read_actions_allowed_for_approver(client):
    _connect_all(client)
    assert _act(client, "stage", "position", user="bob").status_code == 200


def test_roleless_token_cannot_act(client):
    token = client.platform.tokens.issue(Identity(user_id="ghost", roles=set()))
    headers = {"Authorization": f"Bearer {token}"}
    _connect_all(client)
    response = client.post("/devices/stage/actions/position",
                           json={"params": {}}, headers=headers)
    assert response.status_code == 403


# ------------------------------------------------------------ interlocks
def test_motion_refused_while_beam_on(client):
    _connect_all(client)
    client.platform.registry.by_kind("laser").output_on = True
    response = _act(client, "stage", "jog", {"axis": 0, "distance_mm": 0.1})
    assert response.status_code == 502
    assert "output is on" in response.json()["detail"]


def test_halt_permitted_even_with_beam_on(client):
    """A stop path must never be interlocked out — that is the whole point."""
    _connect_all(client)
    client.platform.registry.by_kind("laser").output_on = True
    assert _act(client, "stage", "halt").status_code == 200


def test_motion_refused_while_a_plan_is_running(client, good_spec):
    _connect_all(client)
    client.platform.registry.by_kind("stage").time_scale = 0.02
    plan = client.post("/plans", json={"spec": good_spec.model_dump()},
                       headers=auth(client, "alice")).json()
    pid = plan["plan_id"]
    client.post(f"/plans/{pid}/approve", headers=auth(client, "bob"))
    client.post(f"/plans/{pid}/execute", headers=auth(client, "alice"))
    response = _act(client, "stage", "jog", {"axis": 0, "distance_mm": 0.1})
    client.platform.engine.wait(pid, timeout_s=30)
    assert response.status_code == 409
    assert "plan is running" in response.json()["detail"]
    assert client.platform.store.get(pid).state == PlanState.COMPLETED


def test_beam_on_not_exposed_when_policy_disabled(client):
    _connect_all(client)
    names = [a["name"] for a in
             client.get("/devices/laser/actions", headers=auth(client, "alice")).json()]
    assert "output_on" not in names
    assert _act(client, "laser", "output_on").status_code == 422


def test_beam_on_appears_only_when_policy_allows(cfg):
    from labgate.api.app import Platform
    cfg.allow_manual_beam = True
    platform = Platform(cfg)
    names = [a.name for a in platform.registry.by_kind("laser").actions()]
    assert "output_on" in names


def test_unknown_state_laser_is_treated_as_beam_on(platform):
    """Conservative reading: if we cannot tell, we refuse motion."""
    laser = platform.registry.by_kind("laser")
    laser.connect()
    laser.output_on = None  # unknown
    assert platform.devicectl._beam_is_on() is True


# ---------------------------------------------------------------- camera
def test_snapshot_round_trip(client):
    _connect_all(client)
    response = _act(client, "camera", "snapshot", {"label": "align"})
    assert response.status_code == 200
    url = response.json()["data"]["url"]
    image = client.get(url, headers=auth(client, "alice"))
    assert image.status_code == 200
    assert image.content.startswith(b"\x89PNG")


def test_snapshot_path_traversal_refused(client):
    response = client.get("/snapshots/..%2Faudit.jsonl", headers=auth(client, "alice"))
    assert response.status_code == 404


# ----------------------------------------------------- diagnose/preflight
def test_diagnose_is_read_only_and_reports_remedies(client):
    response = client.post("/devices/stage/diagnose", headers=auth(client, "alice"))
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False  # not connected yet
    connected = next(c for c in body["checks"] if c["check"] == "stage.connected")
    assert connected["remedy"] == "POST /devices/stage/connect"


def test_preflight_blocks_on_essentials_then_clears(client):
    before = client.get("/system/preflight", headers=auth(client, "alice")).json()
    assert before["ready"] is False and before["blockers"] >= 2
    blocked = {c["check"] for c in before["checks"]
               if not c["ok"] and c["severity"] == "blocker"}
    assert {"stage.connected", "laser.connected"} <= blocked

    _connect_all(client)
    after = client.get("/system/preflight", headers=auth(client, "alice")).json()
    assert after["ready"] is True


def test_preflight_camera_is_warning_not_blocker(client):
    """A print can run without imaging; preflight must not claim otherwise."""
    for device in ("stage", "laser"):
        client.post(f"/devices/{device}/connect", headers=auth(client, "alice"))
    report = client.get("/system/preflight", headers=auth(client, "alice")).json()
    assert report["ready"] is True
    camera = next(c for c in report["checks"] if c["check"] == "camera.connected")
    assert camera["severity"] == "warning"


def test_sim_mode_is_always_surfaced(client):
    report = client.get("/system/preflight", headers=auth(client, "alice")).json()
    mode = next(c for c in report["checks"] if c["check"] == "platform.mode")
    assert "SIMULATION" in mode["detail"]


# ---------------------------------------------------------- system/estop
def test_system_status_shape(client):
    _connect_all(client)
    body = client.get("/system/status", headers=auth(client, "alice")).json()
    assert body["mode"] == "sim"
    assert body["devices_connected"] == body["devices_total"] == 4
    assert body["policy"]["allow_manual_beam"] is False
    assert set(body["devices"]) == {"stage", "laser", "camera", "white_light"}


def test_estop_safe_states_everything(client):
    _connect_all(client)
    client.platform.registry.by_kind("laser").output_on = True
    client.platform.registry.by_kind("white_light").on_state = True
    body = client.post("/system/estop", headers=auth(client, "alice")).json()
    assert body["ok"] is True
    assert client.platform.registry.by_kind("laser").output_on is False
    assert client.platform.registry.by_kind("white_light").on_state is False


def test_estop_requires_operator(client):
    assert client.post("/system/estop", headers=auth(client, "bob")).status_code == 403


# ------------------------------------------------------------------ audit
def test_mutating_actions_are_audited_reads_are_not(client):
    _connect_all(client)
    _act(client, "stage", "enable_axes")
    _act(client, "stage", "position")          # read — should not be audited
    events = client.platform.audit.read_all()
    actions = [e for e in events if e["event"] == "device_action"]
    assert any(e["payload"]["action"] == "enable_axes" for e in actions)
    assert not any(e["payload"]["action"] == "position" for e in actions)


def test_motion_intent_audited_before_it_happens(client):
    _connect_all(client)
    _act(client, "stage", "enable_axes")
    _act(client, "stage", "jog", {"axis": 1, "distance_mm": 0.2})
    events = [e for e in client.platform.audit.read_all()
              if e["event"] == "device_action_attempt"]
    assert any(e["payload"]["action"] == "jog" for e in events)


def test_unknown_device_and_action_are_distinguishable(client):
    assert client.post("/devices/microscope/actions/home", json={"params": {}},
                       headers=auth(client, "alice")).status_code == 404
    _connect_all(client)
    assert _act(client, "stage", "teleport").status_code == 422
