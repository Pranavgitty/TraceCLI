//! Minimal hand-rolled CLI argument parsing.
//!
//! We avoid a CLI-parsing dependency here: the surface is one subcommand
//! (`run`) with a single optional tracecli-owned flag, plus a target
//! executable and its arguments passed through verbatim. Once a token that
//! isn't a recognized tracecli flag is seen, everything from there on
//! (including tokens that look like flags) belongs to the target program.

use std::path::PathBuf;

use tracecli::TraceError;

/// A parsed `tracecli run ...` invocation.
#[derive(Debug, Clone)]
pub struct RunArgs {
    pub executable: PathBuf,
    pub target_arguments: Vec<String>,
    pub context_lines: Option<u32>,
}

/// Parses argv (excluding the program name) into a [`RunArgs`].
///
/// Expected shape: `run [--context-lines N] <executable> [target-args...]`.
pub fn parse_args(args: &[String]) -> Result<RunArgs, TraceError> {
    let mut iter = args.iter();

    match iter.next() {
        Some(cmd) if cmd == "run" => {}
        Some(other) => {
            return Err(TraceError::InvalidArguments(format!(
                "unknown command '{other}'; expected 'run'"
            )));
        }
        None => {
            return Err(TraceError::InvalidArguments(
                "no command given; expected 'run <executable> [args...]'".to_string(),
            ));
        }
    }

    let mut context_lines = None;
    let mut executable: Option<PathBuf> = None;
    let mut target_arguments = Vec::new();

    while let Some(arg) = iter.next() {
        if executable.is_none() && arg == "--context-lines" {
            let value = iter.next().ok_or_else(|| {
                TraceError::InvalidArguments("--context-lines requires a value".to_string())
            })?;
            context_lines = Some(value.parse::<u32>().map_err(|_| {
                TraceError::InvalidArguments(format!(
                    "--context-lines expects a number, got '{value}'"
                ))
            })?);
            continue;
        }

        if executable.is_none() {
            executable = Some(PathBuf::from(arg));
        } else {
            target_arguments.push(arg.clone());
        }
    }

    let executable = executable.ok_or_else(|| {
        TraceError::InvalidArguments(
            "no target executable given; expected 'run <executable> [args...]'".to_string(),
        )
    })?;

    Ok(RunArgs {
        executable,
        target_arguments,
        context_lines,
    })
}

/// A parsed `tracecli diagnose ...` invocation (Part 4).
///
/// Deliberately a separate type from [`RunArgs`], and parsed by a separate
/// function below, rather than folded into [`parse_args`]: `run`'s parsing
/// and its tests stay untouched, so adding `diagnose` can never change
/// `run`'s behavior.
#[derive(Debug, Clone)]
pub struct DiagnoseArgs {
    pub executable: PathBuf,
    pub target_arguments: Vec<String>,
    pub context_lines: Option<u32>,
}

/// Parses argv (excluding the program name) into a [`DiagnoseArgs`].
///
/// Expected shape: `diagnose [--context-lines N] <executable> [target-args...]`.
pub fn parse_diagnose_args(args: &[String]) -> Result<DiagnoseArgs, TraceError> {
    let mut iter = args.iter();

    match iter.next() {
        Some(cmd) if cmd == "diagnose" => {}
        Some(other) => {
            return Err(TraceError::InvalidArguments(format!(
                "unknown command '{other}'; expected 'diagnose'"
            )));
        }
        None => {
            return Err(TraceError::InvalidArguments(
                "no command given; expected 'diagnose <executable> [args...]'".to_string(),
            ));
        }
    }

    let mut context_lines = None;
    let mut executable: Option<PathBuf> = None;
    let mut target_arguments = Vec::new();

    while let Some(arg) = iter.next() {
        if executable.is_none() && arg == "--context-lines" {
            let value = iter.next().ok_or_else(|| {
                TraceError::InvalidArguments("--context-lines requires a value".to_string())
            })?;
            context_lines = Some(value.parse::<u32>().map_err(|_| {
                TraceError::InvalidArguments(format!(
                    "--context-lines expects a number, got '{value}'"
                ))
            })?);
            continue;
        }

        if executable.is_none() {
            executable = Some(PathBuf::from(arg));
        } else {
            target_arguments.push(arg.clone());
        }
    }

    let executable = executable.ok_or_else(|| {
        TraceError::InvalidArguments(
            "no target executable given; expected 'diagnose <executable> [args...]'".to_string(),
        )
    })?;

    Ok(DiagnoseArgs {
        executable,
        target_arguments,
        context_lines,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_bare_executable() {
        let args = vec!["run".to_string(), "./program".to_string()];
        let parsed = parse_args(&args).unwrap();
        assert_eq!(parsed.executable, PathBuf::from("./program"));
        assert!(parsed.target_arguments.is_empty());
    }

    #[test]
    fn parses_executable_with_target_args() {
        let args = vec![
            "run".to_string(),
            "./program".to_string(),
            "arg1".to_string(),
            "arg2".to_string(),
        ];
        let parsed = parse_args(&args).unwrap();
        assert_eq!(parsed.target_arguments, vec!["arg1", "arg2"]);
    }

    #[test]
    fn target_args_that_look_like_flags_pass_through_untouched() {
        let args = vec![
            "run".to_string(),
            "./program".to_string(),
            "--context-lines".to_string(),
            "3".to_string(),
        ];
        let parsed = parse_args(&args).unwrap();
        // --context-lines appears AFTER the executable, so it belongs to
        // the target program, not tracecli.
        assert_eq!(parsed.context_lines, None);
        assert_eq!(parsed.target_arguments, vec!["--context-lines", "3"]);
    }

    #[test]
    fn parses_context_lines_flag_before_executable() {
        let args = vec![
            "run".to_string(),
            "--context-lines".to_string(),
            "8".to_string(),
            "./program".to_string(),
        ];
        let parsed = parse_args(&args).unwrap();
        assert_eq!(parsed.context_lines, Some(8));
        assert_eq!(parsed.executable, PathBuf::from("./program"));
    }

    #[test]
    fn missing_executable_is_invalid_arguments() {
        let args = vec!["run".to_string()];
        let err = parse_args(&args).unwrap_err();
        assert!(matches!(err, TraceError::InvalidArguments(_)));
    }

    #[test]
    fn unknown_command_is_invalid_arguments() {
        let args = vec!["debug".to_string(), "./program".to_string()];
        let err = parse_args(&args).unwrap_err();
        assert!(matches!(err, TraceError::InvalidArguments(_)));
    }

    #[test]
    fn no_command_is_invalid_arguments() {
        let err = parse_args(&[]).unwrap_err();
        assert!(matches!(err, TraceError::InvalidArguments(_)));
    }

    #[test]
    fn diagnose_parses_bare_executable() {
        let args = vec!["diagnose".to_string(), "./program".to_string()];
        let parsed = parse_diagnose_args(&args).unwrap();
        assert_eq!(parsed.executable, PathBuf::from("./program"));
        assert!(parsed.target_arguments.is_empty());
        assert_eq!(parsed.context_lines, None);
    }

    #[test]
    fn diagnose_parses_context_lines_and_target_args() {
        let args = vec![
            "diagnose".to_string(),
            "--context-lines".to_string(),
            "8".to_string(),
            "./program".to_string(),
            "arg1".to_string(),
        ];
        let parsed = parse_diagnose_args(&args).unwrap();
        assert_eq!(parsed.context_lines, Some(8));
        assert_eq!(parsed.executable, PathBuf::from("./program"));
        assert_eq!(parsed.target_arguments, vec!["arg1"]);
    }

    #[test]
    fn diagnose_rejects_run_command() {
        let args = vec!["run".to_string(), "./program".to_string()];
        let err = parse_diagnose_args(&args).unwrap_err();
        assert!(matches!(err, TraceError::InvalidArguments(_)));
    }

    #[test]
    fn diagnose_missing_executable_is_invalid_arguments() {
        let args = vec!["diagnose".to_string()];
        let err = parse_diagnose_args(&args).unwrap_err();
        assert!(matches!(err, TraceError::InvalidArguments(_)));
    }
}
