"""
TraceCLI Part 3: LLM Engine.

    ErrorContext -> DebugPlanner -> structured debug actions
    ErrorContext + evidence -> DiagnosisEngine -> structured Diagnosis

Both stages sit behind the `LLMProvider` abstraction (`GeminiProvider` is
the only implementation so far); neither this package nor its callers
depend on GDB, subprocess execution, or the CLI.
"""
from .config import DEFAULT_API_KEY_ENV, DEFAULT_CONFIG_FILENAME, LLMConfig, StageConfig, load_config
from .diagnosis import DiagnosisEngine
from .errors import (
    AuthenticationError,
    LLMEngineError,
    NetworkError,
    OutputValidationError,
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
    RateLimitError,
)
from .factory import build_engines
from .planner import DebugPlanner
from .provider import GeminiProvider, GenerationRequest, LLMProvider
from .schemas import (
    DIAGNOSIS_SCHEMA,
    PLANNER_ACTION_SCHEMA,
    Diagnosis,
    PlanResult,
    validate_diagnosis_output,
    validate_planner_output,
)

__all__ = [
    "DEFAULT_API_KEY_ENV",
    "DEFAULT_CONFIG_FILENAME",
    "LLMConfig",
    "StageConfig",
    "load_config",
    "DiagnosisEngine",
    "DebugPlanner",
    "build_engines",
    "LLMProvider",
    "GeminiProvider",
    "GenerationRequest",
    "PlanResult",
    "Diagnosis",
    "PLANNER_ACTION_SCHEMA",
    "DIAGNOSIS_SCHEMA",
    "validate_planner_output",
    "validate_diagnosis_output",
    "LLMEngineError",
    "ProviderError",
    "AuthenticationError",
    "RateLimitError",
    "ProviderTimeoutError",
    "NetworkError",
    "ProviderResponseError",
    "OutputValidationError",
]
