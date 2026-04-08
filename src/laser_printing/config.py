"""
Configuration management for the laser printing system.

Loads settings from YAML files and provides typed access to all
hardware and experiment parameters. The config is a plain dict
so any module can read what it needs without coupling to this file.
"""

from pathlib import Path
from typing import Any

import yaml

# Project root is three levels up from this file: src/laser_printing/config.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "default.yaml"


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load configuration from a YAML file.

    Args:
        path: Path to YAML config file. If None, loads config/default.yaml.

    Returns:
        Configuration dictionary with sections: laser, stage, synchronization, logging.
    """
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        config = yaml.safe_load(f)

    _validate(config)
    return config


def _validate(config: dict) -> None:
    """Check that required config sections and keys exist."""
    required_sections = ["laser", "stage", "synchronization", "logging"]
    for section in required_sections:
        if section not in config:
            raise ValueError(f"Config missing required section: '{section}'")

    laser = config["laser"]
    if not laser.get("ip"):
        raise ValueError("Config laser.ip must be set")

    stage = config["stage"]
    if not stage.get("ip"):
        raise ValueError("Config stage.ip must be set")
    if not stage.get("axes"):
        raise ValueError("Config stage.axes must be a non-empty list")

    sync = config["synchronization"]
    valid_strategies = ("sequential", "velocity_timed")
    if sync.get("strategy") not in valid_strategies:
        raise ValueError(
            f"Config synchronization.strategy must be one of {valid_strategies}"
        )
