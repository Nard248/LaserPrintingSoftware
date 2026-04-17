"""Layer 0 transport for the Phoebe laser.

Raw HTTP REST wrapper - no caching, no policy, no validation.
Endpoints measured on PH2 firmware:

    GET  /phoebe/v0/Basic                         -> full status JSON
    PUT  /phoebe/v0/Basic/TargetAttenuatorPercentage <body: "30">
    PUT  /phoebe/v0/Basic/TargetPpDivider             <body: "1">
    POST /phoebe/v0/Basic/EnableOutput                <body: {}>
    POST /phoebe/v0/Basic/CloseOutput                 <body: {}>

Typical status keys:
    IsOutputEnabled, IsOutputOpen, IsEmissionWarningActive,
    ActualStateName, ActualStateName2,
    ActualAttenuatorPercentage, TargetAttenuatorPercentage,
    ActualPpDivider, TargetPpDivider,
    ActualOutputPower, ActualOutputFrequency, ActualHarmonic,
    Errors (list), Warnings (list).
"""
from __future__ import annotations

from typing import Any

import requests


class LaserHttp:
    """Thin HTTP transport for the Phoebe REST API."""

    DEFAULT_IP = "192.168.244.10"
    DEFAULT_BASE = "/phoebe/v0/Basic"

    def __init__(self, ip: str = DEFAULT_IP, api_base: str = DEFAULT_BASE,
                 timeout_s: float = 5.0):
        self.ip = ip
        self.base_url = f"http://{ip}{api_base}"
        self.timeout_s = timeout_s
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Origin": f"http://{ip}",
            "Referer": f"http://{ip}/basic",
        })

    # -- Primitive verbs --------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """GET /Basic -> full status JSON."""
        r = self._session.get(self.base_url, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    def put(self, endpoint: str, value: str | int | float) -> int:
        """PUT /Basic/<endpoint> with the string form of `value` as the body.

        Returns the HTTP status code (usually 200).
        """
        url = f"{self.base_url}/{endpoint}"
        r = self._session.put(url, data=str(value), timeout=self.timeout_s)
        r.raise_for_status()
        return r.status_code

    def post(self, endpoint: str) -> int:
        """POST /Basic/<endpoint> with an empty JSON body.

        Returns the HTTP status code.
        """
        url = f"{self.base_url}/{endpoint}"
        r = self._session.post(url, json={}, timeout=self.timeout_s)
        r.raise_for_status()
        return r.status_code

    # -- Convenience endpoint names (one per firmware verb) ---------------

    def enable_output(self) -> int:
        return self.post("EnableOutput")

    def close_output(self) -> int:
        return self.post("CloseOutput")

    def set_target_attenuator(self, percentage: float) -> int:
        return self.put("TargetAttenuatorPercentage", percentage)

    def set_target_pp_divider(self, divider: int) -> int:
        return self.put("TargetPpDivider", int(divider))

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._session.close()

    def __enter__(self) -> "LaserHttp":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
