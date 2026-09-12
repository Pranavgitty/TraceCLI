"""
Tests for LLM Engine configuration loading.

Covers spec section 15 items 9-10: configuration, and (via context.py)
token/context bounding.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tracecli_llm.config import DEFAULT_API_KEY_ENV, load_config
from tracecli_llm.context import (
    MAX_EVIDENCE_EVENTS,
    MAX_OUTPUT_CHARS,
    compact_error_context,
    compact_evidence,
)
from tracecli_llm.errors import AuthenticationError


class ConfigTests(unittest.TestCase):
    def test_missing_file_yields_builtin_defaults(self):
        config = load_config(Path("/nonexistent/tracecli.toml"))

        self.assertEqual(config.planner.provider, "gemini")
        self.assertEqual(config.planner.model, "gemini-2.5-flash-lite")
        self.assertEqual(config.diagnosis.model, "gemini-2.5-flash")
        self.assertEqual(config.api_key_env, DEFAULT_API_KEY_ENV)

    def test_file_overrides_are_applied(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tracecli.toml"
            path.write_text(
                """
                [llm]
                api_key_env = "MY_KEY"

                [llm.planner]
                provider = "gemini"
                model = "gemini-2.5-flash-lite"
                temperature = 0.5
                max_output_tokens = 42
                timeout_s = 9
                max_retries = 1

                [llm.diagnosis]
                provider = "gemini"
                model = "gemini-2.5-flash"
                temperature = 0.2
                max_output_tokens = 99
                timeout_s = 11
                max_retries = 1
                """
            )
            config = load_config(path)

            self.assertEqual(config.api_key_env, "MY_KEY")
            self.assertEqual(config.planner.temperature, 0.5)
            self.assertEqual(config.planner.max_output_tokens, 42)
            self.assertEqual(config.diagnosis.timeout_s, 11)

    def test_api_key_never_read_from_file_only_from_env(self):
        config = load_config(Path("/nonexistent/tracecli.toml"))
        env_var = config.api_key_env
        os.environ.pop(env_var, None)

        with self.assertRaises(AuthenticationError):
            config.api_key()

        os.environ[env_var] = "test-value"
        try:
            self.assertEqual(config.api_key(), "test-value")
        finally:
            del os.environ[env_var]


class ContextBoundingTests(unittest.TestCase):
    def test_large_stdout_stderr_are_truncated(self):
        huge = "x" * (MAX_OUTPUT_CHARS * 5)
        compacted = compact_error_context({"stdout": huge, "stderr": huge, "classification": "runtime_error"})

        self.assertLessEqual(len(compacted["stdout"]), MAX_OUTPUT_CHARS + 50)
        self.assertLessEqual(len(compacted["stderr"]), MAX_OUTPUT_CHARS + 50)

    def test_build_context_is_dropped(self):
        compacted = compact_error_context(
            {"classification": "runtime_error", "build_context": {"compiler": "g++", "compiler_version": "..."}}
        )
        self.assertNotIn("build_context", compacted)

    def test_evidence_is_bounded_and_raw_is_stripped(self):
        events = [{"event": "variable", "name": f"v{i}", "value": "1", "raw": "big raw text"} for i in range(100)]

        compacted = compact_evidence(events)

        self.assertEqual(len(compacted), MAX_EVIDENCE_EVENTS)
        self.assertTrue(all("raw" not in e for e in compacted))
        # keeps the most recent events, not the oldest
        self.assertEqual(compacted[-1]["name"], "v99")

    def test_empty_evidence_yields_empty_list(self):
        self.assertEqual(compact_evidence(None), [])
        self.assertEqual(compact_evidence([]), [])


if __name__ == "__main__":
    unittest.main()
