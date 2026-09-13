'use strict';

// Structured error type for the Error & Project Context Layer -- mirrors
// Rust's `TraceError` enum. Every expected failure mode is a plain value
// (returned as `{ ok: false, error }`), never a thrown exception, in
// keeping with the "never panic/throw for normal user errors" rule this
// component has followed since the original Rust implementation.

const TraceErrorKind = Object.freeze({
  ExecutableNotFound: 'ExecutableNotFound',
  PermissionDenied: 'PermissionDenied',
  SpawnFailed: 'SpawnFailed',
  InvalidWorkingDirectory: 'InvalidWorkingDirectory',
  SourceFileUnavailable: 'SourceFileUnavailable',
  MalformedOutput: 'MalformedOutput',
  InvalidArguments: 'InvalidArguments',
});

function executableNotFound(pathStr) {
  return { kind: TraceErrorKind.ExecutableNotFound, path: pathStr };
}
function permissionDenied(pathStr) {
  return { kind: TraceErrorKind.PermissionDenied, path: pathStr };
}
function spawnFailed(pathStr, message) {
  return { kind: TraceErrorKind.SpawnFailed, path: pathStr, message };
}
function invalidWorkingDirectory(pathStr) {
  return { kind: TraceErrorKind.InvalidWorkingDirectory, path: pathStr };
}
function sourceFileUnavailable(pathStr) {
  return { kind: TraceErrorKind.SourceFileUnavailable, path: pathStr };
}
function malformedOutput(message) {
  return { kind: TraceErrorKind.MalformedOutput, message };
}
function invalidArguments(message) {
  return { kind: TraceErrorKind.InvalidArguments, message };
}

/** Equivalent of the Rust `Display` impl. */
function traceErrorToString(err) {
  switch (err.kind) {
    case TraceErrorKind.ExecutableNotFound:
      return `executable not found: ${err.path}`;
    case TraceErrorKind.PermissionDenied:
      return `permission denied executing: ${err.path}`;
    case TraceErrorKind.SpawnFailed:
      return `failed to spawn ${err.path}: ${err.message}`;
    case TraceErrorKind.InvalidWorkingDirectory:
      return `invalid working directory: ${err.path}`;
    case TraceErrorKind.SourceFileUnavailable:
      return `source file unavailable: ${err.path}`;
    case TraceErrorKind.MalformedOutput:
      return `malformed process output: ${err.message}`;
    case TraceErrorKind.InvalidArguments:
      return `invalid arguments: ${err.message}`;
    default:
      return 'unknown tracecli error';
  }
}

const Ok = (value) => ({ ok: true, value });
const Err = (error) => ({ ok: false, error });

module.exports = {
  TraceErrorKind,
  executableNotFound,
  permissionDenied,
  spawnFailed,
  invalidWorkingDirectory,
  sourceFileUnavailable,
  malformedOutput,
  invalidArguments,
  traceErrorToString,
  Ok,
  Err,
};
