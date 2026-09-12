"""
TraceCLI Part 4: Orchestrator, Trace System & Final Integration.

Wires together the previously-built components without modifying any of
their public contracts:

    Part 1 (Rust `tracecli run`)  -> ErrorContext JSON on stdout
    Part 2 (tracecli_debugger)    -> DebuggerSession, DebugAction, Evidence
    Part 3 (tracecli_llm)         -> build_engines(), DebugPlanner, DiagnosisEngine

`orchestrate.run()` drives the iterative planner/debugger loop under hard
safety limits (`limits.Limits`) and writes a canonical JSONL trace
(`trace.TraceWriter`) as it goes. `__main__.py` is the `tracecli diagnose`
entry point invoked by the Rust binary as a subprocess.
"""
