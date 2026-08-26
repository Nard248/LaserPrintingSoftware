"""Layer 1 laser primitives.

Builds friendly primitive actions on top of the raw HTTP transport in
`laser_printing.hardware.laser_http`:

    laser.on()                        # wait until IsOutputEnabled is True
    laser.off()                       # wait until IsOutputEnabled is False
    laser.set_attenuator(percentage)  # 0..100 validated, skip if already there
    laser.set_pp_divider(n)           # integer >= 1
    laser.status()                    # parsed status dict
    laser.power_w / laser.frequency_hz / laser.is_on / ...  # convenience props

Why poll for the state change?
    On our PH2 firmware the POST returns in ~10-50 ms but the physical
    output transition takes ~150-350 ms. The old code's 100 ms blind
    `sleep` was too short. Here we actively poll `IsOutputEnabled` and
    record the observed settle time so sync logic can compensate.

Safe to import without the laser connected - all network I/O happens in
explicit methods.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from laser_printing.hardware.laser_http import LaserHttp

logger = logging.getLogger(__name__)


@dataclass
class ToggleMetrics:
    """Latency samples captured the last time we toggled the output."""
    post_ms: float = 0.0
    settle_ms: float = 0.0
    poll_count: int = 0


@dataclass
class LaserState:
    """Cached snapshot of the last known laser state."""
    output_enabled: bool | None = None
    attenuator_pct: float | None = None
    pp_divider: int | None = None
    last_toggle: ToggleMetrics = field(default_factory=ToggleMetrics)


class LaserController:
    """Layer 1 primitives for the Phoebe laser.

    Example:
        with LaserController.from_config(config["laser"]) as laser:
            laser.set_attenuator(30)
            laser.set_pp_divider(1)
            laser.on()
            ...
            laser.off()

    Design:
      - `connect()` reads the current hardware state into the local cache.
      - `on()` / `off()` POST the endpoint and **poll until the status
        actually reflects the change**, up to `settle_timeout_s`.
      - `set_attenuator` / `set_pp_divider` short-circuit if already at
        target to avoid unnecessary commands.
    """

    def __init__(
        self,
        ip: str = LaserHttp.DEFAULT_IP,
        api_base: str = LaserHttp.DEFAULT_BASE,
        settle_timeout_s: float = 1.0,
        poll_interval_s: float = 0.01,
    ):
        self._http = LaserHttp(ip, api_base)
        self._settle_timeout_s = settle_timeout_s
        self._poll_interval_s = poll_interval_s
        self._state = LaserState()
        self._connected = False

    @classmethod
    def from_config(cls, cfg: dict) -> "LaserController":
        return cls(
            ip=cfg["ip"],
            api_base=cfg.get("api_base", LaserHttp.DEFAULT_BASE),
            settle_timeout_s=cfg.get("settle_timeout_s", 1.0),
            poll_interval_s=cfg.get("poll_interval_s", 0.01),
        )

    # -- Lifecycle --------------------------------------------------------

    def connect(self) -> None:
        """Read hardware state into the cache. Does NOT touch the output."""
        s = self._http.get_status()
        self._state.output_enabled = bool(s["IsOutputEnabled"])
        self._state.attenuator_pct = float(s.get("TargetAttenuatorPercentage", 0.0))
        self._state.pp_divider = int(s.get("TargetPpDivider", 1))
        self._connected = True
        logger.info(
            "Laser connected. enabled=%s attenuator=%.1f%% divider=%d",
            self._state.output_enabled, self._state.attenuator_pct,
            self._state.pp_divider,
        )

    def disconnect(self, leave_output: bool | None = None) -> None:
        """Optionally set output state, then release the HTTP session.

        Args:
            leave_output: If False/True, force that output state before
                closing. If None (default), leave the output as-is - useful
                when other users/software are co-operating on the laser.
        """
        if self._connected and leave_output is not None:
            if leave_output:
                try:
                    self.on()
                except Exception:
                    logger.warning("Failed to enable output on disconnect",
                                   exc_info=True)
            else:
                try:
                    self.off()
                except Exception:
                    logger.warning("Failed to disable output on disconnect",
                                   exc_info=True)
        self._http.close()
        self._connected = False

    def __enter__(self) -> "LaserController":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # On any context-manager exit, turn the laser off for safety.
        self.disconnect(leave_output=False)

    # -- Status / cache ---------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Full live status dict from the laser."""
        return self._http.get_status()

    @property
    def is_on(self) -> bool | None:
        return self._state.output_enabled

    @property
    def attenuator_pct(self) -> float | None:
        return self._state.attenuator_pct

    @property
    def pp_divider(self) -> int | None:
        return self._state.pp_divider

    @property
    def last_toggle(self) -> ToggleMetrics:
        return self._state.last_toggle

    def power_w(self) -> float:
        return float(self.status().get("ActualOutputPower", 0.0))

    def frequency_hz(self) -> float:
        return float(self.status().get("ActualOutputFrequency", 0.0)) * 1000.0

    # -- Primitive actions -----------------------------------------------

    def on(self) -> ToggleMetrics:
        """Enable output and wait until the status confirms it.

        Returns a `ToggleMetrics` with the measured post + settle times.
        """
        return self._set_output(True)

    def off(self, force: bool = False) -> ToggleMetrics:
        """Disable output and wait until the status confirms it.

        force=True bypasses the cached-state short-circuit and always POSTs
        CloseOutput + polls — the safety path (safe_state) must use this,
        since after a failed toggle the cache may be stale/unknown.
        """
        return self._set_output(False, force=force)

    def set_attenuator(self, percentage: float) -> None:
        """Set target attenuator percentage (0..100).

        No-op if already at the target.
        """
        if not (0.0 <= float(percentage) <= 100.0):
            raise ValueError(
                f"Attenuator percentage must be in [0, 100], got {percentage}")
        if self._state.attenuator_pct is not None and \
                abs(self._state.attenuator_pct - float(percentage)) < 1e-6:
            return
        self._http.set_target_attenuator(percentage)
        self._state.attenuator_pct = float(percentage)
        logger.info("Attenuator target set to %.2f%%", percentage)

    def set_pp_divider(self, divider: int) -> None:
        """Set pulse-picker divider (>= 1)."""
        divider = int(divider)
        if divider < 1:
            raise ValueError(f"PP divider must be >= 1, got {divider}")
        if self._state.pp_divider == divider:
            return
        self._http.set_target_pp_divider(divider)
        self._state.pp_divider = divider
        logger.info("PP divider set to %d", divider)

    # -- Internals -------------------------------------------------------

    def _set_output(self, enable: bool, force: bool = False) -> ToggleMetrics:
        if not force and self._state.output_enabled is enable:
            metrics = ToggleMetrics()
            self._state.last_toggle = metrics
            return metrics

        # From the moment we attempt the POST the physical state is uncertain:
        # any failure below poisons the cache to None (unknown) so no later
        # on()/off() can short-circuit on stale state. The firmware may have
        # accepted an ON POST even though our settle poll failed.
        try:
            t0 = time.perf_counter()
            if enable:
                self._http.enable_output()
            else:
                self._http.close_output()
            post_ms = (time.perf_counter() - t0) * 1000

            # Poll IsOutputEnabled until it matches the requested state.
            settle_start = time.perf_counter()
            polls = 0
            deadline = settle_start + self._settle_timeout_s
            while True:
                polls += 1
                s = self._http.get_status()
                if bool(s["IsOutputEnabled"]) is enable:
                    break
                if time.perf_counter() >= deadline:
                    raise TimeoutError(
                        f"Laser did not reach IsOutputEnabled={enable} in "
                        f"{self._settle_timeout_s:.2f}s "
                        f"(state={s.get('ActualStateName')!r})"
                    )
                if self._poll_interval_s > 0:
                    time.sleep(self._poll_interval_s)
            settle_ms = (time.perf_counter() - settle_start) * 1000
        except Exception:
            self._state.output_enabled = None  # unknown — never trust it again
            raise

        self._state.output_enabled = enable
        metrics = ToggleMetrics(post_ms=post_ms, settle_ms=settle_ms,
                                poll_count=polls)
        self._state.last_toggle = metrics
        logger.debug("Laser %s: post=%.1fms settle=%.1fms polls=%d",
                     "ON" if enable else "OFF", post_ms, settle_ms, polls)
        return metrics
