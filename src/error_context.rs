//! Normalizes a completed [`ExecutionResult`] into a stable, serializable
//! [`ErrorContext`] — the artifact this component exists to produce.
//!
//! This is where raw evidence (exit code, signal, stdout/stderr text) is
//! turned into: a failure classification, an optional source location, an
//! optional bounded source excerpt, and best-effort build context. None of
//! this diagnoses a root cause; it only organizes evidence for later
//! stages.

use std::path::PathBuf;
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::build_context::{BuildContext, detect_build_context};
use crate::classification::{FailureClassification, classify};
use crate::error::TraceError;
use crate::execution::ExecutionResult;
use crate::signal::SignalInfo;
use crate::source_context::{SourceContext, SourceContextConfig, collect_source_context};
use crate::source_location::{CppSourceLocationExtractor, SourceLocation, SourceLocationExtractor};

/// The stable, serializable representation of a target program's execution
/// and (if it failed) the evidence gathered about that failure.
///
/// This is the integration contract for later TraceCLI components: it is
/// plain data, has no dependency on GDB/LLM/Python, and is safe to
/// serialize to JSON and hand off as-is.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ErrorContext {
    pub executable: PathBuf,
    pub arguments: Vec<String>,
    pub working_directory: PathBuf,
    pub exit_code: Option<i32>,
    pub signal: Option<SignalInfo>,
    pub stdout: String,
    pub stderr: String,
    pub classification: FailureClassification,
    pub source_location: Option<SourceLocation>,
    pub source_context: Option<SourceContext>,
    pub build_context: Option<BuildContext>,
    #[serde(with = "crate::execution::duration_millis")]
    pub duration: Duration,
    pub success: bool,
}

/// Tuning knobs for context collection. Kept separate from
/// [`SourceContextConfig`] so this module can grow independent knobs later
/// without disturbing that struct's focused purpose.
#[derive(Debug, Clone, Copy)]
pub struct ErrorContextConfig {
    pub source_context: SourceContextConfig,
    /// When false, skips shelling out to detect compiler/debug-symbol
    /// info (useful for fast/hermetic tests).
    pub collect_build_context: bool,
}

impl Default for ErrorContextConfig {
    fn default() -> Self {
        Self {
            source_context: SourceContextConfig::default(),
            collect_build_context: true,
        }
    }
}

/// Builds a stable [`ErrorContext`] from a completed [`ExecutionResult`].
///
/// This never fails on missing enrichment: an undetectable source location,
/// an unreadable source file, or unavailable build tooling all degrade to
/// `None` fields rather than propagating an error. `Result` is kept for API
/// consistency with [`crate::execution::execute_target`] and to leave room
/// for future fatal cases (e.g. invalid config).
pub fn collect_error_context(
    execution: ExecutionResult,
    config: &ErrorContextConfig,
) -> Result<ErrorContext, TraceError> {
    let classification = classify(
        execution.success,
        execution.exit_code,
        execution.signal.as_ref(),
        &execution.stderr,
    );

    let extractor = CppSourceLocationExtractor::new();
    let combined = format!("{}\n{}", execution.stderr, execution.stdout);
    let source_location = extractor.extract_first(&combined);

    let source_context = source_location.as_ref().and_then(|loc| {
        collect_source_context(loc, &execution.working_directory, &config.source_context).ok()
    });

    let build_context = if config.collect_build_context {
        Some(detect_build_context(&execution.executable))
    } else {
        None
    };

    Ok(ErrorContext {
        executable: execution.executable,
        arguments: execution.arguments,
        working_directory: execution.working_directory,
        exit_code: execution.exit_code,
        signal: execution.signal,
        stdout: execution.stdout,
        stderr: execution.stderr,
        classification,
        source_location,
        source_context,
        build_context,
        duration: execution.duration,
        success: execution.success,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::execution::{CommandSpec, execute_target};
    use std::path::Path;

    fn quiet_config() -> ErrorContextConfig {
        ErrorContextConfig {
            collect_build_context: false,
            ..ErrorContextConfig::default()
        }
    }

    fn shell_spec(script: &str) -> CommandSpec {
        CommandSpec::new(
            PathBuf::from("/bin/sh"),
            vec!["-c".to_string(), script.to_string()],
        )
    }

    #[test]
    fn successful_run_classifies_as_success() {
        let exec = execute_target(&shell_spec("exit 0")).unwrap();
        let ctx = collect_error_context(exec, &quiet_config()).unwrap();
        assert_eq!(ctx.classification, FailureClassification::Success);
        assert!(ctx.source_location.is_none());
    }

    #[test]
    fn segfault_run_classifies_correctly() {
        let exec = execute_target(&shell_spec("kill -SEGV $$")).unwrap();
        let ctx = collect_error_context(exec, &quiet_config()).unwrap();
        assert_eq!(ctx.classification, FailureClassification::SegmentationFault);
    }

    #[test]
    fn source_location_and_context_are_populated_when_available() {
        let dir = std::env::temp_dir().join(format!("tracecli-errctx-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let src_path = dir.join("broken.cpp");
        let contents: String = (1..=20).map(|i| format!("line {i}\n")).collect();
        std::fs::write(&src_path, contents).unwrap();

        let mut spec = shell_spec("echo 'broken.cpp:10:3: error: bad token' 1>&2; exit 1");
        spec.working_directory = Some(dir.clone());
        let exec = execute_target(&spec).unwrap();
        let ctx = collect_error_context(exec, &quiet_config()).unwrap();

        assert_eq!(ctx.classification, FailureClassification::CompilationError);
        let loc = ctx.source_location.expect("expected a source location");
        assert_eq!(loc.line, 10);
        let src_ctx = ctx.source_context.expect("expected source context");
        assert_eq!(src_ctx.highlighted_line, 10);
        assert!(src_ctx.lines.contains(&"line 10".to_string()));

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn serializes_to_json() {
        let exec = execute_target(&shell_spec("exit 3")).unwrap();
        let ctx = collect_error_context(exec, &quiet_config()).unwrap();
        let json = serde_json::to_string(&ctx).unwrap();
        assert!(json.contains("\"classification\""));
        let _: ErrorContext = serde_json::from_str(&json).unwrap();
    }

    #[test]
    fn malformed_stderr_does_not_panic_or_produce_bogus_location() {
        let exec = execute_target(&shell_spec(
            "printf '::garbage::\\x00\\x01not-a-line\\n' 1>&2; exit 1",
        ))
        .unwrap();
        let ctx = collect_error_context(exec, &quiet_config()).unwrap();
        assert_eq!(ctx.classification, FailureClassification::RuntimeError);
        assert!(ctx.source_location.is_none());
    }

    #[test]
    fn unresolvable_source_file_yields_no_source_context() {
        let exec = execute_target(&shell_spec(
            "echo 'ghost.cpp:5:1: error: nope' 1>&2; exit 1",
        ))
        .unwrap();
        let ctx = collect_error_context(exec, &quiet_config()).unwrap();
        assert!(ctx.source_location.is_some());
        assert!(ctx.source_context.is_none());
        let _ = Path::new("unused");
    }
}
