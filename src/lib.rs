//! # tracecli — Error & Project Context Layer
//!
//! This crate is *Part 1* of TraceCLI: it runs a target C++ program,
//! captures what happened (stdout, stderr, exit code, signal, timing), and
//! normalizes that into a stable, serializable [`ErrorContext`].
//!
//! It deliberately does **not**: diagnose root causes, drive a debugger,
//! call an LLM, or depend on GDB/Python. Those are later stages in the
//! TraceCLI pipeline and consume this crate's output.
//!
//! ## Typical usage
//!
//! ```no_run
//! use tracecli::{execute_target, collect_error_context, CommandSpec, ErrorContextConfig};
//!
//! let spec = CommandSpec::new("./my_program", vec!["arg1".into()]);
//! let execution = execute_target(&spec)?;
//! let context = collect_error_context(execution, &ErrorContextConfig::default())?;
//! println!("{}", serde_json::to_string_pretty(&context)?);
//! # Ok::<(), Box<dyn std::error::Error>>(())
//! ```

pub mod build_context;
pub mod classification;
pub mod error;
pub mod error_context;
pub mod execution;
pub mod signal;
pub mod source_context;
pub mod source_location;

pub use build_context::{BuildContext, detect_build_context};
pub use classification::{FailureClassification, classify};
pub use error::TraceError;
pub use error_context::{ErrorContext, ErrorContextConfig, collect_error_context};
pub use execution::{CommandSpec, ExecutionResult, execute_target};
pub use signal::SignalInfo;
pub use source_context::{SourceContext, SourceContextConfig, collect_source_context};
pub use source_location::{CppSourceLocationExtractor, SourceLocation, SourceLocationExtractor};
