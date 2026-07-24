"""
Base class for laser printing experiments.

Provides the common lifecycle that all experiments share:

    1. Connect to hardware (stage + laser)
    2. Set up initial conditions
    3. Loop over experimental conditions with progress reporting
    4. Save results (CSV, JSON, logs)
    5. Guaranteed safe shutdown (laser off, stage to home)

Concrete experiments subclass BaseExperiment and implement:
    - _define_conditions(): return list of condition dicts
    - _run_condition(index, condition): execute one experimental condition

The base class handles everything else: timing, progress bars, logging,
error recovery, and result saving.
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import pandas as pd

from laser_printing.config import load_config

# Controller imports are deferred to _connect_hardware so this module (and
# every experiment class) is importable and testable on machines without
# the SPiiPlusPython vendor wheel.

logger = logging.getLogger(__name__)


class BaseExperiment(ABC):
    """Base class for all laser printing experiments.

    Usage:
        class MyExperiment(BaseExperiment):
            def _define_conditions(self):
                return [{"power": p} for p in [10, 20, 30]]

            def _run_condition(self, index, condition):
                self.laser.set_attenuator(condition["power"])
                self.sync.execute_print_line(...)
                return {"measured_value": 42}

        exp = MyExperiment(
            experiment_id="my_exp_01",
            output_dir="./results",
            config_path="config/default.yaml",
            z_focus=6.0,
        )
        exp.run()
    """

    def __init__(self, experiment_id: str, output_dir: str | Path,
                 config_path: str | Path | None = None,
                 z_focus: float = 0.0,
                 description: str = ""):
        """Initialize the experiment.

        Args:
            experiment_id: Unique name for this experiment run.
            output_dir: Directory where results will be saved.
            config_path: Path to YAML config file (None = default config).
            z_focus: Z-axis focal plane height in mm.
            description: Human-readable experiment description.
        """
        self.experiment_id = experiment_id
        self.output_dir = Path(output_dir) / experiment_id
        self.config = load_config(config_path)
        self.z_focus = z_focus
        self.description = description

        # Hardware controllers (initialized in setup)
        self.laser: LaserController | None = None
        self.stage: StageController | None = None
        self.sync: PrintSynchronizer | None = None

        # Results accumulator
        self._results: list[dict[str, Any]] = []

    def run(self) -> pd.DataFrame:
        """Execute the full experiment lifecycle.

        Returns:
            DataFrame with one row per condition and all recorded metrics.
        """
        self._setup_output_dir()
        self._setup_logging()

        logger.info("=" * 60)
        logger.info("EXPERIMENT: %s", self.experiment_id)
        logger.info("=" * 60)
        logger.info("Description: %s", self.description)

        try:
            self._connect_hardware()
            conditions = self._define_conditions()
            logger.info("Total conditions: %d", len(conditions))

            start_time = time.time()
            for i, condition in enumerate(conditions):
                elapsed = time.time() - start_time
                if i > 0:
                    avg_per_condition = elapsed / i
                    remaining = avg_per_condition * (len(conditions) - i)
                    remaining_min = remaining / 60
                    logger.info(
                        "--- Condition %d/%d | Elapsed: %.1fs | "
                        "Remaining: ~%.1f min ---",
                        i + 1, len(conditions), elapsed, remaining_min
                    )
                else:
                    logger.info("--- Condition %d/%d ---", i + 1, len(conditions))

                logger.info("Parameters: %s", condition)

                condition_start = time.time()
                try:
                    result = self._run_condition(i, condition)
                except Exception:
                    logger.error(
                        "Condition %d failed!", i, exc_info=True
                    )
                    result = {"error": True}
                    # A faulted condition must not leave the beam on while
                    # we carry on to the next condition (or fall through to
                    # teardown). force=True bypasses a possibly-stale cache.
                    if self.laser is not None:
                        try:
                            self.laser.off(force=True)
                        except Exception:
                            logger.critical(
                                "LASER MAY STILL BE ON — off() failed after "
                                "condition fault", exc_info=True)

                condition_time = time.time() - condition_start
                # Merge condition params + measured results + timing
                row = {**condition, **result, "execution_time_s": condition_time}
                self._results.append(row)

            total_time = time.time() - start_time
            logger.info("=" * 60)
            logger.info("EXPERIMENT COMPLETE in %.1f s (%.1f min)",
                        total_time, total_time / 60)

        except Exception:
            logger.error("Experiment failed!", exc_info=True)
            raise
        finally:
            self._disconnect_hardware()

        results_df = pd.DataFrame(self._results)
        self._save_results(results_df)
        return results_df

    # -- Abstract methods (implement in subclasses) -----------------------

    @abstractmethod
    def _define_conditions(self) -> list[dict[str, Any]]:
        """Define the list of experimental conditions.

        Returns:
            List of dicts, each mapping parameter names to values.
        """

    @abstractmethod
    def _run_condition(self, index: int, condition: dict[str, Any]) -> dict[str, Any]:
        """Execute a single experimental condition.

        Args:
            index: Zero-based condition index.
            condition: Parameter dict from _define_conditions().

        Returns:
            Dict of measured results/metrics for this condition.
        """

    # -- Hardware lifecycle ------------------------------------------------

    def _connect_hardware(self) -> None:
        """Connect to laser and stage, initialize synchronizer."""
        from laser_printing.controllers.laser import LaserController
        from laser_printing.controllers.stage import StageController
        from laser_printing.controllers.sync import PrintSynchronizer

        self.laser = LaserController.from_config(self.config["laser"])
        self.laser.connect()

        self.stage = StageController.from_config(self.config["stage"])
        # Home is fixed at [0, 0, 0] (project convention; connect() enforces
        # it). The focal plane is reached by each experiment's own path
        # blocks, which carry z_focus explicitly.
        self.stage.connect()

        self.sync = PrintSynchronizer.from_config(
            self.config, self.laser, self.stage
        )
        logger.info("Hardware connected.")

    def _disconnect_hardware(self) -> None:
        """Safely disconnect all hardware (guaranteed to run)."""
        if self.laser:
            try:
                # Force output off before the stage homes under the beam —
                # "guaranteed safe shutdown" means laser off, not as-is.
                self.laser.disconnect(leave_output=False)
            except Exception:
                logger.warning("Laser disconnect failed.", exc_info=True)
        if self.stage:
            try:
                self.stage.disconnect()
            except Exception:
                logger.warning("Stage disconnect failed.", exc_info=True)
        logger.info("Hardware disconnected.")

    # -- Output management ------------------------------------------------

    def _setup_output_dir(self) -> None:
        """Create the experiment output directory structure."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "LOGS").mkdir(exist_ok=True)

    def _setup_logging(self) -> None:
        """Configure logging to both console and experiment log file."""
        log_level = getattr(
            logging, self.config["logging"].get("level", "INFO").upper()
        )

        # File handler for this experiment
        log_file = self.output_dir / "LOGS" / "experiment.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))

        # Add to root logger so all modules' log messages are captured
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        root_logger.addHandler(file_handler)

        # Also ensure console output
        if not any(isinstance(h, logging.StreamHandler)
                   and not isinstance(h, logging.FileHandler)
                   for h in root_logger.handlers):
            console = logging.StreamHandler()
            console.setLevel(log_level)
            console.setFormatter(logging.Formatter(
                "[%(levelname)s] %(name)s: %(message)s"
            ))
            root_logger.addHandler(console)

    def _save_results(self, results_df: pd.DataFrame) -> None:
        """Save experiment results to CSV and description to text file."""
        results_df.to_csv(self.output_dir / "experiment_design.csv", index=False)
        logger.info("Results saved to %s", self.output_dir / "experiment_design.csv")

        with open(self.output_dir / "description.txt", "w") as f:
            f.write(self.description)
