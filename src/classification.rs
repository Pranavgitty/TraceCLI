//! Best-effort classification of *why* a target program's execution failed.
//!
//! This module intentionally does not diagnose root causes. It only buckets
//! raw evidence (exit code, signal, output text) into a coarse category so
//! downstream components can decide how to react. Confidence is not
//! uniform across variants: signal-based classification is reliable
//! (the OS told us), while [`FailureClassification::CompilationError`] is a
//! weak heuristic over freeform text.

use serde::{Deserialize, Serialize};

use crate::signal::SignalInfo;

/// Coarse bucket describing the nature of a target program's failure.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FailureClassification {
    /// The process exited with status 0 and was not signaled.
    Success,
    /// Heuristic: output looked like a compiler diagnostic rather than a
    /// runtime failure. Low confidence — see module docs.
    CompilationError,
    /// The process exited non-zero without being signaled, and nothing more
    /// specific could be determined.
    RuntimeError,
    /// The process was terminated by a signal not covered by a more
    /// specific variant below.
    Signal,
    /// Terminated by `SIGSEGV`.
    SegmentationFault,
    /// Terminated by `SIGABRT`.
    Abort,
    /// Terminated by `SIGFPE`.
    FloatingPointException,
    /// Terminated by `SIGILL`.
    IllegalInstruction,
    /// Evidence was insufficient or contradictory to classify.
    UnknownFailure,
}

/// Classifies a completed execution from its exit code, signal, and captured
/// stderr text.
///
/// `success` should reflect the OS-reported exit status (`0` and not
/// signaled). `stderr` is used only for the low-confidence compiler-error
/// heuristic and is otherwise ignored.
pub fn classify(
    success: bool,
    exit_code: Option<i32>,
    signal: Option<&SignalInfo>,
    stderr: &str,
) -> FailureClassification {
    if success {
        return FailureClassification::Success;
    }

    if let Some(sig) = signal {
        return match sig.name.as_deref() {
            Some("SIGSEGV") => FailureClassification::SegmentationFault,
            Some("SIGABRT") => FailureClassification::Abort,
            Some("SIGFPE") => FailureClassification::FloatingPointException,
            Some("SIGILL") => FailureClassification::IllegalInstruction,
            _ => FailureClassification::Signal,
        };
    }

    if exit_code.is_some() {
        if looks_like_compiler_diagnostic(stderr) {
            return FailureClassification::CompilationError;
        }
        return FailureClassification::RuntimeError;
    }

    FailureClassification::UnknownFailure
}

/// Weak heuristic: does this text look like a compiler error rather than
/// program output? Looks for the `path:line:col: error:` shape emitted by
/// both gcc and clang. This will misfire on programs that print similarly
/// shaped diagnostics themselves; treat it as a hint, not a fact.
fn looks_like_compiler_diagnostic(stderr: &str) -> bool {
    stderr.lines().any(|line| {
        let Some(colon_error) = line.find(": error:") else {
            return false;
        };
        let prefix = &line[..colon_error];
        // Expect at least one more ':' before "error" separating file and
        // line (and optionally column), e.g. "file.cpp:142:17".
        let parts: Vec<&str> = prefix.rsplitn(3, ':').collect();
        parts.len() >= 2 && parts[0].chars().all(|c| c.is_ascii_digit())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn success_short_circuits() {
        assert_eq!(
            classify(true, Some(0), None, ""),
            FailureClassification::Success
        );
    }

    #[test]
    fn segfault_from_signal() {
        let sig = SignalInfo::new(11, false);
        assert_eq!(
            classify(false, None, Some(&sig), ""),
            FailureClassification::SegmentationFault
        );
    }

    #[test]
    fn abort_from_signal() {
        let sig = SignalInfo::new(6, true);
        assert_eq!(
            classify(false, None, Some(&sig), ""),
            FailureClassification::Abort
        );
    }

    #[test]
    fn plain_nonzero_exit_is_runtime_error() {
        assert_eq!(
            classify(false, Some(1), None, "oops, bad input"),
            FailureClassification::RuntimeError
        );
    }

    #[test]
    fn compiler_diagnostic_heuristic_matches() {
        let stderr = "parser.cpp:142:17: error: expected ';' before '}' token";
        assert_eq!(
            classify(false, Some(1), None, stderr),
            FailureClassification::CompilationError
        );
    }

    #[test]
    fn compiler_diagnostic_heuristic_does_not_misfire_on_plain_error_word() {
        let stderr = "error: something went wrong at runtime";
        assert_eq!(
            classify(false, Some(1), None, stderr),
            FailureClassification::RuntimeError
        );
    }
}
