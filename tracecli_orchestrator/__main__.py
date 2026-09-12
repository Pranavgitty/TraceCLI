"""
`tracecli diagnose` entry point.

Invoked by the Rust binary as `python3 -m tracecli_orchestrator
[--context-lines N] -- <executable> [target-args...]`, with `TRACECLI_BIN`
set to the Rust binary's own path so step 1 below never depends on `PATH`.

Steps (spec section 9):
    1. execute the target, via `tracecli run` (Part 1's existing, unchanged
       CLI contract) -- this both runs the program and detects failure.
    2. parse its stdout as the `ErrorContext` JSON.
    3. run the planner/debugger/evidence loop (`orchestrate.run`).
    4. render the diagnosis and save the trace.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from .limits import Limits
from .orchestrate import (
    OUTCOME_DEBUGGER_UNAVAILABLE,
    OUTCOME_DIAGNOSIS_UNAVAILABLE,
    OUTCOME_PLANNER_UNAVAILABLE,
    run as run_orchestration,
)
from .render import make_progress_printer, print_header, render_result
from .trace import TraceWriter

# Distinct from tracecli's own CLI-usage exit code (2, from Rust) so a
# failed diagnosis pipeline is distinguishable from bad arguments.
PIPELINE_ERROR_EXIT = 3


def _parse_argv(argv):
    context_lines = None
    i = 0
    if i < len(argv) and argv[i] == "--context-lines":
        if i + 1 >= len(argv):
            raise SystemExit("tracecli_orchestrator: --context-lines requires a value")
        context_lines = int(argv[i + 1])
        i += 2

    if i >= len(argv) or argv[i] != "--":
        raise SystemExit("tracecli_orchestrator: expected '--' before the target executable")
    i += 1

    if i >= len(argv):
        raise SystemExit("tracecli_orchestrator: missing target executable")

    executable = argv[i]
    target_args = argv[i + 1 :]
    return executable, target_args, context_lines


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    executable, target_args, context_lines = _parse_argv(argv)

    tracecli_bin = os.environ.get("TRACECLI_BIN", "tracecli")
    run_cmd = [tracecli_bin, "run"]
    if context_lines is not None:
        run_cmd += ["--context-lines", str(context_lines)]
    run_cmd += [executable, *target_args]

    try:
        proc = subprocess.run(run_cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print(
            f"tracecli: could not find the 'tracecli' binary to run {executable!r} "
            "(stage: execution)",
            file=sys.stderr,
        )
        return PIPELINE_ERROR_EXIT

    try:
        error_context = json.loads(proc.stdout)
    except json.JSONDecodeError:
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        print("tracecli: failed to obtain error context (stage: error_context)", file=sys.stderr)
        return PIPELINE_ERROR_EXIT

    trace = TraceWriter()
    print_header(error_context)
    progress = make_progress_printer()
    progress("error_captured")

    result = run_orchestration(executable, error_context, trace, Limits(), on_progress=progress)
    trace.close()

    render_result(result, trace.path)

    if result.outcome in (
        OUTCOME_PLANNER_UNAVAILABLE,
        OUTCOME_DIAGNOSIS_UNAVAILABLE,
        OUTCOME_DEBUGGER_UNAVAILABLE,
    ):
        return PIPELINE_ERROR_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
