"""
LLM #2 — Diagnosis Engine.

ErrorContext + accumulated debug evidence -> structured Diagnosis
(root cause, evidence, reasoning summary, suggested fix, confidence).
Never modifies the user's code, never executes anything, never talks to
GDB — it only reasons over evidence Part 2 already collected.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config import StageConfig
from .context import build_diagnosis_input
from .errors import OutputValidationError
from .prompts import DIAGNOSIS_SYSTEM_INSTRUCTION, build_diagnosis_prompt
from .provider import GenerationRequest, LLMProvider
from .schemas import DIAGNOSIS_SCHEMA, Diagnosis, validate_diagnosis_output

MAX_CORRECTIVE_ATTEMPTS = 1


class DiagnosisEngine:
    """LLM #2. Turns an `ErrorContext` plus collected evidence into a
    validated `Diagnosis`."""

    def __init__(self, provider: LLMProvider, config: StageConfig):
        self._provider = provider
        self._config = config

    def diagnose(
        self, error_context: Dict[str, Any], evidence: List[Dict[str, Any]]
    ) -> Diagnosis:
        """Runs one diagnosis over the full evidence collected so far.

        Raises `ProviderError` subclasses for provider-side failures, or
        `OutputValidationError` if the model's output still fails
        validation after one corrective retry.
        """
        payload = build_diagnosis_input(error_context, evidence)
        prompt = build_diagnosis_prompt(payload)

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
                    system_instruction=DIAGNOSIS_SYSTEM_INSTRUCTION,
                    prompt=request_prompt,
                    model=self._config.model,
                    schema=DIAGNOSIS_SCHEMA,
                    temperature=self._config.temperature,
                    max_output_tokens=self._config.max_output_tokens,
                    timeout_s=self._config.timeout_s,
                )
            )

            try:
                return validate_diagnosis_output(raw)
            except OutputValidationError as exc:
                last_error = exc

        assert last_error is not None
        raise last_error
