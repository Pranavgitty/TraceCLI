'use strict';

const { linesOf, rsplitn, allAsciiDigits } = require('./textUtil');

// Best-effort classification of *why* a target program's execution failed.
// Does not diagnose root causes -- only buckets raw evidence into a coarse
// category. `compilation_error` is a weak heuristic over freeform text;
// the signal-based variants are reliable (the OS told us).

const FailureClassification = Object.freeze({
  Success: 'success',
  CompilationError: 'compilation_error',
  RuntimeError: 'runtime_error',
  Signal: 'signal',
  SegmentationFault: 'segmentation_fault',
  Abort: 'abort',
  FloatingPointException: 'floating_point_exception',
  IllegalInstruction: 'illegal_instruction',
  UnknownFailure: 'unknown_failure',
});

// Weak heuristic: does this text look like a compiler error rather than
// program output? Looks for the `path:line:col: error:` shape emitted by
// both gcc and clang. Mirrors the Rust implementation exactly, including
// the fact that an empty segment before "error:" still counts as "digits"
// (vacuously true over an empty sequence).
function looksLikeCompilerDiagnostic(stderrText) {
  for (const line of linesOf(stderrText)) {
    const pos = line.indexOf(': error:');
    if (pos === -1) continue;
    const prefix = line.slice(0, pos);
    const parts = rsplitn(prefix, ':', 3);
    if (parts.length >= 2 && allAsciiDigits(parts[0])) return true;
  }
  return false;
}

/**
 * @param {boolean} success
 * @param {number|null} exitCode
 * @param {{number:number,name:string|null,core_dumped:boolean}|null} signal
 * @param {string} stderrText
 */
function classify(success, exitCode, signal, stderrText) {
  if (success) return FailureClassification.Success;

  if (signal) {
    switch (signal.name) {
      case 'SIGSEGV':
        return FailureClassification.SegmentationFault;
      case 'SIGABRT':
        return FailureClassification.Abort;
      case 'SIGFPE':
        return FailureClassification.FloatingPointException;
      case 'SIGILL':
        return FailureClassification.IllegalInstruction;
      default:
        return FailureClassification.Signal;
    }
  }

  if (exitCode !== null && exitCode !== undefined) {
    if (looksLikeCompilerDiagnostic(stderrText)) return FailureClassification.CompilationError;
    return FailureClassification.RuntimeError;
  }

  return FailureClassification.UnknownFailure;
}

module.exports = { FailureClassification, classify };
