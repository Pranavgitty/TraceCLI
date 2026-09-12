"""
Terminal UX (spec sections 10, 14).

Distinguishes OBSERVED (Part 2's own evidence, restated), INFERRED (the
diagnosis engine's conclusion over that evidence), and SUGGESTED (a fix
TraceCLI has not applied or tested). Never claims a fix is "verified" —
TraceCLI only diagnoses and suggests (spec section 15).
"""
from __future__ import annotations

from typing import Any, Dict

from .orchestrate import (
    OUTCOME_DEBUGGER_UNAVAILABLE,
    OUTCOME_DIAGNOSIS_UNAVAILABLE,
    OUTCOME_INSUFFICIENT_EVIDENCE,
    OUTCOME_PLANNER_UNAVAILABLE,
    OUTCOME_TARGET_SUCCEEDED,
    OrchestrationResult,
)

DIVIDER = "─" * 40

STAGE_LABELS = {
    "error_captured": "Error captured",
    "plan_generated": "Debugging plan generated",
    "evidence_collected": "Runtime evidence collected",
    "stack_analyzed": "Stack analyzed",
    "variables_inspected": "Variables inspected",
    "root_cause_identified": "Root cause identified",
}

_UNAVAILABLE_REASONS = {
    OUTCOME_DEBUGGER_UNAVAILABLE: "The debugger (GDB) could not be started.",
    OUTCOME_PLANNER_UNAVAILABLE: "The debug planner (LLM) could not be reached or configured.",
    OUTCOME_DIAGNOSIS_UNAVAILABLE: "The diagnosis engine (LLM) could not be reached.",
    OUTCOME_INSUFFICIENT_EVIDENCE: "Not enough runtime evidence was collected to support a diagnosis.",
}


def make_progress_printer():
    """Returns a callback that prints each checklist line at most once, in
    the order stages actually complete (not pre-announced)."""
    seen = set()

    def _progress(stage: str) -> None:
        if stage in seen:
            return
        seen.add(stage)
        print(f"✓ {STAGE_LABELS.get(stage, stage)}")

    return _progress


def print_header(error_context: Dict[str, Any]) -> None:
    if error_context.get("success"):
        print("✓ Program ran successfully (exit code 0) — nothing to diagnose.\n")
        return

    print("✗ Runtime error detected\n")
    classification = error_context.get("classification") or "unknown_failure"
    print(str(classification).upper().replace("_", " "))

    loc = error_context.get("source_location")
    if loc and loc.get("file"):
        print(f"{loc['file']}:{loc.get('line', '?')}")

    print("\nInvestigating...\n")


def render_result(result: OrchestrationResult, trace_path) -> None:
    print()
    print(DIVIDER)
    print()

    if result.outcome == OUTCOME_TARGET_SUCCEEDED:
        print("Program exited successfully. No failure to diagnose.")
    elif result.outcome in _UNAVAILABLE_REASONS:
        print("INSUFFICIENT EVIDENCE\n")
        print(_UNAVAILABLE_REASONS[result.outcome])
        if result.error:
            print(f"Detail: {result.error}")
        if result.limit_reached:
            print(f"(investigation_limit_reached: {result.limit_reached})")
    else:
        diagnosis = result.diagnosis
        print("ROOT CAUSE (INFERRED)\n")
        print(diagnosis.root_cause)
        print()
        print("EVIDENCE (OBSERVED)\n")
        for item in diagnosis.evidence:
            print(f"- {item}")
        print()
        print("SUGGESTED FIX (not verified — TraceCLI does not apply or test fixes)\n")
        print(diagnosis.suggested_fix)
        print()
        print(f"CONFIDENCE: {round(diagnosis.confidence * 100)}%")
        if result.limit_reached:
            print(f"\n(investigation stopped early: {result.limit_reached})")

    print()
    print(DIVIDER)
    print()
    print("Trace:")
    print(trace_path)
