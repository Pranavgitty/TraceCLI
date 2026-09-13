'use strict';

const { classify } = require('./classification');
const { extractFirst } = require('./sourceLocation');
const { collectSourceContext } = require('./sourceContext');
const { detectBuildContext } = require('./buildContext');
const { Ok } = require('./errors');

// Normalizes a completed execution result into a stable, JSON-serializable
// ErrorContext -- the artifact this component exists to produce, and the
// integration contract consumed by tracecli_orchestrator (Part 4, Python)
// via `tracecli run`'s stdout-JSON. Field names/nesting/null-semantics are
// unchanged from the Rust/C++ implementations, since that JSON shape is
// what Python's `json.loads(...).get(...)` calls depend on.

/**
 * Never fails on missing enrichment: an undetectable source location, an
 * unreadable source file, or unavailable build tooling all degrade to
 * null fields rather than propagating an error. Returns `{ok:true,value}`
 * for API consistency with `executeTarget`.
 */
function collectErrorContext(execution, config = {}) {
  const sourceContextConfig = config.sourceContext ?? {};
  const collectBuildContext = config.collectBuildContext ?? true;

  const classification = classify(
    execution.success,
    execution.exit_code,
    execution.signal,
    execution.stderr
  );

  const combined = `${execution.stderr}\n${execution.stdout}`;
  const sourceLocation = extractFirst(combined) ?? null;

  let sourceContext = null;
  if (sourceLocation) {
    const result = collectSourceContext(
      sourceLocation,
      execution.working_directory,
      sourceContextConfig
    );
    if (result.ok) sourceContext = result.value;
  }

  const buildContext = collectBuildContext ? detectBuildContext(execution.executable) : null;

  return Ok({
    executable: execution.executable,
    arguments: execution.arguments,
    working_directory: execution.working_directory,
    exit_code: execution.exit_code,
    signal: execution.signal,
    stdout: execution.stdout,
    stderr: execution.stderr,
    classification,
    source_location: sourceLocation,
    source_context: sourceContext,
    build_context: buildContext,
    duration: execution.duration_ms,
    success: execution.success,
  });
}

module.exports = { collectErrorContext };
