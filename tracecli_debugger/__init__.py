"""
TraceCLI Debugger Engine (Part 2).
Provides structured debugging abstraction over GDB.
"""
from .session import DebuggerSession
from .actions import DebugAction, ActionValidationError
from .evidence import Evidence
from .gdb_adapter import GDBAdapter, GDBNotFoundError, GDBStartupError, GDBTimeoutError

__all__ = [
    "DebuggerSession",
    "DebugAction",
    "ActionValidationError",
    "Evidence",
    "GDBAdapter",
    "GDBNotFoundError",
    "GDBStartupError",
    "GDBTimeoutError",
]
