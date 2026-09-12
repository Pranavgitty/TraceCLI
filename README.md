# TraceCLI — Part 2: Python Debugger Engine

This component implements **Part 2 of TraceCLI**:
`Structured Debug Actions → Python Debugger Engine → GDB → Structured Debug Evidence`.

It receives structured debugger actions (never raw GDB strings), executes them programmatically against GDB using Machine Interface (GDB/MI v2), and returns normalized structured evidence events while preserving the original raw output.

---

## 1. Directory & File Structure

```
TraceCLI/
├── tracecli_debugger/
│   ├── __init__.py         # Package exports
│   ├── actions.py          # Structured DebugAction model & security validation
│   ├── evidence.py         # Structured Evidence model (preserves raw MI data)
│   ├── gdb_adapter.py      # Low-level GDB/MI process runner & asynchronous I/O
│   ├── session.py          # High-level DebuggerSession API (execute / execute_many)
│   └── protocol.py         # Subprocess JSON-over-stdin/stdout IPC protocol
├── tests/
│   ├── __init__.py
│   ├── test_debugger.py    # Deterministic test suite (21/21 passing against real GDB)
│   └── fixtures/           # C++ test programs
│       ├── nested_calls.cpp
│       ├── null_pointer.cpp
│       ├── divide_zero.cpp
│       ├── assertion_failure.cpp
│       └── infinite_loop.cpp
└── README.md
```

---

## 2. Debugger API

```python
from tracecli_debugger.session import DebuggerSession

# 1. Initialize session with target binary and optional gdb path / timeout
session = DebuggerSession("./target_executable", gdb_path="gdb", timeout=10.0)

# 2. Launch GDB session
events = session.launch()
# -> [{'event': 'session_started', 'executable': '/path/to/target', 'raw': '...'}]

# 3. Set breakpoint
events = session.execute({"type": "breakpoint", "location": "main"})
# -> [{'event': 'breakpoint_set', 'number': '1', 'location': 'main', 'raw': '...'}]

# 4. Continue execution (starts execution on first continue)
events = session.execute({"type": "continue"})
# -> [{'event': 'breakpoint_event', 'function': 'main', 'file': 'main.cpp', 'line': 10, 'raw': '...'}]

# 5. Inspect backtrace
events = session.execute({"type": "backtrace", "depth": 20})
# -> [{'event': 'stack_frame', 'frame': 0, 'function': 'inner', 'file': '...', 'line': 2, 'raw': '...'}, ...]

# 6. Evaluate expression or inspect variable
events = session.execute({"type": "variable", "name": "node"})
events = session.execute({"type": "expression", "expr": "x * 10"})

# 7. Batch execution
events = session.execute_many([
    {"type": "step", "count": 1},
    {"type": "locals"},
])

# 8. Clean termination
session.close()
```

---

## 3. Action Schema (`actions.py`)

All input actions are strictly validated against an allowlist before any command is generated. Unknown types or illegal characters are rejected with an `invalid_action` event without ever reaching GDB.

| Action `type` | Parameters | Defaults | Constraints / Validation |
|---|---|---|---|
| `breakpoint` | `location`: str | Required | Allowed chars `[A-Za-z0-9_./:-]`; no shell metachars |
| `continue` | — | — | Starts inferior if unstarted, resumes otherwise |
| `step` | `count`: int | 1 | Integer $1 \le count \le 1000$ |
| `next` | `count`: int | 1 | Integer $1 \le count \le 1000$ |
| `backtrace` | `depth`: int | 20 | Integer $1 \le depth \le 200$ |
| `frame` | `frame`: int | Required | Non-negative integer |
| `locals` | — | — | Lists local variables in current stack frame |
| `variable` | `name`: str | Required | Matches `^[A-Za-z_][A-Za-z0-9_.\[\]\->]*$` |
| `expression` | `expr`: str | Required | Max 200 chars; blocklist (`system`, `call `, `;`, `` ` ``, `$(`, `&&`, `\|\|`, `fork`, `exec`) |
| `registers` | — | — | Dumps register values |

*Note: For compatibility between planner representations, `{"action": "..."}` is accepted as an alias for `{"type": "..."}`, and `"inspect_variable"` is accepted as an alias for `"variable"`.*

---

## 4. Evidence Schema (`evidence.py`)

Every evidence event returned is a dictionary containing an `event` discriminator and the original `raw` GDB/MI output:

- **`session_started`**: `{"event": "session_started", "executable": str, "raw": str}`
- **`breakpoint_set`**: `{"event": "breakpoint_set", "number": str, "location": str, "raw": str}`
- **`breakpoint_event`**: `{"event": "breakpoint_event", "function": str, "file": Optional[str], "line": Optional[int], "raw": str}`
- **`stack_frame`**: `{"event": "stack_frame", "frame": int, "function": str, "file": Optional[str], "line": Optional[int], "raw": str}`
- **`frame_selected`**: `{"event": "frame_selected", "raw": str}`
- **`variable`**: `{"event": "variable", "name": str, "value": str, "raw": str}`
- **`register`**: `{"event": "register", "number": str, "value": str, "raw": str}`
- **`signal`**: `{"event": "signal", "signal": str, "raw": str}` (e.g. `SIGSEGV`, `SIGFPE`, `SIGABRT`)
- **`process_exit`**: `{"event": "process_exit", "code": str, "raw": str}`
- **`command_result`**: `{"event": "command_result", "raw": str, ...}`
- **`debugger_error`**: `{"event": "debugger_error", "message": str, "raw": str}`
- **`invalid_action`**: `{"event": "invalid_action", "error": str, "raw": str}`
- **`timeout`**: `{"event": "timeout", "message": str, "raw": str}`

---

## 5. GDB Interaction Strategy

1. **Machine Interface Protocol**: GDB is spawned with `--nx -q --interpreter=mi2 <executable>`. This produces structured, parseable MI async records and results rather than unstructured terminal text.
2. **Strict Component Isolation**: `gdb_adapter.py` is the only module aware of GDB commands or MI syntax. `session.py` interacts via high-level action dispatch, enabling future replacement with an LLDB adapter.
3. **Asynchronous MI Handling**: Modern GDB MI returns an immediate `^running` acknowledgment chunk followed later by an asynchronous stop event (`*stopped`, `reason=exited`, or signal). `send_and_wait_for_stop()` polls until the actual stop/exit event is captured.
4. **Buffer & Pipe Safety**: Raw non-blocking byte reads (`os.read`) with explicit line buffering avoid deadlocks from `select()` over buffered Python wrappers.

---

## 6. IPC Protocol Transport (`protocol.py`)

A standalone JSON-over-stdin/stdout subprocess protocol is provided for communication with orchestrators (e.g. Rust CLI):

```bash
python3 -m tracecli_debugger.protocol /path/to/executable
```

- **Inbound (stdin)**: Single JSON action per line (`{"type": "backtrace", "depth": 10}\n`)
- **Outbound (stdout)**: Single JSON evidence event per line (flushed immediately)
- **Termination**: Sending `__close__\n` or closing stdin cleanly terminates the GDB inferior and exits.

---

## 7. Running the Tests

The test suite requires **zero external dependencies** and uses Python's standard library `unittest` (also compatible with `pytest`):

```bash
# Run with standard Python (built-in unittest)
python3 -m unittest discover -s tests -p "test_*.py" -v

# Or run with pytest if installed
pytest tests/ -v
```
