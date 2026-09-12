"""
The orchestration loop (spec sections 2-4, 17):

    ErrorContext -> Planner -> Debugger -> Evidence -> Planner (repeat) -> Diagnosis

Consumes Part 2 and Part 3 exactly as published (`tracecli_debugger`,
`tracecli_llm`) — no changes to either. `ErrorContext` and evidence are
passed around as plain dicts throughout, matching Part 3's own contract.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from tracecli_debugger import DebuggerSession
from tracecli_debugger.gdb_adapter import GDBNotFoundError, GDBStartupError
from tracecli_llm import build_engines
from tracecli_llm.errors import OutputValidationError, ProviderError
from tracecli_llm.schemas import Diagnosis

from .limits import Limits
from .trace import TraceWriter

# Terminal outcomes. "diagnosed*" are the only ones carrying a `Diagnosis`.
OUTCOME_TARGET_SUCCEEDED = "target_succeeded"
OUTCOME_DIAGNOSED = "diagnosed"
OUTCOME_DIAGNOSED_WITH_LIMIT = "diagnosed_with_limit"
OUTCOME_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
OUTCOME_PLANNER_UNAVAILABLE = "planner_unavailable"
OUTCOME_DEBUGGER_UNAVAILABLE = "debugger_unavailable"
OUTCOME_DIAGNOSIS_UNAVAILABLE = "diagnosis_unavailable"

ProgressCallback = Callable[[str], None]


@dataclass
class OrchestrationResult:
    outcome: str
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    diagnosis: Optional[Diagnosis] = None
    iterations: int = 0
    limit_reached: Optional[str] = None
    error: Optional[str] = None


def _has_sufficient_evidence(evidence: List[Dict[str, Any]]) -> bool:
    """Stop asking the planner for more once we can see: the crash itself,
    where it happened on the stack, and at least one inspected value —
    the same shape as the spec's own worked example (backtrace + locals,
    then a targeted variable inspection)."""
    events = {e.get("event") for e in evidence}
    crash_confirmed = bool({"signal", "process_exit"} & events)
    return crash_confirmed and "stack_frame" in events and "variable" in events


def run(
    executable: str,
    error_context: Dict[str, Any],
    trace: TraceWriter,
    limits: Optional[Limits] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> OrchestrationResult:
    limits = limits or Limits()

    def progress(stage: str) -> None:
        if on_progress:
            on_progress(stage)

    trace.write(
        "error_detected",
        classification=error_context.get("classification"),
        exit_code=error_context.get("exit_code"),
        signal=error_context.get("signal"),
        success=error_context.get("success"),
        source_location=error_context.get("source_location"),
        stdout=error_context.get("stdout"),
        stderr=error_context.get("stderr"),
    )

    if error_context.get("success"):
        trace.write("run_finished", outcome=OUTCOME_TARGET_SUCCEEDED)
        return OrchestrationResult(outcome=OUTCOME_TARGET_SUCCEEDED)

    try:
        planner, diagnosis_engine = build_engines()
    except Exception as exc:  # AuthenticationError, ProviderError, etc.
        trace.write("stage_failed", stage="planner_config", error=str(exc))
        trace.write("run_finished", outcome=OUTCOME_PLANNER_UNAVAILABLE)
        return OrchestrationResult(outcome=OUTCOME_PLANNER_UNAVAILABLE, error=str(exc))

    try:
        session = DebuggerSession(executable)
    except (GDBNotFoundError, GDBStartupError, FileNotFoundError) as exc:
        trace.write("stage_failed", stage="debugger", error=str(exc))
        trace.write("run_finished", outcome=OUTCOME_DEBUGGER_UNAVAILABLE)
        return OrchestrationResult(outcome=OUTCOME_DEBUGGER_UNAVAILABLE, error=str(exc))

    evidence: List[Dict[str, Any]] = []
    start = time.monotonic()
    limit_reached: Optional[str] = None
    iterations = 0

    try:
        for i in range(1, limits.max_planner_iterations + 1):
            if time.monotonic() - start > limits.max_wall_seconds:
                limit_reached = "max_wall_seconds"
                break
            iterations = i

            trace.write("planner_request", round=i, prior_evidence_count=len(evidence))
            try:
                plan = planner.plan(error_context, prior_evidence=evidence)
            except (ProviderError, OutputValidationError) as exc:
                trace.write("stage_failed", stage="planner", round=i, error=str(exc))
                limit_reached = "planner_failed"
                break
            trace.write("planner_response", round=i, actions=plan.actions)
            progress("plan_generated")

            for action in plan.actions:
                trace.write("debug_action", round=i, action=action)

            round_evidence = session.execute_many(plan.actions)
            trace.write("debugger_evidence", round=i, evidence=round_evidence)
            evidence.extend(round_evidence)
            progress("evidence_collected")

            if len(evidence) > limits.max_total_evidence:
                evidence = evidence[: limits.max_total_evidence]
                limit_reached = "max_evidence"
                break

            if _has_sufficient_evidence(evidence):
                progress("stack_analyzed")
                progress("variables_inspected")
                break

            if i == limits.max_planner_iterations:
                limit_reached = "max_iterations"
    finally:
        session.close()

    if limit_reached:
        trace.write("investigation_limit_reached", reason=limit_reached, iterations=iterations)

    if not evidence:
        trace.write("run_finished", outcome=OUTCOME_INSUFFICIENT_EVIDENCE)
        return OrchestrationResult(
            outcome=OUTCOME_INSUFFICIENT_EVIDENCE,
            evidence=evidence,
            iterations=iterations,
            limit_reached=limit_reached,
        )

    try:
        diagnosis = diagnosis_engine.diagnose(error_context, evidence)
    except (ProviderError, OutputValidationError) as exc:
        trace.write("stage_failed", stage="diagnosis", error=str(exc))
        trace.write("run_finished", outcome=OUTCOME_DIAGNOSIS_UNAVAILABLE)
        return OrchestrationResult(
            outcome=OUTCOME_DIAGNOSIS_UNAVAILABLE,
            evidence=evidence,
            iterations=iterations,
            limit_reached=limit_reached,
            error=str(exc),
        )

    progress("root_cause_identified")
    trace.write("diagnosis", diagnosis=diagnosis.to_dict())
    outcome = OUTCOME_DIAGNOSED_WITH_LIMIT if limit_reached else OUTCOME_DIAGNOSED
    trace.write("run_finished", outcome=outcome)
    return OrchestrationResult(
        outcome=outcome,
        evidence=evidence,
        diagnosis=diagnosis,
        iterations=iterations,
        limit_reached=limit_reached,
    )
