"""
Tests for LLM #2 (DiagnosisEngine) against a fake `LLMProvider`.

Covers spec section 15 items 4-5: diagnosis receives evidence, returns a
valid schema (and rejects an invalid one, bounded).
"""
import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tracecli_llm.config import StageConfig
from tracecli_llm.diagnosis import DiagnosisEngine
from tracecli_llm.errors import OutputValidationError
from tracecli_llm.provider import GenerationRequest, LLMProvider

STAGE = StageConfig(
    provider="gemini",
    model="gemini-2.5-flash",
    temperature=0.1,
    max_output_tokens=1024,
    timeout_s=10.0,
    max_retries=0,
)

ERROR_CONTEXT = {
    "classification": "segmentation_fault",
    "signal": {"number": 11, "name": "SIGSEGV", "core_dumped": False},
    "success": False,
    "stdout": "",
    "stderr": "Segmentation fault",
}

EVIDENCE = [
    {"event": "stack_frame", "frame": 0, "function": "main", "file": "main.cpp", "line": 10, "raw": "..."},
    {"event": "variable", "name": "node", "value": "0x0", "raw": "..."},
]

VALID_DIAGNOSIS = {
    "root_cause": "Dereferencing a null 'node' pointer at main.cpp:10.",
    "evidence": ["node == 0x0 in frame 0 at main.cpp:10"],
    "reasoning_summary": "Observed: node is 0x0 in the crashing frame. Inferred: the pointer was never assigned before use.",
    "suggested_fix": "Check 'node' for null before dereferencing it, or ensure it is initialized earlier.",
    "confidence": 0.9,
}


class ScriptedProvider(LLMProvider):
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def generate(self, request: GenerationRequest) -> str:  # pragma: no cover - unused here
        raise NotImplementedError

    def generate_structured(self, request: GenerationRequest) -> str:
        self.requests.append(request)
        return self._responses.pop(0)


class DiagnosisEngineTests(unittest.TestCase):
    def test_receives_evidence_and_returns_valid_diagnosis(self):
        provider = ScriptedProvider([json.dumps(VALID_DIAGNOSIS)])
        engine = DiagnosisEngine(provider, STAGE)

        result = engine.diagnose(ERROR_CONTEXT, EVIDENCE)

        self.assertEqual(result.root_cause, VALID_DIAGNOSIS["root_cause"])
        self.assertEqual(result.confidence, 0.9)
        sent_prompt = provider.requests[0].prompt
        self.assertIn("stack_frame", sent_prompt)
        self.assertNotIn('"raw"', sent_prompt)

    def test_missing_required_field_is_rejected_and_corrected(self):
        bad = {k: v for k, v in VALID_DIAGNOSIS.items() if k != "confidence"}
        provider = ScriptedProvider([json.dumps(bad), json.dumps(VALID_DIAGNOSIS)])
        engine = DiagnosisEngine(provider, STAGE)

        result = engine.diagnose(ERROR_CONTEXT, EVIDENCE)

        self.assertEqual(result.confidence, 0.9)
        self.assertEqual(len(provider.requests), 2)

    def test_confidence_out_of_range_is_rejected(self):
        bad = {**VALID_DIAGNOSIS, "confidence": 1.7}
        provider = ScriptedProvider([json.dumps(bad), json.dumps(bad)])
        engine = DiagnosisEngine(provider, STAGE)

        with self.assertRaises(OutputValidationError):
            engine.diagnose(ERROR_CONTEXT, EVIDENCE)

    def test_certainty_claim_is_still_structurally_valid_but_bounded(self):
        # The schema can't forbid overconfident prose; it can and does
        # bound confidence to [0, 1] and cap string lengths.
        bad = {**VALID_DIAGNOSIS, "root_cause": "x" * 10_000}
        provider = ScriptedProvider([json.dumps(bad), json.dumps(bad)])
        engine = DiagnosisEngine(provider, STAGE)

        with self.assertRaises(OutputValidationError):
            engine.diagnose(ERROR_CONTEXT, EVIDENCE)

    def test_to_dict_matches_spec_schema_keys(self):
        provider = ScriptedProvider([json.dumps(VALID_DIAGNOSIS)])
        engine = DiagnosisEngine(provider, STAGE)

        result = engine.diagnose(ERROR_CONTEXT, EVIDENCE)

        self.assertEqual(
            set(result.to_dict().keys()),
            {"root_cause", "evidence", "reasoning_summary", "suggested_fix", "confidence"},
        )


if __name__ == "__main__":
    unittest.main()
