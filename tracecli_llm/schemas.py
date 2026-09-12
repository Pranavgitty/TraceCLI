"""
Structured output schemas for both LLM stages, plus the validators that
stand between "the model said this" and "we trust this enough to act on
it".

Planner actions are validated by running each one through
`tracecli_debugger.actions.DebugAction.from_dict` — Part 2's own action
parser — rather than a second, hand-maintained copy of the same vocabulary
and character-allowlist rules. This is a read-only import (validation
logic only, no GDB, no execution) and it is the strongest available
guarantee that "schema compatible with Part 2" means *exactly* compatible,
not just similarly shaped. It also means a change to Part 2's vocabulary
(a new action type, a tightened regex) is picked up here automatically.

Diagnosis output is validated against the exact schema from the spec
(root_cause / evidence / reasoning_summary / suggested_fix / confidence) —
unchanged, since the spec says to ask before changing it and there was no
reason to.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List

from tracecli_debugger.actions import ActionValidationError, DebugAction

from .errors import OutputValidationError

MAX_ACTIONS_PER_PLAN = 10
MAX_EVIDENCE_ITEMS = 20
MAX_ROOT_CAUSE_CHARS = 500
MAX_REASONING_CHARS = 2000
MAX_FIX_CHARS = 1000
MAX_EVIDENCE_ITEM_CHARS = 500

# JSON-Schema (OpenAPI-3.0 subset, as accepted by Gemini's response_schema)
# for the planner's output. Kept a permissive `object` per-action shape
# (rather than a `oneOf` per action type) because the 2.5 Flash-Lite/Flash
# structured-output implementation supports only a constrained subset of
# JSON Schema; per-type strictness is enforced afterwards by
# `DebugAction.from_dict`, which is authoritative anyway.
PLANNER_ACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "maxItems": MAX_ACTIONS_PER_PLAN,
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "breakpoint",
                            "continue",
                            "step",
                            "next",
                            "backtrace",
                            "frame",
                            "locals",
                            "variable",
                            "expression",
                            "registers",
                        ],
                    },
                    "location": {"type": "string"},
                    "depth": {"type": "integer"},
                    "frame": {"type": "integer"},
                    "name": {"type": "string"},
                    "expr": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["type"],
            },
        }
    },
    "required": ["actions"],
}

# JSON-Schema for the diagnosis engine's output, matching spec section 9
# exactly.
DIAGNOSIS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "reasoning_summary": {"type": "string"},
        "suggested_fix": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": [
        "root_cause",
        "evidence",
        "reasoning_summary",
        "suggested_fix",
        "confidence",
    ],
}


@dataclass(frozen=True)
class PlanResult:
    """Validated output of `DebugPlanner.plan()` — ready to hand to Part 2
    as-is, one dict per action, in `DebugAction`'s own accepted shape."""

    actions: List[Dict[str, Any]]


@dataclass(frozen=True)
class Diagnosis:
    """Validated output of `DiagnosisEngine.diagnose()`, matching spec
    section 9's schema field-for-field so Part 4 can render it directly."""

    root_cause: str
    evidence: List[str]
    reasoning_summary: str
    suggested_fix: str
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_cause": self.root_cause,
            "evidence": list(self.evidence),
            "reasoning_summary": self.reasoning_summary,
            "suggested_fix": self.suggested_fix,
            "confidence": self.confidence,
        }


def _parse_json_object(raw_text: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise OutputValidationError(
            f"model output is not valid JSON: {exc}", raw_output=raw_text
        ) from exc
    if not isinstance(parsed, dict):
        raise OutputValidationError(
            f"expected a JSON object, got {type(parsed).__name__}", raw_output=raw_text
        )
    return parsed


def validate_planner_output(raw_text: str) -> PlanResult:
    """Parses and validates a planner response.

    Every action is re-validated through Part 2's own `DebugAction.from_dict`
    so nothing this component returns can be an action Part 2 would reject.
    Raises `OutputValidationError` (with the offending index/reason) on the
    first invalid action, and on a plan exceeding `MAX_ACTIONS_PER_PLAN` —
    that cap exists so a misbehaving model can't hand Part 2 (and GDB) an
    unbounded batch of operations in one round.
    """
    parsed = _parse_json_object(raw_text)

    actions = parsed.get("actions")
    if not isinstance(actions, list):
        raise OutputValidationError(
            "expected top-level 'actions' to be a list", raw_output=raw_text
        )
    if len(actions) == 0:
        raise OutputValidationError(
            "planner returned zero actions; at least one evidence-producing "
            "action is required",
            raw_output=raw_text,
        )
    if len(actions) > MAX_ACTIONS_PER_PLAN:
        raise OutputValidationError(
            f"planner returned {len(actions)} actions, exceeding the cap of "
            f"{MAX_ACTIONS_PER_PLAN} per round",
            raw_output=raw_text,
        )

    normalized: List[Dict[str, Any]] = []
    for i, action_dict in enumerate(actions):
        try:
            action = DebugAction.from_dict(action_dict)
        except ActionValidationError as exc:
            raise OutputValidationError(
                f"action at index {i} failed validation: {exc}", raw_output=raw_text
            ) from exc
        normalized.append(_action_to_dict(action))

    return PlanResult(actions=normalized)


def _action_to_dict(action: DebugAction) -> Dict[str, Any]:
    """Re-serializes a validated `DebugAction` back to the compact dict
    shape Part 2's `DebuggerSession.execute()` expects, dropping fields
    that don't apply to this action's type."""
    out: Dict[str, Any] = {"type": action.type}
    for field_name in ("location", "depth", "frame", "name", "expr", "count"):
        value = getattr(action, field_name)
        if value is not None:
            out[field_name] = value
    return out


def validate_diagnosis_output(raw_text: str) -> Diagnosis:
    """Parses and validates a diagnosis response against spec section 9's
    schema. Bounds string/list lengths (see MAX_* constants) purely as a
    guard against a misbehaving model producing an unbounded wall of text;
    legitimate diagnoses are expected to sit comfortably under these caps.
    """
    parsed = _parse_json_object(raw_text)

    missing = [k for k in DIAGNOSIS_SCHEMA["required"] if k not in parsed]
    if missing:
        raise OutputValidationError(
            f"diagnosis output missing required field(s): {', '.join(missing)}",
            raw_output=raw_text,
        )

    root_cause = parsed["root_cause"]
    evidence = parsed["evidence"]
    reasoning_summary = parsed["reasoning_summary"]
    suggested_fix = parsed["suggested_fix"]
    confidence = parsed["confidence"]

    if not isinstance(root_cause, str) or not root_cause.strip():
        raise OutputValidationError("'root_cause' must be a non-empty string", raw_output=raw_text)
    if len(root_cause) > MAX_ROOT_CAUSE_CHARS:
        raise OutputValidationError(
            f"'root_cause' exceeds {MAX_ROOT_CAUSE_CHARS} characters", raw_output=raw_text
        )

    if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
        raise OutputValidationError("'evidence' must be a list of strings", raw_output=raw_text)
    if len(evidence) > MAX_EVIDENCE_ITEMS:
        raise OutputValidationError(
            f"'evidence' exceeds {MAX_EVIDENCE_ITEMS} items", raw_output=raw_text
        )
    if any(len(e) > MAX_EVIDENCE_ITEM_CHARS for e in evidence):
        raise OutputValidationError(
            f"an 'evidence' item exceeds {MAX_EVIDENCE_ITEM_CHARS} characters",
            raw_output=raw_text,
        )

    if not isinstance(reasoning_summary, str) or not reasoning_summary.strip():
        raise OutputValidationError(
            "'reasoning_summary' must be a non-empty string", raw_output=raw_text
        )
    if len(reasoning_summary) > MAX_REASONING_CHARS:
        raise OutputValidationError(
            f"'reasoning_summary' exceeds {MAX_REASONING_CHARS} characters", raw_output=raw_text
        )

    if not isinstance(suggested_fix, str) or not suggested_fix.strip():
        raise OutputValidationError(
            "'suggested_fix' must be a non-empty string", raw_output=raw_text
        )
    if len(suggested_fix) > MAX_FIX_CHARS:
        raise OutputValidationError(
            f"'suggested_fix' exceeds {MAX_FIX_CHARS} characters", raw_output=raw_text
        )

    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise OutputValidationError("'confidence' must be a number", raw_output=raw_text)
    confidence = float(confidence)
    if not (0.0 <= confidence <= 1.0):
        raise OutputValidationError(
            f"'confidence' must be between 0 and 1, got {confidence}", raw_output=raw_text
        )

    return Diagnosis(
        root_cause=root_cause.strip(),
        evidence=[e.strip() for e in evidence],
        reasoning_summary=reasoning_summary.strip(),
        suggested_fix=suggested_fix.strip(),
        confidence=confidence,
    )
