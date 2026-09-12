"""
Structured Debug Action model.

The LLM never generates raw GDB commands. It generates structured actions like:
    {"type": "backtrace", "depth": 20}
    {"type": "variable", "name": "node"}

All incoming dictionaries parsed via DebugAction.from_dict() are treated as
untrusted input (originating from an LLM), so every field is strictly validated
against an allow-list of schemas, types, and characters before reaching GDB.
"""
from dataclasses import dataclass
from typing import Optional, Any, Dict
import re

MAX_BACKTRACE_DEPTH = 200
MAX_STEP_COUNT = 1000
MAX_EXPR_LEN = 200

# Canonical action vocabulary per specification Section 4
VALID_ACTION_TYPES = {
    "breakpoint", "continue", "step", "next", "backtrace",
    "frame", "locals", "variable", "expression", "registers",
}

# Alias mapping for robustness (e.g. spec section 3 vs 4)
ACTION_ALIASES = {
    "inspect_variable": "variable",
}

# Characters disallowed in locations or variable names to prevent command injection
_DISALLOWED_CHARS = set(';&|`$(){}\n\r')

_VAR_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_.\[\]\->]*$')
_LOCATION_RE = re.compile(r'^[A-Za-z0-9_./:\-]+$')

# Tokens that could attempt command chaining or external execution
_EXPR_BLOCKLIST = (";", "&&", "||", "`", "$(", "system", "call ", "fork", "exec")


class ActionValidationError(ValueError):
    """Raised when an action dictionary fails validation."""


@dataclass
class DebugAction:
    type: str
    location: Optional[str] = None   # breakpoint: "file:line" or "function"
    depth: Optional[int] = None      # backtrace
    frame: Optional[int] = None      # frame
    name: Optional[str] = None       # variable
    expr: Optional[str] = None       # expression
    count: Optional[int] = None      # step / next

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "DebugAction":
        if not isinstance(d, dict):
            raise ActionValidationError("action must be a JSON object")

        raw_type = d.get("type") or d.get("action")
        if not isinstance(raw_type, str):
            raise ActionValidationError(f"unknown or missing action type: {raw_type!r}")

        t = ACTION_ALIASES.get(raw_type, raw_type)
        if t not in VALID_ACTION_TYPES:
            raise ActionValidationError(f"unknown action type: {raw_type!r}")

        action = DebugAction(type=t)

        if t == "breakpoint":
            loc = d.get("location") or d.get("loc")
            if not isinstance(loc, str) or not loc.strip():
                raise ActionValidationError("breakpoint requires a non-empty 'location'")
            loc = loc.strip()
            if not _LOCATION_RE.match(loc) or (set(loc) & _DISALLOWED_CHARS):
                raise ActionValidationError("breakpoint location contains disallowed characters")
            action.location = loc

        elif t in ("step", "next"):
            c = d.get("count")
            if c is None:
                c = 1
            if not isinstance(c, int) or isinstance(c, bool) or not (1 <= c <= MAX_STEP_COUNT):
                raise ActionValidationError(f"count must be an int between 1 and {MAX_STEP_COUNT}")
            action.count = c

        elif t == "backtrace":
            depth = d.get("depth")
            if depth is None:
                depth = 20
            if not isinstance(depth, int) or isinstance(depth, bool) or not (1 <= depth <= MAX_BACKTRACE_DEPTH):
                raise ActionValidationError(f"depth must be an int between 1 and {MAX_BACKTRACE_DEPTH}")
            action.depth = depth

        elif t == "frame":
            fr = d.get("frame") if d.get("frame") is not None else d.get("level")
            if not isinstance(fr, int) or isinstance(fr, bool) or fr < 0:
                raise ActionValidationError("frame must be a non-negative integer")
            action.frame = fr

        elif t == "variable":
            name = d.get("name") or d.get("var")
            if not isinstance(name, str) or not name.strip():
                raise ActionValidationError("variable requires a non-empty 'name'")
            name = name.strip()
            if not _VAR_NAME_RE.match(name) or (set(name) & _DISALLOWED_CHARS):
                raise ActionValidationError("variable name contains disallowed characters")
            action.name = name

        elif t == "expression":
            expr = d.get("expr") or d.get("expression")
            if not isinstance(expr, str) or not expr.strip():
                raise ActionValidationError("expression requires a non-empty 'expr'")
            expr = expr.strip()
            if len(expr) > MAX_EXPR_LEN:
                raise ActionValidationError(f"expression exceeds maximum length of {MAX_EXPR_LEN} characters")
            lowered = expr.lower()
            for bad in _EXPR_BLOCKLIST:
                if bad in lowered:
                    raise ActionValidationError(f"expression contains disallowed token: {bad!r}")
            action.expr = expr

        # continue, locals, registers have no mandatory parameters
        return action
