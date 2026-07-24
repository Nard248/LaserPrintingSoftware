"""Device adapter interface (architecture layer L2).

Each adapter fronts one physical device and *declares* its capabilities as
data: operations with typed, unit-bearing, bounded parameters. The registry
serves these declarations to clients (`GET /capabilities`) so an AI can
ground itself without prior knowledge; the validation engine enforces the
same bounds deterministically. Bounds live here, close to the hardware
they protect.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, Field

DeviceKind = Literal["stage", "laser", "camera", "white_light"]


class ParamSpec(BaseModel):
    name: str
    type: Literal["float", "int", "bool", "str"]
    unit: str = ""
    min: float | None = None
    max: float | None = None
    description: str = ""


class Capability(BaseModel):
    device_id: str
    name: str
    description: str = ""
    params: list[ParamSpec] = Field(default_factory=list)
    mutates: bool = True


class DeviceState(BaseModel):
    device_id: str
    kind: DeviceKind
    connected: bool
    detail: dict = Field(default_factory=dict)


class DeviceAdapter(ABC):
    """One adapter per physical device.

    Action methods are adapter-specific (the executor holds the dispatch
    table); this base fixes the lifecycle + introspection contract.
    """

    device_id: str
    kind: DeviceKind

    @abstractmethod
    def capabilities(self) -> list[Capability]: ...

    @abstractmethod
    def state(self) -> DeviceState: ...

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def safe_state(self) -> None:
        """Drive the device to its defined safe state.

        MAY raise if the safe state cannot be confirmed — callers (the
        execution engine) catch, record, and continue down the safe-state
        order. Never silently report safe when the device may not be.
        """
