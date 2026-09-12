mod cli;

use std::path::PathBuf;
use std::process::{Command, ExitCode};

use tracecli::{
    CommandSpec, ErrorContextConfig, SourceContextConfig, collect_error_context, execute_target,
};

/// tracecli's own exit code when it cannot even attempt to run the target
/// (bad arguments, missing executable, etc.) — distinct from any exit code
/// the target program itself might produce.
const TRACECLI_ERROR_EXIT: u8 = 2;

/// Exit code for a `diagnose` pipeline failure (planner/debugger/diagnosis
/// unavailable, or the orchestrator itself couldn't be launched) — distinct
/// from `TRACECLI_ERROR_EXIT` so a broken investigation is distinguishable
/// from a bad CLI invocation.
const DIAGNOSE_PIPELINE_ERROR_EXIT: u8 = 3;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();

    match args.first().map(String::as_str) {
        Some("diagnose") => diagnose_main(&args),
        _ => run_main(&args),
    }
}

/// `tracecli run ...` — Part 1's original, unmodified pipeline.
fn run_main(args: &[String]) -> ExitCode {
    let run_args = match cli::parse_args(args) {
        Ok(run_args) => run_args,
        Err(err) => {
            eprintln!("tracecli: {err}");
            eprintln!("usage: tracecli run [--context-lines N] <executable> [args...]");
            eprintln!("       tracecli diagnose [--context-lines N] <executable> [args...]");
            return ExitCode::from(TRACECLI_ERROR_EXIT);
        }
    };

    let spec = CommandSpec::new(run_args.executable, run_args.target_arguments);

    let execution = match execute_target(&spec) {
        Ok(execution) => execution,
        Err(err) => {
            eprintln!("tracecli: {err}");
            return ExitCode::from(TRACECLI_ERROR_EXIT);
        }
    };

    let mut config = ErrorContextConfig::default();
    if let Some(lines) = run_args.context_lines {
        config.source_context = SourceContextConfig {
            context_lines: lines,
            ..SourceContextConfig::default()
        };
    }

    let exit_signal = execution.signal.as_ref().map(|s| s.number);
    let exit_code = execution.exit_code;

    let context = match collect_error_context(execution, &config) {
        Ok(context) => context,
        Err(err) => {
            eprintln!("tracecli: {err}");
            return ExitCode::from(TRACECLI_ERROR_EXIT);
        }
    };

    match serde_json::to_string_pretty(&context) {
        Ok(json) => println!("{json}"),
        Err(err) => {
            eprintln!("tracecli: failed to serialize error context: {err}");
            return ExitCode::from(TRACECLI_ERROR_EXIT);
        }
    }

    // Mirror the target's own exit status so tracecli behaves naturally in
    // shell scripts (like `time`): same exit code, or the conventional
    // 128+signal when the target was killed by a signal.
    match (exit_code, exit_signal) {
        (Some(code), _) => ExitCode::from((code & 0xFF) as u8),
        (None, Some(signal)) => ExitCode::from((128 + signal).clamp(0, 255) as u8),
        (None, None) => ExitCode::from(TRACECLI_ERROR_EXIT),
    }
}

/// `tracecli diagnose ...` (Part 4). Hands off to the Python orchestrator
/// (`tracecli_orchestrator`), which drives Part 2 (`tracecli_debugger`) and
/// Part 3 (`tracecli_llm`) in-process as Python libraries — no FFI, no
/// duplicated pipeline. This process's own path is passed through
/// `TRACECLI_BIN` so the orchestrator's first step (re-invoking `tracecli
/// run` to get the `ErrorContext` JSON) never depends on `PATH`.
fn diagnose_main(args: &[String]) -> ExitCode {
    let diagnose_args = match cli::parse_diagnose_args(args) {
        Ok(diagnose_args) => diagnose_args,
        Err(err) => {
            eprintln!("tracecli: {err}");
            eprintln!("usage: tracecli diagnose [--context-lines N] <executable> [args...]");
            return ExitCode::from(TRACECLI_ERROR_EXIT);
        }
    };

    let mut python_args: Vec<String> = vec!["-m".to_string(), "tracecli_orchestrator".to_string()];
    if let Some(lines) = diagnose_args.context_lines {
        python_args.push("--context-lines".to_string());
        python_args.push(lines.to_string());
    }
    python_args.push("--".to_string());
    python_args.push(diagnose_args.executable.to_string_lossy().into_owned());
    python_args.extend(diagnose_args.target_arguments);

    let tracecli_bin = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("tracecli"));

    let status = Command::new("python3")
        .args(&python_args)
        .env("TRACECLI_BIN", &tracecli_bin)
        .status();

    match status {
        Ok(status) => ExitCode::from(status.code().unwrap_or(1).clamp(0, 255) as u8),
        Err(err) => {
            eprintln!("tracecli: failed to launch the debugging orchestrator (python3): {err}");
            eprintln!(
                "tracecli: 'diagnose' requires Python 3.11+ with tracecli_orchestrator importable \
                 (run from the repository root, or set PYTHONPATH)"
            );
            ExitCode::from(DIAGNOSE_PIPELINE_ERROR_EXIT)
        }
    }
}
