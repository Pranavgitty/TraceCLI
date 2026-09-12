"""
Canonical TraceCLI trace: one JSONL file per run at
`.tracecli/runs/<run-id>.jsonl`, each line an independently parseable JSON
event (spec sections 5-7). This is the canonical, internal representation;
`export_json` derives an optional `.json` view from it after the fact
(spec section 8) rather than the pipeline writing both formats itself.

Never write secrets: events only ever carry `ErrorContext`/evidence/plan
data and stage names, never an API key or its environment variable value.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

DEFAULT_TRACE_DIR = Path(".tracecli") / "runs"

# Keeps a single run's trace file from growing unboundedly if something
# upstream misbehaves (spec section 4's "maximum trace size").
MAX_FIELD_CHARS = 4000


def _bound(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + "...(truncated)..."
    return value


def make_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]


class TraceWriter:
    """Appends one JSON object per line to `.tracecli/runs/<run_id>.jsonl`.

    Every write is flushed immediately so a crash mid-investigation still
    leaves a readable, reproducible partial trace.
    """

    def __init__(self, run_id: Optional[str] = None, base_dir: Path = DEFAULT_TRACE_DIR):
        self.run_id = run_id or make_run_id()
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.base_dir / f"{self.run_id}.jsonl"
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, event: str, **fields: Any) -> None:
        record = {"run_id": self.run_id, "ts": time.time(), "event": event}
        record.update({k: _bound(v) for k, v in fields.items()})
        self._fh.write(json.dumps(record, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def export_json(jsonl_path: Path, out_path: Optional[Path] = None) -> Path:
    """Renders the canonical JSONL trace as a single JSON document.

    Serialization happens after the trace already exists; this never
    re-runs or duplicates the debugging pipeline (spec section 8).
    """
    jsonl_path = Path(jsonl_path)
    events = [json.loads(line) for line in jsonl_path.read_text().splitlines() if line.strip()]
    out_path = Path(out_path) if out_path else jsonl_path.with_suffix(".json")
    out_path.write_text(json.dumps({"run_id": events[0]["run_id"] if events else None, "events": events}, indent=2))
    return out_path
