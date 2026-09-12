# Handoff - HACKELBERRY_FINN

> Updated 2026-09-13T03:15:32+05:30 by iampranav2407 (session 57650d15-c10, track 4)
> Read this first. The full log is cyhi-logs/session.md.

## Current state
- Part 4 (Orchestrator, Trace System, Final Integration) implemented on top of the merged Parts 1-3 (this session first merged origin/master, which had diverged from local master, to bring in Part 1 Rust + Part 2/3 Python — see merge commit).
- New Python package `tracecli_orchestrator/` (limits.py, trace.py, orchestrate.py, render.py, __main__.py) drives the loop: ErrorContext -> DebugPlanner -> DebuggerSession.execute_many() -> evidence -> (repeat, bounded) -> DiagnosisEngine -> terminal render + JSONL trace.
- New Rust `tracecli diagnose [--context-lines N] <executable> [args...]` subcommand (src/cli.rs::parse_diagnose_args, src/main.rs::diagnose_main) added purely additively — `run`'s existing parsing/behavior/tests untouched. `diagnose` shells out to `python3 -m tracecli_orchestrator`, passing its own binary path via `TRACECLI_BIN` so the orchestrator can re-invoke `tracecli run` for the ErrorContext JSON without depending on PATH.
- Canonical trace: `.tracecli/runs/<run-id>.jsonl`, one JSON object per line (error_detected, planner_request/response, debug_action, debugger_evidence, investigation_limit_reached, diagnosis, stage_failed, run_finished). Optional `.json` export via `tracecli_orchestrator.trace.export_json`; TOML export skipped (no stdlib TOML writer, didn't want a new dependency).
- Safety limits (tracecli_orchestrator/limits.py): max_planner_iterations=3, max_total_evidence=200, max_wall_seconds=90. Stop-early heuristic: sufficient evidence = a crash event (signal/process_exit) + a stack_frame + a variable, all observed.
- tests/test_orchestrator.py: 12 tests, all passing — mocked-engine unit tests, real-GDB integration tests (null_pointer/assertion_failure/divide_zero fixtures), and one real CLI smoke test (`tracecli diagnose /bin/true`, built via cargo, no API key needed since target-succeeded short-circuits before any LLM call).

## Works
- `cargo build` / `cargo test`: 31+11 Rust tests pass, no regressions to Part 1.
- `python3 -m unittest discover -s tests -p "test_llm_*.py"` and `test_debugger.py`: all still pass unchanged (Parts 2/3 untouched).
- `python3 -m unittest tests.test_orchestrator`: 12/12 pass.
- Manual run: `tracecli diagnose tests/fixtures/null_pointer` (no GEMINI_API_KEY set) correctly detects SIGSEGV, reports "planner (LLM) could not be reached" with the real AuthenticationError detail, and writes a valid JSONL trace — full pipeline wiring confirmed end to end short of an actual Gemini call.

## Broken
- None known. Full diagnose flow (planner+diagnosis actually calling Gemini) has not been exercised with a live GEMINI_API_KEY in this environment — same caveat Part 3's handoff already flagged for GeminiProvider itself.

## Next 3 things
1. Do one live run with `GEMINI_API_KEY` set against `tests/fixtures/null_pointer` to confirm the full LLM round-trip (planner -> gdb -> diagnosis) end to end, not just the mocked/short-circuit paths already tested.
2. Consider adding `--export json` (or similar) as an actual CLI flag on `tracecli diagnose` — `tracecli_orchestrator.trace.export_json` exists but isn't wired to a flag yet.
3. If judges want to see it live, script a demo: build with `cargo build --release`, run `tracecli diagnose <fixture>` from repo root (required so `python3 -m tracecli_orchestrator` can find the package — no PYTHONPATH/venv setup exists yet).

## Decisions (and why)
- All-Python orchestrator (not Rust) importing `tracecli_debugger`/`tracecli_llm` in-process, invoked as a `python3` subprocess from a new Rust `diagnose` subcommand: matches exactly what Part 3's own HANDOFF.md already assumed ("DebuggerSession.execute_many() called directly... no adapter layer needed"), avoids pyo3/FFI, and reuses Part 1's existing `tracecli run` stdout-JSON contract unchanged rather than adding a second Rust-Python transport.
- Extended `cli.rs`/`main.rs` (Part 1 files) only additively: `parse_args`/`RunArgs`/the `run` pipeline are byte-for-byte unchanged; `diagnose` is entirely new functions/match-arms. This was treated as within Part 4's explicit mandate (spec section 9 requires the new CLI command respect "existing CLI architecture"), not a violation of the "don't modify other parts' APIs" rule.
- Iteration stop heuristic (signal/process_exit + stack_frame + variable all observed) is a Part 4-owned heuristic, not from Part 3 (which intentionally has no "done" signal) — documented in code as a deliberate, simple approximation of the spec's own worked example.
- Diagnosis is skipped entirely (no LLM call) when zero evidence was collected, rather than asking the model to diagnose nothing — cheaper and avoids inviting a hallucinated root cause.
- Merged origin/master into local master before starting (local master had diverged after a repo-root fix commit); resolved .gitignore/HANDOFF.md/cyhi state.json conflicts by combining both sides.

## Don't retry
- Don't try to give `tracecli diagnose` a real exit code distinct per outcome beyond the 0 (completed, including insufficient_evidence/target_succeeded) vs 3 (pipeline stage failure) split already implemented — spec doesn't ask for more granularity and it complicates shell scripting for no benefit.
- Don't add a `tomllib`-based TOML *writer* dependency just for the optional export — intentionally skipped in favor of JSON-only export, consistent with the project's stdlib-only-where-possible precedent.
