"""
Provider/model configuration for the LLM Engine.

Format (approved): a TOML file with `[llm.planner]` / `[llm.diagnosis]`
tables, read with the stdlib `tomllib` (Python >= 3.11, already the
project's minimum) so no new dependency is needed just to read config.
API keys are never read from this file — only from an environment
variable, whose *name* the file may customize but whose *value* it never
contains.

The file is entirely optional: every field has a built-in default matching
the spec's initial model choices, so `load_config()` with no file present
still returns a fully usable configuration.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import AuthenticationError

DEFAULT_CONFIG_FILENAME = "tracecli.toml"
DEFAULT_API_KEY_ENV = "GEMINI_API_KEY"

_STAGE_DEFAULTS = {
    "planner": {
        "provider": "gemini",
        "model": "gemini-2.5-flash-lite",
        "temperature": 0.0,
        "max_output_tokens": 1024,
        "timeout_s": 20.0,
        "max_retries": 2,
    },
    "diagnosis": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "temperature": 0.1,
        "max_output_tokens": 2048,
        "timeout_s": 30.0,
        "max_retries": 2,
    },
}


@dataclass(frozen=True)
class StageConfig:
    """Configuration for one LLM stage (the planner, or the diagnosis engine)."""

    provider: str
    model: str
    temperature: float
    max_output_tokens: int
    timeout_s: float
    max_retries: int


@dataclass(frozen=True)
class LLMConfig:
    """Full configuration for both LLM stages plus where to find the API key."""

    planner: StageConfig
    diagnosis: StageConfig
    api_key_env: str = DEFAULT_API_KEY_ENV

    def api_key(self) -> str:
        """Reads the API key from `self.api_key_env`.

        Raises `AuthenticationError` (not a bare `KeyError`/`ValueError`) so
        callers can catch one error family for every auth-shaped failure,
        whether it originates here or from the provider itself.
        """
        value = os.environ.get(self.api_key_env, "")
        if not value.strip():
            raise AuthenticationError(
                f"environment variable {self.api_key_env!r} is not set (or empty); "
                "the LLM Engine never reads API keys from config files or source"
            )
        return value


def _stage_from_dict(stage_name: str, raw: Dict[str, Any]) -> StageConfig:
    defaults = _STAGE_DEFAULTS[stage_name]
    merged = {**defaults, **raw}
    return StageConfig(
        provider=str(merged["provider"]),
        model=str(merged["model"]),
        temperature=float(merged["temperature"]),
        max_output_tokens=int(merged["max_output_tokens"]),
        timeout_s=float(merged["timeout_s"]),
        max_retries=int(merged["max_retries"]),
    )


def load_config(path: Optional[Path] = None) -> LLMConfig:
    """Loads `tracecli.toml` (or the given path). Missing file -> defaults.

    Only the `[llm]`, `[llm.planner]`, and `[llm.diagnosis]` tables are
    read; unrelated tables belonging to other TraceCLI components are
    ignored rather than rejected, so this component never needs to know
    the full shape of a shared config file.
    """
    candidate = path or Path(DEFAULT_CONFIG_FILENAME)
    raw: Dict[str, Any] = {}
    if candidate.is_file():
        with open(candidate, "rb") as fh:
            raw = tomllib.load(fh)

    llm_section = raw.get("llm", {}) if isinstance(raw.get("llm"), dict) else {}
    planner_raw = llm_section.get("planner", {}) if isinstance(llm_section.get("planner"), dict) else {}
    diagnosis_raw = llm_section.get("diagnosis", {}) if isinstance(llm_section.get("diagnosis"), dict) else {}
    api_key_env = str(llm_section.get("api_key_env", DEFAULT_API_KEY_ENV))

    return LLMConfig(
        planner=_stage_from_dict("planner", planner_raw),
        diagnosis=_stage_from_dict("diagnosis", diagnosis_raw),
        api_key_env=api_key_env,
    )
