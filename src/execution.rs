//! Running a target program and capturing raw evidence about how it exited.
//!
//! This module does exactly one job: spawn the process, wait for it, and
//! record what happened. It does not interpret the result — see
//! [`crate::error_context`] for turning an [`ExecutionResult`] into a
//! normalized [`crate::error_context::ErrorContext`].

use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::error::TraceError;
use crate::signal::SignalInfo;

/// What to run and how.
#[derive(Debug, Clone)]
pub struct CommandSpec {
    pub executable: PathBuf,
    pub arguments: Vec<String>,
    /// Defaults to the current process's working directory when `None`.
    pub working_directory: Option<PathBuf>,
}

impl CommandSpec {
    pub fn new(executable: impl Into<PathBuf>, arguments: Vec<String>) -> Self {
        Self {
            executable: executable.into(),
            arguments,
            working_directory: None,
        }
    }
}

/// Raw evidence collected from running a target program to completion.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionResult {
    pub executable: PathBuf,
    pub arguments: Vec<String>,
    pub working_directory: PathBuf,
    pub exit_code: Option<i32>,
    pub signal: Option<SignalInfo>,
    pub stdout: String,
    pub stderr: String,
    #[serde(with = "duration_millis")]
    pub duration: Duration,
    /// True iff the process exited with status 0 and was not signaled.
    pub success: bool,
}

/// Executes `spec` to completion and captures its outcome.
///
/// Returns a structured [`TraceError`] (never panics) for expected failure
/// modes: missing executable, permission problems, an invalid working
/// directory, or an OS-level spawn failure.
pub fn execute_target(spec: &CommandSpec) -> Result<ExecutionResult, TraceError> {
    let working_directory = match &spec.working_directory {
        Some(dir) => {
            if !dir.is_dir() {
                return Err(TraceError::InvalidWorkingDirectory(dir.clone()));
            }
            dir.clone()
        }
        None => std::env::current_dir()
            .map_err(|_| TraceError::InvalidWorkingDirectory(PathBuf::from(".")))?,
    };

    // No proactive existence check here: a bare name (e.g. `"ls"`) is
    // legitimately resolved via `PATH` by `Command` itself, so we let the
    // OS make that determination and translate its error below.
    let start = Instant::now();
    let output = Command::new(&spec.executable)
        .args(&spec.arguments)
        .current_dir(&working_directory)
        .output();
    let duration = start.elapsed();

    let output = match output {
        Ok(output) => output,
        Err(err) => return Err(classify_spawn_error(&spec.executable, err)),
    };

    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();

    let exit_code = output.status.code();
    let signal = unix_signal(&output.status);
    let success = output.status.success();

    Ok(ExecutionResult {
        executable: spec.executable.clone(),
        arguments: spec.arguments.clone(),
        working_directory,
        exit_code,
        signal,
        stdout,
        stderr,
        duration,
        success,
    })
}

fn classify_spawn_error(executable: &Path, err: io::Error) -> TraceError {
    match err.kind() {
        io::ErrorKind::NotFound => TraceError::ExecutableNotFound(executable.to_path_buf()),
        io::ErrorKind::PermissionDenied => TraceError::PermissionDenied(executable.to_path_buf()),
        _ => TraceError::SpawnFailed {
            path: executable.to_path_buf(),
            source: err,
        },
    }
}

#[cfg(unix)]
fn unix_signal(status: &std::process::ExitStatus) -> Option<SignalInfo> {
    use std::os::unix::process::ExitStatusExt;
    status
        .signal()
        .map(|num| SignalInfo::new(num, status.core_dumped()))
}

#[cfg(not(unix))]
fn unix_signal(_status: &std::process::ExitStatus) -> Option<SignalInfo> {
    None
}

pub(crate) mod duration_millis {
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::time::Duration;

    pub fn serialize<S: Serializer>(d: &Duration, s: S) -> Result<S::Ok, S::Error> {
        (d.as_millis() as u64).serialize(s)
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(d: D) -> Result<Duration, D::Error> {
        let millis = u64::deserialize(d)?;
        Ok(Duration::from_millis(millis))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shell_spec(script: &str) -> CommandSpec {
        CommandSpec::new(
            PathBuf::from("/bin/sh"),
            vec!["-c".to_string(), script.to_string()],
        )
    }

    #[test]
    fn successful_program_is_captured() {
        let result = execute_target(&shell_spec("exit 0")).unwrap();
        assert!(result.success);
        assert_eq!(result.exit_code, Some(0));
        assert!(result.signal.is_none());
    }

    #[test]
    fn nonzero_exit_is_captured() {
        let result = execute_target(&shell_spec("exit 7")).unwrap();
        assert!(!result.success);
        assert_eq!(result.exit_code, Some(7));
    }

    #[test]
    fn stdout_and_stderr_are_captured_separately() {
        let result = execute_target(&shell_spec("echo out-line; echo err-line 1>&2")).unwrap();
        assert_eq!(result.stdout.trim(), "out-line");
        assert_eq!(result.stderr.trim(), "err-line");
    }

    #[test]
    fn sigsegv_is_captured() {
        // kill -SEGV $$ signals the shell itself.
        let result = execute_target(&shell_spec("kill -SEGV $$")).unwrap();
        assert!(!result.success);
        assert_eq!(result.exit_code, None);
        let sig = result.signal.expect("expected a signal");
        assert_eq!(sig.name.as_deref(), Some("SIGSEGV"));
    }

    #[test]
    fn sigabrt_is_captured() {
        let result = execute_target(&shell_spec("kill -ABRT $$")).unwrap();
        assert!(!result.success);
        let sig = result.signal.expect("expected a signal");
        assert_eq!(sig.name.as_deref(), Some("SIGABRT"));
    }

    #[test]
    fn missing_executable_is_a_structured_error() {
        let spec = CommandSpec::new(PathBuf::from("/no/such/path/tracecli-fixture"), vec![]);
        let err = execute_target(&spec).unwrap_err();
        assert!(matches!(err, TraceError::ExecutableNotFound(_)));
    }

    #[test]
    fn invalid_working_directory_is_a_structured_error() {
        let mut spec = shell_spec("exit 0");
        spec.working_directory = Some(PathBuf::from("/no/such/directory/at/all"));
        let err = execute_target(&spec).unwrap_err();
        assert!(matches!(err, TraceError::InvalidWorkingDirectory(_)));
    }

    #[test]
    fn duration_is_recorded() {
        let result = execute_target(&shell_spec("exit 0")).unwrap();
        assert!(result.duration.as_millis() < 5000);
    }
}
