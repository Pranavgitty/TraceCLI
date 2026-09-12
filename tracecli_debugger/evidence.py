"""
Structured Debug Evidence model.

Every event emitted retains the exact raw GDB/MI output it was parsed from,
ensuring parsing failures downstream can be analyzed without data loss.
"""
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class Evidence:
    event: str
    raw: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"event": self.event}
        out.update(self.data)
        out["raw"] = self.raw
        return out
