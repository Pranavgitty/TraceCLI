//! Bounded excerpts of source files around a detected failure location.
//!
//! Deliberately never loads a whole source tree, and by default never loads
//! a whole file either: only a configurable window of lines around the
//! target line is captured, so the API cannot be used to exfiltrate
//! unrelated project source.

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::TraceError;
use crate::source_location::SourceLocation;

/// A bounded excerpt of a source file surrounding a failing line.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceContext {
    pub file: PathBuf,
    /// 1-indexed, inclusive.
    pub start_line: u32,
    /// 1-indexed, inclusive.
    pub end_line: u32,
    /// The line the failure was reported on (matches [`SourceLocation::line`]).
    pub highlighted_line: u32,
    /// Source lines from `start_line` to `end_line`, in order.
    pub lines: Vec<String>,
}

/// Controls how much source context is collected.
#[derive(Debug, Clone, Copy)]
pub struct SourceContextConfig {
    /// Number of lines of context to include on each side of the failing
    /// line. A value of `5` around line 142 yields lines 137-147.
    pub context_lines: u32,
    /// Source files larger than this are skipped rather than read, to
    /// avoid loading unexpectedly huge files. Bytes.
    pub max_file_size: u64,
}

impl Default for SourceContextConfig {
    fn default() -> Self {
        Self {
            context_lines: 5,
            max_file_size: 2 * 1024 * 1024,
        }
    }
}

/// Resolves `location.file` against `working_directory` when it is not
/// already absolute, then reads a bounded window of lines around
/// `location.line`.
///
/// Returns [`TraceError::SourceFileUnavailable`] when the file cannot be
/// found or is too large; this is expected to be treated as "no context
/// available" by callers rather than a fatal error.
pub fn collect_source_context(
    location: &SourceLocation,
    working_directory: &Path,
    config: &SourceContextConfig,
) -> Result<SourceContext, TraceError> {
    let resolved = resolve_source_path(&location.file, working_directory)
        .ok_or_else(|| TraceError::SourceFileUnavailable(location.file.clone()))?;

    let metadata = fs::metadata(&resolved)
        .map_err(|_| TraceError::SourceFileUnavailable(location.file.clone()))?;
    if metadata.len() > config.max_file_size {
        return Err(TraceError::SourceFileUnavailable(location.file.clone()));
    }

    let contents = fs::read_to_string(&resolved)
        .map_err(|_| TraceError::SourceFileUnavailable(location.file.clone()))?;

    let all_lines: Vec<&str> = contents.lines().collect();
    if all_lines.is_empty() {
        return Err(TraceError::SourceFileUnavailable(location.file.clone()));
    }

    let target = location.line.max(1);
    let start_line = target.saturating_sub(config.context_lines).max(1);
    let end_line = (target + config.context_lines).min(all_lines.len() as u32);

    if start_line > all_lines.len() as u32 {
        return Err(TraceError::SourceFileUnavailable(location.file.clone()));
    }

    let lines = all_lines[(start_line - 1) as usize..end_line as usize]
        .iter()
        .map(|s| s.to_string())
        .collect();

    Ok(SourceContext {
        file: location.file.clone(),
        start_line,
        end_line,
        highlighted_line: target,
        lines,
    })
}

fn resolve_source_path(file: &Path, working_directory: &Path) -> Option<PathBuf> {
    if file.is_absolute() {
        return file.exists().then(|| file.to_path_buf());
    }
    let candidate = working_directory.join(file);
    if candidate.exists() {
        return Some(candidate);
    }
    file.exists().then(|| file.to_path_buf())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn write_numbered_lines_file(dir: &Path, name: &str, count: u32) -> PathBuf {
        let path = dir.join(name);
        let mut f = fs::File::create(&path).unwrap();
        for i in 1..=count {
            writeln!(f, "line {i}").unwrap();
        }
        path
    }

    #[test]
    fn extracts_bounded_window_around_target_line() {
        let dir = tempdir();
        let path = write_numbered_lines_file(dir.path(), "sample.cpp", 200);
        let loc = SourceLocation {
            file: path.clone(),
            line: 142,
            column: Some(17),
            function: None,
        };
        let config = SourceContextConfig::default();
        let ctx = collect_source_context(&loc, dir.path(), &config).unwrap();
        assert_eq!(ctx.start_line, 137);
        assert_eq!(ctx.end_line, 147);
        assert_eq!(ctx.highlighted_line, 142);
        assert_eq!(ctx.lines.len(), 11);
        assert_eq!(ctx.lines[0], "line 137");
        assert_eq!(ctx.lines.last().unwrap(), "line 147");
    }

    #[test]
    fn clamps_window_near_start_and_end_of_file() {
        let dir = tempdir();
        let path = write_numbered_lines_file(dir.path(), "short.cpp", 5);
        let loc = SourceLocation {
            file: path,
            line: 1,
            column: None,
            function: None,
        };
        let config = SourceContextConfig::default();
        let ctx = collect_source_context(&loc, dir.path(), &config).unwrap();
        assert_eq!(ctx.start_line, 1);
        assert_eq!(ctx.end_line, 5);
        assert_eq!(ctx.lines.len(), 5);
    }

    #[test]
    fn missing_file_is_reported_as_source_file_unavailable() {
        let dir = tempdir();
        let loc = SourceLocation {
            file: PathBuf::from("does_not_exist.cpp"),
            line: 10,
            column: None,
            function: None,
        };
        let config = SourceContextConfig::default();
        let err = collect_source_context(&loc, dir.path(), &config).unwrap_err();
        assert!(matches!(err, TraceError::SourceFileUnavailable(_)));
    }

    #[test]
    fn oversized_file_is_skipped_rather_than_read() {
        let dir = tempdir();
        let path = write_numbered_lines_file(dir.path(), "big.cpp", 10);
        let loc = SourceLocation {
            file: path,
            line: 1,
            column: None,
            function: None,
        };
        let config = SourceContextConfig {
            context_lines: 5,
            max_file_size: 1,
        };
        let err = collect_source_context(&loc, dir.path(), &config).unwrap_err();
        assert!(matches!(err, TraceError::SourceFileUnavailable(_)));
    }

    /// Minimal self-contained temp dir helper (avoids adding the `tempfile`
    /// crate as a dependency for tests).
    struct TempDir(PathBuf);
    impl TempDir {
        fn path(&self) -> &Path {
            &self.0
        }
    }
    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn tempdir() -> TempDir {
        let mut dir = std::env::temp_dir();
        let unique = format!(
            "tracecli-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        dir.push(unique);
        fs::create_dir_all(&dir).unwrap();
        TempDir(dir)
    }
}
