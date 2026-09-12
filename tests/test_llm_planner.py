"""
Tests for LLM #1 (DebugPlanner) against a fake `LLMProvider` — never a live
API key, never the network, never GDB.

Covers spec section 15 items 1-3: planner receives error context, returns
valid actions, and invalid planner output is rejected (with a bounded
corrective retry).
"""
import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tracecli_llm.config import StageConfig
from tracecli_llm.errors import OutputValidationError
from tracecli_llm.planner import DebugPlanner
from tracecli_llm.provider import GenerationRequest, LLMProvider

STAGE = StageConfig(
    provider="gemini",
    model="gemini-2.5-flash-lite",
    temperature=0.0,
    max_output_tokens=512,
    timeout_s=5.0,
    max_retries=0,
)

SAMPLE_ERROR_CONTEXT = {
    "executable": "/tmp/a.out",
    "arguments": [],
    "working_directory": "/tmp",
    "exit_code": None,
    "signal": {"number": 11, "name": "SIGSEGV", "core_dumped": False},
    "stdout": "",
    "stderr": "Segmentation fault",
    "classification": "segmentation_fault",
    "source_location": {"file": "main.cpp", "line": 10, "column": None, "function": "main"},
    "source_context": {
        "file": "main.cpp",
        "start_line": 5,
        "end_line": 15,
        "highlighted_line": 10,
        "lines": ["int main() {", "  Node* node = nullptr;", "  return node->value;", "}"],
    },
    "build_context": None,
    "duration": 12,
    "success": False,
}


class ScriptedProvider(LLMProvider):
    """Returns each entry in `responses` in order, one per call to
    `generate_structured`. Records every request it was asked to serve."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def generate(self, request: GenerationRequest) -> str:  # pragma: no cover - unused here
        raise NotImplementedError

    def generate_structured(self, request: GenerationRequest) -> str:
        self.requests.append(request)
        return self._responses.pop(0)


class DebugPlannerTests(unittest.TestCase):
    def test_receives_error_context_and_returns_valid_actions(self):
        response = json.dumps(
            {"actions": [{"type": "backtrace", "depth": 20}, {"type": "variable", "name": "node"}]}
        )
        provider = ScriptedProvider([response])
        planner = DebugPlanner(provider, STAGE)

        result = planner.plan(SAMPLE_ERROR_CONTEXT)

        self.assertEqual(
            result.actions,
            [{"type": "backtrace", "depth": 20}, {"type": "variable", "name": "node"}],
        )
        # The prompt sent to the provider carries the compact error context,
        # not the whole dict verbatim (build_context is dropped).
        sent_prompt = provider.requests[0].prompt
        self.assertIn("segmentation_fault", sent_prompt)
        self.assertNotIn("build_context", sent_prompt)

    def test_accepts_prior_evidence_for_later_rounds(self):
        response = json.dumps({"actions": [{"type": "locals"}]})
        provider = ScriptedProvider([response])
        planner = DebugPlanner(provider, STAGE)
        prior_evidence = [
            {"event": "stack_frame", "frame": 0, "function": "main", "file": "main.cpp", "line": 10, "raw": "..."}
        ]

        planner.plan(SAMPLE_ERROR_CONTEXT, prior_evidence=prior_evidence)

        sent_prompt = provider.requests[0].prompt
        self.assertIn("evidence_so_far", sent_prompt)
        self.assertIn("stack_frame", sent_prompt)
        # raw MI text must not be forwarded (token efficiency).
        self.assertNotIn('"raw"', sent_prompt)

    def test_invalid_action_type_is_rejected_and_corrected(self):
        bad_response = json.dumps({"actions": [{"type": "delete_all_breakpoints_forever"}]})
        good_response = json.dumps({"actions": [{"type": "backtrace", "depth": 5}]})
        provider = ScriptedProvider([bad_response, good_response])
        planner = DebugPlanner(provider, STAGE)

        result = planner.plan(SAMPLE_ERROR_CONTEXT)

        self.assertEqual(result.actions, [{"type": "backtrace", "depth": 5}])
        self.assertEqual(len(provider.requests), 2)
        self.assertIn("Validation error", provider.requests[1].prompt)

    def test_invalid_output_after_corrective_retry_raises(self):
        bad_response = json.dumps({"actions": [{"type": "not_a_real_action"}]})
        provider = ScriptedProvider([bad_response, bad_response])
        planner = DebugPlanner(provider, STAGE)

        with self.assertRaises(OutputValidationError):
            planner.plan(SAMPLE_ERROR_CONTEXT)
        self.assertEqual(len(provider.requests), 2)  # bounded: initial + one retry, no more

    def test_action_exceeding_debugger_constraints_is_rejected(self):
        # depth=99999 exceeds Part 2's MAX_BACKTRACE_DEPTH; must never reach
        # the debugger.
        bad_response = json.dumps({"actions": [{"type": "backtrace", "depth": 99999}]})
        provider = ScriptedProvider([bad_response, bad_response])
        planner = DebugPlanner(provider, STAGE)

        with self.assertRaises(OutputValidationError):
            planner.plan(SAMPLE_ERROR_CONTEXT)

    def test_malformed_json_is_rejected(self):
        provider = ScriptedProvider(["not json at all", "not json at all"])
        planner = DebugPlanner(provider, STAGE)

        with self.assertRaises(OutputValidationError):
            planner.plan(SAMPLE_ERROR_CONTEXT)

    def test_zero_actions_is_rejected(self):
        provider = ScriptedProvider([json.dumps({"actions": []})] * 2)
        planner = DebugPlanner(provider, STAGE)

        with self.assertRaises(OutputValidationError):
            planner.plan(SAMPLE_ERROR_CONTEXT)


if __name__ == "__main__":
    unittest.main()
