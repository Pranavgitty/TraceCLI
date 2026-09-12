"""
JSON-over-stdin/stdout Subprocess Protocol Transport (MVP IPC).

Allows external processes (such as a Rust CLI / orchestrator) to interact with
the Python Debugger Engine via standard input and standard output streams.

Protocol definition:
    - Input: One JSON action object per line on stdin
    - Output: One JSON evidence object per line on stdout (flushed immediately)
    - Termination: Writing `__close__` or closing stdin cleanly terminates GDB

Usage:
    python -m tracecli_debugger.protocol /path/to/executable
"""
import sys
import json

from .session import DebuggerSession


def main() -> None:
    if len(sys.argv) < 2:
        print(
            json.dumps({"event": "debugger_error", "message": "usage: protocol.py <executable>"}),
            flush=True,
        )
        sys.exit(1)

    executable = sys.argv[1]

    try:
        session = DebuggerSession(executable)
    except Exception as e:
        print(json.dumps({"event": "debugger_error", "message": str(e)}), flush=True)
        sys.exit(1)

    try:
        # Initial launch event
        for evt in session.launch():
            print(json.dumps(evt), flush=True)

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            if line == "__close__":
                break

            try:
                action = json.loads(line)
            except json.JSONDecodeError as e:
                print(
                    json.dumps({
                        "event": "invalid_action",
                        "error": f"invalid JSON: {e}",
                        "raw": line,
                    }),
                    flush=True,
                )
                continue

            for evt in session.execute(action):
                print(json.dumps(evt), flush=True)

    finally:
        session.close()


if __name__ == "__main__":
    main()
