"""
Deterministic tests for TraceCLI Python Debugger Engine against real GDB.

Covers all requirements from Specification Section 13:
1. launch target
2. breakpoint
3. continue
4. backtrace
5. frame selection
6. local variables
7. variable inspection
8. expression evaluation
9. signal detection (SIGSEGV, SIGFPE, SIGABRT)
10. invalid action handling & security validation
11. missing executable handling
12. missing GDB handling
13. debugger timeout handling

Plus: step & next, registers, execute_many batching, and IPC protocol transport.
Supports both `python3 -m unittest` (zero dependencies) and `pytest`.
"""
import os
import shutil
import subprocess
import sys
import unittest
import json

# Ensure repository root is on sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tracecli_debugger.session import DebuggerSession
from tracecli_debugger.gdb_adapter import GDBNotFoundError, GDBTimeoutError

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def compile_fixture(name: str) -> str:
    src = os.path.join(FIXTURES_DIR, f"{name}.cpp")
    out = os.path.join(FIXTURES_DIR, name)
    if not os.path.exists(out) or os.path.getmtime(src) > os.path.getmtime(out):
        subprocess.run(["g++", "-g", "-O0", "-o", out, src], check=True)
    return out


@unittest.skipIf(not shutil.which("gdb"), "gdb not installed on system")
class TestDebuggerEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nested_calls_bin = compile_fixture("nested_calls")
        cls.null_pointer_bin = compile_fixture("null_pointer")
        cls.divide_zero_bin = compile_fixture("divide_zero")
        cls.assert_fail_bin = compile_fixture("assertion_failure")
        cls.loop_bin = compile_fixture("infinite_loop")

    def test_01_launch_target(self):
        session = DebuggerSession(self.nested_calls_bin)
        try:
            events = session.launch()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"], "session_started")
            self.assertIn("nested_calls", events[0]["executable"])
            self.assertIn("raw", events[0])
        finally:
            session.close()

    def test_02_breakpoint(self):
        session = DebuggerSession(self.nested_calls_bin)
        try:
            session.launch()
            events = session.execute({"type": "breakpoint", "location": "inner"})
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"], "breakpoint_set")
            self.assertEqual(events[0]["number"], "1")
            self.assertIn("raw", events[0])
        finally:
            session.close()

    def test_03_continue_hits_breakpoint(self):
        session = DebuggerSession(self.nested_calls_bin)
        try:
            session.launch()
            session.execute({"type": "breakpoint", "location": "inner"})
            events = session.execute({"type": "continue"})
            hit_events = [e for e in events if e["event"] == "breakpoint_event"]
            self.assertTrue(len(hit_events) >= 1)
            self.assertEqual(hit_events[0]["function"], "inner")
            self.assertEqual(hit_events[0]["line"], 2)
            self.assertIn("raw", hit_events[0])
        finally:
            session.close()

    def test_04_backtrace(self):
        session = DebuggerSession(self.nested_calls_bin)
        try:
            session.launch()
            session.execute({"type": "breakpoint", "location": "inner"})
            session.execute({"type": "continue"})
            events = session.execute({"type": "backtrace", "depth": 10})
            frames = [e for e in events if e["event"] == "stack_frame"]
            funcs = [f["function"] for f in frames]
            self.assertEqual(funcs, ["inner", "middle", "main"])
            self.assertEqual(frames[0]["frame"], 0)
            self.assertEqual(frames[1]["frame"], 1)
            self.assertEqual(frames[2]["frame"], 2)
            self.assertTrue(all("raw" in f for f in frames))
        finally:
            session.close()

    def test_05_frame_selection(self):
        session = DebuggerSession(self.nested_calls_bin)
        try:
            session.launch()
            session.execute({"type": "breakpoint", "location": "inner"})
            session.execute({"type": "continue"})
            events = session.execute({"type": "frame", "frame": 1})
            self.assertEqual(events[0]["event"], "frame_selected")
            self.assertIn("raw", events[0])
        finally:
            session.close()

    def test_06_locals(self):
        session = DebuggerSession(self.nested_calls_bin)
        try:
            session.launch()
            session.execute({"type": "breakpoint", "location": "inner"})
            session.execute({"type": "continue"})
            # Step one line so `doubled` is initialized in scope
            session.execute({"type": "step"})
            events = session.execute({"type": "locals"})
            var_events = [e for e in events if e["event"] == "variable"]
            names = [v["name"] for v in var_events]
            self.assertIn("doubled", names)
            val = next(v["value"] for v in var_events if v["name"] == "doubled")
            self.assertEqual(val, "42")
        finally:
            session.close()

    def test_07_variable_inspection(self):
        session = DebuggerSession(self.nested_calls_bin)
        try:
            session.launch()
            session.execute({"type": "breakpoint", "location": "inner"})
            session.execute({"type": "continue"})
            events = session.execute({"type": "variable", "name": "x"})
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"], "variable")
            self.assertEqual(events[0]["name"], "x")
            self.assertEqual(events[0]["value"], "21")
            self.assertIn("raw", events[0])
        finally:
            session.close()

    def test_08_expression_evaluation(self):
        session = DebuggerSession(self.nested_calls_bin)
        try:
            session.launch()
            session.execute({"type": "breakpoint", "location": "inner"})
            session.execute({"type": "continue"})
            events = session.execute({"type": "expression", "expr": "x * 10 + 3"})
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"], "variable")
            self.assertEqual(events[0]["name"], "x * 10 + 3")
            self.assertEqual(events[0]["value"], "213")
        finally:
            session.close()

    def test_09_signal_detection_null_pointer(self):
        session = DebuggerSession(self.null_pointer_bin)
        try:
            session.launch()
            events = session.execute({"type": "continue"})
            sig = next((e for e in events if e["event"] == "signal"), None)
            self.assertIsNotNone(sig)
            self.assertEqual(sig["signal"], "SIGSEGV")
            self.assertIn("raw", sig)
        finally:
            session.close()

    def test_10_signal_detection_divide_zero(self):
        session = DebuggerSession(self.divide_zero_bin)
        try:
            session.launch()
            events = session.execute({"type": "continue"})
            sig = next((e for e in events if e["event"] == "signal"), None)
            self.assertIsNotNone(sig)
            self.assertEqual(sig["signal"], "SIGFPE")
            self.assertIn("raw", sig)
        finally:
            session.close()

    def test_11_signal_detection_assertion_failure(self):
        session = DebuggerSession(self.assert_fail_bin)
        try:
            session.launch()
            events = session.execute({"type": "continue"})
            sig = next((e for e in events if e["event"] == "signal"), None)
            self.assertIsNotNone(sig)
            self.assertEqual(sig["signal"], "SIGABRT")
            self.assertIn("raw", sig)
        finally:
            session.close()

    def test_12_process_exit_normally(self):
        session = DebuggerSession(self.nested_calls_bin)
        try:
            session.launch()
            events = session.execute({"type": "continue"})
            exit_evt = next((e for e in events if e["event"] == "process_exit"), None)
            self.assertIsNotNone(exit_evt)
            self.assertEqual(exit_evt["code"], "0")
        finally:
            session.close()

    def test_13_step_and_next(self):
        session = DebuggerSession(self.nested_calls_bin)
        try:
            session.launch()
            session.execute({"type": "breakpoint", "location": "inner"})
            session.execute({"type": "continue"})
            step_events = session.execute({"type": "step", "count": 1})
            self.assertTrue(len(step_events) >= 1)
            self.assertIn("raw", step_events[0])

            next_events = session.execute({"type": "next", "count": 1})
            self.assertTrue(len(next_events) >= 1)
            self.assertIn("raw", next_events[0])
        finally:
            session.close()

    def test_14_registers(self):
        session = DebuggerSession(self.nested_calls_bin)
        try:
            session.launch()
            session.execute({"type": "breakpoint", "location": "inner"})
            session.execute({"type": "continue"})
            events = session.execute({"type": "registers"})
            reg_events = [e for e in events if e["event"] == "register"]
            self.assertTrue(len(reg_events) > 10)
            self.assertIn("number", reg_events[0])
            self.assertIn("value", reg_events[0])
        finally:
            session.close()

    def test_15_execute_many_batch(self):
        session = DebuggerSession(self.nested_calls_bin)
        try:
            session.launch()
            actions = [
                {"type": "breakpoint", "location": "inner"},
                {"type": "continue"},
                {"type": "variable", "name": "x"},
            ]
            events = session.execute_many(actions)
            event_types = [e["event"] for e in events]
            self.assertIn("breakpoint_set", event_types)
            self.assertIn("breakpoint_event", event_types)
            self.assertIn("variable", event_types)
        finally:
            session.close()

    def test_16_invalid_actions_and_injection_prevention(self):
        session = DebuggerSession(self.nested_calls_bin)
        try:
            session.launch()
            bad_actions = [
                {"type": "shell", "cmd": "rm -rf /"},
                {"type": "breakpoint", "location": "main; rm -rf /"},
                {"type": "breakpoint", "location": ""},
                {"type": "expression", "expr": 'system("ls")'},
                {"type": "expression", "expr": "a; fork()"},
                {"type": "backtrace", "depth": 999999},
                {"type": "backtrace", "depth": -5},
                {"type": "variable", "name": "x; DROP TABLE users"},
                {"type": "variable", "name": ""},
                {"type": "frame", "frame": -1},
                "not a dict",
                123,
            ]
            for bad in bad_actions:
                events = session.execute(bad)  # type: ignore
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["event"], "invalid_action")
                self.assertIn("error", events[0])
        finally:
            session.close()

    def test_17_action_aliases(self):
        session = DebuggerSession(self.nested_calls_bin)
        try:
            session.launch()
            session.execute({"type": "breakpoint", "location": "inner"})
            session.execute({"type": "continue"})
            # Using 'action' instead of 'type', and 'inspect_variable' alias
            events = session.execute({"action": "inspect_variable", "name": "x"})
            self.assertEqual(events[0]["event"], "variable")
            self.assertEqual(events[0]["value"], "21")
        finally:
            session.close()

    def test_18_missing_executable(self):
        with self.assertRaises(FileNotFoundError):
            DebuggerSession(os.path.join(FIXTURES_DIR, "nonexistent_binary_xyz"))

    def test_19_missing_gdb(self):
        with self.assertRaises(GDBNotFoundError):
            DebuggerSession(self.nested_calls_bin, gdb_path="/usr/bin/not_a_real_gdb")

    def test_20_debugger_timeout(self):
        session = DebuggerSession(self.loop_bin, timeout=0.5)
        try:
            session.launch()
            events = session.execute({"type": "continue"})
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"], "timeout")
        finally:
            session.close()

    def test_21_protocol_ipc(self):
        cmd = [sys.executable, "-m", "tracecli_debugger.protocol", self.nested_calls_bin]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=REPO_ROOT,
        )

        try:
            # First line is session_started
            line = proc.stdout.readline()
            self.assertTrue(line)
            data = json.loads(line)
            self.assertEqual(data["event"], "session_started")

            # Send breakpoint
            proc.stdin.write(json.dumps({"type": "breakpoint", "location": "inner"}) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            data = json.loads(line)
            self.assertEqual(data["event"], "breakpoint_set")

            # Send continue
            proc.stdin.write(json.dumps({"type": "continue"}) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            data = json.loads(line)
            self.assertEqual(data["event"], "breakpoint_event")
            self.assertEqual(data["function"], "inner")

            # Close gracefully
            proc.stdin.write("__close__\n")
            proc.stdin.flush()
            proc.wait(timeout=3)
            self.assertEqual(proc.returncode, 0)
        finally:
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
                if proc.stdout and not proc.stdout.closed:
                    proc.stdout.close()
                if proc.stderr and not proc.stderr.closed:
                    proc.stderr.close()
            except OSError:
                pass
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    unittest.main(verbosity=2)
