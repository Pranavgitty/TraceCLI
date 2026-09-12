# Handoff - HACKELBERRY_FINN

> Updated 2026-09-13T02:34:00+05:30 by atindrak27 (session 0913-0222, track 4)
> Read this first. The full log is cyhi-logs/session.md.

## Current state
- **Part 1 (Rust - Error & Project Context Layer)**: Implemented and complete. Greenfield Rust project (`cargo test` passes 38 tests). Verified against real C++ fixtures.
- **Part 2 (Python - Debugger Engine)**: Implemented and complete. Standalone Python package `tracecli_debugger` (`python3 -m unittest` passes 21 tests against real GDB). Zero external runtime dependencies.

## Works
### Part 1: Error & Project Context (Rust)
- `tracecli run [--context-lines N] <exe> [args...]` executes a target, captures stdout/stderr/exit code/signal/duration, classifies the failure, and prints a pretty-JSON `ErrorContext` to stdout.
- tracecli mirrors the target's exit status (same code, or 128+signal) so it composes in shell scripts.
- Source location extraction handles both gcc/clang colon diagnostics (`file.cpp:142:17: error: ...`) and macOS/BSD libc `assert()` messages (`function main, file x.cpp, line 4.`) via `CppSourceLocationExtractor`.
- Bounded source context around a detected failure line.
- Best-effort build context: compiler + version probing and debug-symbol heuristic.

### Part 2: Debugger Engine (Python)
- Structured action validation model (`actions.py`) strictly enforcing vocabulary: `breakpoint`, `continue`, `step`, `next`, `backtrace`, `frame`, `locals`, `variable`, `expression`, `registers` with input sanitization against shell injection.
- Low-level GDB/MI adapter (`gdb_adapter.py`) running `gdb --interpreter=mi2` with non-blocking raw I/O and async MI stop-detection.
- High-level `DebuggerSession` (`session.py`) supporting `launch()`, `execute()`, `execute_many()`, and `close()`.
- Structured evidence model (`evidence.py`) retaining full `raw` MI records while emitting typed events.
- JSON-over-stdin/stdout subprocess protocol transport (`protocol.py`).
- Deterministic C++ fixtures for null pointer, divide-by-zero, assertion failure, infinite loop, and nested calls.
- 21 unit tests in `tests/test_debugger.py` passing under standard library `unittest` and `pytest`.

## Broken
- None.

## Next 3 things
1. Connect Part 1 `ErrorContext` to LLM #1 Debug Planner (Part 3) to generate structured actions.
2. Route structured actions to the Python Debugger Engine via `protocol.py` (JSON-over-stdin/stdout).
3. Wire structured evidence output into LLM #2 Diagnosis Engine (Part 4) for root-cause analysis.

## Decisions (and why)
- Part 1 implemented in Rust (`src/`) for fast execution and exit-code mirroring; Part 2 in Python (`tracecli_debugger/`) for GDB integration.
- Part 2 treated `continue` as "start or resume": first invocation issues `-exec-run`, subsequent invocations issue `-exec-continue`.
- Used Python standard library exclusively: zero runtime and test dependencies required.
- Implemented robust per-frame attribute parsing in backtrace to ensure compatibility across diverse GDB versions.
- Accepted `{"action": "..."}` as alias for `{"type": "..."}` and `"inspect_variable"` for planner compatibility.

## Don't retry
- Don't reach for `regex` or `clap` in Part 1 — hand-rolled parsing avoids heavy external dependencies.
- Do not use select() over buffered TextIOWrapper.readline(); it causes deadlocks due to Python internal buffer caching.
- Do not let LLM generate arbitrary GDB commands; always validate through `DebugAction`.
