"""
LLM provider abstraction.

`LLMProvider` is the seam that keeps `DebugPlanner` and `DiagnosisEngine`
ignorant of which model vendor answers them. Adding Groq, a local model, or
anything else later means writing one new class here — nothing in
`planner.py`, `diagnosis.py`, or their callers changes.

`GeminiProvider` is the only implementation for now. It talks to Gemini via
the official `google-genai` SDK (approved: SDK over raw HTTP) and is a
synchronous, blocking call (approved: sync over async), constraining output
with Gemini's native structured-output mode (approved: schema-constrained
generation over free-text parsing).

The `google.genai` import is deferred to `GeminiProvider.__init__` rather
than module load time, so importing `tracecli_llm` — and running its tests
against a fake provider — never requires the SDK to be installed.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .errors import (
    AuthenticationError,
    NetworkError,
    ProviderResponseError,
    ProviderTimeoutError,
    RateLimitError,
)


@dataclass(frozen=True)
class GenerationRequest:
    """Everything a provider needs to answer one prompt.

    `schema` is a plain JSON-Schema-shaped dict (see `schemas.py`) rather
    than any provider-specific type, so callers never import an SDK type.
    Leave it `None` for unconstrained free-text generation via `generate()`.
    """

    system_instruction: str
    prompt: str
    model: str
    schema: Optional[Dict[str, Any]] = None
    temperature: float = 0.0
    max_output_tokens: Optional[int] = None
    timeout_s: float = 30.0


class LLMProvider(ABC):
    """A model vendor capable of answering a prompt, optionally schema-constrained."""

    @abstractmethod
    def generate(self, request: GenerationRequest) -> str:
        """Returns the model's raw text response. Raises `ProviderError`
        subclasses on failure. Does not retry and does not validate the
        response against any semantic schema — callers own both."""

    @abstractmethod
    def generate_structured(self, request: GenerationRequest) -> str:
        """Like `generate`, but asks the provider to constrain its output to
        `request.schema` at decode time where the provider supports that.
        Still returns raw text (a JSON string): callers remain responsible
        for parsing and for semantic validation beyond JSON-Schema shape
        (e.g. our action vocabulary, character allowlists)."""


def _is_transient(exc: Exception) -> bool:
    return isinstance(exc, (RateLimitError, ProviderTimeoutError, NetworkError, ProviderResponseError))


class GeminiProvider(LLMProvider):
    """`LLMProvider` backed by Google's Gemini API via the `google-genai` SDK."""

    def __init__(
        self,
        api_key: str,
        *,
        max_retries: int = 2,
        backoff_base_s: float = 1.0,
        _client: Any = None,
    ):
        """`_client` is an injection seam for tests; production callers
        never pass it and get a real `google.genai.Client`."""
        if not api_key:
            raise AuthenticationError(
                "no Gemini API key provided (expected a non-empty string from "
                "the configured environment variable)"
            )
        if _client is not None:
            self._client = _client
        else:
            from google import genai  # deferred: see module docstring

            self._client = genai.Client(api_key=api_key)
        self._max_retries = max(0, max_retries)
        self._backoff_base_s = backoff_base_s

    def generate(self, request: GenerationRequest) -> str:
        return self._call(request, structured=False)

    def generate_structured(self, request: GenerationRequest) -> str:
        return self._call(request, structured=True)

    # -- internals ---------------------------------------------------

    def _call(self, request: GenerationRequest, *, structured: bool) -> str:
        attempt = 0
        while True:
            try:
                return self._call_once(request, structured=structured)
            except Exception as exc:  # noqa: BLE001 - re-raised as our own types below
                mapped = self._map_exception(exc)
                if not _is_transient(mapped) or attempt >= self._max_retries:
                    raise mapped
                time.sleep(self._backoff_base_s * (2**attempt))
                attempt += 1

    def _call_once(self, request: GenerationRequest, *, structured: bool) -> str:
        from google.genai import types

        config_kwargs: Dict[str, Any] = {
            "system_instruction": request.system_instruction,
            "temperature": request.temperature,
            "http_options": types.HttpOptions(timeout=int(request.timeout_s * 1000)),
        }
        if request.max_output_tokens is not None:
            config_kwargs["max_output_tokens"] = request.max_output_tokens
        if structured and request.schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = request.schema

        response = self._client.models.generate_content(
            model=request.model,
            contents=request.prompt,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        text = getattr(response, "text", None)
        if not text:
            raise ProviderResponseError(
                f"Gemini returned an empty response for model {request.model!r}"
            )
        return text

    def _map_exception(self, exc: Exception) -> Exception:
        try:
            from google.genai import errors as genai_errors
        except ImportError:
            genai_errors = None  # type: ignore[assignment]

        if genai_errors is not None and isinstance(exc, genai_errors.APIError):
            code = getattr(exc, "code", None)
            message = str(exc)
            if code in (401, 403):
                return AuthenticationError(message)
            if code == 429:
                return RateLimitError(message)
            if code is not None and 500 <= code < 600:
                return ProviderResponseError(message)
            return ProviderResponseError(message)

        name = type(exc).__name__.lower()
        if "timeout" in name:
            return ProviderTimeoutError(str(exc))
        if "connect" in name or "network" in name or "dns" in name:
            return NetworkError(str(exc))

        return ProviderResponseError(f"{type(exc).__name__}: {exc}")
