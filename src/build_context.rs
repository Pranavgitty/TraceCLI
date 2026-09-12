//! Best-effort information about how the target executable was built.
//!
//! This intentionally does not parse build systems (CMake/Make/Cargo). Per
//! the component's scope, that would be an architectural decision requiring
//! sign-off; see the unresolved design gap noted in the project report.
//! What's implemented here covers the common case only: which compiler is
//! on `PATH` (or named by `CXX`/`CC`), and a weak heuristic for whether the
//! binary retains debug symbols.

use std::path::{Path, PathBuf};
use std::process::Command;

use serde::{Deserialize, Serialize};

/// Best-effort facts about the target executable's build, gathered without
/// any build-system-specific parsing.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BuildContext {
    pub executable_path: PathBuf,
    /// Name of a C/C++ compiler found via `CXX`/`CC` or common binary
    /// names on `PATH`. This is a system-wide signal, not proof the
    /// executable was built with it.
    pub compiler: Option<String>,
    pub compiler_version: Option<String>,
    /// Heuristic: `Some(true)` if the binary appears to retain debug/symbol
    /// information, `Some(false)` if it appears stripped, `None` if this
    /// could not be determined on the current platform.
    pub debug_build: Option<bool>,
    /// Not populated in this MVP: would require build-system-specific
    /// discovery (CMake/Make/Cargo), which is out of scope pending a
    /// design decision. Reserved for later extension.
    pub build_configuration: Option<String>,
}

/// Detects best-effort build context for `executable`. Never fails: any
/// individual signal that can't be determined is simply `None`.
pub fn detect_build_context(executable: &Path) -> BuildContext {
    let (compiler, compiler_version) = detect_compiler();
    BuildContext {
        executable_path: executable.to_path_buf(),
        compiler,
        compiler_version,
        debug_build: detect_debug_build(executable),
        build_configuration: None,
    }
}

fn detect_compiler() -> (Option<String>, Option<String>) {
    let candidates: Vec<String> = std::env::var("CXX")
        .into_iter()
        .chain(std::env::var("CC"))
        .chain(
            ["c++", "g++", "clang++", "cc", "gcc", "clang"]
                .iter()
                .map(|s| s.to_string()),
        )
        .collect();

    for name in candidates {
        if let Ok(output) = Command::new(&name).arg("--version").output()
            && output.status.success()
        {
            let text = String::from_utf8_lossy(&output.stdout);
            let first_line = text.lines().next().unwrap_or("").trim().to_string();
            return (Some(name), Some(first_line));
        }
    }
    (None, None)
}

/// Weak heuristic for whether `executable` retains debug/symbol
/// information. Tries `file(1)` first (reports "not stripped" on many
/// platforms), then falls back to `nm(1)` producing any symbol output.
/// Returns `None` if neither tool is usable or the result is ambiguous.
fn detect_debug_build(executable: &Path) -> Option<bool> {
    if let Ok(output) = Command::new("file").arg(executable).output()
        && output.status.success()
    {
        let text = String::from_utf8_lossy(&output.stdout).to_lowercase();
        if text.contains("not stripped") {
            return Some(true);
        }
        if text.contains("stripped") {
            return Some(false);
        }
    }

    if let Ok(output) = Command::new("nm").arg("-p").arg(executable).output() {
        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr).to_lowercase();
        if stderr.contains("no symbols") {
            return Some(false);
        }
        if output.status.success() && !stdout.trim().is_empty() {
            return Some(true);
        }
    }

    None
}
