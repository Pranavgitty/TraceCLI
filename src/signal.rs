//! Best-effort POSIX signal identification.
//!
//! We deliberately avoid a libc dependency for this: the set of signals we
//! actually need to name for classification purposes is small and stable.

use serde::{Deserialize, Serialize};

/// Information about a POSIX signal that terminated a process.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SignalInfo {
    /// Raw signal number (e.g. `11` for `SIGSEGV`).
    pub number: i32,
    /// Symbolic name, if recognized (e.g. `"SIGSEGV"`).
    pub name: Option<String>,
    /// Whether the OS reported that a core dump was produced.
    pub core_dumped: bool,
}

impl SignalInfo {
    pub fn new(number: i32, core_dumped: bool) -> Self {
        Self {
            number,
            name: signal_name(number).map(str::to_string),
            core_dumped,
        }
    }
}

/// Maps common POSIX signal numbers to their symbolic names.
///
/// Only the signals relevant to diagnosing native crashes are covered; an
/// unrecognized number yields `None` rather than a guess.
///
/// Numbering here follows macOS/BSD (`sys/signal.h`), which is the
/// development and test platform for this component. The signals that
/// drive classification (`SIGSEGV`=11, `SIGABRT`=6, `SIGFPE`=8, `SIGILL`=4)
/// share the same numbers on Linux; `SIGBUS`, `SIGSYS`, `SIGUSR1`, and
/// `SIGUSR2` do not, so their names may be misreported when TraceCLI is
/// eventually run on Linux. That is a known platform limitation, not a
/// correctness target for this MVP.
pub fn signal_name(number: i32) -> Option<&'static str> {
    match number {
        1 => Some("SIGHUP"),
        2 => Some("SIGINT"),
        3 => Some("SIGQUIT"),
        4 => Some("SIGILL"),
        5 => Some("SIGTRAP"),
        6 => Some("SIGABRT"),
        7 => Some("SIGEMT"),
        8 => Some("SIGFPE"),
        9 => Some("SIGKILL"),
        10 => Some("SIGBUS"),
        11 => Some("SIGSEGV"),
        12 => Some("SIGSYS"),
        13 => Some("SIGPIPE"),
        14 => Some("SIGALRM"),
        15 => Some("SIGTERM"),
        30 => Some("SIGUSR1"),
        31 => Some("SIGUSR2"),
        _ => None,
    }
}
