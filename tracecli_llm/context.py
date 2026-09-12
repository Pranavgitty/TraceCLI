"""
Compacts Part 1's `ErrorContext` and Part 2's evidence events into the
small JSON blobs actually sent to the model.

This exists because the Token Efficiency requirements are specific: don't
send the whole project, don't resend evidence redundantly across planner
rounds, bound source snippets and debugger traces. `ErrorContext.stdout`/
`stderr` can be arbitrarily long (Part 1 does not bound them — a target
program controls its own output volume), so this module — not Part 1 or
Part 2 — is the place that enforces prompt-sized bounds.

Both `ErrorContext` and evidence events arrive here as plain dicts (JSON
already decoded from Part 1's stdout / Part 2's protocol), so this module
has no dependency on the Rust crate and only a light, optional dependency
shape on Part 2's evidence field names.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

MAX_OUTPUT_CHARS = 2000
MAX_EVIDENCE_EVENTS = 40


def _tail(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return "...(truncated)...\n" + text[-max_chars:]


def compact_error_context(error_context: Dict[str, Any]) -> Dict[str, Any]:
    """Strips an `ErrorContext` dict down to what a planner or diagnosis
    prompt actually needs: classification, location, a bounded source
    excerpt, and bounded output tails. Drops `build_context` for the
    planner/diagnosis prompts — it informs neither "what to inspect next"
    nor "what broke", only how the binary was built."""
    out: Dict[str, Any] = {
        "classification": error_context.get("classification"),
        "exit_code": error_context.get("exit_code"),
        "signal": error_context.get("signal"),
        "success": error_context.get("success"),
    }

    source_location = error_context.get("source_location")
    if source_location:
        out["source_location"] = source_location

    source_context = error_context.get("source_context")
    if source_context:
        out["source_context"] = source_context

    stdout = error_context.get("stdout") or ""
    stderr = error_context.get("stderr") or ""
    if stdout:
        out["stdout"] = _tail(stdout, MAX_OUTPUT_CHARS)
    if stderr:
        out["stderr"] = _tail(stderr, MAX_OUTPUT_CHARS)

    return out


def compact_evidence(
    evidence: Optional[List[Dict[str, Any]]], *, max_events: int = MAX_EVIDENCE_EVENTS
) -> List[Dict[str, Any]]:
    """Strips Part 2 evidence events down to their parsed fields.

    Drops the `raw` GDB/MI text on every event: it is preserved by Part 2
    for offline debugging of the parser itself, but is redundant with the
    structured fields for an LLM's purposes and is often the single
    largest contributor to prompt size. Keeps only the most recent
    `max_events` — planner rounds accumulate evidence, and a model needs
    "what have we already learned", not a full replay of every MI record.
    """
    if not evidence:
        return []
    trimmed = evidence[-max_events:]
    return [{k: v for k, v in event.items() if k != "raw"} for event in trimmed]


def build_planner_input(
    error_context: Dict[str, Any], prior_evidence: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Compact JSON payload for the planner prompt: the failure plus
    whatever evidence earlier rounds already produced, so the planner
    doesn't re-request the same thing."""
    payload: Dict[str, Any] = {"error": compact_error_context(error_context)}
    compacted_evidence = compact_evidence(prior_evidence)
    if compacted_evidence:
        payload["evidence_so_far"] = compacted_evidence
    return payload


def build_diagnosis_input(
    error_context: Dict[str, Any], evidence: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Compact JSON payload for the diagnosis prompt: the failure plus all
    evidence collected across every planner round."""
    return {
        "error": compact_error_context(error_context),
        "evidence": compact_evidence(evidence),
    }
