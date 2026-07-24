"""Capability registry — the machine-readable ground truth clients plan against."""

from __future__ import annotations

from .devices.base import Capability, DeviceAdapter, DeviceState
from .errors import LabgateError


class CapabilityRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, DeviceAdapter] = {}

    def register(self, adapter: DeviceAdapter) -> None:
        if adapter.device_id in self._adapters:
            raise LabgateError(f"duplicate device_id: {adapter.device_id}")
        self._adapters[adapter.device_id] = adapter

    def adapter(self, device_id: str) -> DeviceAdapter:
        return self._adapters[device_id]

    def by_kind(self, kind: str) -> DeviceAdapter:
        matches = [a for a in self._adapters.values() if a.kind == kind]
        if len(matches) != 1:
            raise LabgateError(f"expected exactly one '{kind}' device, found {len(matches)}")
        return matches[0]

    def adapters(self) -> list[DeviceAdapter]:
        return list(self._adapters.values())

    def capabilities(self) -> list[Capability]:
        return [c for a in self._adapters.values() for c in a.capabilities()]

    def device_states(self) -> list[DeviceState]:
        return [a.state() for a in self._adapters.values()]

    def snapshot(self) -> dict:
        """JSON-able registry dump; the payload of GET /capabilities.

        Shaped so each capability can later map 1:1 onto an MCP tool.
        """
        return {
            "devices": [s.model_dump() for s in self.device_states()],
            "capabilities": [c.model_dump() for c in self.capabilities()],
        }
