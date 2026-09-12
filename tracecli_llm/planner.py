"""
LLM #1 — Debug Planner.

ErrorContext (+ prior evidence, on later rounds) -> structured debug
actions. Never diagnoses, never drives the debugger, never talks to GDB
directly — it only decides what Part 2 should be asked to collect next.
Part 4 owns the loop that feeds this back in each round; see
`plan(prior_evidence=...)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .config import StageConfig
from .context import build_planner_input
from .errors import OutputValidationError
from .prompts import PLANNER_SYSTEM_INSTRUCTION, build_planner_prompt
from .provider import GenerationRequest, LLMProvider
from .schemas import PLANNER_ACTION_SCHEMA, PlanResult, validate_planner_output

# One corrective re-prompt on invalid structured output, in addition to the
# initial attempt. Bounded per the spec ("do not implement infinite
# retries"); this is independent of the provider's own bounded retries for
# transient network/rate-limit/timeout failures within each attempt.
MAX_CORRECTIVE_ATTEMPTS = 1


class DebugPlanner:
    """LLM #1. Turns an `ErrorContext` (plus optional prior evidence) into
    a validated `PlanResult` of structured debug actions."""

    def __init__(self, provider: LLMProvider, config: StageConfig):
        self._provider = provider
        self._config = config

    def plan(
        self,
        error_context: Dict[str, Any],
        prior_evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> PlanResult:
        """Runs one planning round.

        `prior_evidence` is the accumulated evidence from every previous
        round of this same debugging session (Part 4's loop), so the
        planner can avoid re-requesting what it already has. Omit it (or
        pass an empty list) for the first round.

        Raises `ProviderError` subclasses for provider-side failures, or
        `OutputValidationError` if the model's output still fails
        validation after one corrective retry.
        """
        payload = build_planner_input(error_context, prior_evidence)
        prompt = build_planner_prompt(payload)

        last_error: Optional[OutputValidationError] = None
        for attempt in range(MAX_CORRECTIVE_ATTEMPTS + 1):
            request_prompt = prompt
            if last_error is not None:
                request_prompt = (
                    f"{prompt}\n\n"
                    "Your previous response was invalid and is being retried. "
                    f"Validation error: {last_error}\n"
                    "Fix this and return only the corrected structured output."
                )

            raw = self._provider.generate_structured(
                GenerationRequest(
                    system_instruction=PLANNER_SYSTEM_INSTRUCTION,
                    prompt=request_prompt,
                    model=self._config.model,
                    schema=PLANNER_ACTION_SCHEMA,
                    temperature=self._config.temperature,
                    max_output_tokens=self._config.max_output_tokens,
                    timeout_s=self._config.timeout_s,
                )
            )

            try:
                return validate_planner_output(raw)
            except OutputValidationError as exc:
                last_error = exc

        assert last_error is not None
        raise last_error
