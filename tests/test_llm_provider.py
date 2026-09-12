"""
Tests for `GeminiProvider` — provider failure handling, retries, timeouts.

Never calls the real network or requires an API key: a fake object shaped
like `google.genai.Client` is injected via the `_client` constructor
parameter. Requires `google-genai` to be installed (it is Part 3's one
declared dependency, in requirements.txt) purely so real
`google.genai.errors` exception types can be raised and mapped, exactly as
they would be in production; no network call is ever made to get them.

Covers spec section 15 items 6-8: provider failures, timeouts, retries.
"""
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    from google.genai import errors as genai_errors

    HAVE_GENAI = True
except ImportError:
    HAVE_GENAI = False

from tracecli_llm.errors import (
    AuthenticationError,
    NetworkError,
    ProviderResponseError,
    ProviderTimeoutError,
    RateLimitError,
)
from tracecli_llm.provider import GeminiProvider, GenerationRequest


def make_request(**overrides):
    defaults = dict(
        system_instruction="sys",
        prompt="hello",
        model="gemini-2.5-flash-lite",
        schema=None,
        temperature=0.0,
        max_output_tokens=100,
        timeout_s=1.0,
    )
    defaults.update(overrides)
    return GenerationRequest(**defaults)


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    """Stands in for `client.models`. `behaviors` is a list of callables
    (or exceptions to raise) consumed one per call, in order."""

    def __init__(self, behaviors):
        self._behaviors = list(behaviors)
        self.call_count = 0

    def generate_content(self, *, model, contents, config):
        self.call_count += 1
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        return behavior


class FakeClient:
    def __init__(self, behaviors):
        self.models = FakeModels(behaviors)


@unittest.skipUnless(HAVE_GENAI, "google-genai is not installed")
class GeminiProviderTests(unittest.TestCase):
    def test_missing_api_key_raises_authentication_error(self):
        with self.assertRaises(AuthenticationError):
            GeminiProvider("")

    def test_successful_structured_call_returns_text(self):
        client = FakeClient([FakeResponse('{"actions": []}')])
        provider = GeminiProvider("fake-key", _client=client)

        result = provider.generate_structured(make_request())

        self.assertEqual(result, '{"actions": []}')
        self.assertEqual(client.models.call_count, 1)

    def test_auth_error_is_never_retried(self):
        client = FakeClient(
            [genai_errors.ClientError(401, {"message": "bad key", "status": "UNAUTHENTICATED"})]
        )
        provider = GeminiProvider("fake-key", max_retries=3, backoff_base_s=0, _client=client)

        with self.assertRaises(AuthenticationError):
            provider.generate_structured(make_request())
        self.assertEqual(client.models.call_count, 1)

    def test_rate_limit_is_retried_then_succeeds(self):
        client = FakeClient(
            [
                genai_errors.ClientError(429, {"message": "slow down", "status": "RESOURCE_EXHAUSTED"}),
                FakeResponse('{"ok": true}'),
            ]
        )
        provider = GeminiProvider("fake-key", max_retries=2, backoff_base_s=0, _client=client)

        result = provider.generate_structured(make_request())

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(client.models.call_count, 2)

    def test_server_error_retried_up_to_bound_then_raises(self):
        five_hundreds = [
            genai_errors.ServerError(503, {"message": "unavailable", "status": "UNAVAILABLE"})
            for _ in range(5)
        ]
        client = FakeClient(five_hundreds)
        provider = GeminiProvider("fake-key", max_retries=2, backoff_base_s=0, _client=client)

        with self.assertRaises(ProviderResponseError):
            provider.generate_structured(make_request())
        # Bounded: 1 initial attempt + 2 retries = 3 total calls, never more.
        self.assertEqual(client.models.call_count, 3)

    def test_timeout_exception_is_mapped_and_retried(self):
        class FakeTimeout(Exception):
            pass

        client = FakeClient([FakeTimeout("deadline exceeded"), FakeResponse("{}")])
        provider = GeminiProvider("fake-key", max_retries=1, backoff_base_s=0, _client=client)

        result = provider.generate_structured(make_request())
        self.assertEqual(result, "{}")

    def test_timeout_exhausting_retries_raises_provider_timeout_error(self):
        class FakeTimeout(Exception):
            pass

        client = FakeClient([FakeTimeout("deadline exceeded"), FakeTimeout("deadline exceeded")])
        provider = GeminiProvider("fake-key", max_retries=1, backoff_base_s=0, _client=client)

        with self.assertRaises(ProviderTimeoutError):
            provider.generate_structured(make_request())

    def test_empty_response_is_treated_as_provider_error(self):
        client = FakeClient([FakeResponse(""), FakeResponse("")])
        provider = GeminiProvider("fake-key", max_retries=1, backoff_base_s=0, _client=client)

        with self.assertRaises(ProviderResponseError):
            provider.generate_structured(make_request())


if __name__ == "__main__":
    unittest.main()
