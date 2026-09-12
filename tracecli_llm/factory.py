"""
Convenience wiring for Part 4: builds both engines from config in one call,
so the orchestrator does not need to know provider construction details.

    from tracecli_llm import build_engines
    planner, diagnosis = build_engines()
    plan = planner.plan(error_context)
    ...
    diagnosis_result = diagnosis.diagnose(error_context, all_evidence)

Adding a provider later only changes `_build_provider` here; `DebugPlanner`
and `DiagnosisEngine` never change.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from .config import LLMConfig, StageConfig, load_config
from .diagnosis import DiagnosisEngine
from .errors import ProviderError
from .planner import DebugPlanner
from .provider import GeminiProvider, LLMProvider


def _build_provider(stage: StageConfig, config: LLMConfig) -> LLMProvider:
    if stage.provider == "gemini":
        return GeminiProvider(config.api_key(), max_retries=stage.max_retries)
    raise ProviderError(f"unknown provider {stage.provider!r}; only 'gemini' is implemented")


def build_engines(
    config_path: Optional[Path] = None,
) -> Tuple[DebugPlanner, DiagnosisEngine]:
    """Loads config (see `config.load_config`) and returns
    `(DebugPlanner, DiagnosisEngine)`, each wired to its own provider
    instance and stage settings."""
    config = load_config(config_path)
    planner = DebugPlanner(_build_provider(config.planner, config), config.planner)
    diagnosis = DiagnosisEngine(_build_provider(config.diagnosis, config), config.diagnosis)
    return planner, diagnosis
