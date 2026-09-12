# Handoff - HACKELBERRY_FINN

> Updated 2026-09-13T03:00:02+05:30 by dhruvipatil2906 (session 6bbe4fc8-00d, track 4)
> Read this first. The full log is cyhi-logs/session.md.

## Current state
- **Part 1 (Rust)**: complete, from origin (avik/atindrak27's session). `ErrorContext` JSON contract in `src/error_context.rs`.
- **Part 2 (Python `tracecli_debugger`)**: complete, from origin. `DebugAction`/`Evidence` schemas in `actions.py`/`evidence.py`.
- **Part 3 (Python `tracecli_llm`, this session)**: implemented and unit-tested. `DebugPlanner` (LLM #1) and `DiagnosisEngine` (LLM #2) behind an `LLMProvider` abstraction, `GeminiProvider` the only implementation. 27/27 new tests pass (`python3 -m unittest discover -s tests -p "test_llm_*.py"`), all mocked — no live API key needed.

## Works
- `tracecli_llm.build_engines()` reads `tracecli.toml` (`[llm.planner]` / `[llm.diagnosis]`, falls back to built-in defaults if the file is absent) and returns a wired `(DebugPlanner, DiagnosisEngine)` pair. API key comes only from `$GEMINI_API_KEY` (name configurable via `api_key_env`), never from the file.
- `DebugPlanner.plan(error_context, prior_evidence=None) -> PlanResult(actions=[...])`. Every action is validated by running it through Part 2's own `tracecli_debugger.actions.DebugAction.from_dict` before being returned, so the planner can never hand Part 2 something Part 2 would reject.
- `DiagnosisEngine.diagnose(error_context, evidence) -> Diagnosis` matching spec section 9 exactly (`root_cause`, `evidence`, `reasoning_summary`, `suggested_fix`, `confidence`) with a `.to_dict()` for Part 4 to render.
- Gemini calls use native structured output (`response_mime_type=application/json` + `response_schema`), are synchronous, and go through the official `google-genai` SDK (the project's one Python runtime dependency, in `requirements.txt`).
- Bounded retries: transient provider errors (rate limit / timeout / network / 5xx) retry with backoff inside `GeminiProvider` (`max_retries`, default 2); `AuthenticationError` never retries; invalid structured output gets exactly one corrective re-prompt at the planner/diagnosis level, then raises `OutputValidationError`.
- Token efficiency: `tracecli_llm.context` truncates stdout/stderr tails, drops `build_context`, strips the verbose `raw` GDB/MI field from evidence sent to the model, and caps evidence history to the most recent 40 events.
- All 4 material architecture decisions (SDK vs HTTP, structured-output mechanism, sync vs async, config format) were confirmed with the user via AskUserQuestion before implementation, per the spec's strict-permission rule; all went with the recommended option.

## Broken
- None known. `GeminiProvider` has not been exercised against the real Gemini API in this session (no API key in this environment) — only against a fake injected client. Recommend one live smoke test before Part 4 integration.

## Next 3 things
1. Part 4: wire `tracecli_llm.build_engines()` into the orchestration loop (`ErrorContext -> Planner -> actions -> tracecli_debugger.DebuggerSession.execute_many() -> evidence -> Planner (repeat) -> DiagnosisEngine -> render`). Planner/diagnosis take plain dicts (JSON already decoded), so no adapter layer is needed between Part 1/Part 2's JSON and Part 3.
2. Part 4 should decide the loop's stop condition (max rounds, or a planner-signaled "enough evidence") — Part 3 intentionally does not own this per the scope boundary.
3. Set `GEMINI_API_KEY` and do one live end-to-end run before demo, to confirm real Gemini structured-output responses validate the same way the mocked tests assume.

## Decisions (and why)
- New `tracecli_llm/` Python package (not Rust): matches Part 2's language, and Gemini's structured-output SDK is Python-first.
- `google-genai` SDK over raw HTTP (user-approved): gets native JSON-schema-constrained decoding, retry/timeout primitives, and multi-provider room for free; diverges from Part 2's "zero dependencies" precedent, but that precedent was about avoiding GDB-adjacent deps, not network SDKs.
- Native Gemini structured output over free-text parsing (user-approved): lowest malformed-output rate; we still validate locally regardless (never trust the provider's schema enforcement alone).
- Synchronous provider calls (user-approved): matches Part 1's blocking exec and Part 2's blocking GDB/MI session; Part 4's planner<->debugger loop is inherently sequential, so async buys nothing here.
- `tracecli.toml` + env-var API key (user-approved): matches the spec's sketched config shape; stdlib `tomllib` needs no new dependency.
- Planner action validation reuses Part 2's `DebugAction.from_dict` directly instead of a second hand-written schema, so "compatible with Part 2" is exact, not approximate.
- Diagnosis schema (section 9) implemented unchanged, since the spec said to ask before changing it and there was no reason to.
- Merged origin/master (Part 1 + Part 2 commits) into local master before starting; resolved trivial infra conflicts (.gitignore, HANDOFF.md, cyhi state.json) by taking origin's content where it was more complete.

## Don't retry
- Don't import `google.genai` at `tracecli_llm` module load time — it's deferred into `GeminiProvider.__init__`/`_call_once` so the test suite and any code just doing config loading never needs the SDK installed.
- Don't let the planner's JSON-Schema alone gate action validity (Gemini's response_schema subset can't express Part 2's numeric ranges/regexes) — `DebugAction.from_dict` is the real gate, always run it.
- Don't hand-roll a second retry policy for malformed structured output at the provider layer — that's a semantic/schema concern only `DebugPlanner`/`DiagnosisEngine` can correct via a re-prompt; the provider layer only retries transport-shaped failures.
