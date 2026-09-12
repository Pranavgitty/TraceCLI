//! Extraction of source file/line/column/function references from freeform
//! compiler or runtime output.
//!
//! This is deliberately a small, extensible interface rather than a general
//! parser framework: [`SourceLocationExtractor`] is the seam later work can
//! use to add extractors for other languages or tools. C++ is the only
//! implementation required for this component.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

/// A single source location referenced in program or compiler output.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceLocation {
    pub file: PathBuf,
    pub line: u32,
    pub column: Option<u32>,
    pub function: Option<String>,
}

/// Extracts candidate [`SourceLocation`]s from a block of text.
///
/// Implementations should be tolerant of arbitrary/malformed input: never
/// panic, and prefer returning no matches over a wrong one.
pub trait SourceLocationExtractor {
    /// Returns every location this extractor can find, in the order
    /// encountered.
    fn extract_all(&self, text: &str) -> Vec<SourceLocation>;

    /// Returns the first (typically most relevant) location found, if any.
    fn extract_first(&self, text: &str) -> Option<SourceLocation> {
        self.extract_all(text).into_iter().next()
    }
}

/// Recognizes the source-location shapes emitted by gcc/clang diagnostics
/// and by glibc's `assert()` macro:
///
/// - `path/to/file.cpp:142:17: error: message`
/// - `path/to/file.cpp:142: error: message` (no column)
/// - `path/to/file.cpp:142: void foo(): Assertion 'x' failed.` (assert-style)
/// - a bare `path/to/file.cpp:142:17` with no trailing message
pub struct CppSourceLocationExtractor {
    extensions: Vec<&'static str>,
}

impl Default for CppSourceLocationExtractor {
    fn default() -> Self {
        Self {
            extensions: vec![
                ".cpp", ".cc", ".cxx", ".c++", ".c", ".h", ".hpp", ".hh", ".hxx",
            ],
        }
    }
}

impl CppSourceLocationExtractor {
    pub fn new() -> Self {
        Self::default()
    }

    fn has_recognized_extension(&self, token: &str) -> bool {
        let lower = token.to_ascii_lowercase();
        self.extensions.iter().any(|ext| lower.ends_with(ext))
    }

    /// Tries to parse a single line as `file:line[:col][: rest]`.
    fn parse_line(&self, line: &str) -> Option<SourceLocation> {
        let line = line.trim();
        if line.is_empty() {
            return None;
        }

        let parts: Vec<&str> = line.splitn(4, ':').collect();
        if parts.len() < 2 {
            return None;
        }

        let file_token = parts[0];
        if file_token.len() < 3
            || file_token.contains(char::is_whitespace)
            || !self.has_recognized_extension(file_token)
        {
            return None;
        }

        let line_no: u32 = parts[1].trim().parse().ok()?;

        let mut column: Option<u32> = None;
        let mut rest_index = 2;
        if parts.len() > 2
            && let Ok(col) = parts[2].trim().parse::<u32>()
        {
            column = Some(col);
            rest_index = 3;
        }

        let function = parts
            .get(rest_index)
            .and_then(|rest| extract_function_hint(rest));

        Some(SourceLocation {
            file: PathBuf::from(file_token),
            line: line_no,
            column,
            function,
        })
    }

    /// Parses the BSD/macOS libc `assert()` message shape, comma-separated
    /// as `Assertion failed: (cond), function NAME, file FILE, line N.`
    /// (column is occasionally present too). This is unrelated to the
    /// glibc colon-joined shape handled by [`Self::parse_line`].
    fn parse_assert_style(&self, line: &str) -> Option<SourceLocation> {
        let mut file: Option<&str> = None;
        let mut line_no: Option<u32> = None;
        let mut column: Option<u32> = None;
        let mut function: Option<String> = None;

        for segment in line.split(',') {
            let segment = segment.trim();
            if let Some(rest) = segment.strip_prefix("file ") {
                if self.has_recognized_extension(rest) {
                    file = Some(rest);
                }
            } else if let Some(rest) = segment.strip_prefix("line ") {
                line_no = rest.trim_end_matches('.').trim().parse().ok();
            } else if let Some(rest) = segment.strip_prefix("column ") {
                column = rest.trim_end_matches('.').trim().parse().ok();
            } else if let Some(rest) = segment.strip_prefix("function ") {
                function = Some(rest.trim_end_matches('.').trim().to_string());
            }
        }

        Some(SourceLocation {
            file: PathBuf::from(file?),
            line: line_no?,
            column,
            function,
        })
    }
}

/// Pulls a plausible function name out of assert-style trailer text like
/// `" void foo(int): Assertion \`x' failed."`. Returns `None` for
/// diagnostic-severity words (`error`, `warning`, `note`) which are not
/// function names.
fn extract_function_hint(rest: &str) -> Option<String> {
    let candidate = rest.split(':').next()?.trim();
    if candidate.is_empty() {
        return None;
    }
    let lower = candidate.to_ascii_lowercase();
    if lower.starts_with("error") || lower.starts_with("warning") || lower.starts_with("note") {
        return None;
    }
    if candidate.contains('(') {
        Some(candidate.to_string())
    } else {
        None
    }
}

impl SourceLocationExtractor for CppSourceLocationExtractor {
    fn extract_all(&self, text: &str) -> Vec<SourceLocation> {
        text.lines()
            .filter_map(|line| {
                self.parse_line(line)
                    .or_else(|| self.parse_assert_style(line))
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_gcc_style_diagnostic_with_column() {
        let extractor = CppSourceLocationExtractor::new();
        let loc = extractor
            .extract_first("parser.cpp:142:17: error: expected ';' before '}' token")
            .expect("expected a match");
        assert_eq!(loc.file, PathBuf::from("parser.cpp"));
        assert_eq!(loc.line, 142);
        assert_eq!(loc.column, Some(17));
    }

    #[test]
    fn parses_absolute_path_without_message() {
        let extractor = CppSourceLocationExtractor::new();
        let loc = extractor
            .extract_first("/project/src/parser.cpp:142:17")
            .expect("expected a match");
        assert_eq!(loc.file, PathBuf::from("/project/src/parser.cpp"));
        assert_eq!(loc.line, 142);
        assert_eq!(loc.column, Some(17));
    }

    #[test]
    fn parses_assert_style_without_column_and_extracts_function() {
        let extractor = CppSourceLocationExtractor::new();
        let loc = extractor
            .extract_first("main.cpp:142: void foo(int): Assertion `x > 0' failed.")
            .expect("expected a match");
        assert_eq!(loc.file, PathBuf::from("main.cpp"));
        assert_eq!(loc.line, 142);
        assert_eq!(loc.column, None);
        assert_eq!(loc.function.as_deref(), Some("void foo(int)"));
    }

    #[test]
    fn parses_macos_libc_assert_message() {
        let extractor = CppSourceLocationExtractor::new();
        let loc = extractor
            .extract_first(
                "Assertion failed: (x == 6), function main, file assert_fail.cpp, line 4.",
            )
            .expect("expected a match");
        assert_eq!(loc.file, PathBuf::from("assert_fail.cpp"));
        assert_eq!(loc.line, 4);
        assert_eq!(loc.column, None);
        assert_eq!(loc.function.as_deref(), Some("main"));
    }

    #[test]
    fn ignores_lines_without_a_recognized_extension() {
        let extractor = CppSourceLocationExtractor::new();
        assert!(
            extractor
                .extract_first("this is just some text: not a file")
                .is_none()
        );
    }

    #[test]
    fn does_not_panic_on_malformed_input() {
        let extractor = CppSourceLocationExtractor::new();
        let garbage = "::::\n\0\0\0\ncpp.cpp:\n.cpp:abc:def\n:::.cpp:1:2";
        // Must not panic; matches are not required to be meaningful.
        let _ = extractor.extract_all(garbage);
    }

    #[test]
    fn picks_first_of_multiple_matches_and_ignores_prose_prefix() {
        let extractor = CppSourceLocationExtractor::new();
        let text = "In file included from main.cpp:1:\nparser.cpp:142:17: error: bad token\nlexer.cpp:9:1: note: see here";
        let locs = extractor.extract_all(text);
        // The "In file included from ..." line contains whitespace before
        // the ".cpp" token and is correctly rejected as not a bare path.
        assert_eq!(locs.len(), 2);
        assert_eq!(locs[0].file, PathBuf::from("parser.cpp"));
        assert_eq!(locs[1].file, PathBuf::from("lexer.cpp"));
    }
}
