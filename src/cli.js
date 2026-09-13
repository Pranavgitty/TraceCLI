'use strict';

const { Ok, Err, invalidArguments } = require('./errors');
const { parseU32 } = require('./textUtil');

// Minimal hand-rolled CLI argument parsing -- mirrors the Rust/C++ ports
// exactly. One subcommand at a time, a single optional tracecli-owned
// flag, then the target executable and its arguments passed through
// verbatim. Once a token that isn't a recognized tracecli flag is seen,
// everything from there on (including tokens that look like flags)
// belongs to the target program.

function parseCommand(args, expectedCommand, missingCommandMessage, unknownCommandFn, missingExecutableMessage) {
  let i = 0;

  if (i >= args.length) {
    return Err(invalidArguments(missingCommandMessage));
  }
  if (args[i] !== expectedCommand) {
    return Err(invalidArguments(unknownCommandFn(args[i])));
  }
  i += 1;

  let contextLines;
  let executable;
  const targetArguments = [];

  for (; i < args.length; i++) {
    const arg = args[i];
    if (executable === undefined && arg === '--context-lines') {
      if (i + 1 >= args.length) {
        return Err(invalidArguments('--context-lines requires a value'));
      }
      i += 1;
      const value = parseU32(args[i]);
      if (value === undefined) {
        return Err(invalidArguments(`--context-lines expects a number, got '${args[i]}'`));
      }
      contextLines = value;
      continue;
    }

    if (executable === undefined) {
      executable = arg;
    } else {
      targetArguments.push(arg);
    }
  }

  if (executable === undefined) {
    return Err(invalidArguments(missingExecutableMessage));
  }

  return Ok({ executable, targetArguments, contextLines });
}

/** Expected shape: `run [--context-lines N] <executable> [target-args...]`. */
function parseArgs(args) {
  return parseCommand(
    args,
    'run',
    "no command given; expected 'run <executable> [args...]'",
    (other) => `unknown command '${other}'; expected 'run'`,
    "no target executable given; expected 'run <executable> [args...]'"
  );
}

/** Expected shape: `diagnose [--context-lines N] <executable> [target-args...]`. */
function parseDiagnoseArgs(args) {
  return parseCommand(
    args,
    'diagnose',
    "no command given; expected 'diagnose <executable> [args...]'",
    (other) => `unknown command '${other}'; expected 'diagnose'`,
    "no target executable given; expected 'diagnose <executable> [args...]'"
  );
}

module.exports = { parseArgs, parseDiagnoseArgs };
