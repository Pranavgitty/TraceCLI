"""
DebuggerSession: Main programmatic interface for the TraceCLI Debugger Engine.

Example usage:
    session = DebuggerSession("./target_bin")
    session.launch()
    session.execute({"type": "breakpoint", "location": "main"})
    session.execute({"type": "continue"})
    events = session.execute({"type": "backtrace", "depth": 20})
    session.close()
"""
import re
from typing import List, Dict, Any, Optional

from .actions import DebugAction, ActionValidationError
from .gdb_adapter import GDBAdapter, GDBTimeoutError, GDBNotFoundError, GDBStartupError
from .evidence import Evidence

_QUOTED = r'((?:[^"\\]|\\.)*)'


class DebuggerSession:
    def __init__(self, executable: str, gdb_path: str = "gdb", timeout: float = 10.0):
        self.adapter = GDBAdapter(executable, gdb_path=gdb_path, timeout=timeout)
        self._launched = False
        self._running = False

    # ---- lifecycle ---------------------------------------------------

    def launch(self) -> List[Dict[str, Any]]:
        """Starts GDB and loads the target executable."""
        lines = self.adapter.start()
        self._launched = True
        return [
            Evidence(
                "session_started",
                raw=self._raw(lines),
                data={"executable": self.adapter.executable},
            ).to_dict()
        ]

    def close(self) -> None:
        """Terminates GDB and cleans up inferior processes."""
        self.adapter.close()
        self._launched = False
        self._running = False

    # ---- public action API --------------------------------------------

    def execute(self, action_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Executes a single structured debug action and returns a list of evidence events.
        """
        try:
            action = DebugAction.from_dict(action_dict)
        except ActionValidationError as e:
            return [
                Evidence(
                    "invalid_action",
                    raw=repr(action_dict),
                    data={"error": str(e)},
                ).to_dict()
            ]

        if not self._launched:
            # Auto-launch if caller forgot to explicitly call launch()
            try:
                self.launch()
            except (GDBNotFoundError, GDBStartupError, FileNotFoundError) as e:
                return [
                    Evidence("debugger_error", raw=str(e), data={"message": str(e)}).to_dict()
                ]

        try:
            return self._dispatch(action)
        except GDBTimeoutError as e:
            return [Evidence("timeout", raw=str(e), data={"message": str(e)}).to_dict()]
        except (GDBNotFoundError, GDBStartupError) as e:
            return [Evidence("debugger_error", raw=str(e), data={"message": str(e)}).to_dict()]

    def execute_many(self, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Executes a batch of structured actions in sequence, returning all evidence."""
        out: List[Dict[str, Any]] = []
        for a in actions:
            out.extend(self.execute(a))
        return out

    # ---- action dispatch ----------------------------------------------

    def _dispatch(self, action: DebugAction) -> List[Dict[str, Any]]:
        if action.type == "breakpoint":
            return self._parse_breakpoint(self.adapter.send(f"-break-insert {action.location}"))

        if action.type == "continue":
            # GDB requires -exec-run for the first execution and -exec-continue thereafter
            cmd = "-exec-run" if not self._running else "-exec-continue"
            self._running = True
            return self._parse_exec_result(self.adapter.send_and_wait_for_stop(cmd))

        if action.type == "step":
            return self._parse_exec_result(
                self.adapter.send_and_wait_for_stop(f"-exec-step {action.count}")
            )

        if action.type == "next":
            return self._parse_exec_result(
                self.adapter.send_and_wait_for_stop(f"-exec-next {action.count}")
            )

        if action.type == "backtrace":
            depth = action.depth if action.depth is not None else 20
            return self._parse_backtrace(self.adapter.send(f"-stack-list-frames 0 {depth - 1}"))

        if action.type == "frame":
            return self._parse_generic(
                self.adapter.send(f"-stack-select-frame {action.frame}"), "frame_selected"
            )

        if action.type == "locals":
            return self._parse_locals(self.adapter.send("-stack-list-locals --all-values"))

        if action.type == "variable":
            return self._parse_variable(
                action.name or "",
                self.adapter.send(f'-data-evaluate-expression "{action.name}"'),
            )

        if action.type == "expression":
            return self._parse_variable(
                action.expr or "",
                self.adapter.send(f'-data-evaluate-expression "{action.expr}"'),
            )

        if action.type == "registers":
            return self._parse_registers(self.adapter.send("-data-list-register-values x"))

        return [
            Evidence(
                "invalid_action",
                raw=str(action),
                data={"error": f"unhandled action type: {action.type}"},
            ).to_dict()
        ]

    # ---- MI output parsing ---------------------------------------------

    @staticmethod
    def _raw(lines: List[str]) -> str:
        return "\n".join(lines)

    def _parse_breakpoint(self, lines: List[str]) -> List[Dict[str, Any]]:
        raw = self._raw(lines)
        if any(l.startswith("^error") for l in lines):
            msg = re.search(r'msg="' + _QUOTED + '"', raw)
            return [
                Evidence(
                    "debugger_error",
                    raw=raw,
                    data={"message": msg.group(1) if msg else raw},
                ).to_dict()
            ]

        num_m = re.search(r'number="(\d+)"', raw)
        loc_m = re.search(r'original-location="([^"]*)"', raw) or re.search(r'func="([^"]*)"', raw)
        return [
            Evidence(
                "breakpoint_set",
                raw=raw,
                data={
                    "number": num_m.group(1) if num_m else None,
                    "location": loc_m.group(1) if loc_m else None,
                },
            ).to_dict()
        ]

    def _parse_exec_result(self, lines: List[str]) -> List[Dict[str, Any]]:
        raw = self._raw(lines)
        events: List[Dict[str, Any]] = []

        sig = re.search(r'signal-name="([A-Za-z0-9]+)"', raw)
        if sig:
            events.append(Evidence("signal", raw=raw, data={"signal": sig.group(1)}).to_dict())

        if "*stopped,reason=\"exited" in raw or ",reason=\"exited-normally\"" in raw:
            code = re.search(r'exit-code="(\S+)"', raw)
            events.append(
                Evidence(
                    "process_exit",
                    raw=raw,
                    data={"code": code.group(1) if code else "0"},
                ).to_dict()
            )

        bp_hit = re.search(
            r'reason="breakpoint-hit".*?func="([^"]*)"(?:.*?file="([^"]*)")?(?:.*?line="(\d+)")?',
            raw,
        )
        if bp_hit:
            events.append(
                Evidence(
                    "breakpoint_event",
                    raw=raw,
                    data={
                        "function": bp_hit.group(1),
                        "file": bp_hit.group(2),
                        "line": int(bp_hit.group(3)) if bp_hit.group(3) else None,
                    },
                ).to_dict()
            )

        step_stop = re.search(
            r'reason="end-stepping-range".*?func="([^"]*)"(?:.*?file="([^"]*)")?(?:.*?line="(\d+)")?',
            raw,
        )
        if step_stop and not events:
            events.append(
                Evidence(
                    "command_result",
                    raw=raw,
                    data={
                        "function": step_stop.group(1),
                        "file": step_stop.group(2),
                        "line": int(step_stop.group(3)) if step_stop.group(3) else None,
                    },
                ).to_dict()
            )

        if any(l.startswith("^error") for l in lines):
            msg = re.search(r'msg="' + _QUOTED + '"', raw)
            events.append(
                Evidence(
                    "debugger_error",
                    raw=raw,
                    data={"message": msg.group(1) if msg else raw},
                ).to_dict()
            )

        if not events:
            events.append(Evidence("command_result", raw=raw, data={}).to_dict())

        return events

    def _parse_backtrace(self, lines: List[str]) -> List[Dict[str, Any]]:
        raw = self._raw(lines)
        if any(l.startswith("^error") for l in lines):
            msg = re.search(r'msg="' + _QUOTED + '"', raw)
            return [
                Evidence(
                    "debugger_error",
                    raw=raw,
                    data={"message": msg.group(1) if msg else raw},
                ).to_dict()
            ]

        # Robust frame-by-frame parsing independent of attribute order
        frame_pattern = re.compile(r'frame=\{([^}]+)\}')
        frame_matches = frame_pattern.findall(raw)
        events: List[Dict[str, Any]] = []

        for content in frame_matches:
            lvl_m = re.search(r'level="(\d+)"', content)
            func_m = re.search(r'func="([^"]*)"', content)
            file_m = re.search(r'file="([^"]*)"', content)
            line_m = re.search(r'line="(\d+)"', content)

            if lvl_m and func_m:
                events.append(
                    Evidence(
                        "stack_frame",
                        raw=raw,
                        data={
                            "frame": int(lvl_m.group(1)),
                            "function": func_m.group(1),
                            "file": file_m.group(1) if file_m else None,
                            "line": int(line_m.group(1)) if line_m else None,
                        },
                    ).to_dict()
                )

        if not events:
            events.append(
                Evidence(
                    "debugger_error",
                    raw=raw,
                    data={"message": "could not parse backtrace output"},
                ).to_dict()
            )

        return events

    def _parse_locals(self, lines: List[str]) -> List[Dict[str, Any]]:
        raw = self._raw(lines)
        if any(l.startswith("^error") for l in lines):
            msg = re.search(r'msg="' + _QUOTED + '"', raw)
            return [
                Evidence(
                    "debugger_error",
                    raw=raw,
                    data={"message": msg.group(1) if msg else raw},
                ).to_dict()
            ]

        pattern = re.compile(r'name="([^"]*)",value="' + _QUOTED + '"')
        events = [
            Evidence("variable", raw=raw, data={"name": n, "value": v}).to_dict()
            for n, v in pattern.findall(raw)
        ]
        if not events:
            events.append(Evidence("command_result", raw=raw, data={}).to_dict())
        return events

    def _parse_variable(self, name: str, lines: List[str]) -> List[Dict[str, Any]]:
        raw = self._raw(lines)
        if any(l.startswith("^error") for l in lines):
            msg = re.search(r'msg="' + _QUOTED + '"', raw)
            return [
                Evidence(
                    "debugger_error",
                    raw=raw,
                    data={"message": msg.group(1) if msg else raw},
                ).to_dict()
            ]

        m = re.search(r'value="' + _QUOTED + '"', raw)
        return [
            Evidence(
                "variable",
                raw=raw,
                data={"name": name, "value": m.group(1) if m else None},
            ).to_dict()
        ]

    def _parse_registers(self, lines: List[str]) -> List[Dict[str, Any]]:
        raw = self._raw(lines)
        if any(l.startswith("^error") for l in lines):
            msg = re.search(r'msg="' + _QUOTED + '"', raw)
            return [
                Evidence(
                    "debugger_error",
                    raw=raw,
                    data={"message": msg.group(1) if msg else raw},
                ).to_dict()
            ]

        pattern = re.compile(r'number="(\d+)",value="' + _QUOTED + '"')
        events = [
            Evidence("register", raw=raw, data={"number": n, "value": v}).to_dict()
            for n, v in pattern.findall(raw)
        ]
        if not events:
            events.append(Evidence("command_result", raw=raw, data={}).to_dict())
        return events

    def _parse_generic(self, lines: List[str], event_name: str) -> List[Dict[str, Any]]:
        raw = self._raw(lines)
        if any(l.startswith("^error") for l in lines):
            msg = re.search(r'msg="' + _QUOTED + '"', raw)
            return [
                Evidence(
                    "debugger_error",
                    raw=raw,
                    data={"message": msg.group(1) if msg else raw},
                ).to_dict()
            ]
        return [Evidence(event_name, raw=raw, data={}).to_dict()]
