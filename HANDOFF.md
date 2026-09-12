# Handoff - HACKELBERRY_FINN

> Updated 2026-09-13T02:02:10+05:30 by avikshama2006 (session df06143d-c17, track 4)
> Read this first. The full log is cyhi-logs/session.md.

## Current state
Part 1 of TraceCLI (Error & Project Context Layer) is implemented and complete. Greenfield Rust project set up from scratch (no prior Cargo/CLI code existed). `cargo build`/`cargo test` both pass (38 tests). Verified end-to-end against real compiled C++ fixtures (SIGSEGV via null deref, SIGABRT via assert()).

## Works
- `tracecli run [--context-lines N] <exe> [args...]` executes a target, captures stdout/stderr/exit code/signal/duration, classifies the failure, and prints a pretty-JSON `ErrorContext` to stdout.
- tracecli mirrors the target's exit status (same code, or 128+signal) so it composes in shell scripts.
- Source location extraction handles both gcc/clang colon diagnostics (`file.cpp:142:17: error: ...`) and macOS/BSD libc `assert()` messages (`function main, file x.cpp, line 4.`) via `CppSourceLocationExtractor`, built behind an extensible `SourceLocationExtractor` trait for future languages.
- Bounded source context (default ±5 lines, configurable, size-capped file read) around a detected failure line.
- Best-effort build context: compiler + version via `CXX`/`CC`/PATH probing, debug-symbol heuristic via `file`/`nm`. No CMake/Make/Cargo-specific parsing (flagged as an unresolved design gap below).
- All errors are structured `TraceError` variants, never panics, for: executable not found, permission denied, spawn failure, invalid working dir, source file unavailable, malformed output, invalid CLI args.

## Broken
Nothing known-broken. Two known low-confidence areas (by design, documented in code):
- `FailureClassification::CompilationError` heuristic (looks for `: error:` preceded by `file:line[:col]`) can misfire on programs that print similarly shaped text.
- Signal *names* for anything other than SIGSEGV/SIGABRT/SIGFPE/SIGILL use macOS/BSD numbering and will misreport on Linux (e.g. SIGBUS, SIGUSR1/2). The 4 signals that matter for classification are correct on both platforms.

## Next 3 things
1. Whoever builds the Python debugger / GDB stage (later part) should consume `ErrorContext` by shelling out to `tracecli run ...` and parsing the JSON on stdout, or by depending on the `tracecli` lib crate directly (`execute_target` + `collect_error_context` in `src/lib.rs`).
2. If build-system-aware context (CMake/Make/Cargo project detection) becomes valuable, that needs an explicit design decision per AGENTS.md scope rules — not done here.
3. No CI/lint step wired up yet beyond local `cargo test`/`cargo clippy`/`cargo fmt` — consider adding if the team wants a check before merging further parts.

## Decisions (and why)
- No Rust toolchain existed on the machine; installed via `brew install rust` since the entire deliverable requires it.
- Single crate, lib (`src/lib.rs`) + bin (`src/main.rs`), not a workspace — simplest structure for one component with a clean public API other Rust code can import directly.
- Dependencies kept to `serde` + `serde_json` only (pre-approved by the spec for serialization). Deliberately avoided `clap` (hand-rolled arg parsing is trivial for this surface and keeps target-program args from ever being misinterpreted as tracecli flags) and `regex` (manual colon/comma splitting was sufficient and avoids a dependency for a fixed, small grammar).
- `ErrorContext` flattens the spec's conceptual `command` field into `executable` + `arguments` (still has `working_directory` at top level per spec) — a refinement, not a material architecture change.
- tracecli's own process exit code mirrors the target's exit status (or 128+signal), distinct from `TRACECLI_ERROR_EXIT=2` used when tracecli itself fails before/during execution.

## Don't retry
- Don't reach for `regex` or `clap` for this component's scope — deliberately avoided per the spec's "minimize dependencies" rule; hand-rolled parsing already covers everything required.
- Don't try to parse CMakeLists.txt/Makefile/Cargo.toml for build_configuration — explicitly out of scope pending a design decision (see AGENTS.md-derived spec section 8/13).
- Don't assume `nm`/`file`-based debug-symbol detection is reliable across platforms — it's a documented weak heuristic, not a hard signal.
