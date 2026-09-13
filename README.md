# TraceCLI

TraceCLI runs a C++ program, catches it when it fails, and uses an LLM-driven
GDB investigation to explain *why*. It never edits your code — it observes,
plans, debugs, and suggests.

```
C++ program → TraceCLI → Error & Project Context → LLM Planner
            → Debugger Engine (GDB) → Evidence → LLM Diagnosis → terminal report
```

```
$ tracecli diagnose ./parser

✗ Runtime error detected

SEGMENTATION FAULT
parser.cpp:142

Investigating...

✓ Error captured
✓ Debugging plan generated
✓ Runtime evidence collected
✓ Stack analyzed
✓ Variables inspected
✓ Root cause identified

────────────────────────────────────────

ROOT CAUSE (INFERRED)

`node` is null at parser.cpp:142.

EVIDENCE (OBSERVED)

- node = 0x0
- find_node() returned nullptr

SUGGESTED FIX (not verified — TraceCLI does not apply or test fixes)

Validate the result of find_node() before dereferencing node.

CONFIDENCE: 91%

────────────────────────────────────────

Trace:
.tracecli/runs/20260913T120000Z-a1b2c3d4.jsonl
```

TraceCLI collects runtime debugging evidence and uses LLM-based planning and
diagnosis to identify likely underlying causes and suggest fixes. It does not
claim to solve every C++ bug, and it never modifies your source code.

---

## Architecture

The project is four components, each owned independently and integrated
without changing one another's public contracts:

| Part | Language | Package | Responsibility |
|---|---|---|---|
| 1 | Rust | `tracecli` (this crate) | Run the target, capture stdout/stderr/exit/signal, classify the failure, extract source context — produces `ErrorContext` |
| 2 | Python | `tracecli_debugger` | Structured `DebugAction` → GDB (via MI2) → structured `Evidence`, with strict input validation |
| 3 | Python | `tracecli_llm` | `ErrorContext` (+ evidence) → `DebugPlanner` (LLM #1, decides what to inspect next) and `DiagnosisEngine` (LLM #2, produces the final root cause / evidence / fix / confidence) |
| 4 | Python + Rust | `tracecli_orchestrator` + `tracecli diagnose` | Runs the iterative Planner ⇄ Debugger loop under hard safety limits, writes the canonical trace, renders the terminal report |

```
ErrorContext ──▶ Planner ──▶ Debugger ──▶ Evidence ──┐
                    ▲                                │
                    └────────── (repeat, bounded) ───┘
                                       │
                                       ▼
                                  Diagnosis ──▶ terminal + trace
```

Part 4 (the orchestrator) is the only piece that spans both languages, and it
does so at the edges only: it shells out to `tracecli run`'s existing,
unmodified stdout-JSON contract to get an `ErrorContext`, then drives Parts 2
and 3 in-process as ordinary Python libraries.

---

## Requirements

- Rust (edition 2024) and `cargo`
- Python 3.11+ (for `tomllib`)
- `gdb` on `PATH`
- A C++ compiler (`g++`/`clang++`) if you want to build the test fixtures
- `google-genai` (see `requirements.txt`) and a `GEMINI_API_KEY` — only
  needed for `tracecli diagnose`; `tracecli run` has no Python/LLM dependency

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...   # only required for `diagnose`
cargo build --release
```

`tracecli diagnose` must currently be run from the repository root (or with
`PYTHONPATH` pointing at it), so `python3 -m tracecli_orchestrator` can find
the `tracecli_debugger`/`tracecli_llm`/`tracecli_orchestrator` packages.

---

## CLI

### `tracecli run`

Runs a target and prints its `ErrorContext` as JSON — no debugger, no LLM,
no network access. This is Part 1 in isolation.

```bash
tracecli run [--context-lines N] <executable> [args...]
```

Exit code mirrors the target's own exit status (or `128+signal` if it was
killed by a signal); `2` means tracecli itself couldn't run it.

### `tracecli diagnose`

Runs the full pipeline: executes the target, and if it failed, investigates
with GDB under LLM guidance and reports a diagnosis.

```bash
tracecli diagnose [--context-lines N] <executable> [args...]
```

- Exit `0`: the investigation completed (this includes the target having
  succeeded, or the evidence being insufficient — those are valid, honestly
  reported outcomes, not tracecli failures).
- Exit `3`: a pipeline stage failed outright (debugger, planner, or
  diagnosis engine unavailable).
- Exit `2`: bad CLI usage, same as `run`.

The report distinguishes what was actually **OBSERVED** by GDB from what the
diagnosis model **INFERRED** from that evidence, and always labels a fix
**SUGGESTED** — TraceCLI does not apply or test fixes, so it never claims one
is verified.

---

## Configuration

`tracecli.toml` (optional; every field has a built-in default):

```toml
[llm]
api_key_env = "GEMINI_API_KEY"   # never the key itself

[llm.planner]
provider = "gemini"
model = "gemini-3.5-flash-lite"
temperature = 0.0

[llm.diagnosis]
provider = "gemini"
model = "gemini-3.5-flash"
temperature = 0.1
```

API keys are only ever read from the environment variable named by
`api_key_env`, never from this file.

---

## Safety limits

An LLM never drives an unbounded investigation. Each `diagnose` run is capped
by `tracecli_orchestrator/limits.py`:

- at most 3 planner rounds
- at most 200 accumulated evidence events
- at most 90 seconds of wall-clock time
- at most 10 actions per planner round (enforced by Part 3's own schema)

The loop also stops early, before any limit is hit, once evidence shows the
crash itself, its location on the stack, and at least one inspected variable
— the same shape as the pipeline's own worked example above. If any limit is
hit first, the trace records `investigation_limit_reached` and the diagnosis
(if one could be produced at all) is clearly marked as based on a
limit-bounded investigation.

---

## Trace format

Every `diagnose` run writes a canonical, append-only JSONL trace to
`.tracecli/runs/<run-id>.jsonl` — one independently-parseable JSON object per
line, so a run stays inspectable even if it's interrupted partway through:

```jsonl
{"event": "error_detected", "run_id": "...", "ts": ..., "classification": "segmentation_fault", ...}
{"event": "planner_request", "run_id": "...", "ts": ..., "round": 1, "prior_evidence_count": 0}
{"event": "planner_response", "run_id": "...", "ts": ..., "round": 1, "actions": [...]}
{"event": "debug_action", "run_id": "...", "ts": ..., "round": 1, "action": {"type": "backtrace"}}
{"event": "debugger_evidence", "run_id": "...", "ts": ..., "round": 1, "evidence": [...]}
{"event": "diagnosis", "run_id": "...", "ts": ..., "diagnosis": {...}}
{"event": "run_finished", "run_id": "...", "ts": ..., "outcome": "diagnosed"}
```

No secrets (API keys, tokens) are ever written to the trace. A single JSON
export of a trace is available via `tracecli_orchestrator.trace.export_json`.

---

## Repository layout

```
TraceCLI/
├── src/                      # Part 1: Rust — Error & Project Context Layer
│   ├── main.rs, cli.rs       #   `run` and `diagnose` CLI dispatch
│   ├── execution.rs          #   spawns the target, captures stdout/stderr/exit/signal
│   ├── error_context.rs      #   assembles the stable ErrorContext JSON contract
│   ├── classification.rs     #   coarse failure classification (segfault, abort, ...)
│   ├── signal.rs             #   POSIX signal naming
│   ├── source_context.rs     #   bounded source excerpt around a failure
│   ├── source_location.rs    #   extracts file:line from compiler/runtime output
│   └── build_context.rs      #   best-effort compiler/debug-symbol detection
├── tracecli_debugger/         # Part 2: Python — structured actions/evidence over GDB
├── tracecli_llm/               # Part 3: Python — LLM planner + diagnosis engine
├── tracecli_orchestrator/     # Part 4: Python — the diagnose loop, trace, terminal UX
├── tests/
│   ├── test_debugger.py, test_llm_*.py, test_orchestrator.py
│   └── fixtures/             # C++ programs used by the test suites
└── tracecli.toml              # shared, optional configuration
```

---

## Running the tests

```bash
cargo test                                              # Part 1
python3 -m unittest discover -s tests -p "test_llm_*.py" # Part 3 (mocked, no API key needed)
python3 -m unittest tests.test_debugger                  # Part 2 (needs real gdb)
python3 -m unittest tests.test_orchestrator              # Part 4 (mocked + real-gdb + real-CLI cases)
```

---

## Known limitations

- No automatic code modification: TraceCLI only diagnoses and suggests.
- `debug_build`/compiler detection is best-effort and doesn't parse build
  systems (CMake/Make/etc.).
- `tracecli diagnose` currently needs to run from the repository root (no
  packaging/installation story yet).
- Diagnosis quality depends entirely on what GDB can observe; a crash with
  no debug symbols, or one GDB itself can't reach, yields an honestly
  reported `insufficient evidence` rather than a guess.
