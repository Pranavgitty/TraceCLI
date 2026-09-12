mod cli;

use std::process::ExitCode;

use tracecli::{
    CommandSpec, ErrorContextConfig, SourceContextConfig, collect_error_context, execute_target,
};

/// tracecli's own exit code when it cannot even attempt to run the target
/// (bad arguments, missing executable, etc.) — distinct from any exit code
/// the target program itself might produce.
const TRACECLI_ERROR_EXIT: u8 = 2;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();

    let run_args = match cli::parse_args(&args) {
        Ok(run_args) => run_args,
        Err(err) => {
            eprintln!("tracecli: {err}");
            eprintln!("usage: tracecli run [--context-lines N] <executable> [args...]");
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
