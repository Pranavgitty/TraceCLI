'use strict';

const fs = require('node:fs');
const { spawnSync } = require('node:child_process');
const {
  Ok,
  Err,
  invalidWorkingDirectory,
  executableNotFound,
  permissionDenied,
  spawnFailed,
} = require('./errors');
const { signalInfoFromName } = require('./signal');

// Running a target program and capturing raw evidence about how it
// exited. Does exactly one job: spawn the process, wait for it, and
// record what happened -- see errorContext.js for turning this into a
// normalized ErrorContext.

// A generous cap, not a real limit: Rust/C++'s `Command::output()` has no
// buffer ceiling at all, so this exists only to guard against truly
// pathological output rather than to match a specific number.
const MAX_BUFFER = 256 * 1024 * 1024;

function classifySpawnError(executable, err) {
  if (err.code === 'ENOENT') return executableNotFound(executable);
  if (err.code === 'EACCES' || err.code === 'EPERM') return permissionDenied(executable);
  return spawnFailed(executable, err.message);
}

/**
 * Executes `spec` ({ executable, arguments, workingDirectory }) to
 * completion and captures its outcome. Returns `{ ok: false, error }`
 * (never throws) for expected failure modes: missing executable,
 * permission problems, an invalid working directory, or an OS-level spawn
 * failure.
 */
function executeTarget(spec) {
  let workingDirectory;
  if (spec.workingDirectory) {
    let stat;
    try {
      stat = fs.statSync(spec.workingDirectory);
    } catch {
      return Err(invalidWorkingDirectory(spec.workingDirectory));
    }
    if (!stat.isDirectory()) return Err(invalidWorkingDirectory(spec.workingDirectory));
    workingDirectory = spec.workingDirectory;
  } else {
    workingDirectory = process.cwd();
  }

  const start = process.hrtime.bigint();
  const result = spawnSync(spec.executable, spec.arguments, {
    cwd: workingDirectory,
    encoding: 'utf8',
    maxBuffer: MAX_BUFFER,
  });
  const durationMs = Number((process.hrtime.bigint() - start) / 1000000n);

  if (result.error) {
    return Err(classifySpawnError(spec.executable, result.error));
  }

  const signal = result.signal ? signalInfoFromName(result.signal) : null;
  const exitCode = signal ? null : result.status;
  const success = signal === null && result.status === 0;

  return Ok({
    executable: spec.executable,
    arguments: spec.arguments,
    working_directory: workingDirectory,
    exit_code: exitCode,
    signal,
    stdout: result.stdout ?? '',
    stderr: result.stderr ?? '',
    duration_ms: durationMs,
    success,
  });
}

module.exports = { executeTarget };
