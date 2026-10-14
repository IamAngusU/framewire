"""Small, stable contracts used by sources, trackers and outputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class Target:
    """A point in the camera image. x/y are normalized to [0, 1]."""

    x: float
    y: float
    size: float
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Event:
    sequence: int
    type: str
    source: str
    target: Target | None = None
    detail: str | None = None
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "framewire.event.v1",
            "time": datetime.now(timezone.utc).isoformat(),
            "sequence": self.sequence,
            "type": self.type,
            "source": self.source,
        }
        if self.target is not None:
            result["target"] = self.target.to_dict()
        if self.detail:
            result["detail"] = self.detail
        if self.data is not None:
            result["data"] = self.data
        return result
