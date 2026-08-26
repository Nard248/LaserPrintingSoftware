"""Validation engine, plan lifecycle, auth rules, dry-run."""

import pytest

from labgate.auth import Identity, Role, TokenStore, require_role
from labgate.dryrun import DryRunEstimator
from labgate.errors import AuthError, TransitionError
from labgate.lifecycle import PlanState, PlanStore
from labgate.spec import ExperimentSpec
from labgate.validation import ValidationEngine

from helpers import ALICE, BOB


def spec_with(ops) -> ExperimentSpec:
    return ExperimentSpec.model_validate({"title": "t", "operations": ops})


# ---------------------------------------------------------------- validation
def test_validation_passes_good_spec(cfg, good_spec):
    report = ValidationEngine(cfg).validate(good_spec)
    assert report.ok, report.summary()


def test_validation_rejects_out_of_range_geometry(cfg):
    report = ValidationEngine(cfg).validate(spec_with([
        {"op": "set_laser_power", "attenuator_percent": 30},
        {"op": "move_stage", "target_mm": [30.0, 0, 0]},
    ]))
    assert not report.ok
    assert any("travel range" in c.detail for c in report.checks if not c.ok)


def test_validation_rejects_array_extent_beyond_range(cfg):
    # y_start 20 + 60 lines * 0.1 pitch = 25.9 > 25
    report = ValidationEngine(cfg).validate(spec_with([
        {"op": "set_laser_power", "attenuator_percent": 30},
        {"op": "write_array", "x_start_mm": 0, "x_end_mm": 5, "y_start_mm": 20.0,
         "y_pitch_mm": 0.1, "line_count": 60, "z_mm": 6, "velocity_mm_s": 5},
    ]))
    assert not report.ok


def test_validation_rejects_write_before_power(cfg):
    report = ValidationEngine(cfg).validate(spec_with([
        {"op": "write_line", "start_mm": [0, 0, 6], "end_mm": [5, 0, 6],
         "velocity_mm_s": 5},
    ]))
    assert not report.ok
    assert any("set_laser_power" in c.detail for c in report.checks if not c.ok)


def test_validation_rejects_wl_during_exposure(cfg):
    report = ValidationEngine(cfg).validate(spec_with([
        {"op": "set_laser_power", "attenuator_percent": 30},
        {"op": "set_white_light", "on": True},
        {"op": "write_line", "start_mm": [0, 0, 6], "end_mm": [5, 0, 6],
         "velocity_mm_s": 5},
    ]))
    assert not report.ok


def test_validation_warns_on_short_line_but_passes(cfg):
    report = ValidationEngine(cfg).validate(spec_with([
        {"op": "set_laser_power", "attenuator_percent": 30},
        {"op": "write_line", "start_mm": [0, 0, 6], "end_mm": [0.5, 0, 6],
         "velocity_mm_s": 5},
    ]))
    assert report.ok  # warning, not error
    assert any(c.severity == "warning" and not c.ok for c in report.checks)


def test_validation_rejects_attenuator_out_of_bounds(cfg):
    report = ValidationEngine(cfg).validate(spec_with([
        {"op": "set_laser_power", "attenuator_percent": 150},
    ]))
    assert not report.ok


# ------------------------------------------------------------------- auth
def test_token_store_round_trip():
    store = TokenStore()
    token = store.issue(ALICE)
    assert store.resolve(token).user_id == "alice"
    with pytest.raises(AuthError):
        store.resolve("nope")


def test_require_role():
    require_role(BOB, Role.APPROVER)
    with pytest.raises(AuthError):
        require_role(ALICE, Role.APPROVER)
    admin = Identity(user_id="root", roles={Role.ADMIN})
    require_role(admin, Role.APPROVER)  # admin implies all


# --------------------------------------------------------------- lifecycle
def test_lifecycle_happy_path(cfg, good_spec, tmp_path):
    store = PlanStore(tmp_path / "s")
    record = store.create(good_spec, ALICE)
    assert record.state == PlanState.DRAFT
    report = ValidationEngine(cfg).validate(good_spec)
    record = store.set_validated(record.plan_id, report, "validator")
    assert record.state == PlanState.VALIDATED
    record = store.approve(record.plan_id, BOB)
    assert record.state == PlanState.APPROVED
    assert record.approver == "bob"


def test_lifecycle_rejects_self_approval(cfg, good_spec, tmp_path):
    store = PlanStore(tmp_path / "s")
    record = store.create(good_spec, ALICE)
    store.set_validated(record.plan_id, ValidationEngine(cfg).validate(good_spec), "v")
    alice_as_approver = Identity(user_id="alice", roles={Role.APPROVER})
    with pytest.raises(AuthError):
        store.approve(record.plan_id, alice_as_approver)


def test_lifecycle_rejects_illegal_transition(cfg, good_spec, tmp_path):
    store = PlanStore(tmp_path / "s")
    record = store.create(good_spec, ALICE)
    with pytest.raises(TransitionError):
        store.approve(record.plan_id, BOB)  # not validated yet
    with pytest.raises(TransitionError):
        store.transition(record.plan_id, PlanState.RUNNING, "x")


def test_plan_persistence_survives_reload(cfg, good_spec, tmp_path):
    store = PlanStore(tmp_path / "s")
    record = store.create(good_spec, ALICE)
    store2 = PlanStore(tmp_path / "s")
    assert store2.get(record.plan_id).spec.title == good_spec.title


# ----------------------------------------------------------------- dry-run
def test_dryrun_estimates_known_spec(cfg, good_spec):
    report = DryRunEstimator(cfg).estimate(good_spec)
    assert report.exposure_events == 5  # 5 lines x 1 rep
    assert report.total_duration_s > 0
    assert report.bounding_box_mm["min"][0] <= -5
    assert len(report.per_op) == len(good_spec.operations)
