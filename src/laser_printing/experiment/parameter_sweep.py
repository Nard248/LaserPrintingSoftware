"""
Unified parameter sweep experiment.

Merges the functionality of the original parameter_optimization.py and
parameter_optimization_for_2pp.py into a single configurable experiment.

Supports two modes:

    1. "line_doe" - Full factorial Design of Experiments with single horizontal
       lines. Varies: attenuator %, pulse divider, motor speed, line repetitions.
       (Original: parameter_optimization.py)

    2. "z_stack" - Z-stack power sweep for two-photon polymerization.
       At each power level, prints a vertical stack of lines.
       (Original: parameter_optimization_for_2pp.py)

Both modes include indicator lines at experiment boundaries and save
complete experiment metadata (CSV, JSON path data, description).
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from laser_printing.experiment.base import BaseExperiment
from laser_printing.path_generation.coordinates import (
    lines_with_pitch,
    z_stacks_with_power,
)
from laser_printing.utils.doe import ExperimentDesign
from laser_printing.utils.io import save_path_data

logger = logging.getLogger(__name__)


class ParameterSweep(BaseExperiment):
    """Configurable parameter sweep experiment for line printing.

    Example (line DOE):
        exp = ParameterSweep(
            experiment_id="ParOpt_01",
            output_dir="./results",
            z_focus=35.88,
            mode="line_doe",
            x_start=-20, x_end=-16,
            y_start=-22, y_pitch=0.1,
            parameters=pd.DataFrame({
                "AP (%)": [10, 20, 30],
                "RRD": [10, 5, 1],
                "MS (mm/s)": [1, 3, 5],
                "LR": [1, 3, 5],
            }),
            replication_count=5,
        )
        results = exp.run()

    Example (z-stack):
        exp = ParameterSweep(
            experiment_id="2PP_ZStack_01",
            output_dir="./results",
            z_focus=5.9981,
            mode="z_stack",
            x_start=4, x_end=-1,
            y_start=8, y_pitch=0.03,
            z_step=0.001, z_count=5,
            start_power=30, end_power=42, power_step=2,
        )
        results = exp.run()
    """

    def __init__(
        self,
        experiment_id: str,
        output_dir: str,
        z_focus: float,
        mode: str = "line_doe",
        x_start: float = 0,
        x_end: float = 0,
        y_start: float = 0,
        y_pitch: float = 0.1,
        # line_doe mode parameters
        parameters: pd.DataFrame | None = None,
        replication_count: int = 1,
        # z_stack mode parameters
        z_step: float = 0.001,
        z_count: int = 5,
        start_power: int = 30,
        end_power: int = 42,
        power_step: int = 2,
        motor_velocity: float = 5.0,
        repetitions: int = 1,
        # common
        config_path: str | None = None,
        description: str = "",
    ):
        super().__init__(experiment_id, output_dir, config_path, z_focus, description)

        self.mode = mode
        self.x_start = x_start
        self.x_end = x_end
        self.y_start = y_start
        self.y_pitch = y_pitch

        # line_doe
        self.parameters = parameters
        self.replication_count = replication_count

        # z_stack
        self.z_step = z_step
        self.z_count = z_count
        self.start_power = start_power
        self.end_power = end_power
        self.power_step = power_step
        self.motor_velocity = motor_velocity
        self.repetitions = repetitions

        # Generated during setup
        self._blocks = []
        self._all_path_data = []

    def _define_conditions(self) -> list[dict[str, Any]]:
        if self.mode == "line_doe":
            return self._define_line_doe_conditions()
        elif self.mode == "z_stack":
            return self._define_z_stack_conditions()
        else:
            raise ValueError(f"Unknown mode: {self.mode}")

    def _run_condition(self, index: int, condition: dict[str, Any]) -> dict[str, Any]:
        if self.mode == "line_doe":
            return self._run_line_doe_condition(index, condition)
        elif self.mode == "z_stack":
            return self._run_z_stack_condition(index, condition)
        return {}

    # -- line_doe mode ----------------------------------------------------

    def _define_line_doe_conditions(self) -> list[dict[str, Any]]:
        """Generate full-factorial conditions from the parameter table."""
        if self.parameters is None:
            raise ValueError("parameters DataFrame required for line_doe mode")

        design = ExperimentDesign(self.parameters)
        replicated = design.replicate(self.replication_count)

        # Generate all line blocks
        self._blocks = lines_with_pitch(
            x_start=self.x_start, x_end=self.x_end,
            y_start=self.y_start, y_pitch=self.y_pitch,
            z_focus=self.z_focus, count=len(replicated),
            repetitions=replicated["LR"].tolist()
                if "LR" in replicated.columns else 1,
        )

        # Save all path data
        save_path_data(self._blocks, self.output_dir / "lines_and_laser_states.json")

        # First block is home positioning - execute it now
        if self._blocks:
            self.stage.move_to(
                [self._blocks[0][0][0][0],
                 self._blocks[0][0][0][1],
                 self._blocks[0][0][0][2]]
            )

        # Return conditions list (one per line block after the home block)
        conditions = []
        for i in range(len(replicated)):
            row = replicated.iloc[i].to_dict()
            conditions.append(row)
        return conditions

    def _run_line_doe_condition(self, index: int,
                                condition: dict[str, Any]) -> dict[str, Any]:
        """Execute one line with the given DOE parameters."""
        # Apply laser parameters
        if "AP (%)" in condition:
            self.laser.set_attenuator(condition["AP (%)"])
        if "RRD" in condition:
            self.laser.set_pp_divider(int(condition["RRD"]))
        if "MS (mm/s)" in condition:
            self.stage.set_velocity(condition["MS (mm/s)"])

        # Execute the line block (index+1 because blocks[0] is home)
        block_index = index + 1
        if block_index < len(self._blocks):
            block = self._blocks[block_index]
            self.sync.execute_path(block)

        # Record Y position for the results
        y_pos = self.y_start + index * self.y_pitch
        return {"y_position_mm": y_pos}

    # -- z_stack mode -----------------------------------------------------

    def _define_z_stack_conditions(self) -> list[dict[str, Any]]:
        """Generate z-stack conditions with power sweep."""
        self._blocks, power_per_line = z_stacks_with_power(
            x_start=self.x_start, x_end=self.x_end,
            y_start=self.y_start, y_pitch=self.y_pitch,
            z_focus=self.z_focus, z_step=self.z_step,
            z_count=self.z_count,
            start_power=self.start_power, end_power=self.end_power,
            power_step=self.power_step, repetitions=self.repetitions,
        )

        save_path_data(self._blocks, self.output_dir / "lines_and_laser_states.json")

        # Set motor velocity
        self.stage.set_velocity(self.motor_velocity)

        # Home positioning
        if self._blocks:
            home = self._blocks[0][0][0]
            self.stage.move_to(list(home))

        conditions = []
        for i, power in enumerate(power_per_line):
            conditions.append({"power_pct": power, "line_index": i})
        return conditions

    def _run_z_stack_condition(self, index: int,
                               condition: dict[str, Any]) -> dict[str, Any]:
        """Execute one line within a z-stack power sweep."""
        self.laser.set_attenuator(condition["power_pct"])

        block_index = index + 1
        if block_index < len(self._blocks):
            block = self._blocks[block_index]
            self.sync.execute_path(block)

        y_pos = block[0][0][1] if block_index < len(self._blocks) else 0
        return {"y_position_mm": y_pos}
