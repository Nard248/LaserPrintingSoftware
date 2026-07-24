"""Shared fixtures: a simulation-mode platform and role-bearing tokens."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))  # make helpers importable
from helpers import ALICE, BOB  # noqa: E402

from labgate.api.app import Platform, create_app
from labgate.config import LabgateConfig
from labgate.spec import ExperimentSpec


@pytest.fixture()
def cfg(tmp_path) -> LabgateConfig:
    return LabgateConfig(mode="sim", storage_dir=tmp_path / "data")


@pytest.fixture()
def platform(cfg) -> Platform:
    return Platform(cfg)


@pytest.fixture()
def client(cfg):
    app = create_app(cfg)
    platform = app.state.platform
    tokens = {
        "alice": platform.tokens.issue(ALICE),
        "bob": platform.tokens.issue(BOB),
    }
    client = TestClient(app)
    client.tokens = tokens
    client.platform = platform
    return client


@pytest.fixture()
def good_spec() -> ExperimentSpec:
    return ExperimentSpec.model_validate({
        "title": "5x5 parameter array",
        "operations": [
            {"op": "set_laser_power", "attenuator_percent": 30, "pp_divider": 1},
            {"op": "write_array", "x_start_mm": -5, "x_end_mm": 0, "y_start_mm": -5,
             "y_pitch_mm": 0.1, "line_count": 5, "z_mm": 6.0, "velocity_mm_s": 5.0},
            {"op": "capture_image", "label": "after_print"},
        ],
    })
