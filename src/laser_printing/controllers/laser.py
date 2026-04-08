"""
Controller for the Phoebe femtosecond pulsed laser via HTTP REST API.

The laser exposes a browser-accessible control panel at its IP address.
Under the hood it's a standard REST API that we wrap here. Key controls:

    - Attenuator percentage (0-100%): controls beam power via mechanical attenuator
    - Pulse-picker divider (1+): divides the base repetition rate
    - Output enable/disable: master switch for laser emission

Hardware: Phoebe PH2, 515nm, ~200 kHz base repetition rate
Protocol: HTTP REST at http://<ip>/phoebe/v0/Basic
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)


class LaserController:
    """Controls the Phoebe laser via its HTTP REST API.

    Usage:
        # Explicit lifecycle
        laser = LaserController(ip="192.168.244.10")
        laser.connect()
        laser.set_attenuator(30)
        laser.enable_output()
        ...
        laser.disconnect()

        # Or as a context manager (preferred)
        with LaserController.from_config(config["laser"]) as laser:
            laser.set_attenuator(30)
            laser.enable_output()
    """

    def __init__(self, ip: str, api_base: str = "/phoebe/v0/Basic",
                 command_delay_s: float = 0.1):
        """Initialize controller (does NOT connect yet).

        Args:
            ip: Laser's IP address (e.g. "192.168.244.10").
            api_base: REST API base path.
            command_delay_s: Delay after each command for hardware stabilization.
        """
        self._ip = ip
        self._base_url = f"http://{ip}{api_base}"
        self._command_delay_s = command_delay_s
        self._headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Origin": f"http://{ip}",
            "Referer": f"http://{ip}/basic",
        }
        # Cached state to avoid redundant hardware commands.
        # None means "unknown" (not yet read from hardware).
        self._attenuator_pct: float | None = None
        self._pp_divider: int | None = None
        self._output_enabled: bool | None = None
        self._connected = False

    @classmethod
    def from_config(cls, laser_config: dict) -> LaserController:
        """Create a LaserController from the 'laser' section of the config."""
        return cls(
            ip=laser_config["ip"],
            api_base=laser_config.get("api_base", "/phoebe/v0/Basic"),
            command_delay_s=laser_config.get("command_delay_s", 0.1),
        )

    # -- Lifecycle --------------------------------------------------------

    def connect(self) -> None:
        """Verify the laser is reachable and initialize to a safe state."""
        logger.info("Connecting to laser at %s ...", self._base_url)
        status = self.get_status()
        logger.info("Laser status: %s", status)
        # Start from a known safe state: output disabled
        self.disable_output()
        self._connected = True
        logger.info("Laser connected and output disabled.")

    def disconnect(self) -> None:
        """Ensure laser output is disabled on shutdown."""
        if self._connected:
            try:
                self.disable_output()
                logger.info("Laser disconnected (output disabled).")
            except Exception:
                logger.warning("Failed to disable laser output during disconnect.",
                               exc_info=True)
            self._connected = False

    def __enter__(self) -> LaserController:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()

    # -- Properties (cached state) ----------------------------------------

    @property
    def attenuator_pct(self) -> float | None:
        """Last-set attenuator percentage, or None if unknown."""
        return self._attenuator_pct

    @property
    def pp_divider(self) -> int | None:
        """Last-set pulse-picker divider, or None if unknown."""
        return self._pp_divider

    @property
    def output_enabled(self) -> bool | None:
        """Whether laser output is currently enabled, or None if unknown."""
        return self._output_enabled

    # -- Commands ---------------------------------------------------------

    def set_attenuator(self, percentage: float) -> None:
        """Set the attenuator to a given power percentage (0-100).

        Skips the command if the attenuator is already at the requested value
        (the mechanical attenuator is slow to move).
        """
        if not (0 <= percentage <= 100):
            raise ValueError(
                f"Attenuator percentage must be 0-100, got {percentage}"
            )
        if percentage == self._attenuator_pct:
            logger.debug("Attenuator already at %.1f%%, skipping.", percentage)
            return
        self._put("TargetAttenuatorPercentage", str(percentage))
        self._attenuator_pct = percentage
        logger.info("Attenuator set to %.1f%%", percentage)

    def set_pp_divider(self, divider: int) -> None:
        """Set the pulse-picker divider (>= 1).

        Divider of 1 = full repetition rate. Divider of N = rate / N.
        """
        divider = int(divider)
        if divider < 1:
            raise ValueError(f"PP divider must be >= 1, got {divider}")
        if divider == self._pp_divider:
            logger.debug("PP divider already at %d, skipping.", divider)
            return
        self._put("TargetPpDivider", str(divider))
        self._pp_divider = divider
        logger.info("PP divider set to %d", divider)

    def enable_output(self) -> None:
        """Enable laser emission (master switch ON)."""
        if self._output_enabled is True:
            return
        self._post("EnableOutput")
        self._output_enabled = True
        logger.debug("Laser output ENABLED")

    def disable_output(self) -> None:
        """Disable laser emission (master switch OFF)."""
        if self._output_enabled is False:
            return
        self._post("CloseOutput")
        self._output_enabled = False
        logger.debug("Laser output DISABLED")

    def get_status(self) -> dict[str, Any]:
        """Query the laser's current status (power, mode, temperatures, etc.)."""
        response = requests.get(
            self._base_url, headers=self._headers, verify=False, timeout=5
        )
        response.raise_for_status()
        return response.json()

    # -- Internal helpers -------------------------------------------------

    def _put(self, endpoint: str, data: str) -> None:
        """Send a PUT request to set a parameter value."""
        url = f"{self._base_url}/{endpoint}"
        response = requests.put(
            url, headers=self._headers, data=data, verify=False, timeout=5
        )
        response.raise_for_status()
        time.sleep(self._command_delay_s)

    def _post(self, endpoint: str) -> None:
        """Send a POST request to trigger an action (enable/disable)."""
        url = f"{self._base_url}/{endpoint}"
        response = requests.post(
            url, headers=self._headers, json={}, verify=False, timeout=5
        )
        response.raise_for_status()
        time.sleep(self._command_delay_s)
