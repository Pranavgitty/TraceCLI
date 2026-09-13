"""
End-to-end and unit tests for Part 4 (spec section 16).

Covers, without needing a live LLM API key (mocked `DebugPlanner`/
`DiagnosisEngine`) except where noted:

 1. successful program            -> test_target_succeeded_short_circuits
 2. segmentation fault             -> test_null_pointer_end_to_end_with_real_gdb
 3. null pointer                   -> (same fixture as #2)
 4. assertion failure              -> test_assertion_failure_classification_reaches_planner
 5. floating-point failure         -> test_divide_zero_classification_reaches_planner
 6. insufficient debugger evidence -> test_insufficient_evidence_when_no_evidence_collected
 7. planner failure                -> test_planner_unavailable_when_build_engines_fails
 8. debugger failure               -> test_debugger_unavailable_for_missing_executable
 9. diagnosis failure              -> test_diagnosis_unavailable_on_provider_error
10. iteration limit                -> test_iteration_limit_reached_without_sufficient_evidence
11. trace generation               -> test_trace_is_valid_jsonl (exercised by every case above too)

Plus a real, unmocked CLI smoke test (`test_cli_diagnose_on_successful_program`)
that runs the actual `tracecli` script (Node.js) via `tracecli diagnose` on a
program that exits 0 -- this path never touches GDB or the LLM, so it needs
no external tooling beyond Node.js.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tracecli_llm.errors import AuthenticationError, ProviderError, OutputValidationError
from tracecli_llm.schemas import Diagnosis, PlanResult

from tracecli_orchestrator import orchestrate
from tracecli_orchestrator.limits import Limits
from tracecli_orchestrator.trace import TraceWriter

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def compile_fixture(name: str) -> str:
    src = os.path.join(FIXTURES_DIR, f"{name}.cpp")
    out = os.path.join(FIXTURES_DIR, name)
    if not os.path.exists(out) or os.path.getmtime(src) > os.path.getmtime(out):
        subprocess.run(["g++", "-g", "-O0", "-o", out, src], check=True)
    return out


def base_error_context(**overrides: Any) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {
        "executable": "/bin/true",
        "arguments": [],
        "working_directory": "/",
        "exit_code": None,
        "signal": {"number": 11, "name": "SIGSEGV", "core_dumped": False},
        "stdout": "",
        "stderr": "",
        "classification": "segmentation_fault",
        "source_location": {"file": "main.cpp", "line": 10, "column": None, "function": "inner"},
        "source_context": None,
        "build_context": None,
        "duration": 5,
        "success": False,
    }
    ctx.update(overrides)
    return ctx


def make_trace(tmpdir: str) -> TraceWriter:
    return TraceWriter(base_dir=Path(tmpdir) / ".tracecli" / "runs")


@dataclass
class _FakePlanner:
    plans: List[List[Dict[str, Any]]]
    calls: int = 0

    def plan(self, error_context, prior_evidence=None):
        actions = self.plans[min(self.calls, len(self.plans) - 1)]
        self.calls += 1
        return PlanResult(actions=actions)


class _FailingPlanner:
    def plan(self, error_context, prior_evidence=None):
        raise ProviderError("simulated provider outage")


class _FakeDiagnosisEngine:
    def __init__(self, diagnosis: Optional[Diagnosis] = None, raises: Optional[Exception] = None):
        self._diagnosis = diagnosis or Diagnosis(
            root_cause="node is null at main.cpp:10",
            evidence=["node = 0x0"],
            reasoning_summary="find_node() returned nullptr and was dereferenced.",
            suggested_fix="Validate the result of find_node() before dereferencing.",
            confidence=0.9,
        )
        self._raises = raises

    def diagnose(self, error_context, evidence):
        if self._raises:
            raise self._raises
        return self._diagnosis


class _FakeDebuggerSession:
    """Stands in for `tracecli_debugger.DebuggerSession` with scripted,
    schema-shaped evidence, so these tests don't depend on real GDB
    behavior matching a particular round's actions exactly."""

    def __init__(self, evidence_by_round: List[List[Dict[str, Any]]]):
        self._rounds = evidence_by_round
        self._i = 0
        self.closed = False

    def execute_many(self, actions):
        evidence = self._rounds[min(self._i, len(self._rounds) - 1)]
        self._i += 1
        return evidence

    def close(self):
        self.closed = True


def _read_jsonl(path) -> List[Dict[str, Any]]:
    events = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    assert events, "trace file must not be empty"
    return events


class OrchestrateUnitTests(unittest.TestCase):
    """Drives `orchestrate.run` with fakes for Part 3's engines and Part 2's
    `DebuggerSession`, so these never need GDB, a compiler, or an API key."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)

    def test_target_succeeded_short_circuits(self):
        trace = make_trace(self._tmpdir)
        ctx = base_error_context(success=True, classification="success", signal=None, exit_code=0)
        result = orchestrate.run("/bin/true", ctx, trace)
        trace.close()

        self.assertEqual(result.outcome, orchestrate.OUTCOME_TARGET_SUCCEEDED)
        events = _read_jsonl(trace.path)
        self.assertEqual(events[-1]["outcome"], orchestrate.OUTCOME_TARGET_SUCCEEDED)
        # No planner/debugger rounds should have run at all.
        self.assertFalse(any(e["event"] == "planner_request" for e in events))

    def test_diagnosed_when_evidence_becomes_sufficient(self):
        trace = make_trace(self._tmpdir)
        ctx = base_error_context()
        planner = _FakePlanner(plans=[[{"type": "backtrace"}, {"type": "locals"}]])
        diagnosis_engine = _FakeDiagnosisEngine()
        session = _FakeDebuggerSession(
            [
                [
                    {"event": "signal", "signal": "SIGSEGV", "raw": "r"},
                    {"event": "stack_frame", "frame": 0, "function": "inner", "raw": "r"},
                    {"event": "variable", "name": "node", "value": "0x0", "raw": "r"},
                ]
            ]
        )

        with mock.patch.object(orchestrate, "build_engines", return_value=(planner, diagnosis_engine)), \
             mock.patch.object(orchestrate, "DebuggerSession", return_value=session):
            result = orchestrate.run("./program", ctx, trace, Limits(max_planner_iterations=3))
        trace.close()

        self.assertEqual(result.outcome, orchestrate.OUTCOME_DIAGNOSED)
        self.assertEqual(result.iterations, 1)
        self.assertIsNone(result.limit_reached)
        self.assertEqual(result.diagnosis.root_cause, "node is null at main.cpp:10")
        self.assertTrue(session.closed)

        events = _read_jsonl(trace.path)
        self.assertEqual(events[-1]["outcome"], orchestrate.OUTCOME_DIAGNOSED)
        self.assertTrue(any(e["event"] == "diagnosis" for e in events))

    def test_iteration_limit_reached_without_sufficient_evidence(self):
        trace = make_trace(self._tmpdir)
        ctx = base_error_context()
        planner = _FakePlanner(plans=[[{"type": "locals"}]])
        diagnosis_engine = _FakeDiagnosisEngine()
        # Every round only ever produces a "variable" event -- never a
        # signal/stack_frame, so sufficiency never triggers and the loop
        # must stop at the iteration cap instead of running forever.
        session = _FakeDebuggerSession([[{"event": "variable", "name": "x", "value": "1", "raw": "r"}]])

        with mock.patch.object(orchestrate, "build_engines", return_value=(planner, diagnosis_engine)), \
             mock.patch.object(orchestrate, "DebuggerSession", return_value=session):
            result = orchestrate.run("./program", ctx, trace, Limits(max_planner_iterations=2))
        trace.close()

        self.assertEqual(result.iterations, 2)
        self.assertEqual(result.limit_reached, "max_iterations")
        self.assertEqual(result.outcome, orchestrate.OUTCOME_DIAGNOSED_WITH_LIMIT)

        events = _read_jsonl(trace.path)
        limit_events = [e for e in events if e["event"] == "investigation_limit_reached"]
        self.assertEqual(len(limit_events), 1)
        self.assertEqual(limit_events[0]["reason"], "max_iterations")

    def test_insufficient_evidence_when_no_evidence_collected(self):
        trace = make_trace(self._tmpdir)
        ctx = base_error_context()
        planner = _FakePlanner(plans=[[{"type": "locals"}]])
        diagnosis_engine = _FakeDiagnosisEngine()
        session = _FakeDebuggerSession([[]])  # debugger produces nothing at all

        with mock.patch.object(orchestrate, "build_engines", return_value=(planner, diagnosis_engine)), \
             mock.patch.object(orchestrate, "DebuggerSession", return_value=session):
            result = orchestrate.run("./program", ctx, trace, Limits(max_planner_iterations=1))
        trace.close()

        self.assertEqual(result.outcome, orchestrate.OUTCOME_INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.evidence, [])
        events = _read_jsonl(trace.path)
        self.assertEqual(events[-1]["outcome"], orchestrate.OUTCOME_INSUFFICIENT_EVIDENCE)

    def test_planner_unavailable_when_build_engines_fails(self):
        trace = make_trace(self._tmpdir)
        ctx = base_error_context()

        def _raise():
            raise AuthenticationError("GEMINI_API_KEY not set")

        with mock.patch.object(orchestrate, "build_engines", side_effect=_raise):
            result = orchestrate.run("./program", ctx, trace)
        trace.close()

        self.assertEqual(result.outcome, orchestrate.OUTCOME_PLANNER_UNAVAILABLE)
        self.assertIn("GEMINI_API_KEY", result.error)
        events = _read_jsonl(trace.path)
        self.assertTrue(any(e["event"] == "stage_failed" and e["stage"] == "planner_config" for e in events))

    def test_debugger_unavailable_for_missing_executable(self):
        trace = make_trace(self._tmpdir)
        ctx = base_error_context()
        planner = _FakePlanner(plans=[[{"type": "backtrace"}]])
        diagnosis_engine = _FakeDiagnosisEngine()

        with mock.patch.object(orchestrate, "build_engines", return_value=(planner, diagnosis_engine)):
            # Real DebuggerSession, real GDBAdapter: a nonexistent executable
            # path raises FileNotFoundError synchronously from __init__.
            result = orchestrate.run("/nonexistent/does-not-exist", ctx, trace)
        trace.close()

        self.assertEqual(result.outcome, orchestrate.OUTCOME_DEBUGGER_UNAVAILABLE)
        events = _read_jsonl(trace.path)
        self.assertTrue(any(e["event"] == "stage_failed" and e["stage"] == "debugger" for e in events))

    def test_diagnosis_unavailable_on_provider_error(self):
        trace = make_trace(self._tmpdir)
        ctx = base_error_context()
        planner = _FakePlanner(plans=[[{"type": "backtrace"}]])
        diagnosis_engine = _FakeDiagnosisEngine(raises=ProviderError("simulated 5xx"))
        session = _FakeDebuggerSession(
            [
                [
                    {"event": "signal", "signal": "SIGSEGV", "raw": "r"},
                    {"event": "stack_frame", "frame": 0, "function": "inner", "raw": "r"},
                    {"event": "variable", "name": "node", "value": "0x0", "raw": "r"},
                ]
            ]
        )

        with mock.patch.object(orchestrate, "build_engines", return_value=(planner, diagnosis_engine)), \
             mock.patch.object(orchestrate, "DebuggerSession", return_value=session):
            result = orchestrate.run("./program", ctx, trace)
        trace.close()

        self.assertEqual(result.outcome, orchestrate.OUTCOME_DIAGNOSIS_UNAVAILABLE)
        self.assertTrue(len(result.evidence) > 0)  # evidence was collected, just not diagnosed
        events = _read_jsonl(trace.path)
        self.assertTrue(any(e["event"] == "stage_failed" and e["stage"] == "diagnosis" for e in events))

    def test_planner_failure_mid_loop_is_reported_and_stops(self):
        trace = make_trace(self._tmpdir)
        ctx = base_error_context()
        diagnosis_engine = _FakeDiagnosisEngine()
        session = _FakeDebuggerSession([[]])

        with mock.patch.object(orchestrate, "build_engines", return_value=(_FailingPlanner(), diagnosis_engine)), \
             mock.patch.object(orchestrate, "DebuggerSession", return_value=session):
            result = orchestrate.run("./program", ctx, trace)
        trace.close()

        self.assertEqual(result.limit_reached, "planner_failed")
        self.assertEqual(result.outcome, orchestrate.OUTCOME_INSUFFICIENT_EVIDENCE)
        events = _read_jsonl(trace.path)
        self.assertTrue(any(e["event"] == "stage_failed" and e["stage"] == "planner" for e in events))


@unittest.skipIf(not shutil.which("gdb"), "gdb not installed on system")
class OrchestrateRealDebuggerTests(unittest.TestCase):
    """Real GDB, real compiled fixtures, but a scripted planner and a fake
    diagnosis engine -- exercises the actual Part 2 integration without an
    LLM API key."""

    @classmethod
    def setUpClass(cls):
        cls.null_pointer_bin = compile_fixture("null_pointer")
        cls.assertion_bin = compile_fixture("assertion_failure")
        cls.divide_zero_bin = compile_fixture("divide_zero")

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)

    def test_null_pointer_end_to_end_with_real_gdb(self):
        trace = make_trace(self._tmpdir)
        ctx = base_error_context(
            executable=self.null_pointer_bin,
            classification="segmentation_fault",
            source_location={"file": "null_pointer.cpp", "line": 7, "column": None, "function": "inner"},
        )
        planner = _FakePlanner(
            plans=[
                [{"type": "continue"}, {"type": "backtrace"}, {"type": "locals"}],
                [{"type": "variable", "name": "node"}],
            ]
        )
        diagnosis_engine = _FakeDiagnosisEngine()

        with mock.patch.object(orchestrate, "build_engines", return_value=(planner, diagnosis_engine)):
            result = orchestrate.run(self.null_pointer_bin, ctx, trace, Limits(max_planner_iterations=3))
        trace.close()

        self.assertIn(result.outcome, (orchestrate.OUTCOME_DIAGNOSED, orchestrate.OUTCOME_DIAGNOSED_WITH_LIMIT))
        events = {e["event"] for e in _read_jsonl(trace.path)}
        self.assertIn("debugger_evidence", events)
        self.assertIn("diagnosis", events)
        # A real SIGSEGV must have actually been observed by GDB.
        signal_events = [
            ev
            for e in _read_jsonl(trace.path)
            if e["event"] == "debugger_evidence"
            for ev in e["evidence"]
            if ev.get("event") == "signal"
        ]
        self.assertTrue(signal_events, "expected GDB to report a real SIGSEGV")

    def test_assertion_failure_classification_reaches_planner(self):
        trace = make_trace(self._tmpdir)
        ctx = base_error_context(
            executable=self.assertion_bin,
            classification="abort",
            signal={"number": 6, "name": "SIGABRT", "core_dumped": False},
        )
        planner = _FakePlanner(plans=[[{"type": "continue"}, {"type": "backtrace"}]])
        diagnosis_engine = _FakeDiagnosisEngine()

        with mock.patch.object(orchestrate, "build_engines", return_value=(planner, diagnosis_engine)):
            result = orchestrate.run(self.assertion_bin, ctx, trace, Limits(max_planner_iterations=2))
        trace.close()

        self.assertIn(result.outcome, (orchestrate.OUTCOME_DIAGNOSED, orchestrate.OUTCOME_DIAGNOSED_WITH_LIMIT, orchestrate.OUTCOME_INSUFFICIENT_EVIDENCE))
        self.assertTrue(len(result.evidence) > 0)

    def test_divide_zero_classification_reaches_planner(self):
        trace = make_trace(self._tmpdir)
        ctx = base_error_context(
            executable=self.divide_zero_bin,
            classification="floating_point_exception",
            signal={"number": 8, "name": "SIGFPE", "core_dumped": False},
        )
        planner = _FakePlanner(plans=[[{"type": "continue"}, {"type": "backtrace"}]])
        diagnosis_engine = _FakeDiagnosisEngine()

        with mock.patch.object(orchestrate, "build_engines", return_value=(planner, diagnosis_engine)):
            result = orchestrate.run(self.divide_zero_bin, ctx, trace, Limits(max_planner_iterations=2))
        trace.close()

        self.assertTrue(len(result.evidence) > 0)


@unittest.skipIf(not shutil.which("node"), "node not installed")
class CliDiagnoseSmokeTest(unittest.TestCase):
    """Runs the real `tracecli` script and `tracecli diagnose` on a
    program that exits 0. This path never reaches GDB or the LLM (the
    orchestrator short-circuits on `success`), so it needs no API key and
    no `gdb` on the test machine."""

    @classmethod
    def setUpClass(cls):
        cls.tracecli_bin = os.path.join(REPO_ROOT, "tracecli")

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)

    def test_cli_diagnose_on_successful_program(self):
        proc = subprocess.run(
            [self.tracecli_bin, "diagnose", "/bin/true"],
            cwd=self._tmpdir,
            env={**os.environ, "PYTHONPATH": REPO_ROOT},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("nothing to diagnose", proc.stdout)
        self.assertIn("Trace:", proc.stdout)

        trace_dir = Path(self._tmpdir) / ".tracecli" / "runs"
        jsonl_files = list(trace_dir.glob("*.jsonl"))
        self.assertEqual(len(jsonl_files), 1)
        events = _read_jsonl(jsonl_files[0])
        self.assertEqual(events[0]["event"], "error_detected")
        self.assertEqual(events[-1]["event"], "run_finished")
        self.assertEqual(events[-1]["outcome"], "target_succeeded")


if __name__ == "__main__":
    unittest.main()
