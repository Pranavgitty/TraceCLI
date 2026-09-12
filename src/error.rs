//! Structured error type for the Error & Project Context Layer.

use std::fmt;
use std::path::PathBuf;

/// Errors that can occur while executing a target program or collecting
/// context about its failure.
///
/// These are always returned, never panicked, for expected failure modes
/// (bad paths, permission problems, spawn failures, ...).
#[derive(Debug)]
pub enum TraceError {
    /// The requested executable does not exist on disk.
    ExecutableNotFound(PathBuf),
    /// The requested executable exists but could not be executed due to
    /// filesystem permissions.
    PermissionDenied(PathBuf),
    /// The OS refused to spawn the process for a reason other than the two
    /// cases above (e.g. `ENOEXEC`, resource limits).
    SpawnFailed {
        path: PathBuf,
        source: std::io::Error,
    },
    /// The requested working directory does not exist or is not a directory.
    InvalidWorkingDirectory(PathBuf),
    /// A source file referenced by a detected source location could not be
    /// read (missing, unreadable, or too large to safely load).
    SourceFileUnavailable(PathBuf),
    /// Process output could not be interpreted (e.g. captured bytes could
    /// not be handled safely).
    MalformedOutput(String),
    /// The CLI was invoked with arguments that do not form a valid command.
    InvalidArguments(String),
}

impl fmt::Display for TraceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            TraceError::ExecutableNotFound(path) => {
                write!(f, "executable not found: {}", path.display())
            }
            TraceError::PermissionDenied(path) => {
                write!(f, "permission denied executing: {}", path.display())
            }
            TraceError::SpawnFailed { path, source } => {
                write!(f, "failed to spawn {}: {}", path.display(), source)
            }
            TraceError::InvalidWorkingDirectory(path) => {
                write!(f, "invalid working directory: {}", path.display())
            }
            TraceError::SourceFileUnavailable(path) => {
                write!(f, "source file unavailable: {}", path.display())
            }
            TraceError::MalformedOutput(msg) => {
                write!(f, "malformed process output: {}", msg)
            }
            TraceError::InvalidArguments(msg) => {
                write!(f, "invalid arguments: {}", msg)
            }
        }
    }
}

impl std::error::Error for TraceError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            TraceError::SpawnFailed { source, .. } => Some(source),
            _ => None,
        }
    }
}
