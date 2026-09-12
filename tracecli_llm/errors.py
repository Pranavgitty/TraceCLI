"""
Exception hierarchy for the LLM Engine.

Two independent failure categories exist, and callers should handle them
differently:

- `ProviderError` and its subclasses: something went wrong *talking to* the
  model (network, auth, rate limit, timeout, an unparseable HTTP response).
  Raised by `LLMProvider` implementations. Transient subclasses are retried
  a bounded number of times *inside* the provider; `AuthenticationError`
  never is.
- `OutputValidationError`: the model was reached and answered, but its
  answer does not satisfy our schema/vocabulary. Raised by the validators in
  `schemas.py`. `DebugPlanner`/`DiagnosisEngine` retry this once with a
  corrective prompt before giving up.
"""
from __future__ import annotations


class LLMEngineError(Exception):
    """Base class for every error this package raises."""


class ProviderError(LLMEngineError):
    """Base class for provider-side (network/API) failures."""


class AuthenticationError(ProviderError):
    """The provider rejected our credentials. Never retried."""


class RateLimitError(ProviderError):
    """The provider is throttling us (HTTP 429). Retried with backoff."""


class ProviderTimeoutError(ProviderError):
    """The request did not complete within the configured timeout. Retried."""


class NetworkError(ProviderError):
    """A transport-level failure (DNS, connection reset, ...). Retried."""


class ProviderResponseError(ProviderError):
    """The provider returned an error we don't have a more specific class
    for (e.g. a 5xx, or an unexpected HTTP status). Retried."""


class OutputValidationError(LLMEngineError):
    """The model's output does not satisfy the required schema/vocabulary.

    Carries the raw text that failed validation so callers can log it for
    diagnostics without the caller needing to re-derive it.
    """

    def __init__(self, message: str, *, raw_output: str = ""):
        super().__init__(message)
        self.raw_output = raw_output
